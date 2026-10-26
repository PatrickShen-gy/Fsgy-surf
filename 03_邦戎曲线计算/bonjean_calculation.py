"""阶段03：从02外包络三角网格切片，计算邦戎面积与对基线面积矩并校核PDF。

python bonjean_calculation.py
python bonjean_calculation.py --preview

只读02网格与PDF，不修改02模型。以hydro_faces计算浸水横剖面积，
不能把船壳/隔板的钢材截面积当成排水横剖面积。
"""
from pathlib import Path
from collections import defaultdict
import argparse
import csv
import hashlib
import json
import re
import sys
import webbrowser

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.append(str(PROJECT.parent/'.cad_deps'))
import numpy as np
import pymupdf
from shapely.geometry import Polygon, box, LineString


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def extract_reference(pdf, out):
    """按表头X坐标及每行首尾吃水双重核对，保存原值与来源页码。"""
    fingerprint = digest(pdf)
    cache = out/'reference/pdf_bonjean.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        cached = json.loads(cache.read_text(encoding='utf-8'))
        if cached.get('source_sha256') == fingerprint and cached.get('parser_version') == 1:
            return cached
    rows = []
    with pymupdf.open(pdf) as document:
        for page_number in range(32, 39):
            text = document[page_number-1].get_text()
            xs = [float(v) for v in re.findall(r'\bX\s+([-+]?\d+\.\d+)', text)]
            if len(xs) != (2 if page_number == 38 else 4):
                raise ValueError(f'PDF第{page_number}页站位表头识别失败。')
            table = []
            for line in text.splitlines():
                values = [float(v) for v in re.findall(r'[-+]?\d+\.\d+', line)]
                if len(values) != 2+2*len(xs) or not values or not 1 <= values[0] <= 13:
                    continue
                if values[0] != values[-1]:
                    raise ValueError(f'PDF第{page_number}页行首尾吃水不一致。')
                table.append(values)
                for i, x in enumerate(xs):
                    rows.append(dict(x_m=x, draft_m=values[0], area_m2=values[1+2*i],
                                     moment_bl_m3=values[2+2*i], pdf_page=page_number,
                                     source_row_text=line.strip()))
            if len(table) != 25 or not np.allclose([r[0] for r in table], np.arange(1, 13.01, .5)):
                raise ValueError(f'PDF第{page_number}页不是完整的25档吃水。')
    if len(rows) != 650 or len({(r['x_m'], r['draft_m']) for r in rows}) != 650:
        raise ValueError('参考表必须包含650个唯一的站位/吃水数据对。')
    result = dict(parser_version=1, source_pdf=str(Path(pdf).resolve()), source_sha256=fingerprint,
                  pages=list(range(32, 39)), symbols=dict(AREA='浸水横剖面积，m2', MOM_BL='对基线的一次面积矩，m3'),
                  values_not_modified=True, rows=rows)
    save_json(cache, result)
    write_csv(out/'reference/pdf_bonjean.csv', rows)
    return result


