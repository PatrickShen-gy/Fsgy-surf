"""04 静水力：裁切02的三角网格，独立积分，并与装载手册39—67页对照。

python hydrostatics_calculation.py
python hydrostatics_calculation.py --preview
结果写入同目录output。后续可直接调用HydrostaticCalculator.evaluate(T, trim)。
"""
from pathlib import Path
import argparse
import json
import re
import sys
import time
import webbrowser

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.append(str(PROJECT.parent / '.cad_deps'))
sys.path.insert(0, str(PROJECT / '03_邦戎曲线计算'))
import numpy as np
import pymupdf
from bonjean_calculation import MeshSlicer, submerged_properties, digest, save_json, write_csv

MESH = PROJECT / '02_母型计算几何重建/CAD_mesh/02_计算网格/calculation_mesh.npz'
BASIC = ['DISP', 'VOLM', 'WLA', 'WSA', 'LCB', 'VCB', 'LCA']
EXTRA = ['KMT', 'KML', 'MCT', 'CB', 'TCP', 'CP', 'CM', 'CW']
TRIM_METRICS = ['DISP', 'VOLM', 'LCB', 'KMT', 'KML', 'LCA', 'TCP']
UNITS = dict(DISP='t', VOLM='m³', WLA='m²', WSA='m²', LCB='m', VCB='m', LCA='m',
             KMT='m', KML='m', MCT='t·m/cm', CB='1', TCP='t/cm', CP='1', CM='1', CW='1')
NAMES = dict(DISP='排水量（18mm一阶修正）', VOLM='型排水体积', WLA='水线面投影面积', WSA='湿表面积',
             LCB='纵向浮心', VCB='垂向浮心', LCA='纵向漂心', KMT='横稳心高度', KML='纵稳心高度',
             MCT='每厘米纵倾力矩', CB='方形系数', TCP='每厘米吃水吨数', CP='棱形系数', CM='中横剖面系数', CW='水线面系数')
DRAFTS = sorted(set(np.round(np.arange(2, 14.6001, .1), 2).tolist() + [9.83, 10.27]))
TRIMS = [-4., -3., -2., -1.5, -1., -.5, 0., .5]


