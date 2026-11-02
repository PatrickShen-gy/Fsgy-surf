"""One entry point, dependency ordering and per-case reusable stage artifacts."""
from pathlib import Path
from importlib import import_module
import time
import numpy as np
from fsgy_surf import SOURCE
from fsgy_surf.config.case import load_case, TASKS
from fsgy_surf.io.files import digest, fingerprint, read_json, save_json

MODULES = {
    1: 'read_dxf', 2: 'cad_reconstruction', 3: 'bonjean_calculation',
    4: 'hydrostatics_calculation', 5: 'tank_capacity_calculation',
    6: 'weight_cg_calculation', 7: 'equilibrium_calculation',
    8: 'stability_calculation', 9: 'stability_criteria',
    10: 'longitudinal_strength', 11: 'hold_mass_check', 12: 'run_pipeline',
}
DEPENDENCIES = {1: [], 2: [1], 3: [2], 4: [2], 5: [2], 6: [5],
                7: [6], 8: [7], 9: [8], 10: [7], 11: [7], 12: []}
CONFIG_KEYS = {
    1: ['mother_ship'], 2: ['design', 'mesh', 'compartments'],
    3: ['bonjean'], 4: ['hydrostatics', 'environment'], 5: ['capacity', 'environment'],
    6: ['loading', 'environment'], 7: ['environment'], 8: ['stability', 'environment'],
    9: ['criteria', 'environment'], 10: ['longitudinal', 'environment'],
    11: ['hold_loads'], 12: ['name', 'tasks'],
}
# Some stages use a numerical class from another stage without needing its output table.
# Track those code dependencies separately from the calculation/data dependencies.
CODE_DEPENDENCIES = {4: [3], 5: [4], 7: [4], 8: [4], 10: [3]}


def code_files(number):
    stages = set()
    def include(n):
        stages.add(n)
        for dependency in CODE_DEPENDENCIES.get(n, []):
            if dependency not in stages:
                include(dependency)
    include(number)
    return sorted(p for n in stages for folder in SOURCE.glob(f'{n:02d}_*')
                  for p in folder.iterdir() if p.suffix in ('.py', '.html'))


class Context:
    def __init__(self, config, config_path):
        self.config = config
        self.config_path = config_path
        self.case_dir = config_path.parent
        self.cache_root = (self.case_dir / config.get('cache_dir', 'cache')).resolve()
        self.result_root = (self.case_dir / config.get('results_dir', 'results')).resolve()
        if self.cache_root == self.result_root:
            raise ValueError('cache_dir and results_dir must be different')
        self.data = {}
        self.run_records = []
        self._arrays = {}
        self._slicer = None
        self._hydro = None
        self._tanks = {}
        self._fills = {}
        self._fill_samples_loaded = False

    def cache(self, number):
        path = self.cache_root / f'{number:02d}'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def output(self, number):
        path = self.result_root / f'{number:02d}'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def arrays(self, path):
        key = str(path)
        if key not in self._arrays:
            with np.load(path, allow_pickle=False) as archive:
                self._arrays[key] = {name: archive[name] for name in archive.files}
        return self._arrays[key]

    def geometry(self):
        return self.arrays(self.data[2]['mesh_file'])

    def slicer(self):
        if self._slicer is None:
            from bonjean_calculation import MeshSlicer
            mesh = self.geometry()
            self._slicer = MeshSlicer(mesh['outer_vertices'], mesh['hydro_faces'])
        return self._slicer

    def hydro(self):
        if self._hydro is None:
            from hydrostatics_calculation import HydrostaticCalculator
            env = self.config['environment']
            dimensions = self.data[2]['dimensions']
            self._hydro = HydrostaticCalculator.from_npz(
                self.data[2]['mesh_file'], lbp=dimensions['lbp_m'],
                beam=dimensions['beam_m'], xref=dimensions['lbp_m'] / 2,
                rho=env['water_density_t_m3'],
                shell_thickness=env['hydro_shell_thickness_mm'] / 1000)
        return self._hydro

    def tank(self, ident):
        if ident not in self._tanks:
            from tank_capacity_calculation import TankCalculator
            mesh = self.arrays(self.data[2]['compartments_file'])
            if ident + '_vertices' not in mesh:
                raise ValueError(f'Unknown geometry_id: {ident}')
            block = next(space for space in self.data[2]['compartments'] if space['id'] == ident)
            self._tanks[ident] = TankCalculator(mesh[ident + '_vertices'], mesh[ident + '_faces'],
                                               full_properties=block)
        return self._tanks[ident]

    def fill(self, ident, fraction):
        fraction = float(fraction)
        if not self._fill_samples_loaded and 5 in self.data:
            self._fill_samples_loaded = True
            for row in read_json(self.data[5]['filling_file']):
                self._fills[(row['geometry_id'], round(row['requested_fill_fraction'], 12))] = {
                    k: v for k, v in row.items() if k != 'geometry_id'}
        key = (ident, round(fraction, 12))
        if key not in self._fills:
            self._fills[key] = self.tank(ident).at_fill(fraction)
        return dict(self._fills[key])