class MeshSlicer:
    """由三角面与X=常数平面求交；不使用02的原始型值或直接拿站位环代替切片。"""
    def __init__(self, vertices, faces, tolerance=1e-8):
        self.vertices, self.faces = np.asarray(vertices), np.asarray(faces, dtype=int)
        if not np.isfinite(self.vertices).all() or self.faces.min() < 0 or self.faces.max() >= len(self.vertices):
            raise ValueError('网格包含非法坐标或顶点索引。')
        self.triangles = self.vertices[self.faces]
        self.xmin = self.triangles[:, :, 0].min(axis=1)
        self.xmax = self.triangles[:, :, 0].max(axis=1)
        self.tolerance = tolerance
        self.cache = {}

    def section(self, x):
        key = round(float(x), 9)
        if key in self.cache:
            return self.cache[key]
        tol = self.tolerance
        selected = self.triangles[(self.xmin <= x+tol) & (self.xmax >= x-tol)]
        edges = set()
        for tri in selected:
            delta = tri[:, 0]-x
            if np.all(np.abs(delta) < tol):
                continue  # 共面的端盖由相邻侧面的边界给出，避免内部三角线混入截线。
            hits = set()
            for i in range(3):
                a, b = tri[i], tri[(i+1)%3]
                da, db = delta[i], delta[(i+1)%3]
                if abs(da) < tol:
                    hits.add(tuple(np.round(a[1:], 8)))
                if da*db < 0 and abs(da) >= tol and abs(db) >= tol:
                    p = a[1:]+(b[1:]-a[1:])*(-da)/(db-da)
                    hits.add(tuple(np.round(p, 8)))
            if len(hits) == 2:
                a, b = sorted(hits)
                if np.linalg.norm(np.array(a)-b) > tol:
                    edges.add((a, b))
            elif len(hits) > 2:
                raise ValueError(f'X={x}切片出现超过两个不共面交点。')
        if not edges:
            raise ValueError(f'X={x}没有截线，或站位位于网格之外。')
        graph = defaultdict(set)
        for a, b in edges:
            graph[a].add(b)
            graph[b].add(a)
        degrees = [len(n) for n in graph.values()]
        if any(d != 2 for d in degrees):
            raise ValueError(f'X={x}截线未闭合或有分支，节点度范围{min(degrees)}..{max(degrees)}。')
        unvisited = set(graph)
        loops = []
        while unvisited:
            start = next(iter(unvisited))
            previous, current, points = None, start, []
            while True:
                points.append(current)
                unvisited.discard(current)
                choices = graph[current]-({previous} if previous is not None else set())
                nxt = next(iter(choices))
                previous, current = current, nxt
                if current == start:
                    break
                if len(points) > len(graph):
                    raise ValueError('截线连接循环未结束。')
            polygon = Polygon(points)
            if not polygon.is_valid or polygon.area <= 0:
                raise ValueError(f'X={x}截面自交或面积无效。')
            loops.append(polygon)
        # 嵌套闭合环按奇偶规则处理孔洞；分离的球艏区域可保留为MultiPolygon。
        result = loops[0]
        for polygon in loops[1:]:
            result = result.symmetric_difference(polygon)
        if not result.is_valid:
            raise ValueError(f'X={x}截面组合无效。')
        self.cache[key] = result
        return result


def submerged_properties(section, draft):
    ymin, zmin, ymax, zmax = section.bounds
    wet = section.intersection(box(ymin-1, zmin-1, ymax+1, draft))
    area = float(wet.area)
    if area < 1e-10:
        return dict(area_m2=0., moment_bl_m3=0., centroid_z_m=None, waterline_breadth_m=0.)
    centroid = float(wet.centroid.y)
    breadth = section.intersection(LineString([(ymin-1, draft), (ymax+1, draft)])).length
    return dict(area_m2=area, moment_bl_m3=area*centroid, centroid_z_m=centroid,
                waterline_breadth_m=float(breadth))


def reference_anomalies(rows):
    anomalies = []
    for x in sorted({r['x_m'] for r in rows}):
        station = sorted([r for r in rows if r['x_m'] == x], key=lambda r: r['draft_m'])
        for a, b in zip(station[:-1], station[1:]):
            for field in ('area_m2', 'moment_bl_m3'):
                if b[field] < a[field]-.010001:
                    anomalies.append(dict(x_m=x, previous_draft_m=a['draft_m'], draft_m=b['draft_m'],
                        quantity=field, previous_value=a[field], value=b[field], pdf_page=b['pdf_page'],
                        note='随吃水增加而减小，超过原表两位小数舍入量级；保留原值，待核定。'))
    return anomalies


def mesh_volume_check(slicer):
    v, f = slicer.vertices, slicer.faces
    ref = (v.min(axis=0)+v.max(axis=0))/2
    tri = v[f]-ref
    dv = np.einsum('ij,ij->i', tri[:, 0], np.cross(tri[:, 1], tri[:, 2]))/6
    volume_mesh = float(dv.sum())
    z_moment_mesh = float((dv*(tri[:, :, 2].sum(axis=1)/4+ref[2])).sum())
    xs = np.unique(np.round(v[:, 0], 9))
    volume_slice, moment_slice = 0., 0.
    def whole(x):
        section = slicer.section(x)
        return np.array([section.area, section.area*section.centroid.y])
    # 三角网格在相邻顶点X坐标之间，截面积至多为二次、面积矩至多为三次。
    for a, b in zip(xs[:-1], xs[1:]):
        values = (whole(a)+4*whole((a+b)/2)+whole(b))*(b-a)/6
        volume_slice += values[0]
        moment_slice += values[1]
    relative = abs(volume_slice-volume_mesh)/max(abs(volume_mesh), 1e-12)
    moment_relative = abs(moment_slice-z_moment_mesh)/max(abs(z_moment_mesh), 1e-12)
    if relative > 1e-6 or moment_relative > 1e-6:
        raise ValueError('切片积分与网格四面体积分不一致，不能输出可信邦戎结果。')
    return dict(volume_mesh_m3=volume_mesh, volume_from_sections_m3=float(volume_slice),
                relative_volume_error=float(relative), moment_mesh_m4=z_moment_mesh,
                moment_from_sections_m4=float(moment_slice), relative_moment_error=float(moment_relative),
                passed=True, integration='逐网格X区间Simpson积分，与有向四面体积分独立对照')


