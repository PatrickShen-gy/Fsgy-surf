"""Mesh helpers for stage 02; never reads source DXF/PDF."""
from pathlib import Path
from collections import Counter
import sys
import struct
sys.path.append(str(Path(__file__).resolve().parents[2]/'.cad_deps'))
import numpy as np
THICKNESS = 0.012

def validate(vertices, faces):
    from collections import Counter
    edges = Counter(tuple(sorted((int(a), int(b)))) for f in faces for a, b in zip(f, np.roll(f, -1)))
    normals = np.cross(vertices[faces[:, 1]]-vertices[faces[:, 0]], vertices[faces[:, 2]]-vertices[faces[:, 0]])
    if not np.isfinite(vertices).all() or any(count != 2 for count in edges.values()):
        raise ValueError('网格存在非有限坐标或未封闭边。')
    if np.any(np.linalg.norm(normals, axis=1) < 1e-10):
        raise ValueError('网格存在退化三角形。')
    directed = Counter((int(a), int(b)) for f in faces for a, b in zip(f, np.roll(f, -1)))
    if any(directed[(a, b)] != directed[(b, a)] for a, b in edges):
        raise ValueError('相邻面方向不一致。')
    return dict(vertices=len(vertices), triangles=len(faces), closed_edges=True,
                bounds_m=[vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()],
                signed_volume_m3=float(np.einsum('ij,ij->i', vertices[faces[:, 0]], np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]])).sum()/6))

def closed_loft(rings):
    """相同顶点数的闭合剖面放样，端面采用平面耳切三角化。

    使用耳切处理凹的边舱剖面，避免从中心作扇形封口越出舱界。
    """
    from ezdxf.math import Vec2
    from ezdxf.math.triangulation import mapbox_earcut_2d
    rings = [np.array(r, copy=True) for r in rings]
    yz = rings[0][:, 1:]
    area = np.sum(yz[:, 0]*np.roll(yz[:, 1], -1)-yz[:, 1]*np.roll(yz[:, 0], -1))
    if area < 0:
        rings = [r[::-1] for r in rings]
    vertices = np.vstack(rings)
    n = len(rings[0])
    faces = []
    for i in range(len(rings)-1):
        for j in range(n):
            a, b = i*n+j, i*n+(j+1)%n
            faces.extend([(a, b, b+n), (a, b+n, a+n)])
    for offset, reverse in [(0, True), ((len(rings)-1)*n, False)]:
        polygon = [Vec2(p[1], p[2]) for p in vertices[offset:offset+n]]
        indices = {(round(p.x, 9), round(p.y, 9)): offset+i for i, p in enumerate(polygon)}
        triangles = [tuple(indices[(round(p.x, 9), round(p.y, 9))] for p in tri)
                     for tri in mapbox_earcut_2d(polygon)]
        triangles = [tri for tri in triangles if np.linalg.norm(np.cross(
            vertices[tri[1]]-vertices[tri[0]], vertices[tri[2]]-vertices[tri[0]])) > 1e-10]
        # 耳切可能省略共线边界点；拆回边界分段以保持端面与侧面拓扑一致。
        for point in range(offset, offset+n):
            split = []
            p = vertices[point, 1:]
            for tri in triangles:
                found = False
                if point not in tri:
                    for j in range(3):
                        a, b, other = tri[j], tri[(j+1)%3], tri[(j+2)%3]
                        u, v = vertices[a, 1:], vertices[b, 1:]
                        delta = v-u
                        t = np.dot(p-u, delta)/np.dot(delta, delta)
                        if 1e-8 < t < 1-1e-8 and np.linalg.norm(p-u-t*delta) < 1e-8:
                            split.extend([(a, point, other), (point, b, other)])
                            found = True
                            break
                if not found:
                    split.append(tri)
            triangles = split
        faces.extend(face[::-1] if reverse else face for face in triangles)
    faces = np.array(faces, dtype=int)
    volume = np.einsum('ij,ij->i', vertices[faces[:, 0]], np.cross(vertices[faces[:, 1]], vertices[faces[:, 2]])).sum()/6
    if volume < 0:
        faces = faces[:, ::-1]
    return vertices, faces

def face_geometry(vertices, faces):
    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    lengths = np.linalg.norm(cross, axis=1)
    if np.any(lengths < 1e-12):
        raise ValueError('存在退化三角面。')
    return cross/lengths[:, None], lengths/2, triangles.mean(axis=1)

def volume_centroid(vertices, faces):
    # 平移参考点改善薄壳体积的数值相消。
    ref = (vertices.min(axis=0)+vertices.max(axis=0))/2
    tri = vertices[faces]-ref
    dv = np.einsum('ij,ij->i', tri[:, 0], np.cross(tri[:, 1], tri[:, 2]))/6
    volume = dv.sum()
    if volume <= 0:
        raise ValueError('有向体积非正。')
    center = (dv[:, None]*tri.sum(axis=1)/4).sum(axis=0)/volume+ref
    return float(volume), center