def extract_reference(pdf, output):
    """同时核对列数、吃水网格、零纵倾重复表，保留原始数值及物理页码。"""
    target = output / 'reference'
    target.mkdir(parents=True, exist_ok=True)
    fingerprint = digest(pdf)
    cache = target / 'pdf_hydrostatics.json'
    if cache.exists():
        data = json.loads(cache.read_text(encoding='utf-8'))
        if data.get('source_sha256') == fingerprint and data.get('parser_version') == 1:
            return data
    even = {t: dict(draft_m=t, trim_m=0., pages={}) for t in DRAFTS}
    tilted = {(t, tr): dict(draft_m=t, trim_m=tr, pages={}) for t in DRAFTS for tr in TRIMS}
    source_rows = []
    with pymupdf.open(pdf) as document:
        conditions_text = document[38].get_text()
        for phrase in ['1.025', '0.018', '18.00', '86.00', '172.00', '28.80']:
            if phrase not in conditions_text:
                raise ValueError(f'PDF39页条件发生变化，缺少{phrase}，请核对。')
        for page in range(41, 47):
            for line in document[page-1].get_text().splitlines():
                if not re.match(r'^\s*\d+\.\d{3}\s+', line):
                    continue
                values = [float(v) for v in re.findall(r'[-+]?\d+(?:\.\d+)?', line)]
                if len(values) not in (8, 9) or values[0] not in even:
                    raise ValueError(f'静水力表{page}页行格式无法识别：{line}')
                fields = BASIC if len(values) == 8 else EXTRA
                row = even[values[0]]
                for key, value in zip(fields, values[1:]):
                    if key in row:
                        raise ValueError('重复的正浮参考值')
                    row[key], row['pages'][key] = value, page
                source_rows.append(dict(page=page, kind='even_keel', text=line.strip()))
        for i, key in enumerate(TRIM_METRICS):
            for page in range(47+3*i, 50+3*i):
                text = document[page-1].get_text()
                header, body = text.split('draught', 1)
                trims = [float(v) for v in re.findall(r'[-+]?\d+\.\d+', header.split('trim', 1)[1])]
                if trims != TRIMS:
                    raise ValueError(f'纵倾表{page}页列头不匹配')
                tokens = re.findall(r'[-+]?\d+\.\d+', body)
                if len(tokens) % 9:
                    raise ValueError(f'纵倾表{page}页列数不完整')
                for j in range(0, len(tokens), 9):
                    t = float(tokens[j])
                    for tr, val in zip(TRIMS, tokens[j+1:j+9]):
                        row = tilted[(t, tr)]
                        if key in row:
                            raise ValueError('重复的纵倾参考值')
                        row[key], row['pages'][key] = float(val), page
                    source_rows.append(dict(page=page, kind=key, text=' '.join(tokens[j:j+9])))
    if not all(all(k in r for k in BASIC+EXTRA) for r in even.values()):
        raise ValueError('正浮表未提取完整')
    if not all(all(k in r for k in TRIM_METRICS) for r in tilted.values()):
        raise ValueError('纵倾表未提取完整')
    for t in DRAFTS:
        for k in TRIM_METRICS:
            if even[t][k] != tilted[(t, 0.)][k]:
                raise ValueError(f'PDF重复表不一致：T={t}, {k}')
    data = dict(parser_version=1, source_pdf=str(pdf.resolve()), source_sha256=fingerprint,
                pages=list(range(39, 68)), conditions_text=conditions_text,
                conditions=dict(LBP_m=172., beam_m=28.8, x_reference_m=86., rho_t_m3=1.025,
                                shell_thickness_m=.018, keel_thickness_m=.020, heel_deg=0.,
                                trim_definition='TF-TA，T为x=86m处型吃水'),
                even_keel=list(even.values()), trimmed=list(tilted.values()), source_rows=source_rows,
                repeated_zero_trim_cells_checked=129*7, values_not_modified=True)
    save_json(cache, data)
    for name, rows in [('pdf_even_keel.csv', data['even_keel']), ('pdf_trimmed.csv', data['trimmed'])]:
        write_csv(target / name, [dict((k, v) for k, v in r.items() if k != 'pages') for r in rows])
    return data


