"""05：23个现有舱室网格的舱容/容积中心/液位曲线及PDF舱表核对。

python tank_capacity_calculation.py [--preview]
不修改02。几何计算与PDF参考舱表分别导出，未建模小舱不伪造几何。
"""
from pathlib import Path
from collections import defaultdict
import argparse
import json
import re
import sys
import time
import webbrowser

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.append(str(PROJECT.parent/'.cad_deps'))
sys.path.insert(0, str(PROJECT/'04_静水力计算'))
from hydrostatics_calculation import HydrostaticCalculator, MeshSlicer, np, digest, save_json, write_csv
import pymupdf
from scipy.optimize import brentq, minimize_scalar

DISPLAY = PROJECT/'02_母型计算几何重建/CAD_mesh/01_展示模型'
FILL_FRACTIONS = np.linspace(0., 1., 51)
FIELDS = ['VOLM', 'VNET', 'CGX', 'CGY', 'CGZ', 'IYMAX', 'FSM']
UNITS = dict(VOLM='m³', VNET='m³', CGX='m', CGY='m', CGZ='m', IYMAX='m⁴', FSM='t·m')


def pdf_lines(page):
    """按视觉基线重组表格行，避免提取器把相同一行的数字分到不同文本行。"""
    groups = defaultdict(list)
    for word in page.get_text('words'):
        groups[round(word[1], 1)].append(word)
    return [(y, sorted(words, key=lambda w:w[0])) for y, words in sorted(groups.items())]


