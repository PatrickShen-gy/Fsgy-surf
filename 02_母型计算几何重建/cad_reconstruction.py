"""阶段02：仅从01缓存重建母型船体和主要分舱，生成展示及计算两套输出。

python cad_reconstruction.py
python cad_reconstruction.py --preview display
python cad_reconstruction.py --preview mesh --thickness-mm 12
"""
from pathlib import Path
from collections import Counter
import argparse
import base64
import csv
import hashlib
import json
import sys
import webbrowser

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(PROJECT/'01_输入整理与对照数据'))
from read_dxf import load_inputs
import mesh_utils as mu
import numpy as np
import ezdxf
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely import constrained_delaunay_triangles


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def cell(row, key):
    values = row['cells'].get(key, {}).get('values_m')
    return None if values is None else values[0]


def horizontal_width(points, z):
    values = []
    for a, b in zip(points[:-1], points[1:]):
        if abs(a[1]-z) < 1e-8:
            values.append(a[0])
        if min(a[1], b[1])-1e-10 <= z <= max(a[1], b[1])+1e-10 and abs(b[1]-a[1]) > 1e-10:
            values.append(a[0]+(b[0]-a[0])*(z-a[1])/(b[1]-a[1]))
    if abs(points[-1, 1]-z) < 1e-8:
        values.append(points[-1, 0])
    return max(values) if values else None


def table_profile(row):
    zdeck = cell(row, 'upper_deck_height')
    ydeck = cell(row, 'upper_deck_half_breadth')
    zmin = min(row['cells']['centerline_height']['values_m'])
    points = [(0., zmin)]
    bottom = cell(row, 'flat_of_bottom')
    if bottom is not None and zmin < 1e-6:
        points.append((bottom, 0.))
    levels = []
    for name, entry in row['cells'].items():
        if name.startswith('half_breadth_wl_') and entry['values_m'] is not None:
            z = float(name.split('_')[-1])/1000
            if zmin <= z <= zdeck:
                levels.append((entry['values_m'][0], z))
    first = min(z for _, z in levels) if levels else zdeck
    for name, entry in row['cells'].items():
        if name.startswith('height_at_half_breadth_') and entry['values_m']:
            y = float(name.split('_')[-1])/1000
            z = min(entry['values_m'])
            if zmin <= z < first:
                points.append((y, z))
    points += levels+[(ydeck, zdeck)]
    return np.array(sorted(set(points), key=lambda p: (p[1], p[0])), float)


def curve_candidates(drawing):
    polylines = [e for e in drawing['modelspace'] if e['type'] == 'POLYLINE' and e.get('vertices')]
    cluster, count = Counter(tuple(round(v, 2) for v in e['vertices'][0]['location'][:2]) for e in polylines).most_common(1)[0]
    group = [e for e in polylines if tuple(round(v, 2) for v in e['vertices'][0]['location'][:2]) == cluster]
    origin = np.median([e['vertices'][0]['location'][:2] for e in group], axis=0)
    candidates = []
    for e in polylines:
        if e['layer'] != '0':
            continue
        p = (np.array([v['location'][:2] for v in e['vertices']])-origin)/1000
        if len(p) < 10 or abs(p[0, 0]) > .002 or not (-.01 <= p[0, 1] < 12):
            continue
        if np.max(np.abs(p[:, 0])) > 14.5 or p[:, 1].min() < -.01 or p[:, 1].max() > 20.5:
            continue
        p[:, 0] = np.abs(p[:, 0])
        candidates.append((e['handle'], p))
    return candidates, dict(drawing_origin_xy_mm=origin.tolist(), method='most_common_body_polyline_start', supporting_curves=count)


def truncate_at_deck(points, ydeck, zdeck):
    p = [points[0]]
    for a, b in zip(points[:-1], points[1:]):
        if a[1] <= zdeck < b[1]:
            t = (zdeck-a[1])/(b[1]-a[1])
            p.append(a+(b-a)*t)
            break
        if b[1] <= zdeck+1e-7:
            p.append(b)
        else:
            break
    p = np.array(p)
    if p[-1, 1] < zdeck-.001:
        # 仅把平行中体的局部舭部折线接至已知垂直舷侧，不外推艏艉。
        if abs(p[-1, 0]-ydeck) < .03 and abs(ydeck-14.4) < .01:
            p = np.vstack([p, [ydeck, zdeck]])
        else:
            return None
    if abs(p[-1, 0]-ydeck) > .15:
        return None
    p[-1] = [ydeck, zdeck]
    return p


def resample(points, count):
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    keep = np.r_[True, np.diff(distance) > 1e-9]
    p, distance = points[keep], distance[keep]
    t = np.linspace(0, distance[-1], count)
    return np.column_stack([np.interp(t, distance, p[:, i]) for i in range(2)])


def reconstruct_sections(inputs, samples):
    drawing = next(d for d in inputs['drawings'] if d['source']['name'] == inputs['hull_offsets']['source'])
    candidates, body_datum = curve_candidates(drawing)
    sections, evidence = [], []
    for row in inputs['hull_offsets']['stations']:
        reference = table_profile(row)
        ydeck, zdeck = reference[-1]
        matches = []
        for handle, raw in candidates:
            p = truncate_at_deck(raw, ydeck, zdeck)
            if p is None:
                continue
            errors = [abs(p[0, 1]-reference[0, 1])]
            fob = cell(row, 'flat_of_bottom')
            if fob is not None:
                at_bottom = horizontal_width(p, 0.)
                errors.append(10. if at_bottom is None else abs(at_bottom-fob))
            for key, entry in row['cells'].items():
                if key.startswith('half_breadth_wl_') and entry['values_m']:
                    z = float(key.split('_')[-1])/1000
                    actual = horizontal_width(p, z)
                    if actual is None:
                        errors.append(10.)
                    else:
                        errors.append(abs(actual-entry['values_m'][0]))
            error = float(np.sqrt(np.mean(np.square(errors))))
            matches.append((error, handle, p))
        best = min(matches, key=lambda v: v[0]) if matches else None
        multiple_center_intersections = len(row['cells']['centerline_height']['values_m']) > 1
        parallel_body = row['station'] != 'TRANSOM' and 8 <= float(row['station']) <= 15
        if best is not None and best[0] < .045 and not multiple_center_intersections and not parallel_body:
            error, handle, profile = best
            method = 'cached_body_polyline'
        else:
            profile, error, handle, method = reference, None, None, 'offset_table_interpolation'
            if multiple_center_intersections:
                method = 'multilobe_table_approximation'
            elif parallel_body:
                method = 'parallel_body_table'
        half = resample(profile, samples)
        yz = np.vstack([half, half[:0:-1]*[-1, 1]])
        ring = np.column_stack([np.full(len(yz), row['x_m']), yz])
        sections.append(ring)
        evidence.append(dict(station=row['station'], x_m=row['x_m'], method=method,
                             curve_handle=handle, table_fit_rms_m=error,
                             raw_half_profile_yz_m=profile.tolist()))
    return sections, evidence, body_datum


