"""Mother hull reconstruction and design geometry."""
from pathlib import Path
from collections import Counter
import base64, json
import numpy as np
import ezdxf
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely import constrained_delaunay_triangles
import mesh_utils as mu
from fsgy_surf.io.files import save_json, write_csv
HERE = Path(__file__).resolve().parent

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
    drawing = next(d for d in inputs['drawings'] if d['role'] == 'general_arrangement')
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
    typical = next(d for d in inputs['drawings'] if d['role'] == 'typical_section')
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


def make_compartments(inputs, rings, bulkhead_thickness=.008, partition_thickness=.008, layout=None):
    spans, config = layout if layout is not None else drawing_spans(inputs)
    scale_y, scale_z = config.get('scale_y', 1.), config.get('scale_z', 1.)
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
        levels = np.array([config['double_bottom_m'], 3*scale_z, 5.75*scale_z,
                           8*scale_z, config['top_slope_start_m'],
                           config['top_slope_end_m'], config['depth_m']])
        if np.any(np.diff(levels) <= 0):
            raise ValueError('Compartment vertical levels must be strictly increasing')
        cargo, port, starboard, bottom = [], [], [], []
        shells = [ring_at(x) for x in locations]
        outer_rows, inner_rows = [], []
        for shell in shells:
            half = shell[:(len(shell)+1)//2, 1:]
            outer = np.array([horizontal_width(half, z) for z in levels])
            if any(v is None for v in outer):
                raise ValueError('货舱高度超出船壳。')
            inner = np.minimum(np.interp(levels,
                [config['double_bottom_m'], config['top_slope_start_m'], config['top_slope_end_m'], config['depth_m']],
                [config['inner_half_breadth_m'], config['inner_half_breadth_m'],
                 config['upper_half_breadth_m'], config['upper_half_breadth_m']]), outer-config['side_tank_m'])
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
            zl = np.linspace(0, config['double_bottom_m'], 6)
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


def spatial_blocks(components, mesh_file, purposes=None):
    """Publish each closed geometric region once, including its physical-space properties."""
    purposes = purposes or {}
    identifiers = {part['id'] for part in components}
    unknown = set(purposes)-identifiers
    if unknown:
        raise ValueError(f'Unknown compartment IDs in compartments.purposes: {sorted(unknown)}')
    defaults = {'cargo': 'cargo', 'ballast': 'ballast_water', 'machinery': 'machinery',
                'double_bottom': 'unassigned', 'peak': 'unassigned'}
    allowed = {'cargo', 'ballast_water', 'fresh_water', 'fuel_oil',
               'lubricating_oil', 'machinery', 'void', 'unassigned'}
    rows = []
    for part in components:
        ident = part['id']
        purpose = purposes.get(ident, defaults[part['category']])
        if purpose not in allowed:
            raise ValueError(f'{ident}: unsupported compartment purpose {purpose}')
        volume, center = mu.volume_centroid(part['vertices'], part['faces'])
        rows.append(dict(id=ident, name=part['name'], category=part['category'],
            purpose=purpose, loadable=purpose in {'cargo', 'ballast_water', 'fresh_water', 'fuel_oil', 'lubricating_oil'},
            side='port' if ident.endswith('_P') else 'starboard' if ident.endswith('_S') else 'center',
            start_m=part['start_m'], end_m=part['end_m'],
            bounds_m=[part['vertices'].min(axis=0).tolist(), part['vertices'].max(axis=0).tolist()],
            volume_m3=volume, center_m=center.tolist(),
            geometry=dict(mesh_file=str(mesh_file), vertices_key=ident+'_vertices', faces_key=ident+'_faces')))
    return rows


def run(ctx):
    from read_dxf import load_drawings
    data = ctx.data[1]
    inputs = load_drawings(data)
    source = data['dimensions']
    target = dict(ctx.config['design']['target_dimensions'])
    scale = np.array([target['lbp_m']/source['lbp_m'],
                      target['beam_m']/source['beam_m'],
                      target['depth_m']/source['depth_m']])
    affine_loa = source['loa_m'] * scale[0]
    if 'loa_m' in target and not np.isclose(target['loa_m'], affine_loa, rtol=1e-6):
        raise ValueError('Affine design requires loa_m/lbp_m to match the mother hull; omit loa_m to derive it')
    target['loa_m'] = affine_loa
    target.setdefault('scantling_draft_m', source.get('scantling_draft_m', source['design_draft_m'])*scale[2])
    settings = ctx.config['mesh']
    sections, _, _ = reconstruct_sections(inputs, settings['samples'])
    outer, hydro_faces, plate_faces, rings, _ = make_hull(
        sections, settings['subdivisions'], source, settings['bow_closure'])
    outer = outer * scale
    rings = [ring * scale for ring in rings]
    source_stations = np.array([ring[0, 0]*scale[0] for ring in sections])
    mu.validate(outer, hydro_faces)
    mu.THICKNESS = settings['shell_thickness_mm']/1000
    inner, solid_v, solid_f, _ = mu.offset_shell(outer, plate_faces)
    spans, layout = drawing_spans(inputs)
    for span in spans:
        span['start_m'] *= scale[0]
        span['end_m'] *= scale[0]
    for key in ('double_bottom_m', 'top_slope_start_m', 'top_slope_end_m'):
        layout[key] *= scale[2]
    for key in ('side_tank_m', 'inner_half_breadth_m', 'upper_half_breadth_m'):
        layout[key] *= scale[1]
    layout.update(scale_y=float(scale[1]), scale_z=float(scale[2]), depth_m=target['depth_m'])
    overrides = ctx.config.get('compartments', {})
    if 'spans' in overrides:
        spans = [dict(span) for span in overrides['spans']]
        if len(spans) != 6 or [s['id'] for s in spans] != ['ENGINE_ROOM', 'HOLD_5', 'HOLD_4', 'HOLD_3', 'HOLD_2', 'HOLD_1']:
            raise ValueError('This adapter needs ordered ENGINE_ROOM, HOLD_5 ... HOLD_1 spans')
        for index, span in enumerate(spans):
            span.setdefault('name', span['id'])
            if span['end_m'] <= span['start_m']:
                raise ValueError('Compartment span end must exceed start')
            if index and not np.isclose(span['start_m'], spans[index-1]['end_m'], atol=1e-8):
                raise ValueError('Compartment spans must share consecutive boundaries')
        if spans[0]['start_m'] <= rings[0][0, 0] or spans[-1]['end_m'] >= rings[-1][0, 0]:
            raise ValueError('Compartment spans must be inside the hull longitudinal extent')
    for key in ('double_bottom_m', 'side_tank_m', 'inner_half_breadth_m',
                'upper_half_breadth_m', 'top_slope_start_m', 'top_slope_end_m'):
        if key in overrides:
            layout[key] = float(overrides[key])
    components, layout, partitions = make_compartments(inputs, rings,
        settings['bulkhead_thickness_mm']/1000, settings['partition_thickness_mm']/1000,
        layout=(spans, layout))
    bulkheads = make_bulkheads(inner, plate_faces, layout['bulkhead_centers_x_m'],
                              settings['bulkhead_thickness_mm']/1000)
    plates = bulkheads + partitions
    structure_v, structure_f = combine_meshes([
        dict(vertices=solid_v, faces=solid_f)] + plates)
    mesh_file = ctx.cache(2)/'calculation_mesh.npz'
    compartment_file = ctx.cache(2)/'compartments.npz'
    np.savez_compressed(mesh_file, outer_vertices=outer, hydro_faces=hydro_faces,
        plate_faces=plate_faces, inner_vertices=inner,
        solid_vertices=solid_v, solid_faces=solid_f,
        structure_vertices=structure_v, structure_faces=structure_f,
        ring_x=np.array([r[0, 0] for r in rings]), source_station_x=source_stations,
        thickness_m=mu.THICKNESS)
    np.savez_compressed(compartment_file, **{
        key: value for part in components for key, value in
        [(part['id']+'_vertices', part['vertices']), (part['id']+'_faces', part['faces'])]})
    out = ctx.output(2)
    hull = dict(id='HULL', name='Hull', category='shell', color='#588fa7', vertices=outer, faces=plate_faces)
    write_scene_obj(out/'ship.obj', [hull] + components + plates)
    write_html(out/'ship.html', [hull] + components + plates, 'display', target)
    formats = settings.get('export_formats', ['obj', 'stl'])
    exporters = {'obj': mu.write_obj, 'stl': mu.write_stl, 'vtk': mu.write_vtk}
    if any(fmt not in exporters for fmt in formats):
        raise ValueError('mesh.export_formats accepts obj, stl, vtk')
    for name, vertices, faces in [('hydro_outer', outer, hydro_faces),
                                  ('steel_structure', structure_v, structure_f)]:
        for fmt in formats:
            exporters[fmt](out/f'{name}.{fmt}', vertices, faces)
    if settings.get('export_dxf', False):
        write_dxf(out/'ship.dxf', outer, plate_faces, components + plates)
    density = float(settings.get('steel_density_t_m3', 7.85))
    if not np.isfinite(density) or density <= 0:
        raise ValueError('steel_density_t_m3 must be positive')
    materials = []
    for part in [dict(id='STEEL_SHELL', name='Hull shell', vertices=solid_v, faces=solid_f)] + plates:
        volume, center = mu.volume_centroid(part['vertices'], part['faces'])
        materials.append(dict(id=part['id'], name=part['name'], volume_m3=volume,
                              mass_t=volume*density, center_m=center.tolist()))
    mass = sum(row['mass_t'] for row in materials)
    center = sum(row['mass_t']*np.array(row['center_m']) for row in materials)/mass
    spaces = spatial_blocks(components, compartment_file, overrides.get('purposes'))
    registry_file = out/'spatial_blocks.json'
    save_json(registry_file, dict(schema_version='1.0',
        coordinates='AP=0; X forward; Y port; Z above baseline; units m', blocks=spaces))
    write_csv(out/'spatial_blocks.csv', spaces)
    result = dict(dimensions=target, mother_dimensions=source, scale_xyz=scale.tolist(),
        mesh_file=str(mesh_file), compartments_file=str(compartment_file),
        spatial_blocks_file=str(registry_file),
        compartments=spaces, materials=materials, structure_mass_t=mass,
        structure_center_m=center.tolist(), layout=layout,
        coordinates='AP=0; X forward; Y port; Z above baseline; units m',
        model_scope='Main hull with virtual deck closure; 23 approximate spaces; shell and primary partitions only')
    save_json(out/'geometry.json', result)
    write_csv(out/'steel_materials.csv', materials)
    return result