def extract_reference(pdf, output):
    refdir = output/'reference'
    refdir.mkdir(parents=True, exist_ok=True)
    fingerprint = digest(pdf)
    path = refdir/'pdf_tank_capacity.json'
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('source_sha256') == fingerprint and saved.get('parser_version') == 1:
            return saved
    locations, capacities, totals, settings = {}, {}, [], {}
    with pymupdf.open(pdf) as document:
        for pn in range(81, 84):
            category = None
            for y, words in pdf_lines(document[pn-1]):
                line = ' '.join(w[4] for w in words)
                header = re.search(r'CAPACITY OF (.+?)\s+\(RHO=([\d.]+)\)', line)
                if header:
                    category, density = header[1], float(header[2])
                # 位置表右四列的字符全部为数字，NAME/DES按列区分。
                if len(words) < 5 or words[0][0] > 80 or not category:
                    continue
                numeric_text = ' '.join(w[4] for w in words if w[0] >= 360)
                values = re.findall(r'[-+]?\d+(?:\.\d+)?', numeric_text)
                if len(values) != 4 or re.sub(r'[-+\d.\s]', '', numeric_text):
                    continue
                ident = words[0][4]
                if ident in locations:
                    raise ValueError(f'重复的位置表舱名{ident}')
                locations[ident] = dict(id=ident, description=' '.join(w[4] for w in words[1:] if w[0] < 360),
                    category=category, location_density_t_m3=density, frame_aft=float(values[0]), frame_fore=float(values[1]),
                    zmin_m=float(values[2]), zmax_m=float(values[3]), location_page=pn, location_source_row=line)
        # 84—85页跨页的重油组保留其表头；86页另有Void组。
        current = None
        for pn in range(84, 87):
            for y, words in pdf_lines(document[pn-1]):
                line = ' '.join(w[4] for w in words)
                header = re.search(r'Capacity of (.+?),\s*RHO=([\d.]+),\s*Reduction=([\d.]+),\s*FILL=([\d.]+)', line)
                if header:
                    current = header[1]
                    settings[current] = dict(density_t_m3=float(header[2]), reduction_header=float(header[3]),
                                             fill_header=float(header[4]), page=pn, source_row=line)
                    continue
                if not current or not words or words[0][0] > 80:
                    continue
                ident = words[0][4]
                if ident not in locations and ident != 'TOTAL':
                    continue
                # CGX/负CGY有时无空格，例如125.72-10.47；按符号识别。
                values = [float(v) for v in re.findall(r'[-+]?\d+(?:\.\d+)?', ' '.join(w[4] for w in words[1:]))]
                if ident == 'TOTAL':
                    if len(values) != 6:
                        raise ValueError(f'PDF{pn}页合计行格式异常：{line}')
                    totals.append(dict(category=current, page=pn, source_row=line,
                                       **dict(zip(['VOLM','VNET','CGX','CGY','CGZ','FSM'], values))))
                    continue
                if len(values) != 7 or ident in capacities:
                    raise ValueError(f'PDF{pn}页容量行格式异常：{line}')
                capacities[ident] = dict(id=ident, category=current, capacity_page=pn,
                                         capacity_source_row=line, **dict(zip(FIELDS, values)), **settings[current])
    # 不把位置表中的未列舱容舱当作零容积。
    missing = sorted(set(locations)-set(capacities))
    if len(locations) != 84 or len(capacities) != 83 or missing != ['CLS']:
        raise ValueError(f'舱表数量需回查：位置{len(locations)}、舱容{len(capacities)}、缺项{missing}')
    anomalies = []
    catalog = []
    for ident, location in locations.items():
        row = dict(location)
        cap = capacities.get(ident)
        if cap:
            if location['category'] != cap['category']:
                raise ValueError(f'{ident}类别不一致')
            row.update(cap)
            row['net_ratio_from_table'] = row['VNET']/row['VOLM']
            if cap['density_t_m3'] != location['location_density_t_m3']:
                anomalies.append(dict(id=ident, kind='density_conflict', location_value=location['location_density_t_m3'],
                                      capacity_value=cap['density_t_m3'], pages=[location['location_page'],cap['capacity_page']]))
        else:
            row.update(dict((key, None) for key in FIELDS))
            row['density_t_m3'] = location['location_density_t_m3']
            anomalies.append(dict(id=ident, kind='capacity_missing', page=location['location_page']))
        catalog.append(row)
    # 允许原表每个一位小数行带来的累计舍入量；不修补原值。
    total_checks = []
    for total in totals:
        group = [r for r in capacities.values() if r['category'] == total['category']]
        check = dict(category=total['category'], count=len(group), page=total['page'])
        for key in ['VOLM','VNET','FSM']:
            s = sum(r[key] for r in group)
            tol = (len(group)+1)*.050001
            check[key] = dict(sum_of_printed_rows=s, printed_total=total[key], difference=s-total[key],
                              rounding_tolerance=tol, passed=abs(s-total[key]) <= tol)
            if abs(s-total[key]) > tol:
                anomalies.append(dict(kind='subtotal_outside_rounding', category=total['category'], metric=key,
                                      difference=s-total[key], page=total['page']))
        total_checks.append(check)
    anomalies.append(dict(kind='grain_reduction_header', pages=[84],
                          note='Grain Load表头Reduction=1，而VNET/VOLM约0.998；原值保留，不使用1-Reduction推算净舱容。'))
    data = dict(parser_version=1, source_pdf=str(pdf.resolve()), source_sha256=fingerprint,
                pages=list(range(80,87)), location_count=len(locations), capacity_count=len(capacities),
                capacity_missing_ids=missing, settings=settings, totals=totals, total_checks=total_checks,
                anomalies=anomalies, catalog=catalog, values_not_modified=True)
    save_json(path, data)
    keys = ['id','description','category','frame_aft','frame_fore','zmin_m','zmax_m','location_page',
            'capacity_page','density_t_m3','location_density_t_m3','reduction_header','fill_header']+FIELDS
    write_csv(refdir/'pdf_tank_catalog.csv', [{k:r.get(k) for k in keys} for r in catalog])
    return data