def make_hull(sections, subdivisions, dimensions, bow_closure):
    rings = []
    for a, b in zip(sections[:-1], sections[1:]):
        rings.extend(a*(1-t)+b*t for t in np.arange(subdivisions)/subdivisions)
    rings.append(sections[-1])
    assumptions = []
    if bow_closure == 'nominal':
        nominal_end = rings[0][0, 0]+dimensions['loa_m']
        if nominal_end > rings[-1][0, 0]+.01:
            last = rings[-1]
            zdeck = last[:, 2].max()
            # 有限宽度的凸艏端封口；不把复杂球艏剖面缩成小于板厚的尖点。
            angle = np.linspace(-np.pi/2, np.deg2rad(80), 500)
            nose = np.column_stack([.4*np.cos(angle), zdeck-.3*np.sin(angle[-1])+.3*np.sin(angle)])
            nose[0, 0] = 0.
            half = resample(nose, (len(last)+1)//2)
            yz = np.vstack([half, half[:0:-1]*[-1, 1]])
            tip = np.column_stack([np.full(len(yz), nominal_end), yz])
            for t in np.linspace(0, 1, subdivisions+1)[1:]:
                blend = 1-(1-t)**2
                ring = last*(1-blend)+tip*blend
                ring[:, 0] = last[0, 0]*(1-t)+nominal_end*t
                rings.append(ring)
            assumptions.append(dict(kind='approximate_bow_completion', start_x_m=float(last[0, 0]),
                end_x_m=float(nominal_end), note='原型值表未覆盖完整总长；艏端收尖补全是假设，未拉伸原站位。可用--bow-closure station取消。'))
    vertices, faces = mu.closed_loft(rings)
    n = len(rings[0])
    if bow_closure == 'nominal' and assumptions:
        # 凸封口用中心扇形，避免大量共线点的瘦长耳切三角形在内偏移后翻面。
        last_offset = (len(rings)-1)*n
        faces = faces[~np.all(faces >= last_offset, axis=1)]
        center = len(vertices)
        vertices = np.vstack([vertices, vertices[last_offset:last_offset+n].mean(axis=0)])
        faces = np.vstack([faces, [(center, last_offset+j, last_offset+(j+1)%n) for j in range(n)]])
    side_count = 2*(len(rings)-1)*n
    half_count = (n+1)//2
    mask = np.ones(len(faces), dtype=bool)
    for i in range(len(rings)-1):
        mask[2*(i*n+half_count-1):2*(i*n+half_count-1)+2] = False
    if side_count > len(faces):
        raise ValueError('放样面索引不一致。')
    return vertices, faces, faces[mask], rings, assumptions


def drawing_spans(inputs):
    drawing = next(d for d in inputs['drawings'] if '总布置' in d['source']['name'])
    coords = [e for e in drawing['modelspace'] if e['type'] == 'INSERT' and e['dxf']['name'] == 'COORD']
    origin = max(coords, key=lambda e: e['dxf']['insert'][1])['dxf']['insert']
    dims = [e for e in drawing['modelspace'] if e['type'] == 'DIMENSION'
            and abs(e['dxf'].get('angle', 0)) < 1e-6
            and abs(e['dxf']['defpoint'][1]-origin[1]+1440.30173472) < 5
            and e['geometric_measurement'] > 10000]
    dims.sort(key=lambda e: e['dxf']['defpoint2'][0])
    if len(dims) != 6:
        raise ValueError('总布置机舱+五货舱尺寸链识别失败。')
    spans = []
    for i, e in enumerate(dims):
        spans.append(dict(id='ENGINE_ROOM' if i == 0 else f'HOLD_{6-i}',
            name='机舱包络' if i == 0 else f'第{6-i}货舱',
            start_m=(e['dxf']['defpoint2'][0]-origin[0])/1000,
            end_m=(e['dxf']['defpoint3'][0]-origin[0])/1000, source_handle=e['handle']))
    typical = next(d for d in inputs['drawings'] if '典型横剖面' in d['source']['name'])
    measured = [e['geometric_measurement']/1000 for e in typical['modelspace'] if e['type'] == 'DIMENSION']
    for target in (1.75, 1.3, 26.2, 20.):
        if not any(abs(v-target) < .001 for v in measured):
            raise ValueError(f'典型剖面尺寸{target}m未通过核对。')
    return spans, dict(ga_drawing=drawing['source']['name'], ap_drawing_xy_mm=origin[:2],
                       double_bottom_m=1.75, side_tank_m=1.3, inner_half_breadth_m=13.1,
                       upper_half_breadth_m=10., top_slope_start_m=10.6, top_slope_end_m=12.673913)


def partition_profiles(locations, levels, mid_y, thickness):
    """对内舷折线按面法向半厚度偏移，斜板不能简单沿Y平移4mm。

    计入局部纵向斜率；折角处对相邻偏移线求交。端点裁在原高度上，
    保持与双层底和舱顶的交界；变剖面区仍为离散放样近似。
    """
    dx = np.gradient(mid_y, locations, axis=0)
    sides = []
    for sign in (-1., 1.):
        result = []
        for i, y in enumerate(mid_y):
            slopes = np.diff(y)/np.diff(levels)
            longitudinal = (dx[i, :-1]+dx[i, 1:])/2
            scale = np.sqrt(1+slopes**2+longitudinal**2)
            intercept = y[:-1]-slopes*levels[:-1]+sign*thickness/2*scale
            p = np.column_stack([y.copy(), levels.copy()])
            p[0, 0] += sign*thickness/2*scale[0]
            p[-1, 0] += sign*thickness/2*scale[-1]
            for j in range(1, len(levels)-1):
                if abs(slopes[j]-slopes[j-1]) < .001:
                    p[j, 0] += sign*thickness/2*(scale[j-1]+scale[j])/2
                else:
                    z = (intercept[j-1]-intercept[j])/(slopes[j]-slopes[j-1])
                    p[j] = [intercept[j]+slopes[j]*z, z]
            if np.any(np.diff(p[:, 1]) <= 0):
                raise ValueError('纵隔板偏移折线次序异常。')
            result.append(p)
        sides.append(np.array(result))
    return sides[0], sides[1]


def partition_mesh(rings):
    """配对两侧的三角形对角线，避免薄板在变剖面处形成假体积或交叉。"""
    rings = np.array(rings)
    nz = rings.shape[1]//2
    positive = rings[:, :nz].reshape(-1, 3)
    negative = rings[:, nz:][:, ::-1].reshape(-1, 3)
    v = np.vstack([positive, negative])
    n = len(positive)
    surface = []
    for i in range(len(rings)-1):
        for j in range(nz-1):
            a, b = i*nz+j, i*nz+j+1
            surface.extend([(a, b, b+nz), (a, b+nz, a+nz)])
    surface = np.array(surface, dtype=int)
    f = list(map(tuple, surface))+list(map(tuple, surface[:, ::-1]+n))
    edges = Counter((int(a), int(b)) for face in surface for a, b in zip(face, np.roll(face, -1)))
    for a, b in edges:
        if (b, a) not in edges:
            f.extend([(b, a, a+n), (b, a+n, b+n)])
    f = np.array(f, dtype=int)
    signed = np.einsum('ij,ij->i', v[f[:, 0]], np.cross(v[f[:, 1]], v[f[:, 2]])).sum()/6
    if signed < 0:
        f = f[:, ::-1]
    return v, f


def compartment_loft(rings):
    """共享内舷界面采用一致的纵高向对角线，与隔板两侧逐面贴合。"""
    v, f = mu.closed_loft(rings)
    n = len(rings[0])
    side_count = 2*(len(rings)-1)*n
    for i in range(len(rings)-1):
        for j in range(n):
            a, b = i*n+j, i*n+(j+1)%n
            if v[a, 2] > v[b, 2]+1e-9:
                index = 2*(i*n+j)
                f[index:index+2] = [(a, b, a+n), (b, b+n, a+n)]
    if side_count > len(f):
        raise ValueError('舱室放样面数异常。')
    return v, f


def make_compartments(inputs, rings, bulkhead_thickness=.008, partition_thickness=.008):
    spans, config = drawing_spans(inputs)
    boundaries = sorted(set(round(s[k], 8) for s in spans for k in ('start_m', 'end_m')))
    config.update(bulkhead_thickness_m=bulkhead_thickness, bulkhead_centers_x_m=boundaries,
                  compartment_end_setback_m=bulkhead_thickness/2,
                  longitudinal_partition_thickness_m=partition_thickness)
    xs = np.array([r[0, 0] for r in rings])
    def ring_at(x):
        i = min(max(int(np.searchsorted(xs, x))-1, 0), len(xs)-2)
        t = (x-xs[i])/(xs[i+1]-xs[i])
        r = rings[i]*(1-t)+rings[i+1]*t
        r[:, 0] = x
        return r
    components, partitions = [], []
    colors = ['#e6b85c', '#6fbad1', '#b09ed9', '#80bb9a', '#df9582']
    def add(ident, name, category, color, rr, source):
        v, f = compartment_loft(rr) if category in ('cargo', 'ballast') else mu.closed_loft(rr)
        checks = mu.validate(v, f)
        components.append(dict(id=ident, name=name, category=category, color=color,
            vertices=v, faces=f, checks=checks, source=source,
            start_m=float(v[:, 0].min()), end_m=float(v[:, 0].max())))
    for span in spans:
        a, b = span['start_m']+bulkhead_thickness/2, span['end_m']-bulkhead_thickness/2
        if a >= b:
            raise ValueError('隔板厚度大于舱段长度。')
        locations = np.unique(np.round(np.r_[np.linspace(a, b, 14), xs[(xs>a)&(xs<b)][::4]], 8))
        if span['id'] == 'ENGINE_ROOM':
            add(span['id'], span['name'], 'machinery', '#a1acbe', [ring_at(x) for x in locations], span)
            continue
        levels = np.array([1.75, 3, 5.75, 8, 10.6, config['top_slope_end_m'], 14.6])
        cargo, port, starboard, bottom = [], [], [], []
        shells = [ring_at(x) for x in locations]
        outer_rows, inner_rows = [], []
        for shell in shells:
            half = shell[:(len(shell)+1)//2, 1:]
            outer = np.array([horizontal_width(half, z) for z in levels])
            if any(v is None for v in outer):
                raise ValueError('货舱高度超出船壳。')
            inner = np.minimum(np.interp(levels, [1.75, 10.6, config['top_slope_end_m'], 14.6], [13.1, 13.1, 10., 10.]), outer-1.3)
            outer_rows.append(outer)
            inner_rows.append(inner)
        inner_rows = np.array(inner_rows)
        cargo_side, ballast_side = partition_profiles(locations, levels, inner_rows, partition_thickness)
        wall_rings = {'P': [], 'S': []}
        mid_rings = {'P': [], 'S': []}
        for i, x in enumerate(locations):
            shell = shells[i]
            half = shell[:(len(shell)+1)//2, 1:]
            outer = outer_rows[i]
            cy, cz = cargo_side[i].T
            wy, wz = ballast_side[i].T
            def r(y, z):
                return np.column_stack([np.full(len(y), x), y, z])
            cargo.append(r(np.r_[cy, -cy[::-1]], np.r_[cz, cz[::-1]]))
            # 本项目采用Y正为左舷P，与装载手册舱重心符号一致。
            port.append(r(np.r_[outer, wy[::-1]], np.r_[levels, wz[::-1]]))
            starboard.append(r(np.r_[-wy, -outer[::-1]], np.r_[wz, levels[::-1]]))
            wall_rings['P'].append(r(np.r_[wy, cy[::-1]], np.r_[wz, cz[::-1]]))
            wall_rings['S'].append(r(np.r_[-cy, -wy[::-1]], np.r_[cz, wz[::-1]]))
            mid_rings['P'].append(r(inner_rows[i], levels))
            mid_rings['S'].append(r(-inner_rows[i], levels))
            zl = np.linspace(0, 1.75, 6)
            yw = np.array([horizontal_width(half, z) for z in zl])
            bottom.append(r(np.r_[yw, -yw[::-1]], np.r_[zl, zl[::-1]]))
        num = span['id'].split('_')[1]
        add(span['id'], span['name'], 'cargo', colors[int(num)-1], cargo, span)
        add('WBT_'+num+'_P', f'{num}号左边舱', 'ballast', '#5891b3', port, span)
        add('WBT_'+num+'_S', f'{num}号右边舱', 'ballast', '#417c9f', starboard, span)
        add('DB_'+num, f'{num}号双层底空间', 'double_bottom', '#99866a', bottom, span)
        for side, label in [('P', '左'), ('S', '右')]:
            v, f = partition_mesh(wall_rings[side])
            checks = mu.validate(v, f)
            volume, cg = mu.volume_centroid(v, f)
            mv = np.vstack(mid_rings[side])
            mf = []
            for i in range(len(locations)-1):
                for j in range(len(levels)-1):
                    aa, bb = i*len(levels)+j, i*len(levels)+j+1
                    mf.extend([(aa, bb, bb+len(levels)), (aa, bb+len(levels), aa+len(levels))])
            mf = np.array(mf, dtype=int)
            _, areas, _ = mu.face_geometry(mv, mf)
            approximate = float(areas.sum()*partition_thickness)
            if abs(volume/approximate-1) > .02:
                raise ValueError(f'纵隔板{num}{side}体积{volume}与中面估计{approximate}偏差超过2%。')
            partitions.append(dict(id=f'PARTITION_{num}_{side}', name=f'{num}号货舱{label}内舷隔板',
                category='partition', color='#c9ac7a', vertices=v, faces=f, checks=checks,
                midsurface_vertices=mv, midsurface_faces=mf,
                start_m=float(v[:, 0].min()), end_m=float(v[:, 0].max()),
                center_x_m=float((a+b)/2), thickness_m=partition_thickness,
                material_volume_m3=volume, cg_m=cg.tolist(), density_kg_m3=7850., mass_t=volume*7.85,
                midsurface_area_m2=float(areas.sum()), thin_plate_volume_m3=approximate,
                source=dict(hold=span['id'], side=side, boundary='货舱与边压载舱共同内舷界面，含上部斜板',
                            nominal_boundary=span, setback='各侧法向半板厚，变剖面为离散偏移近似')))
    for ident, name, a, b in [('AFT_PEAK', '艉端空间', xs[0], spans[0]['start_m']-bulkhead_thickness/2),
                               ('FORE_PEAK', '艏端空间', spans[-1]['end_m']+bulkhead_thickness/2, xs[-1])]:
        loc = np.unique(np.round(np.r_[np.linspace(a, b, 12), xs[(xs>a)&(xs<b)][::4]], 8))
        add(ident, name, 'peak', '#74a6a7', [ring_at(x) for x in loc], dict(note='主分舱边界间的外包络，未细分小舱'))
    return components, config, partitions


def inner_section_polygon(vertices, faces, x):
    """在真实内壁三角网上切片，连接从一舷至另一舷的开口轮廓。"""
    triangles = vertices[faces]
    selected = triangles[(triangles[:, :, 0].min(axis=1) <= x) &
                         (triangles[:, :, 0].max(axis=1) >= x)]
    graph, coords = {}, {}
    for tri in selected:
        intersections = {}
        for a, b in zip(tri, np.roll(tri, -1, axis=0)):
            if abs(a[0]-x) < 1e-10:
                p = a[1:]
                intersections[tuple(np.round(p, 8))] = p
            if (a[0]-x)*(b[0]-x) < 0:
                p = a[1:]+(b[1:]-a[1:])*(x-a[0])/(b[0]-a[0])
                intersections[tuple(np.round(p, 8))] = p
        if len(intersections) == 2:
            a, b = intersections
            coords.update(intersections)
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
        elif len(intersections) > 2:
            raise ValueError('切片穿过共面三角形，请调整隔板位置或切片容差。')
    ends = [p for p, neighbors in graph.items() if len(neighbors) == 1]
    if len(ends) != 2 or any(len(n) > 2 for n in graph.values()):
        raise ValueError(f'X={x:.6f}处内壁截线不是单一开口链。')
    path, previous, current = [], None, ends[0]
    while current is not None:
        path.append(coords[current])
        choices = graph[current]-({previous} if previous is not None else set())
        previous, current = current, next(iter(choices)) if choices else None
        if len(path) > len(graph):
            raise ValueError('内壁切片路径形成循环。')
    if len(path) != len(graph):
        raise ValueError('内壁截线含未连接的分支。')
    polygon = Polygon(path)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f'X={x:.6f}处内壁截面无效。')
    return polygon


def make_bulkheads(inner, plate_faces, centers, thickness):
    """8mm横板：边界为中面，截面取板厚区间内壁切片的共同区域。

    沿内轮廓预留0.05mm数值间隙，避免与船壳材料重叠、重复计量。
    各板是独立闭合材料域，装配网格不作焊缝布尔融合。
    """
    bulkheads = []
    for i, center in enumerate(centers, 1):
        a, b = center-thickness/2, center+thickness/2
        sections = [inner_section_polygon(inner, plate_faces, x) for x in np.linspace(a, b, 5)]
        profile = sections[0]
        for section in sections[1:]:
            profile = profile.intersection(section)
        profile = profile.buffer(-.00005, join_style=2).simplify(.00001, preserve_topology=True)
        if profile.geom_type != 'Polygon' or not profile.is_valid or profile.interiors:
            raise ValueError(f'隔板{i}截面交集不是无孔单一多边形。')
        v, f = extrude_plate(profile, a, b)
        checks = mu.validate(v, f)
        volume, cg = mu.volume_centroid(v, f)
        expected = profile.area*thickness
        if abs(volume-expected) > max(expected*1e-7, 1e-8):
            raise ValueError('隔板体积与截面积×厚度不一致。')
        if not all(p.buffer(2e-6).covers(profile) for p in sections):
            raise ValueError('隔板截面越过内壁。')
        bulkheads.append(dict(id=f'BULKHEAD_{i:02d}', name=f'{i}号横隔板', category='bulkhead',
            color='#dec086', vertices=v, faces=f, checks=checks, center_x_m=center,
            start_m=a, end_m=b, thickness_m=thickness, cross_section_area_m2=float(profile.area),
            material_volume_m3=volume, cg_m=cg.tolist(), density_kg_m3=7850., mass_t=volume*7.85,
            source=dict(basis='总布置主舱段边界；按用户指定厚度的连续平板近似',
                        shell_fit='板厚区间内5处内壁截面交集', edge_clearance_m=.00005)))
    return bulkheads


def extrude_plate(profile, a, b):
    """约束Delaunay端面保留真实边界分段，避免细薄板端面出现T形接缝。"""
    profile = orient(profile, sign=1.)
    yz = list(profile.exterior.coords[:-1])
    boundary_count = len(yz)
    indices = {tuple(np.round(p, 9)): i for i, p in enumerate(yz)}
    caps = []
    for triangle in constrained_delaunay_triangles(profile).geoms:
        tri = []
        for p in list(triangle.exterior.coords)[:-1]:
            key = tuple(np.round(p, 9))
            if key not in indices:
                indices[key] = len(yz)
                yz.append(p)
            tri.append(indices[key])
        aa, bb, cc = (np.array(yz[i]) for i in tri)
        if (bb[0]-aa[0])*(cc[1]-aa[1])-(bb[1]-aa[1])*(cc[0]-aa[0]) < 0:
            tri.reverse()
        caps.append(tri)
    yz = np.array(yz)
    n = len(yz)
    v = np.vstack([np.column_stack([np.full(n, x), yz]) for x in (a, b)])
    f = [tuple(reversed(t)) for t in caps]+[tuple(i+n for i in t) for t in caps]
    for i in range(boundary_count):
        j = (i+1)%boundary_count
        f.extend([(i, j, j+n), (i, j+n, i+n)])
    return v, np.array(f, dtype=int)


def combine_meshes(parts):
    vertices, faces, vertex_offset = [], [], 0
    for part in parts:
        vertices.append(part['vertices'])
        faces.append(part['faces']+vertex_offset)
        vertex_offset += len(part['vertices'])
    return np.vstack(vertices), np.vstack(faces)


def write_scene_obj(path, components):
    with path.open('w', encoding='utf-8') as f, path.with_suffix('.mtl').open('w', encoding='utf-8') as mat:
        f.write(f'mtllib {path.with_suffix(".mtl").name}\n# Units m\n')
        offset = 1
        for part in components:
            f.write(f"o {part['id']}\nusemtl {part['id']}\n")
            rgb = [int(part['color'][i:i+2], 16)/255 for i in (1, 3, 5)]
            mat.write(f"newmtl {part['id']}\nKd {rgb[0]:.5f} {rgb[1]:.5f} {rgb[2]:.5f}\n\n")
            np.savetxt(f, part['vertices'], fmt='v %.9f %.9f %.9f')
            np.savetxt(f, part['faces']+offset, fmt='f %d %d %d')
            offset += len(part['vertices'])


def write_dxf(path, hull_v, hull_f, components):
    doc = ezdxf.new('R2010')
    doc.units = 6
    all_parts = [dict(id='HULL', color='#528ba5', vertices=hull_v, faces=hull_f)]+components
    for part in all_parts:
        rgb = tuple(int(part['color'][i:i+2], 16) for i in (1, 3, 5))
        doc.layers.new(part['id'], dxfattribs={'true_color': ezdxf.colors.rgb2int(rgb)})
        for face in part['faces']:
            doc.modelspace().add_3dface(part['vertices'][face].tolist(), dxfattribs={'layer': part['id']})
    doc.saveas(path)


def html_mesh(part, smooth=True):
    v, f = part['vertices'], part['faces']
    normals, _, _ = mu.face_geometry(v, f)
    if smooth:
        vn = np.zeros_like(v)
        for i in range(3):
            np.add.at(vn, f[:, i], normals)
        vout, nout, fout = v, vn, f
    else:
        vout, nout = v[f].reshape(-1, 3), np.repeat(normals, 3, axis=0)
        fout = np.arange(len(vout)).reshape(-1, 3)
    enc = lambda a, t: base64.b64encode(np.asarray(a, dtype=t).tobytes()).decode('ascii')
    return dict(id=part['id'], name=part.get('name', part['id']), category=part.get('category', 'shell'), color=part['color'],
                thickness_mm=part.get('thickness_m', 0)*1000,
                positions=enc(vout, '<f4'), normals=enc(nout, '<f4'), faces=enc(fout, '<u4'),
                triangles=len(f), bounds=[v.min(axis=0).tolist(), v.max(axis=0).tolist()])


def write_html(path, parts, mode, dimensions):
    template = (HERE/'viewer_template.html').read_text(encoding='utf-8')
    payload = dict(mode=mode, dimensions=dimensions, thickness_mm=mu.THICKNESS*1000,
                   bulkhead_thickness_mm=next((p['thickness_m']*1000 for p in parts if p.get('category') == 'bulkhead'), None),
                   partition_thickness_mm=next((p['thickness_m']*1000 for p in parts if p.get('category') == 'partition'), None),
                   meshes=[html_mesh(p, True) for p in parts])
    path.write_text(template.replace('MESH_PAYLOAD', json.dumps(payload, ensure_ascii=False)), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-cache', type=Path, default=PROJECT/'01_输入整理与对照数据/output')
    parser.add_argument('--output', type=Path, default=HERE/'CAD_mesh')
    parser.add_argument('--samples', type=int, default=129)
    parser.add_argument('--subdivisions', type=int, default=8)
    parser.add_argument('--thickness-mm', type=float, default=12.)
    parser.add_argument('--bulkhead-thickness-mm', type=float, default=8.)
    parser.add_argument('--partition-thickness-mm', type=float, default=8.)
    parser.add_argument('--bow-closure', choices=['nominal', 'station'], default='nominal')
    parser.add_argument('--preview', choices=['display', 'mesh'])
    args = parser.parse_args()
    if args.samples < 16 or args.subdivisions < 1 or not all(1 <= t <= 30 for t in (args.thickness_mm, args.bulkhead_thickness_mm, args.partition_thickness_mm)):
        parser.error('samples>=16、subdivisions>=1、船壳和隔板厚度范围1..30mm。')
    inputs = load_inputs(args.input_cache, include_drawings=True)
    if inputs['quality']['errors']:
        raise ValueError('01包含未解决错误，请先核对quality_report.json。')
    dimensions = {k: v['value_m'] for k, v in inputs['principal_dimensions']['dimensions'].items()}
    if any(v is None for v in dimensions.values()):
        raise ValueError('主尺度缺失或冲突。')
    sections, evidence, body_datum = reconstruct_sections(inputs, args.samples)
    outer, hydro_faces, plate_faces, rings, assumptions = make_hull(sections, args.subdivisions, dimensions, args.bow_closure)
    outer_check = mu.validate(outer, hydro_faces)
    mu.THICKNESS = args.thickness_mm/1000
    inner, solid_v, solid_f, material_check = mu.offset_shell(outer, plate_faces)
    components, compartment_config, partitions = make_compartments(inputs, rings,
        args.bulkhead_thickness_mm/1000, args.partition_thickness_mm/1000)
    bulkheads = make_bulkheads(inner, plate_faces, compartment_config['bulkhead_centers_x_m'],
                              args.bulkhead_thickness_mm/1000)
    bulkhead_v, bulkhead_f = combine_meshes(bulkheads)
    partition_v, partition_f = combine_meshes(partitions)
    partition_mid_v, partition_mid_f = combine_meshes([
        dict(vertices=p['midsurface_vertices'], faces=p['midsurface_faces']) for p in partitions])
    internal_plates = bulkheads+partitions
    bulkhead_midparts = []
    for p in bulkheads:
        cap = p['faces'][np.all(np.abs(p['vertices'][p['faces'], 0]-p['start_m']) < 1e-8, axis=1)]
        used, inverse = np.unique(cap, return_inverse=True)
        mid_v = p['vertices'][used].copy()
        mid_v[:, 0] = p['center_x_m']
        bulkhead_midparts.append(dict(vertices=mid_v, faces=inverse.reshape(-1, 3)))
    bulk_mid_v, bulk_mid_f = combine_meshes(bulkhead_midparts)
    structure_v, structure_f = combine_meshes([dict(vertices=solid_v, faces=solid_f)]+internal_plates)
    structure_check = mu.validate(structure_v, structure_f)
    mid = (outer+inner)/2
    out = args.output.resolve()
    display, compute = out/'01_展示模型', out/'02_计算网格'
    display.mkdir(parents=True, exist_ok=True)
    compute.mkdir(parents=True, exist_ok=True)
    hull_part = dict(id='HULL', name='船体外壳', category='shell', color='#588fa7', vertices=outer, faces=plate_faces)
    write_scene_obj(display/'ship_display.obj', [hull_part]+components+internal_plates)
    np.savez_compressed(display/'compartments.npz', **{
        key: value for part in components+internal_plates for key, value in
        [(part['id']+'_vertices', part['vertices']), (part['id']+'_faces', part['faces'])]})
    write_dxf(display/'ship_compartments.dxf', outer, plate_faces, components+internal_plates)
    write_html(display/'ship_display.html', [hull_part]+components+internal_plates, 'display', dimensions)
    mu.write_obj(compute/'steel_shell.obj', solid_v, solid_f)
    mu.write_stl(compute/'steel_shell.stl', solid_v, solid_f)
    mu.write_vtk(compute/'steel_shell.vtk', solid_v, solid_f)
    for name, v, f in [('bulkheads', bulkhead_v, bulkhead_f),
                        ('longitudinal_partitions', partition_v, partition_f),
                        ('steel_structure', structure_v, structure_f)]:
        mu.write_obj(compute/(name+'.obj'), v, f)
        mu.write_stl(compute/(name+'.stl'), v, f,
                     f'{name}; m; shell {args.thickness_mm:g}mm; transverse {args.bulkhead_thickness_mm:g}mm; longitudinal {args.partition_thickness_mm:g}mm')
        mu.write_vtk(compute/(name+'.vtk'), v, f)
    mu.write_obj(compute/'hydro_outer.obj', outer, hydro_faces)
    mu.write_stl(compute/'hydro_outer.stl', outer, hydro_faces)
    mu.write_vtk(compute/'hydro_outer.vtk', outer, hydro_faces)
    mu.write_vtk(compute/'shell_midsurface.vtk', mid, plate_faces, mu.THICKNESS)
    mu.write_bdf(compute/'shell_midsurface.bdf', mid, plate_faces, 7850.)
    mu.write_vtk(compute/'bulkhead_midsurface.vtk', bulk_mid_v, bulk_mid_f, args.bulkhead_thickness_mm/1000)
    try:
        mu.THICKNESS = args.bulkhead_thickness_mm/1000
        mu.write_bdf(compute/'bulkhead_midsurface.bdf', bulk_mid_v, bulk_mid_f, 7850.)
    finally:
        mu.THICKNESS = args.thickness_mm/1000
    mu.write_vtk(compute/'partition_midsurface.vtk', partition_mid_v, partition_mid_f, args.partition_thickness_mm/1000)
    try:
        mu.THICKNESS = args.partition_thickness_mm/1000
        mu.write_bdf(compute/'partition_midsurface.bdf', partition_mid_v, partition_mid_f, 7850.)
    finally:
        mu.THICKNESS = args.thickness_mm/1000
    ring_x = np.array([r[0, 0] for r in rings])
    np.savez_compressed(compute/'calculation_mesh.npz', outer_vertices=outer, hydro_faces=hydro_faces,
        plate_faces=plate_faces, inner_vertices=inner, midsurface_vertices=mid,
        solid_vertices=solid_v, solid_faces=solid_f, thickness_m=mu.THICKNESS,
        structure_vertices=structure_v, structure_faces=structure_f,
        bulkhead_vertices=bulkhead_v, bulkhead_faces=bulkhead_f,
        bulkhead_midsurface_vertices=bulk_mid_v, bulkhead_midsurface_faces=bulk_mid_f,
        bulkhead_centers_x=np.array(compartment_config['bulkhead_centers_x_m']),
        bulkhead_thickness_m=args.bulkhead_thickness_mm/1000,
        partition_vertices=partition_v, partition_faces=partition_f,
        partition_midsurface_vertices=partition_mid_v, partition_midsurface_faces=partition_mid_f,
        partition_thickness_m=args.partition_thickness_mm/1000,
        ring_x=ring_x, ring_vertex_count=len(rings[0]), source_station_x=np.array([r[0, 0] for r in sections]))
    n = len(outer)
    shell_parts = [dict(id='STEEL_OUTER', name='外钢板面', category='outer', color='#479ab8', vertices=outer, faces=solid_f[:len(plate_faces)]),
        dict(id='STEEL_INNER', name='内钢板面', category='inner', color='#c4d2d7', vertices=inner, faces=solid_f[len(plate_faces):2*len(plate_faces)]-n),
        dict(id='STEEL_RIM', name='顶缘封边', category='rim', color='#dda768', vertices=solid_v, faces=solid_f[2*len(plate_faces):])]
    write_html(compute/'steel_shell_mesh.html', shell_parts+internal_plates, 'mesh', dimensions)
    shell_volume, shell_cg = mu.volume_centroid(solid_v, solid_f)
    materials = [dict(id='STEEL_SHELL', name='船壳', material_volume_m3=shell_volume,
                      cg_m=shell_cg.tolist(), density_kg_m3=7850., mass_t=shell_volume*7.85)]+[
        {k: p[k] for k in ('id', 'name', 'center_x_m', 'thickness_m', 'material_volume_m3', 'cg_m', 'density_kg_m3', 'mass_t')}
        for p in internal_plates]
    total_volume = sum(p['material_volume_m3'] for p in materials)
    total_cg = sum(p['material_volume_m3']*np.array(p['cg_m']) for p in materials)/total_volume
    if not np.isclose(total_volume, structure_check['signed_volume_m3'], rtol=1e-7):
        raise ValueError('钢结构装配体积与分项体积合计不一致。')
    save_json(compute/'steel_materials.json', dict(materials=materials, total_material_volume_m3=total_volume,
        total_mass_t=total_volume*7.85, total_cg_m=total_cg.tolist(),
        note='船壳+7道横隔板+10块货舱与边压载舱间内舷纵隔板；无甲板、内底实体或骨架；独立闭合材料域装配，未作焊缝布尔融合。'))
    save_json(compute/'bulkheads.json', dict(thickness_m=args.bulkhead_thickness_mm/1000,
        bulkheads=[{k: v for k, v in p.items() if k not in ('vertices', 'faces')} for p in bulkheads]))
    save_json(compute/'longitudinal_partitions.json', dict(thickness_m=args.partition_thickness_mm/1000,
        partitions=[{k: v for k, v in p.items() if k not in ('vertices', 'faces', 'midsurface_vertices', 'midsurface_faces')}
                    for p in partitions]))
    save_json(display/'compartments.json', dict(configuration=compartment_config,
        compartments=[{k: v for k, v in p.items() if k not in ('vertices', 'faces')} for p in components],
        limitations=['23个主要分舱空间为典型横剖面限宽近似，尚未细分油水小舱、管弄和墩座。',
                     '各舱段按横隔板半厚度退让，隔板厚度区间不计入舱室空间。',
                     '货舱与左右边压载舱沿共同内舷界面各退让半板厚，含斜板法向偏移。',
                     '彩色展示的平滑法向仅改变渲染，不改变导出的几何。']))
    save_json(out/'reconstruction_report.json', dict(dimensions=dimensions, thickness_m=mu.THICKNESS,
        input_manifest_sha256=hashlib.sha256((args.input_cache/'manifest.json').read_bytes()).hexdigest(),
        source_drawings=inputs['manifest']['drawings'], source_dxf_reads=0,
        section_evidence=evidence, body_plan_datum=body_datum, assumptions=assumptions,
        outer_mesh=outer_check, material_mesh=material_check, compartments=len(components),
        bulkheads=len(bulkheads), bulkhead_thickness_m=args.bulkhead_thickness_mm/1000,
        longitudinal_partitions=len(partitions), partition_thickness_m=args.partition_thickness_mm/1000,
        bulkhead_model='连续平板近似，未模拟波纹、扶强材、门孔或开口',
        steel_structure_mesh=structure_check, steel_structure_material_volume_m3=total_volume,
        units='m, kg, s', coordinates='AP=0; X向艏; Y向左舷P; Z自基线向上',
        readiness=dict(section_hydrostatics_mesh=True, shell_material_volume_mesh=True,
                       cfd_surface_candidate=True, cfd_volume_mesh=False, global_self_intersection_certified=False),
        limitations=['没有读取原始DXF/PDF；仅使用01缓存。', '三维几何为离散放样，未生成NURBS/STEP实体。',
                     '20及20.25站中线多交点剖面暂按型值连续连接，球艏多区域拓扑尚未精确复原。',
                     '艏艉、球艏局部细节仍需后续剖面对照；未完成全局自交认证。',
                     'CFD目录输出的是表面前处理候选网格，不含流场域、边界层和流体体网格。',
                     'hydro_outer中的顶封口是虚拟水密包络，不属于钢板质量；steel_shell是开口空心材料域。']))
    (out/'README.md').write_text('''# 阶段02输出

- `01_展示模型/ship_display.html`：离线彩色船体与主要分舱，默认无网格。
- `01_展示模型/ship_display.obj`及MTL、`ship_compartments.dxf`：按对象/图层区分船壳及23个主要分舱。
- `02_计算网格/steel_shell_mesh.html`：真实内外钢板和顶缘网格，可旋转、切半舷或截取船长。
- `02_计算网格/steel_shell.obj/stl/vtk`：开口、空心、含顶缘封边的钢板材料域，不含舱壁和甲板。
- `02_计算网格/bulkheads.obj/stl/vtk`：独立的8mm横隔板材料域。
- `02_计算网格/longitudinal_partitions.obj/stl/vtk`：10块货舱与左右边压载舱间的8mm内舷纵隔板，含上部斜板。
- `02_计算网格/steel_structure.obj/stl/vtk`：船壳、横隔板与内舷纵隔板的完整材料装配，用于分项或合计体积、质量和重心计算。
- `02_计算网格/bulkhead_midsurface.bdf/vtk`：8mm隔板中面壳单元。
- `02_计算网格/partition_midsurface.bdf/vtk`：内舷纵隔板中面壳单元，默认厚度8mm。
- `02_计算网格/bulkheads.json`与`steel_materials.json`：位置、厚度、分项体积/质量/重心及合计。
- `02_计算网格/hydro_outer.obj/stl/vtk`：水密外包络，仅供浮力/切片/CFD表面前处理，不可当作钢材实体。
- `02_计算网格/shell_midsurface.bdf/vtk`：恒定板厚的壳单元中面。
- `02_计算网格/calculation_mesh.npz`：后续程序建议直接读取的双精度顶点、三角面及站位索引。
- `reconstruction_report.json`：来源指纹、逐站曲线匹配、网格检查和建模假设。

全部长度单位m。X向艏，Y向左舷，Z向上。源数据为母型172m垂线间长，不是34000t改型。
默认12mm板厚可用`--thickness-mm`调整；法向偏移几何误差见报告，BDF/VTK属性严格采用设定厚度。
默认8mm横隔板可用`--bulkhead-thickness-mm`调整，以图纸舱界为中面，两侧舱室各退让半个板厚。
默认8mm内舷纵隔板可用`--partition-thickness-mm`调整，左右5对，共10块；货舱和边压载舱各按法向半厚度退让。
纵隔板沿典型剖面内舷界面布置，包含垂直段和上部斜段；纵向变剖面采用局部斜率修正的离散偏移。
纵隔板止于横隔板的内表面，顶部和底部裁在原高度。双层底上方的水平内底板不在本次新增范围内。
横隔板沿真实内壁裁形，板厚区间内5处切片取交集，沿轮廓预留0.05mm数值间隙，避免与船壳重复计量。
7道横隔板位于X=7.4、25.8、53.8、82.6、111.4、140.2、165.0m，为连续平板近似，未模拟波纹、门孔或扶强材。
钢结构输出为各自闭合的材料域装配，未作焊缝布尔融合；合计质量仅含船壳、横隔板和内舷纵隔板，非整船空船重量。
默认把型值表最前站之后按总长收尖补全；该段是假设，可用`--bow-closure station`保留原站位范围。
分舱为主要空间示意，尚未复原全部油水小舱、管弄和墩座，不能作为核定舱容。
面闭合、面方向和退化面检查通过不代表已通过CFD网格验收；CFD还需流场域、体网格、边界层及几何质量核查。

后续读取示例：
```python
import numpy as np
mesh = np.load('calculation_mesh.npz')
v, f = mesh['outer_vertices'], mesh['hydro_faces']  # 03/04包络切片
steel_v, steel_f = mesh['structure_vertices'], mesh['structure_faces']  # 船壳+全部隔板材料
# solid_vertices/solid_faces: 纯船壳；bulkhead_*: 横隔板；partition_*: 内舷纵隔板
```
''', encoding='utf-8')
    matches = sum(e['method'] == 'cached_body_polyline' for e in evidence)
    print(f'Output: {out}\nBody curves matched: {matches}/{len(evidence)}; compartments: {len(components)}')
    print(f'Outer: {len(hydro_faces)} triangles; steel material: {len(solid_f)} triangles')
    print(f'Bulkheads: {len(bulkheads)}, thickness {args.bulkhead_thickness_mm:g} mm; total steel material {total_volume:.6f} m3')
    print(f'Longitudinal partitions: {len(partitions)}, thickness {args.partition_thickness_mm:g} mm')
    print(f"Normal thickness gap mm: {material_check['normal_gap_min_mm']:.3f}..{material_check['normal_gap_max_mm']:.3f}")
    if args.preview:
        target = display/'ship_display.html' if args.preview == 'display' else compute/'steel_shell_mesh.html'
        webbrowser.open(target.as_uri())


if __name__ == '__main__':
    main()