class HydrostaticCalculator:
    """对任意纵倾水面进行三角面裁切；水面封口与线积分共用同一组交线。

    坐标AP/CL/BL；x向艏、z向上；T为xref处吃水，trim=TF-TA。
    仅供零横倾、小纵倾；不包含横倾稳性或重量重心求解。
    """
    def __init__(self, vertices, faces, physical_mask=None, lbp=172., beam=28.8,
                 xref=86., rho=1.025, shell_thickness=.018):
        self.vertices = np.asarray(vertices, dtype=float)
        self.faces = np.asarray(faces, dtype=int)
        self.tri = self.vertices[self.faces]
        if not np.isfinite(self.tri).all():
            raise ValueError('非有限网格坐标')
        self.physical = np.ones(len(faces), dtype=bool) if physical_mask is None else np.asarray(physical_mask, bool)
        self.lbp, self.beam, self.xref, self.rho, self.shell_t = lbp, beam, xref, rho, shell_thickness
        self.normal2 = np.cross(self.tri[:, 1]-self.tri[:, 0], self.tri[:, 2]-self.tri[:, 0])
        self.areas = np.linalg.norm(self.normal2, axis=1)/2
        q = self.tri - [xref, 0., 0.]
        self.base_dv = np.einsum('ij,ij->i', q[:, 0], np.cross(q[:, 1], q[:, 2]))/6
        self.qsum = q.sum(axis=1)
        if self.base_dv.sum() <= 0:
            raise ValueError('外包络网格需闭合且法向朝外')
        self.full_volume = float(self.base_dv.sum())
        self.zmax = float(self.vertices[:, 2].max())
        self.deck_vertices = self.tri[~self.physical].reshape(-1, 3)

    @classmethod
    def from_npz(cls, path=MESH, **kwargs):
        with np.load(path) as data:
            faces = data['hydro_faces']
            plates = {tuple(f) for f in data['plate_faces']}
            mask = np.array([tuple(f) in plates for f in faces])
            if mask.sum() != len(plates):
                raise ValueError('物理外板面未与外包络面一一对应')
            return cls(data['outer_vertices'], faces, mask, **kwargs)

    def evaluate(self, draft, trim=0., midship_area=None):
        slope = trim/self.lbp
        c = float(np.hypot(1., slope))
        apex = np.array([self.xref, 0., draft])
        d = (self.tri[:, :, 2]-draft-slope*(self.tri[:, :, 0]-self.xref))/c
        d[np.abs(d) < 1e-10] = 0.
        dmin, dmax = d.min(axis=1), d.max(axis=1)
        full = (dmax <= 0) & (dmin < 0)
        cut = (dmin < 0) & (dmax > 0)
        # 以水面上的一点为四面体共同顶点，水面封口的体积贡献为零。
        dv = self.base_dv[full]-draft*self.normal2[full, 2]/6
        volume = dv.sum()
        moment = (dv[:, None]*(self.qsum[full]+[0., 0., draft])/4).sum(axis=0)
        wetted = self.areas[full & self.physical].sum()
        edge_start, edge_end = [], []
        indices = np.flatnonzero(cut)
        counts = (d[indices] <= 0).sum(axis=1)
        for count in (1, 2):
            ids = indices[counts == count]
            if not len(ids):
                continue
            ds, triangles = d[ids], self.tri[ids]
            first = np.argmax(ds <= 0 if count == 1 else ds > 0, axis=1)
            order = (first[:, None]+np.arange(3)) % 3
            p = np.take_along_axis(triangles, order[:, :, None], axis=1)
            dd = np.take_along_axis(ds, order, axis=1)
            a = p[:, 0]+(p[:, 1]-p[:, 0])*(-dd[:, 0]/(dd[:, 1]-dd[:, 0]))[:, None]
            b = p[:, 0]+(p[:, 2]-p[:, 0])*(-dd[:, 0]/(dd[:, 2]-dd[:, 0]))[:, None]
            if count == 1:
                wet = np.stack([p[:, 0], a, b], axis=1)
                material = self.physical[ids]
                edge_start.append(b); edge_end.append(a)
            else:
                wet = np.concatenate([np.stack([a, p[:, 1], p[:, 2]], axis=1), np.stack([a, p[:, 2], b], axis=1)])
                material = np.tile(self.physical[ids], 2)
                edge_start.append(a); edge_end.append(b)
            q = wet-apex
            vv = np.einsum('ij,ij->i', q[:, 0], np.cross(q[:, 1], q[:, 2]))/6
            volume += vv.sum()
            moment += (vv[:, None]*((wet.sum(axis=1)+apex)/4-[self.xref, 0., 0.])).sum(axis=0)
            aa = np.linalg.norm(np.cross(wet[:, 1]-wet[:, 0], wet[:, 2]-wet[:, 0]), axis=1)/2
            wetted += aa[material].sum()
        # 水面恰好穿过网格边时，使用下方湿面的边，排除共面封顶面。
        for i, j in ((0, 1), (1, 2), (2, 0)):
            on = full & (d[:, i] == 0) & (d[:, j] == 0)
            if on.any():
                edge_start.append(self.tri[on, j]); edge_end.append(self.tri[on, i])
        if volume <= 0 or not edge_start:
            raise ValueError(f'T={draft}, trim={trim} 无有效水线面')
        start, end = np.concatenate(edge_start), np.concatenate(edge_end)
        direction = np.array([1., 0., slope])/c
        u0, u1 = (start-apex)@direction, (end-apex)@direction
        y0, y1 = start[:, 1], end[:, 1]
        cr = u0*y1-u1*y0
        area = cr.sum()/2
        if area <= 0:
            raise ValueError('水线封口面积为负，检查网格法向或闭合性')
        uc, yc = ((u0+u1)*cr).sum()/(6*area), ((y0+y1)*cr).sum()/(6*area)
        it = ((y0*y0+y0*y1+y1*y1)*cr).sum()/12-area*yc*yc
        il = ((u0*u0+u0*u1+u1*u1)*cr).sum()/12-area*uc*uc
        buoyancy = moment/volume+[self.xref, 0., 0.]
        aw = area/c
        row = dict(draft_m=float(draft), trim_m=float(trim),
                   DISP=float(self.rho*(volume+self.shell_t*wetted)), VOLM=float(volume), WLA=float(aw),
                   WSA=float(wetted), LCB=float(buoyancy[0]), VCB=float(buoyancy[2]), LCA=float(self.xref+uc/c),
                   KMT=float(buoyancy[2]+it/volume/c), KML=float(buoyancy[2]+il/volume/c),
                   MCT=float(self.rho*il/(100*self.lbp)), TCP=float(self.rho*aw/100),
                   CB=float(volume/(self.lbp*self.beam*draft)),
                   CP=None if midship_area is None else float(volume/(self.lbp*midship_area)),
                   CM=None if midship_area is None else float(midship_area/(self.beam*draft)),
                   CW=float(aw/(self.lbp*self.beam)),
                   displacement_moulded_t=float(self.rho*volume),
                   displacement_12mm_estimate_t=float(self.rho*(volume+.012*wetted)),
                   shell_correction_18mm_t=float(self.rho*self.shell_t*wetted),
                   waterplane_area_actual_m2=float(area), waterplane_IT_m4=float(it), waterplane_IL_m4=float(il),
                   TCB_m=float(buoyancy[1]), TCF_m=float(yc),
                   BMT_m=float(it/volume), BML_m=float(il/volume),
                   waterline_closure_m=float(np.linalg.norm((end-start).sum(axis=0))),
                   draft_AP_m=float(draft-slope*self.xref), draft_FP_m=float(draft+slope*(self.lbp-self.xref)),
                   # 水线到达/越过封顶后仍可积分，但不具备上层建筑/甲板进水的物理意义。
                   deck_limit_reached=bool(np.min(self.deck_vertices[:, 2]-draft-slope*(self.deck_vertices[:, 0]-self.xref)) <= 1e-9)
                   if len(self.deck_vertices) else bool(np.max(draft+slope*(self.vertices[:, 0]-self.xref)) >= self.zmax-1e-9))
        return row


