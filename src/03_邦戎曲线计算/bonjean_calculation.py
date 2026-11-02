"""Mesh sections, Bonjean areas and moments."""
from collections import defaultdict
import numpy as np
from shapely.geometry import Polygon, box, LineString
from fsgy_surf.io.files import save_json, write_csv

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


def draft_grid(ctx, settings):
    dimensions = ctx.data[2]['dimensions']
    if 'drafts_m' in settings:
        drafts = np.asarray(settings['drafts_m'], float)
    else:
        interval = float(settings.get('draft_step_m', .5))
        if not np.isfinite(interval) or interval <= 0:
            raise ValueError('draft_step_m must be positive')
        drafts = np.r_[np.arange(interval, dimensions['depth_m']+.000001, interval),
                       dimensions['design_draft_m'], dimensions['scantling_draft_m']]
    if not len(drafts) or not np.isfinite(drafts).all() or np.any(drafts <= 0):
        raise ValueError('drafts_m must be nonempty, positive and finite')
    return np.unique(drafts)


def run(ctx):
    settings = ctx.config.get('bonjean', {})
    mesh = ctx.geometry()
    xs = np.asarray(settings.get('stations_m', mesh['source_station_x']), float)
    if not len(xs) or not np.isfinite(xs).all():
        raise ValueError('stations_m must be nonempty and finite')
    xs = np.unique(xs)
    drafts = draft_grid(ctx, settings)
    rows = []
    slicer = ctx.slicer()
    for x in xs:
        section = slicer.section(float(x))
        for draft in drafts:
            rows.append(dict(x_m=float(x), draft_m=float(draft),
                             **submerged_properties(section, float(draft))))
    path = ctx.output(3)/'bonjean.csv'
    write_csv(path, rows)
    arrays = ctx.cache(3)/'bonjean.npz'
    np.savez_compressed(arrays, x_m=xs, draft_m=drafts,
        area_m2=np.array([r['area_m2'] for r in rows]).reshape(len(xs), len(drafts)),
        moment_bl_m3=np.array([r['moment_bl_m3'] for r in rows]).reshape(len(xs), len(drafts)))
    return dict(table_file=str(path), arrays_file=str(arrays),
                stations=len(xs), drafts=len(drafts))