class TankCalculator:
    """同一舱室零横倾、零纵倾的水平液面；充满舱的实际自由液面为零。

    只计算输入闭合容积几何，不自动扣结构、添加舱口或假设小舱。
    """
    def __init__(self, vertices, faces):
        vertices, faces = np.asarray(vertices, float), np.asarray(faces, int)
        self.kernel = HydrostaticCalculator(vertices, faces, rho=1., shell_thickness=0.)
        self.vertices, self.faces = vertices, faces
        self.zmin, self.zmax = float(vertices[:,2].min()), float(vertices[:,2].max())
        self.volume = self.kernel.full_volume
        ref = (vertices.min(axis=0)+vertices.max(axis=0))/2
        q = vertices[faces]-ref
        dv = np.einsum('ij,ij->i', q[:,0], np.cross(q[:,1],q[:,2]))/6
        self.center = (dv[:,None]*q.sum(axis=1)/4).sum(axis=0)/dv.sum()+ref
        if self.volume <= 0 or self.zmax <= self.zmin:
            raise ValueError('舱室网格无正体积/高度')

    def at_level(self, level):
        if level <= self.zmin+1e-10:
            v, center, area, it, il, closure = 0., [None]*3, 0., 0., 0., 0.
        elif level >= self.zmax-1e-10:
            v, center, area, it, il, closure = self.volume, self.center.tolist(), 0., 0., 0., 0.
        else:
            r = self.kernel.evaluate(float(level))
            v, center, area = r['VOLM'], [r['LCB'], r['TCB_m'], r['VCB']], r['WLA']
            it, il, closure = r['waterplane_IT_m4'], r['waterplane_IL_m4'], r['waterline_closure_m']
            if it < -1e-5 or il < -1e-5 or v < -1e-6 or v > self.volume+1e-6:
                raise ValueError('部分舱容/自由液面惯性矩异常')
            it, il = max(it, 0.), max(il, 0.)
        return dict(level_BL_m=float(level), height_from_bottom_m=float(level-self.zmin),
                    fill_fraction=float(v/self.volume), volume_m3=float(v),
                    CGX_m=center[0], CGY_m=center[1], CGZ_m=center[2],
                    free_surface_area_m2=float(area), free_surface_IT_m4=float(it), free_surface_IL_m4=float(il),
                    FSM_water_1p025_tm=float(1.025*it), waterline_closure_m=float(closure))

    def at_fill(self, fill):
        if not 0 <= fill <= 1:
            raise ValueError('fill必须在0—1之间')
        if fill in (0., 1.):
            level = self.zmin if fill == 0 else self.zmax
        else:
            level = brentq(lambda z:self.at_level(z)['volume_m3']-fill*self.volume, self.zmin, self.zmax, xtol=1e-9)
        result = self.at_level(level)
        result['requested_fill_fraction'] = float(fill)
        return result

    def scan(self, samples=201):
        levels = np.linspace(self.zmin, self.zmax, samples)
        # 加入较少的折角高度；艏艉放样有很多微小变化，改用均匀采样加局部极值搜索。
        unique = np.unique(np.round(self.vertices[:,2], 9))
        if len(unique) < 100:
            levels = np.unique(np.r_[levels, unique[(unique>self.zmin)&(unique<self.zmax)]])
        rows = [self.at_level(float(z)) for z in levels]
        candidates = []
        vals = [r['free_surface_IT_m4'] for r in rows]
        for i in range(1, len(rows)-1):
            if vals[i] >= vals[i-1] and vals[i] >= vals[i+1] and (vals[i] > vals[i-1]+1e-8 or vals[i] > vals[i+1]+1e-8):
                candidates.append(i)
        candidates = sorted(candidates, key=lambda i:vals[i], reverse=True)[:12]
        for i in candidates:
            opt = minimize_scalar(lambda z:-self.at_level(z)['free_surface_IT_m4'],
                                  bounds=(levels[i-1],levels[i+1]), method='bounded', options={'xatol':1e-8})
            rows.append(self.at_level(float(opt.x)))
        # 顶部平面满舱无自由液面；上端左极限仍用于求部分装载最大值。
        rows.append(self.at_level(self.zmax-min(1e-6, (self.zmax-self.zmin)*1e-6)))
        rows.sort(key=lambda r:r['level_BL_m'])
        maximum = max(rows, key=lambda r:r['free_surface_IT_m4'])
        return rows, maximum