def compare(calculated, references, metrics):
    lookup = {(r['draft_m'], r['trim_m']): r for r in calculated}
    rows = []
    for ref in references:
        calc = lookup[(ref['draft_m'], ref['trim_m'])]
        for key in metrics:
            err = calc[key]-ref[key]
            rows.append(dict(draft_m=ref['draft_m'], trim_m=ref['trim_m'], metric=key, unit=UNITS[key],
                             calculated=calc[key], reference=ref[key], difference=err,
                             error_percent=100*err/ref[key] if ref[key] else None,
                             pdf_page=ref['pages'][key], deck_limit_reached=calc['deck_limit_reached']))
    return rows


def summarize(rows):
    summary = {}
    for key in dict.fromkeys(r['metric'] for r in rows):
        group = [r for r in rows if r['metric'] == key]
        worst = max(group, key=lambda r: abs(r['error_percent']))
        summary[key] = dict(count=len(group), unit=UNITS[key],
                            mean_absolute_error=float(np.mean([abs(r['difference']) for r in group])),
                            mean_absolute_percent_error=float(np.mean([abs(r['error_percent']) for r in group])),
                            max_absolute_percent_error=abs(worst['error_percent']), worst=worst)
    return summary


def numerical_checks(calc, section):
    checks = []
    for t, tr in ((3.17, 0.), (9.83, 0.), (10.27, -2.)):
        r = calc.evaluate(t, tr)
        h = 1e-4
        minus, plus = calc.evaluate(t-h, tr), calc.evaluate(t+h, tr)
        deriv = (plus['VOLM']-minus['VOLM'])/(2*h)
        checks.append(dict(draft_m=t, trim_m=tr, dV_dT_m2=deriv, WLA_m2=r['WLA'],
                           relative_difference=float(abs(deriv-r['WLA'])/r['WLA']),
                           waterline_closure_m=r['waterline_closure_m']))
    # 独立的纵向剖面积分，对照封闭三角网格的水下体积。
    x = np.unique(calc.vertices[:, 0])
    points = np.unique(np.r_[x, (x[:-1]+x[1:])/2])
    areas = np.array([submerged_properties(section.section(float(xx)), 9.83)['area_m2'] for xx in points])
    from scipy.integrate import simpson
    # 每个网格纵向区间内三角截面面积为分段二次函数；水线交点处可能再分段。
    sliced = float(sum(simpson(areas[2*i:2*i+3], x=points[2*i:2*i+3]) for i in range(len(x)-1)))
    mesh_v = calc.evaluate(9.83)['VOLM']
    relative = abs(sliced-mesh_v)/mesh_v
    return dict(derivative_checks=checks, section_integral_volume_m3=sliced, mesh_volume_m3=mesh_v,
                section_integral_relative_difference=relative,
                passed=bool(max(r['relative_difference'] for r in checks) < 1e-6 and relative < 1e-4
                            and max(r['waterline_closure_m'] for r in checks) < 1e-6))