def offset_shell(vertices, faces):
    """沿离散切平面内移12 mm；转角通过相邻面法向方程求交。

    多个非共面三角面共顶点时用最小二乘求交，内壁网格是等厚板的
    几何近似；权威板厚由BDF的PSHELL及VTK逐面属性固定为12 mm。
    """
    normals, _, _ = face_geometry(vertices, faces)
    matrix = np.zeros((len(vertices), 3, 3))
    rhs = np.zeros((len(vertices), 3))
    nn = normals[:, :, None]*normals[:, None, :]
    for i in range(3):
        np.add.at(matrix, faces[:, i], nn)
        np.add.at(rhs, faces[:, i], normals*THICKNESS)
    displacement = np.einsum('ijk,ik->ij', np.linalg.pinv(matrix, rcond=1e-5), rhs)
    inner = vertices-displacement
    n_inner, _, _ = face_geometry(inner, faces)
    bad = np.einsum('ij,ij->i', normals, n_inner) <= 0
    if np.any(bad):
        locations = vertices[faces[bad]].mean(axis=1)
        raise ValueError(f'内移翻面{int(bad.sum())}片；位置范围{locations.min(0)}..{locations.max(0)}')
    gaps = np.einsum('fij,fj->fi', displacement[faces], normals)
    errors = gaps-THICKNESS
    edges = Counter((int(a), int(b)) for face in faces for a, b in zip(face, np.roll(face, -1)))
    boundary = [(a, b) for a, b in edges if (b, a) not in edges]
    n = len(vertices)
    rims = [(b, a, a+n) for a, b in boundary]+[(b, a+n, b+n) for a, b in boundary]
    solid_vertices = np.vstack([vertices, inner])
    solid_faces = np.vstack([faces, faces[:, ::-1]+n, np.array(rims, dtype=int)])
    check = validate(solid_vertices, solid_faces)
    check.update(boundary_edges_before_rim=len(boundary),
                 normal_gap_min_mm=float(gaps.min()*1000),
                 normal_gap_max_mm=float(gaps.max()*1000),
                 normal_gap_rms_error_mm=float(np.sqrt(np.mean(errors**2))*1000),
                 note=f'内壁为离散等距近似；PSHELL/VTK板厚属性严格为{THICKNESS*1000:g} mm。')
    return inner, solid_vertices, solid_faces, check

def write_obj(path, vertices, faces):
    with path.open('w', encoding='utf-8') as f:
        f.write('# units: m; indexed triangle mesh\n')
        for p in vertices:
            f.write('v %.9f %.9f %.9f\n' % tuple(p))
        for face in faces+1:
            f.write('f %d %d %d\n' % tuple(face))

def write_stl(path, vertices, faces, description=None):
    normals, _, _ = face_geometry(vertices, faces)
    dtype = np.dtype([('normal', '<f4', 3), ('points', '<f4', (3, 3)), ('attribute', '<u2')])
    data = np.zeros(len(faces), dtype=dtype)
    data['normal'], data['points'] = normals, vertices[faces]
    with path.open('wb') as f:
        title = description or f'Hollow ship shell; units m; nominal thickness {THICKNESS*1000:g} mm'
        f.write(title.encode('ascii')[:80].ljust(80, b'\0'))
        f.write(struct.pack('<I', len(faces)))
        f.write(data.tobytes())

def write_vtk(path, vertices, faces, thickness=None):
    with path.open('w', encoding='ascii') as f:
        f.write('# vtk DataFile Version 3.0\nShip mesh; units m\nASCII\nDATASET POLYDATA\n')
        f.write(f'POINTS {len(vertices)} double\n')
        np.savetxt(f, vertices, fmt='%.10f')
        f.write(f'POLYGONS {len(faces)} {len(faces)*4}\n')
        np.savetxt(f, np.column_stack([np.full(len(faces), 3), faces]), fmt='%d')
        if thickness is not None:
            f.write(f'CELL_DATA {len(faces)}\nSCALARS thickness_m double 1\nLOOKUP_TABLE default\n')
            np.savetxt(f, np.full(len(faces), thickness), fmt='%.6f')

def write_bdf(path, vertices, faces, density):
    with path.open('w', encoding='ascii') as f:
        f.write('$ SI units m, kg, s. Mesh/property bulk data only; no loads or constraints.\n')
        f.write(f'$ Open top; midsurface triangles; PSHELL thickness {THICKNESS} m.\nBEGIN BULK\n')
        f.write(f'MAT1,1,2.06E11,,0.3,{density:.6f}\nPSHELL,1,1,{THICKNESS:.9f},1,,1\n')
        for i, (x, y, z) in enumerate(vertices, 1):
            f.write(f"{'GRID*':8}{i:16d}{'':16}{x:16.9f}{y:16.9f}\n{'*':8}{z:16.9f}\n")
        for i, face in enumerate(faces+1, 1):
            f.write(f'CTRIA3,{i},1,{face[0]},{face[1]},{face[2]}\n')
        f.write('ENDDATA\n')