def geometry_mapping(reference):
    """同名概念不代表相同边界；压载舱只能标为部分几何或包络。"""
    rows = []
    for r in reference['catalog']:
        ident = r['id']
        if re.fullmatch(r'CH[1-5]', ident):
            ids, status, note = ['HOLD_'+ident[-1]], 'approximate_cargo_geometry', '同号货舱近似包络；缺舱口围板以上容积、局部墩座等'
        elif re.fullmatch(r'WBT[1-5][PS]', ident):
            ids, status, note = [f'WBT_{ident[3]}_{ident[4]}',f'DB_{ident[3]}'], 'partial_geometry', '边舱和整段双层底尚未按真实左右水舱/管弄划分；不可直接相加当作单舱'
        elif ident in ('FPT', 'APT'):
            ids, status, note = ['FORE_PEAK' if ident=='FPT' else 'AFT_PEAK'], 'envelope_only', '艏艉包络还包含其它空间，高度边界与实际尖舱不同'
        else:
            ids, status, note = [], 'reference_only', '02未提供该舱的独立闭合计算几何'
        rows.append(dict(reference_id=ident, related_geometry_ids=ids, geometry_status=status, note=note,
                         has_reference_capacity=r['VOLM'] is not None, capacity_page=r.get('capacity_page'), location_page=r['location_page']))
    return rows


def verify_geometry(items, calculated):
    result = []
    for item in items:
        ident, tank = item['metadata']['id'], item['calculator']
        expected = item['metadata']['checks']['signed_volume_m3']
        derivative_checks = []
        for ratio in (.27, .53, .79):
            level = tank.zmin+ratio*(tank.zmax-tank.zmin)
            eps = (tank.zmax-tank.zmin)*1e-6
            r = tank.at_level(level)
            deriv = (tank.at_level(level+eps)['volume_m3']-tank.at_level(level-eps)['volume_m3'])/(2*eps)
            derivative_checks.append(abs(deriv-r['free_surface_area_m2'])/max(r['free_surface_area_m2'],1.))
        profile = calculated[ident]['fill_curve']
        relative_fill = max(abs(r['fill_fraction']-r['requested_fill_fraction']) for r in profile)
        result.append(dict(id=ident, relative_full_volume_difference=abs(tank.volume-expected)/expected,
                           max_area_derivative_relative_difference=max(derivative_checks),
                           max_fill_fraction_residual=relative_fill,
                           max_waterline_closure_m=max(r['waterline_closure_m'] for r in profile)))
    mirror = []
    for n in range(1,6):
        p, s = calculated[f'WBT_{n}_P'], calculated[f'WBT_{n}_S']
        a,b = p['summary'],s['summary']
        mirror.append(dict(number=n, volume_difference_m3=a['volume_m3']-b['volume_m3'],
                           CGY_sum_m=a['CGY_m']+b['CGY_m'],
                           ITmax_difference_m4=a['ITmax_liquid_m4']-b['ITmax_liquid_m4']))
    passed = all(r['relative_full_volume_difference']<1e-8 and r['max_area_derivative_relative_difference']<1e-5
                 and r['max_fill_fraction_residual']<1e-7 and r['max_waterline_closure_m']<1e-6 for r in result)
    passed = passed and all(abs(r['volume_difference_m3'])<1e-6 and abs(r['CGY_sum_m'])<1e-6 and abs(r['ITmax_difference_m4'])<1e-4 for r in mirror)
    return dict(passed=passed, compartments=result, mirror_pairs=mirror)


def compare_cargo(calculated, reference):
    refs = {r['id']:r for r in reference['catalog']}
    rows = []
    for n in range(1,6):
        r, s = refs[f'CH{n}'], calculated[f'HOLD_{n}']['summary']
        for metric, value in [('VOLM',s['volume_m3']), ('CGX',s['CGX_m']),('CGY',s['CGY_m']),('CGZ',s['CGZ_m']),('IYMAX',s['ITmax_liquid_m4'])]:
            rows.append(dict(geometry_id=f'HOLD_{n}', reference_id=f'CH{n}', metric=metric, unit=UNITS[metric],
                             calculated=value, reference=r[metric], difference=value-r[metric],
                             error_percent=100*(value-r[metric])/r[metric] if r[metric] else None,
                             pdf_page=r['capacity_page'], boundary_match='approximate',
                             note='IYMAX为潜在液体自由液面最大惯性矩；散货不按液体FSM使用' if metric=='IYMAX' else '未拟合PDF，几何范围差异见报告'))
    return rows