def compare_record(calculated, reference, area_atol=.05, moment_atol=.05, relative_tolerance=.01):
    row = dict(x_m=reference['x_m'], draft_m=reference['draft_m'], pdf_page=reference['pdf_page'])
    for name, field, atol in [('area', 'area_m2', area_atol), ('moment', 'moment_bl_m3', moment_atol)]:
        actual, target = calculated[field], reference[field]
        error = actual-target
        row.update({name+'_calculated': actual, name+'_reference': target,
                    name+'_error': error, name+'_error_percent': None if abs(target) < 1e-12 else error/target*100,
                    name+'_pass': abs(error) <= atol+relative_tolerance*abs(target)})
    row['both_pass'] = row['area_pass'] and row['moment_pass']
    return row


def metrics(comparisons):
    result = dict(pairs=len(comparisons), both_pass=sum(r['both_pass'] for r in comparisons))
    for name in ('area', 'moment'):
        errors = np.array([r[name+'_error'] for r in comparisons])
        relative = [abs(r[name+'_error_percent']) for r in comparisons if abs(r[name+'_reference']) >= 1]
        worst = max(comparisons, key=lambda r: abs(r[name+'_error']))
        result[name] = dict(mean_absolute_error=float(np.mean(abs(errors))),
            root_mean_square_error=float(np.sqrt(np.mean(errors**2))), max_absolute_error=float(max(abs(errors))),
            max_absolute_percent_reference_ge_1=float(max(relative)),
            passed=sum(r[name+'_pass'] for r in comparisons),
            worst=dict(x_m=worst['x_m'], draft_m=worst['draft_m'],
                       calculated=worst[name+'_calculated'], reference=worst[name+'_reference'],
                       error=worst[name+'_error'], percent=worst[name+'_error_percent']))
    return result


def rings_for_html(geometry):
    if geometry.is_empty or geometry.area == 0:
        return []
    polys = [geometry] if geometry.geom_type == 'Polygon' else [p for p in geometry.geoms if p.geom_type == 'Polygon']
    return [[[list(p) for p in polygon.exterior.coords]]+
            [[[float(a), float(b)] for a, b in ring.coords] for ring in polygon.interiors] for polygon in polys]


