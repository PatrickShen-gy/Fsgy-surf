"""Compartment volumes, centroids and free surfaces."""
import numpy as np
from scipy.optimize import brentq, minimize_scalar
from hydrostatics_calculation import HydrostaticCalculator
from fsgy_surf.io.files import save_json, write_csv

class TankCalculator:
    """同一舱室零横倾、零纵倾的水平液面；充满舱的实际自由液面为零。

    只计算输入闭合容积几何，不自动扣结构、添加舱口或假设小舱。
    """
    def __init__(self, vertices, faces, full_properties=None):
        vertices, faces = np.asarray(vertices, float), np.asarray(faces, int)
        self.kernel = HydrostaticCalculator(vertices, faces, rho=1., shell_thickness=0.)
        self.vertices, self.faces = vertices, faces
        self.zmin, self.zmax = float(vertices[:,2].min()), float(vertices[:,2].max())
        if full_properties is not None:
            self.volume = float(full_properties['volume_m3'])
            self.center = np.asarray(full_properties['center_m'], float)
        else:
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
                    waterline_closure_m=float(closure))

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


def run(ctx):
    settings = ctx.config.get('capacity', {})
    fractions = np.asarray(settings.get('fill_fractions', np.linspace(0., 1., 51)), float)
    samples = settings.get('level_samples', 201)
    if type(samples) is not int or samples < 3:
        raise ValueError('capacity.level_samples must be an integer >= 3')
    if not len(fractions) or not np.isfinite(fractions).all() or np.any((fractions < 0) | (fractions > 1)):
        raise ValueError('capacity.fill_fractions must be in [0, 1]')
    fractions = np.unique(fractions)
    water_density = ctx.config['environment']['water_density_t_m3']
    capacities, fill_rows, level_rows = [], [], []
    ids = []
    for space in ctx.data[2]['compartments']:
        ident = space['id']
        ids.append(ident)
        tank = ctx.tank(ident)
        levels, maximum = tank.scan(samples)
        for row in levels:
            level_rows.append(dict(geometry_id=ident, FSM_water_tm=water_density*row['free_surface_IT_m4'],
                **{k: v for k, v in row.items() if k != 'waterline_closure_m'}))
        for fill in fractions:
            attributes = ctx.fill(ident, float(fill))
            fill_rows.append(dict(geometry_id=ident, FSM_water_tm=water_density*attributes['free_surface_IT_m4'],
                **{k: v for k, v in attributes.items() if k != 'waterline_closure_m'}))
        capacities.append(dict(space, volume_m3=tank.volume,
            CGX_m=float(tank.center[0]), CGY_m=float(tank.center[1]), CGZ_m=float(tank.center[2]),
            bottom_m=tank.zmin, top_m=tank.zmax,
            maximum_free_surface_IT_m4=maximum['free_surface_IT_m4'],
            maximum_FSM_water_tm=water_density*maximum['free_surface_IT_m4'],
            maximum_IT_level_m=maximum['level_BL_m'], maximum_IT_fill_fraction=maximum['fill_fraction']))
    out = ctx.output(5)
    write_csv(out/'capacities.csv', capacities)
    write_csv(out/'filling_curves.csv', fill_rows)
    write_csv(out/'level_curves.csv', level_rows)
    filling_file = ctx.cache(5)/'filling_samples.json'
    save_json(filling_file, fill_rows)
    np.savez_compressed(ctx.cache(5)/'capacities.npz', geometry_ids=np.array(ids),
        fill_fraction=fractions, volume_m3=np.array([r['volume_m3'] for r in fill_rows]).reshape(len(ids), -1),
        IT_m4=np.array([r['free_surface_IT_m4'] for r in fill_rows]).reshape(len(ids), -1))
    result = dict(compartments=capacities, filling_file=str(filling_file),
                  capacity_file=str(out/'capacities.csv'), fill_curve_file=str(out/'filling_curves.csv'))
    save_json(out/'capacities.json', result)
    return result