def make_plots(output, calculated, reference):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    refs = {r['id']:r for r in reference['catalog']}
    fig, axes = plt.subplots(1,3,figsize=(15,4.5),layout='constrained')
    ns = list(range(1,6))
    axes[0].bar(np.array(ns)-.18,[calculated[f'HOLD_{n}']['summary']['volume_m3'] for n in ns],.36,label='Mesh to 14.6m')
    axes[0].bar(np.array(ns)+.18,[refs[f'CH{n}']['VOLM'] for n in ns],.36,label='PDF incl. hatch')
    axes[0].set(xlabel='Cargo hold number',ylabel='Full volume (m³)',xticks=ns)
    axes[0].legend(fontsize=8)
    for n in ns:
        c=calculated[f'HOLD_{n}']['level_curve']
        axes[1].plot([r['level_BL_m'] for r in c],[r['volume_m3'] for r in c],label=f'Hold {n}')
    axes[1].set(xlabel='Level above BL (m)',ylabel='Volume (m³)')
    axes[1].legend(fontsize=8)
    for n in (1,3,5):
        c=calculated[f'WBT_{n}_P']['fill_curve']
        axes[2].plot([r['fill_fraction']*100 for r in c],[r['free_surface_IT_m4'] for r in c],label=f'Side space {n} P')
    axes[2].set(xlabel='Geometric fill (%)',ylabel='Liquid free-surface IT (m⁴)')
    axes[2].legend(fontsize=8)
    for ax in axes:ax.grid(alpha=.2)
    fig.savefig(output/'tank_capacity_comparison.png',dpi=150)
    plt.close(fig)


