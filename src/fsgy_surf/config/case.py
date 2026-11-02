"""Case configuration, relative paths and user-provided loading data."""
from pathlib import Path
import math
from fsgy_surf.io.files import read_json

TASKS = {
    'input': 1, 'geometry': 2, 'bonjean': 3, 'hydrostatics': 4,
    'compartments': 5, 'weights': 6, 'equilibrium': 7, 'stability': 8,
    'criteria': 9, 'longitudinal': 10, 'hold_loads': 11, 'report': 12,
}


def positive(value, name, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be numeric')
    if not math.isfinite(value) or value < 0 or (not allow_zero and value == 0):
        raise ValueError(f'{name} must be finite and {"nonnegative" if allow_zero else "positive"}')
    return float(value)


def load_case(path):
    path = Path(path).resolve()
    config = read_json(path)
    if config.get('schema_version') != '1.0':
        raise ValueError('case.json requires schema_version = "1.0"')
    mother = config['mother_ship']
    if mother.get('adapter') != 'mother_ship_offsets_v1':
        raise ValueError('Supported DXF adapter: mother_ship_offsets_v1')
    drawings = mother['drawings']
    required = ('lines', 'general_arrangement', 'typical_section')
    for role in required:
        drawing = (path.parent / drawings[role]).resolve()
        if not drawing.is_file() or drawing.suffix.lower() != '.dxf':
            raise ValueError(f'mother_ship.drawings.{role}: DXF file not found: {drawing}')
        drawings[role] = str(drawing)
    if len({Path(drawings[r]).name for r in required}) != len(required):
        raise ValueError('The three drawings must have different file names')
    design = config['design']
    if design.get('method', 'affine') != 'affine':
        raise ValueError('Supported design method: affine')
    target = design['target_dimensions']
    for key in ('lbp_m', 'beam_m', 'depth_m', 'design_draft_m'):
        positive(target[key], f'design.target_dimensions.{key}')
    if target['design_draft_m'] > target['depth_m']:
        raise ValueError('design_draft_m must not exceed depth_m')
    for key in ('loa_m', 'scantling_draft_m'):
        if key in target:
            positive(target[key], key)
    environment = config.setdefault('environment', {})
    environment.setdefault('water_density_t_m3', 1.025)
    positive(environment['water_density_t_m3'], 'water_density_t_m3')
    positive(environment.setdefault('hydro_shell_thickness_mm', 0.), 'hydro_shell_thickness_mm', True)
    mesh = config.setdefault('mesh', {})
    mesh.setdefault('samples', 129)
    mesh.setdefault('subdivisions', 8)
    for key, minimum in [('samples', 16), ('subdivisions', 1)]:
        if type(mesh[key]) is not int or mesh[key] < minimum:
            raise ValueError(f'mesh.{key} must be an integer >= {minimum}')
    for key, default in [('shell_thickness_mm', 12.), ('bulkhead_thickness_mm', 8.),
                         ('partition_thickness_mm', 8.)]:
        positive(mesh.setdefault(key, default), f'mesh.{key}')
    mesh.setdefault('bow_closure', 'nominal')
    if mesh['bow_closure'] not in ('nominal', 'station'):
        raise ValueError('mesh.bow_closure must be nominal or station')
    config.setdefault('tasks', ['geometry', 'bonjean', 'hydrostatics', 'compartments', 'report'])
    if not config['tasks'] or any(task not in TASKS for task in config['tasks']):
        raise ValueError(f'tasks must be a nonempty list selected from {list(TASKS)}')
    loading = config.get('loading')
    if isinstance(loading, str):
        loading = read_json((path.parent / loading).resolve())
    if loading is not None:
        config['loading'] = loading
        cases = loading['cases']
        if not cases or len({c['case_id'] for c in cases}) != len(cases):
            raise ValueError('loading.cases must contain unique case_id values')
        for case in cases:
            ident = case['case_id']
            if not isinstance(ident, str) or not ident or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in ident):
                raise ValueError('case_id accepts only letters, digits, underscore and hyphen')
    return config, path