def make_plots(output, calculated, reference):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 3, figsize=(13, 8), layout='constrained')
    t = [r['draft_m'] for r in calculated]
    for ax, key in zip(axes.flat, ['DISP', 'VCB', 'KMT', 'KML', 'WSA', 'MCT']):
        ax.plot(t, [r[key] for r in calculated], label='Mesh', lw=1.6)
        ax.plot(t, [r[key] for r in reference], '--', label='PDF', lw=1.1)
        ax.set(xlabel='Moulded draft (m)', ylabel=f'{key} ({UNITS[key]})')
        ax.grid(alpha=.25)
        if key == 'DISP':
            ax.legend()
    fig.savefig(output/'hydrostatic_comparison.png', dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    started = time.perf_counter()
    output = HERE/'output'
    output.mkdir(exist_ok=True)
    pdfs = list(PROJECT.glob('*.pdf'))
    if len(pdfs) != 1:
        raise ValueError('Fsgy-surf根目录应有且只有一份参考PDF')
    reference = extract_reference(pdfs[0], output)
    calc = HydrostaticCalculator.from_npz()
    sections = MeshSlicer(calc.vertices, calc.faces)
    mid = sections.section(calc.xref)
    mid_areas = {t: submerged_properties(mid, t)['area_m2'] for t in DRAFTS}
    calculated = []
    for tr in TRIMS:
        for t in DRAFTS:
            calculated.append(calc.evaluate(t, tr, mid_areas[t]))
        print(f'trim={tr:g} m: {len(DRAFTS)} states completed', flush=True)
    even = [r for r in calculated if r['trim_m'] == 0.]
    comparisons = compare(even, reference['even_keel'], BASIC+EXTRA)
    trim_comparisons = compare(calculated, reference['trimmed'], TRIM_METRICS)
    write_csv(output/'hydrostatics_even_keel.csv', even)
    write_csv(output/'hydrostatics_trimmed.csv', calculated)
    write_csv(output/'verification_even_keel.csv', comparisons)
    write_csv(output/'verification_trimmed.csv', trim_comparisons)
    arrays = dict(draft_m=np.array(DRAFTS), trim_m=np.array(TRIMS))
    for key in calculated[0]:
        if key not in ('draft_m', 'trim_m'):
            arrays[key] = np.array([r[key] for r in calculated]).reshape(len(TRIMS), len(DRAFTS))
    np.savez_compressed(output/'hydrostatics_data.npz', **arrays)
    effective_t = [(r['DISP']/calc.rho-r['VOLM'])/r['WSA'] for r in reference['even_keel']]
    notes = [
        '所有几何积分来自02的outer_vertices/hydro_faces；不减去内部隔板或钢材体积。物理湿表面只使用plate_faces，排除虚拟甲板封顶。',
        'PDF使用18mm外板；02钢材网格仍为12mm。DISP采用rho*(VOLM+0.018*WSA)一阶体积修正；不是精确外偏置，也不是把钢材重量加到排水量中。另导出未修正与12mm估计值。',
        '浮心、水线面、稳心及船型系数保持型线网格口径；尚未对外板实际几何、浮心及惯性矩作精确外偏置修正。',
        'KMT/KML是稳心距基线高度，不能当成GM。实际GM需要06的KG及自由液面修正；当前不判定船舶稳性是否合格。',
        'MCT=rho*IL/(100*LBP)，为未知KG时的常用静水力近似。横倾固定为0；trim=TF-TA，水面z=T+trim*(x-86)/172。',
        '纵倾表用实际斜水面惯性矩求BM，KMT/KML导出其船体z坐标。WLA取水平投影，TCP=rho*WLA/100。后续浮态应直接调用积分器，不在到达封顶区域外插。',
        '虚拟封顶的局部高度为14.6—15.1m（含艏部起升）；水面触及任一封顶顶点即标记deck_limit_reached。高吃水/大纵倾须另建甲板以上几何及进水边界才可物理解读。',
        'PDF39页龙骨板20mm仅记作条件；缺少龙骨条宽及专门外形，当前未额外叠加一个20mm全底面修正。',
        'CB=V/(LBP*B*T), CM=Amid/(B*T), CP=V/(LBP*Amid), CW=WLA/(LBP*B)。不为拟合PDF修改型线。',
    ]
    report = dict(stage='04_静水力计算', source_mesh=str(MESH.resolve()), source_mesh_sha256=digest(MESH),
                  source_pdf=reference['source_pdf'], source_pdf_sha256=reference['source_sha256'],
                  conditions=reference['conditions'], steel_model_shell_thickness_m=.012,
                  units=UNITS, names=NAMES, notes=notes,
                  states=len(calculated), even_keel_states=len(even),
                  even_keel_comparison_cells=len(comparisons), trim_comparison_cells=len(trim_comparisons),
                  repeated_reference_zero_trim_cells_checked=reference['repeated_zero_trim_cells_checked'],
                  full_model_volume_m3=calc.full_volume,
                  deck_limit_states=sum(r['deck_limit_reached'] for r in calculated),
                  numerical_checks=numerical_checks(calc, sections),
                  even_keel_summary=summarize(comparisons),
                  nonzero_trim_summary=summarize([r for r in trim_comparisons if r['trim_m'] != 0.]),
                  nonzero_trim_below_deck_summary=summarize([r for r in trim_comparisons if r['trim_m'] != 0. and not r['deck_limit_reached']]),
                  pdf_implied_shell_thickness_m=dict(min=min(effective_t), median=float(np.median(effective_t)), max=max(effective_t)),
                  key_drafts=[dict(calculated=r, reference=next(s for s in reference['even_keel'] if s['draft_m'] == r['draft_m']))
                              for r in even if r['draft_m'] in (9.83, 10.27)],
                  elapsed_seconds=time.perf_counter()-started)
    save_json(output/'verification_report.json', report)
    payload = dict(report=report, calculated=calculated, reference=reference['even_keel'], trimmed_reference=reference['trimmed'])
    template = (HERE/'hydrostatics_viewer.html').read_text(encoding='utf-8')
    (output/'hydrostatics_verification.html').write_text(template.replace('__DATA__', json.dumps(payload, ensure_ascii=False, allow_nan=False)), encoding='utf-8')
    make_plots(output, even, reference['even_keel'])
    print(json.dumps(dict(states=len(calculated), checks=report['numerical_checks'], elapsed_s=report['elapsed_seconds']), ensure_ascii=False, indent=2))
    if not report['numerical_checks']['passed']:
        raise RuntimeError('数值自检未通过，请检查报告')
    if args.preview:
        webbrowser.open((output/'hydrostatics_verification.html').as_uri())


if __name__ == '__main__':
    main()