def load_results(output=None, verify_sources=False):
    """供06及以后读取05缓存；默认不重读DXF、PDF或02几何。

    arrays的轴为[compartment_ids, fill_fraction]；未装载时重心为NaN。
    reference_catalog必须按pdf_reference来源使用，不表示已独立重建。
    """
    output = Path(output) if output is not None else HERE/'output'
    report = json.loads((output/'verification_report.json').read_text(encoding='utf-8'))
    if not report['numerical_checks']['passed']:
        raise ValueError('05缓存的数值自检未通过')
    if verify_sources:
        if digest(DISPLAY/'compartments.npz') != report['source_mesh_sha256'] or digest(DISPLAY/'compartments.json') != report['source_metadata_sha256']:
            raise ValueError('02舱室几何已变化，请重算05')
        pdfs = list(PROJECT.glob('*.pdf'))
        if len(pdfs)!=1 or digest(pdfs[0])!=report['source_pdf_sha256']:
            raise ValueError('PDF已变化，请重算05')
    with np.load(output/'tank_capacity_data.npz') as data:
        arrays = {key:data[key].copy() for key in data.files}
    return dict(report=report, arrays=arrays,
                capacities=json.loads((output/'compartment_capacities.json').read_text(encoding='utf-8')),
                reference_catalog=json.loads((output/'loading_reference_catalog.json').read_text(encoding='utf-8')),
                geometry_mapping=json.loads((output/'geometry_reference_mapping.json').read_text(encoding='utf-8')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    started = time.perf_counter()
    output = HERE/'output'
    output.mkdir(exist_ok=True)
    pdfs = list(PROJECT.glob('*.pdf'))
    if len(pdfs)!=1:raise ValueError('Fsgy-surf根目录须有唯一参考PDF')
    reference = extract_reference(pdfs[0],output)
    metadata = json.loads((DISPLAY/'compartments.json').read_text(encoding='utf-8'))
    mesh = np.load(DISPLAY/'compartments.npz')
    items, calculated, summaries, fill_rows, level_rows = [], {}, [], [], []
    for info in metadata['compartments']:
        ident = info['id']
        tank = TankCalculator(mesh[ident+'_vertices'],mesh[ident+'_faces'])
        item = dict(metadata=info,calculator=tank)
        items.append(item)
        level_curve, maximum = tank.scan()
        fill_curve = [tank.at_fill(float(f)) for f in FILL_FRACTIONS]
        summary = dict(id=ident,name=info['name'],category=info['category'],color=info['color'],
                       volume_m3=tank.volume,CGX_m=float(tank.center[0]),CGY_m=float(tank.center[1]),CGZ_m=float(tank.center[2]),
                       zmin_m=tank.zmin,zmax_m=tank.zmax,start_m=info['start_m'],end_m=info['end_m'],
                       ITmax_liquid_m4=maximum['free_surface_IT_m4'], ITmax_level_BL_m=maximum['level_BL_m'],
                       ITmax_fill_fraction=maximum['fill_fraction'],FSMmax_water_1p025_tm=maximum['free_surface_IT_m4']*1.025,
                       Imax_method='201 levels + geometry corners where few + bounded local refinement; numerical estimate',
                       geometry_role='cargo_envelope' if info['category']=='cargo' else 'space_not_independent_real_tank')
        section=MeshSlicer(tank.vertices,tank.faces).section((info['start_m']+info['end_m'])/2)
        polygons=[section] if section.geom_type=='Polygon' else list(section.geoms)
        contours=[dict(outer=np.array(p.exterior.coords).tolist(),holes=[np.array(h.coords).tolist() for h in p.interiors]) for p in polygons]
        calculated[ident] = dict(summary=summary,fill_curve=fill_curve,level_curve=level_curve,mid_section_yz=contours)
        summaries.append(summary)
        fill_rows.extend(dict(id=ident,**r) for r in fill_curve)
        level_rows.extend(dict(id=ident,**r) for r in level_curve)
        print(f'{ident}: V={tank.volume:.3f} m3, CG=({tank.center[0]:.3f},{tank.center[1]:.3f},{tank.center[2]:.3f})',flush=True)
    mesh.close()
    mapping = geometry_mapping(reference)
    comparisons = compare_cargo(calculated,reference)
    checks = verify_geometry(items,calculated)
    save_json(output/'compartment_capacities.json',dict(source='02 compartment meshes',units='m, m3, m4',compartments=summaries))
    write_csv(output/'compartment_capacities.csv',summaries)
    write_csv(output/'filling_curves.csv',fill_rows)
    write_csv(output/'level_curves.csv',level_rows)
    write_csv(output/'cargo_verification.csv',comparisons)
    save_json(output/'geometry_reference_mapping.json',mapping)
    # 后续计算可以明确选择reference数据，不把它包装成已独立重建结果。
    save_json(output/'loading_reference_catalog.json',dict(data_source='PDF reference, NOT independently reconstructed geometry',
        purpose='供06等按PDF工况选择容量/重心/最大FSM；任意部分装载重心不得由满舱重心直接冒充',
        source_pdf=reference['source_pdf'],source_pdf_sha256=reference['source_sha256'],
        density_rule='容量表密度；保留位置表密度冲突。载荷工况有明确密度时以该工况为准。',
        weight_rule='已知液体体积*rho；仅按表头假设上限时可用VNET*FILL*rho，FILL不重复乘进VNET。散货密度由工况另设。',
        FSM_rule='表中FSM为最大自由液面矩口径，不是满舱实际自由液面矩；用作手册保守工况须明确注明。',
        tanks=[dict(r,data_source='pdf_reference',geometry_status=next(m['geometry_status'] for m in mapping if m['reference_id']==r['id'])) for r in reference['catalog']]))
    ids = [r['id'] for r in summaries]
    arrays = dict(compartment_ids=np.array(ids),fill_fraction=FILL_FRACTIONS)
    for key in fill_rows[0]:
        if key not in ('id','requested_fill_fraction'):
            array_key = 'achieved_fill_fraction' if key == 'fill_fraction' else key
            arrays[array_key] = np.array([[np.nan if r[key] is None else r[key] for r in calculated[ident]['fill_curve']] for ident in ids])
    arrays['capacity_m3'] = np.array([calculated[i]['summary']['volume_m3'] for i in ids])
    arrays['full_center_m'] = np.array([[calculated[i]['summary'][k] for k in ('CGX_m','CGY_m','CGZ_m')] for i in ids])
    np.savez_compressed(output/'tank_capacity_data.npz',**arrays)
    cargo_model = sum(calculated[f'HOLD_{n}']['summary']['volume_m3'] for n in range(1,6))
    cargo_pdf = sum(r['VOLM'] for r in reference['catalog'] if r['category']=='Grain Load')
    notes = metadata['limitations']+[
        '只读02网格；当前23个主要空间不等同于PDF的84个实际舱室。边压载舱、双层底、艏艉包络不能按名称直接替代真实油水舱。',
        '货舱模型顶高14.6m，PDF位置表为16.85m，缺少舱口围板上方容积；另有墩座、管弄和小舱边界未细分。',
        '现有分舱空间并非全部按12mm外壳内表面或内底板厚修剪；8mm横/纵隔板退让已继承02。几何容积不是已扣完所有结构的净舱容。',
        '未按参考容积缩放几何，不用参考满舱重心反算部分装载重心。PDF参考数据保留独立来源供06等明确选用。',
        '液位为基线以上绝对高度，装载率为几何体积比。默认水平液面，零横倾、零纵倾；实际浮态下的舱内液面需另解。',
        '自由液面惯性矩IT对过液面形心的纵向轴积分；FSM=rho_liquid*IT，单位t·m；后续自由液面修正为sum(FSM)/displacement。',
        '空舱及封闭压满舱实际FSM为0；ITmax/FSMmax是部分装载扫描的最大值，不能当成满舱实际值。',
        '货舱液位曲线采用水平填充假设；真实散货有安息角、堆形与不同密度，不默认把液体自由液面公式用于粮食或矿砂。',
        '机舱、双层底及艏艉空间的填充曲线用于几何诊断，尚不代表可实际加注的独立液舱。',
        '水密拓扑和有向体积继承02；尚未认证所有分舱与船壳的全局布尔包含/自交，不能把23空间体积之和当成可利用总舱容。',
        'NPZ中空舱的重心用NaN标记，其余数值有限；JSON/CSV用null/空字段。',
    ]
    report = dict(stage='05_舱室与舱容计算',source_mesh=str((DISPLAY/'compartments.npz').resolve()),
        source_mesh_sha256=digest(DISPLAY/'compartments.npz'), source_metadata_sha256=digest(DISPLAY/'compartments.json'),
        source_pdf=reference['source_pdf'], source_pdf_sha256=reference['source_sha256'],
        geometry_count=len(summaries),fill_points_per_compartment=len(FILL_FRACTIONS),fill_rows=len(fill_rows),level_rows=len(level_rows),
        reference_location_count=reference['location_count'],reference_capacity_count=reference['capacity_count'],
        mapping_counts=dict((status,sum(m['geometry_status']==status for m in mapping)) for status in sorted(set(m['geometry_status'] for m in mapping))),
        reference_anomalies=reference['anomalies'],reference_total_checks=reference['total_checks'],
        numerical_checks=checks,cargo_comparisons=comparisons,
        cargo_total=dict(geometry_volume_m3=cargo_model,pdf_volume_m3=cargo_pdf,error_percent=100*(cargo_model-cargo_pdf)/cargo_pdf),
        notes=notes,elapsed_seconds=time.perf_counter()-started)
    save_json(output/'verification_report.json',report)
    template=(HERE/'tank_capacity_viewer.html').read_text(encoding='utf-8')
    payload=dict(report=report,calculated=calculated,reference=reference['catalog'],mapping=mapping)
    (output/'tank_capacity_verification.html').write_text(template.replace('__DATA__',json.dumps(payload,ensure_ascii=False,allow_nan=False)),encoding='utf-8')
    make_plots(output,calculated,reference)
    print(json.dumps(dict(geometry_count=len(summaries),checks_passed=checks['passed'],cargo_total=report['cargo_total'],elapsed_seconds=report['elapsed_seconds']),indent=2))
    if not checks['passed']:raise RuntimeError('几何积分数值自检未通过')
    if args.preview:webbrowser.open((output/'tank_capacity_verification.html').as_uri())


if __name__=='__main__':main()