def create_plots(out, comparisons):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), constrained_layout=True)
    for ax, x in zip(axes[0], [86., 172.]):
        rows = sorted([r for r in comparisons if abs(r['x_m']-x) < 1e-6], key=lambda r: r['draft_m'])
        ax.plot([r['area_calculated'] for r in rows], [r['draft_m'] for r in rows], label='Mesh', color='#237da4')
        ax.plot([r['area_reference'] for r in rows], [r['draft_m'] for r in rows], '--', label='PDF', color='#d68b2a')
        ax.set(title=f'X = {x:g} m', xlabel='Section area (m²)', ylabel='Draft (m)')
        ax.grid(alpha=.25)
        ax.legend()
    xs = sorted({r['x_m'] for r in comparisons})
    ts = sorted({r['draft_m'] for r in comparisons})
    lookup = {(r['x_m'], r['draft_m']): r for r in comparisons}
    for ax, name in zip(axes[1], ['area', 'moment']):
        array = np.array([[np.nan if lookup[x, t][name+'_error_percent'] is None else lookup[x, t][name+'_error_percent'] for x in xs] for t in ts])
        im = ax.imshow(array, origin='lower', aspect='auto', cmap='coolwarm', vmin=-5, vmax=5)
        ax.set(title=f'{name.capitalize()} error (%) - color limited to ±5%', xlabel='X (m)', ylabel='Draft (m)')
        ticks = sorted(set(range(0, len(xs), 2)) | {len(xs)-1})
        ax.set_xticks(ticks, [f'{xs[i]:g}' for i in ticks], rotation=55)
        ax.set_yticks(range(0, len(ts), 4), [f'{t:g}' for t in ts[::4]])
        fig.colorbar(im, ax=ax, label='%')
    fig.suptitle('Bonjean verification - original stage 02 outer envelope, no shell offset', fontsize=14)
    fig.savefig(out/'bonjean_comparison.png', dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mesh', type=Path, default=PROJECT/'02_母型计算几何重建/CAD_mesh/02_计算网格/calculation_mesh.npz')
    parser.add_argument('--pdf', type=Path)
    parser.add_argument('--output', type=Path, default=HERE/'output')
    parser.add_argument('--area-atol', type=float, default=.05)
    parser.add_argument('--moment-atol', type=float, default=.05)
    parser.add_argument('--relative-tolerance', type=float, default=.01)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    if any(not np.isfinite(t) or t < 0 for t in [args.area_atol, args.moment_atol, args.relative_tolerance]):
        parser.error('误差阈值须为非负有限数。')
    pdfs = list(PROJECT.glob('*.pdf'))
    pdf = args.pdf or (pdfs[0] if len(pdfs) == 1 else None)
    if pdf is None:
        parser.error('请用--pdf指定参考装载手册。')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    reference = extract_reference(pdf, out)
    with np.load(args.mesh) as mesh:
        vertices, faces = mesh['outer_vertices'], mesh['hydro_faces']
        native_x = mesh['source_station_x'].copy()
    slicer = MeshSlicer(vertices, faces)
    xs = sorted(set(np.round(native_x, 6)) | {r['x_m'] for r in reference['rows']})
    drafts = sorted(set(np.arange(1, 13.01, .5)) | {9.83, 10.27})
    results, lookup, html_sections = [], {}, []
    for x in xs:
        section = slicer.section(float(x))
        html_sections.append(dict(x_m=float(x), polygons=rings_for_html(section)))
        for t in drafts:
            values = submerged_properties(section, float(t))
            row = dict(x_m=float(x), draft_m=float(t), **values)
            results.append(row)
            lookup[round(float(x), 6), round(float(t), 6)] = row
    comparisons = [compare_record(lookup[r['x_m'], r['draft_m']], r,
        args.area_atol, args.moment_atol, args.relative_tolerance) for r in reference['rows']]
    anomalies = reference_anomalies(reference['rows'])
    checks = mesh_volume_check(slicer)
    stats = metrics(comparisons)
    # 仅作诊断：第39页静水力条件给出18mm外板；第31页邦戎说明没有明确该修正口径。
    diagnostic = []
    for ref in reference['rows']:
        corrected = slicer.section(ref['x_m']).buffer(.018, join_style=2)
        values = submerged_properties(corrected, ref['draft_m'])
        diagnostic.append(compare_record(values, ref, args.area_atol, args.moment_atol, args.relative_tolerance))
    report = dict(status='passed' if stats['both_pass'] == len(comparisons) and not anomalies else 'needs_review',
        mesh_path=str(args.mesh.resolve()), mesh_sha256=digest(args.mesh),
        reference_pdf=reference['source_pdf'], reference_sha256=reference['source_sha256'], reference_pages=reference['pages'],
        calculation=dict(mesh_keys=['outer_vertices', 'hydro_faces'], units=dict(area='m2', moment_bl='m3'),
                         formula='A=integral(dA); MOM_BL=integral(z*dA); 截面质心Z=MOM_BL/A',
                         steel_material_not_used=True, section_count=len(xs), drafts_m=drafts,
                         baseline='02原始外包络；未作额外外板/龙骨修正，也未扣除内部钢板'),
        tolerances=dict(area_absolute_m2=args.area_atol, moment_absolute_m3=args.moment_atol,
                        relative=args.relative_tolerance, criterion='abs(error)<=absolute+relative*abs(reference)',
                        note='程序默认诊断阈值，不代表船级社验收标准。'),
        verification=stats, numerical_cross_check=checks, reference_anomalies=anomalies,
        diagnostic_18mm=dict(metrics=metrics(diagnostic),
            method='对二维截面向外buffer18mm，再按同一型吃水截取；尖角交线连接。',
            caveat='仅为口径诊断；不等同原手册算法，未单独实现20mm龙骨修正，不修改02几何。'),
        notes=['本次计算并校核，不自动调整02网格来拟合PDF。',
               'PDF仅有26站的数值表；02全部31个原始站位均计算，另有9.83m/10.27m两档结果。',
               '参考值为0时不计算相对百分误差；小量宜优先看绝对误差。',
               '20站球艏及上部多区域剖面在02中采用连续连接近似，且原表存在非单调值，需联合复核。'])
    write_csv(out/'bonjean_results.csv', results)
    write_csv(out/'verification.csv', comparisons)
    station_summary = []
    for x in sorted({r['x_m'] for r in comparisons}):
        group = [r for r in comparisons if r['x_m'] == x]
        station_summary.append(dict(x_m=x, pairs=len(group), both_pass=sum(r['both_pass'] for r in group),
            area_mae_m2=float(np.mean([abs(r['area_error']) for r in group])),
            area_max_error_m2=max(abs(r['area_error']) for r in group),
            moment_mae_m3=float(np.mean([abs(r['moment_error']) for r in group])),
            moment_max_error_m3=max(abs(r['moment_error']) for r in group)))
    write_csv(out/'station_summary.csv', station_summary)
    write_csv(out/'diagnostic_18mm.csv', diagnostic)
    save_json(out/'verification_report.json', report)
    save_json(out/'section_contours.json', dict(coordinates='Y/Z, m', sections=html_sections))
    np.savez_compressed(out/'bonjean_data.npz', x_m=np.array(xs), draft_m=np.array(drafts),
        area_m2=np.array([[lookup[round(float(x), 6), round(float(t), 6)]['area_m2'] for t in drafts] for x in xs]),
        moment_bl_m3=np.array([[lookup[round(float(x), 6), round(float(t), 6)]['moment_bl_m3'] for t in drafts] for x in xs]))
    create_plots(out, comparisons)
    dashboard = dict(report=report, results=results, comparisons=comparisons, sections=html_sections)
    template = (HERE/'bonjean_viewer.html').read_text(encoding='utf-8')
    (out/'bonjean_verification.html').write_text(template.replace('BONJEAN_PAYLOAD', json.dumps(dashboard, ensure_ascii=False)), encoding='utf-8')
    lines = ['# 03 邦戎计算与校核', '', f"状态：{report['status']}。650组对照中，两项同时满足默认阈值：{stats['both_pass']}/650。", '',
             '计算采用02的外包络三角网格，不使用钢板材料截面，不扣内部隔板。', '',
             f"- 面积平均绝对误差：{stats['area']['mean_absolute_error']:.6f} m²",
             f"- 面积矩平均绝对误差：{stats['moment']['mean_absolute_error']:.6f} m³",
             f"- 面积最大绝对误差：{stats['area']['max_absolute_error']:.6f} m²",
             f"- 面积矩最大绝对误差：{stats['moment']['max_absolute_error']:.6f} m³",
             f"- 切片积分与网格体积相对差：{checks['relative_volume_error']:.3e}",
             f'- 原表非单调项：{len(anomalies)}，原值保留在reference目录。', '',
             '## 输出', '', '`bonjean_verification.html`：离线交互对照、剖面与误差表。',
             '`bonjean_results.csv` / `bonjean_data.npz`：31站、27档吃水的计算结果。',
             '`verification.csv`：与PDF的650组逐项对照；`verification_report.json`：统计、阈值、原表异常与数值验证。',
             '`bonjean_comparison.png`：典型站位及误差热图。',
             '`diagnostic_18mm.csv`：18mm截面外扩诊断，不是已确认的原表修正算法。', '',
             '## 后续', '', '优先复核20站球艏及多区域连接；再核定外板修正口径与舭部剖面近似。',
             '当前仍有超差，不能称为已完整复现PDF；无需为消除差值直接改动参考表。']
    (out/'README.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f'Output: {out}\nSections: {len(xs)}; computed rows: {len(results)}; reference pairs: {len(comparisons)}')
    print(f"Both within tolerance: {stats['both_pass']}/650; area MAE {stats['area']['mean_absolute_error']:.6f} m2; moment MAE {stats['moment']['mean_absolute_error']:.6f} m3")
    print(f"Volume cross-check relative error: {checks['relative_volume_error']:.3e}; reference anomalies: {len(anomalies)}")
    if args.preview:
        webbrowser.open((out/'bonjean_verification.html').as_uri())


if __name__ == '__main__':
    main()