def selected_stages(config):
    selected = set()
    def include(number):
        for dependency in DEPENDENCIES[number]:
            include(dependency)
        selected.add(number)
    for task in config['tasks']:
        include(TASKS[task])
    if selected.intersection(range(6, 12)) and not config.get('loading'):
        raise ValueError('The selected tasks require user-supplied loading.cases')
    if 9 in selected and not config.get('criteria'):
        raise ValueError('criteria task requires criteria settings')
    if 11 in selected and not config.get('hold_loads', {}).get('envelopes'):
        raise ValueError('hold_loads task requires hold_loads.envelopes')
    return sorted(selected)


def run_case(path, force=False, tasks=None):
    config, config_path = load_case(path)
    if tasks is not None:
        if any(task not in TASKS for task in tasks):
            raise ValueError(f'Unknown task; choose from {list(TASKS)}')
        config['tasks'] = tasks
    selected = selected_stages(config)
    ctx = Context(config, config_path)
    signatures = {}
    common = fingerprint({str(p.relative_to(SOURCE)): digest(p)
                          for p in sorted((SOURCE / 'fsgy_surf').rglob('*.py'))})
    for number in selected:
        module = import_module(MODULES[number])
        files = code_files(number)
        signature_data = dict(common=common,
                              location=[str(ctx.cache_root), str(ctx.result_root)],
                              code={str(p.relative_to(SOURCE)): digest(p) for p in files},
                              config={k: config.get(k) for k in CONFIG_KEYS[number]},
                              dependencies={str(n): signatures[n] for n in DEPENDENCIES[number]})
        if number == 1:
            signature_data['drawings'] = {role: digest(filename) for role, filename
                                         in config['mother_ship']['drawings'].items()}
        if number == 12:
            signature_data['dependencies'] = dict(signatures)
        signature = fingerprint(signature_data)
        manifest_path = ctx.cache(number) / 'stage.json'
        old = read_json(manifest_path) if manifest_path.exists() else {}
        artifacts = old.get('artifacts', {})
        reusable = (not force and old.get('signature') == signature
                    and all(Path(p).is_file() and digest(p) == sha for p, sha in artifacts.items()))
        started = time.perf_counter()
        print(f'[{number:02d}] {"CACHE" if reusable else "RUN"} {module.__name__}', flush=True)
        if reusable:
            payload = old['data']
        else:
            payload = module.run(ctx)
            produced = [p for folder in (ctx.cache(number), ctx.output(number))
                        for p in folder.rglob('*') if p.is_file() and p != manifest_path]
            save_json(manifest_path, dict(signature=signature, data=payload,
                                         artifacts={str(p): digest(p) for p in produced}))
        signatures[number] = signature
        ctx.data[number] = payload
        ctx.run_records.append(dict(stage=number, reused=reusable,
                                    elapsed_seconds=round(time.perf_counter() - started, 3)))
    save_json(ctx.result_root / 'run.json', dict(name=config.get('name', config_path.parent.name),
              config_file=str(config_path), stages=ctx.run_records,
              outputs={str(n): data for n, data in ctx.data.items() if n != 1}))
    print(f'Results: {ctx.result_root}', flush=True)
    return ctx
