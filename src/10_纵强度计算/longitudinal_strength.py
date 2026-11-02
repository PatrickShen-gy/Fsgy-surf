"""Weight/buoyancy distributions, shear force and bending moment."""
import math
import numpy as np
from scipy.optimize import brentq
from shapely.geometry import Polygon
from fsgy_surf.io.files import save_json, write_csv

def distributed_cell_masses(edges,mass,cg,span=None):
    centers=(edges[:-1]+edges[1:])/2;dx=np.diff(edges)
    result=np.zeros(len(centers))
    if mass==0:return result
    if span is None:
        if not centers[0]<=cg<=centers[-1]:raise ValueError('点载荷重心超出积分范围')
        j=int(np.searchsorted(centers,cg));j=min(max(j,1),len(centers)-1)
        f=(cg-centers[j-1])/(centers[j]-centers[j-1]);result[j-1]=mass*(1-f);result[j]=mass*f
        return result
    mask=(centers>=span[0])&(centers<=span[1]);xx=centers[mask];ww=dx[mask]
    if not len(xx) or not xx[0]<cg<xx[-1]:return distributed_cell_masses(edges,mass,cg,None)
    scale=max(span[1]-span[0],1e-6);xxs=(xx-cg)/scale
    def values(lam):
        a=lam*xxs;a-=a.max();p=ww*np.exp(a);return p/p.sum()
    fn=lambda lam:float(values(lam)@xx)-cg
    lam=brentq(fn,-1e5,1e5,xtol=1e-10)
    result[mask]=mass*values(lam)
    return result


def beam_result(edges,wm,wmx,wmz,bm,bmx,bmz,slope,nz=1.):
    q=np.r_[0.,np.cumsum(wm-bm)]
    mx=np.r_[0.,np.cumsum(wmx-bmx)];mz=np.r_[0.,np.cumsum(wmz-bmz)]
    # 沿倾斜重力方向的轴向力也对不同高度产生纵向弯矩，保证端部力矩可闭合。
    return nz*q,nz*(edges*q-mx-slope*mz)


def run(ctx):
    mesh = ctx.geometry()
    vertices = mesh['outer_vertices']
    xmin, xmax = float(vertices[:, 0].min()), float(vertices[:, 0].max())
    settings = ctx.config.get('longitudinal', {})
    cells = settings.get('cells', 450)
    if type(cells) is not int or cells < 20:
        raise ValueError('longitudinal.cells must be an integer >= 20')
    weight_cases = ctx.data[6]['cases']
    all_items = [item for case in weight_cases for item in case['lightweight_components']+case['items']
                 if item['mass_t'] > 0]
    boundaries = []
    for item in all_items:
        cg = item['LCG_m']
        if not xmin <= cg <= xmax:
            raise ValueError(f'{item["id"]}: LCG is outside the hull')
        if 'span_m' in item:
            a, b = item['span_m']
            if a < xmin-1e-7 or b > xmax+1e-7 or not a <= cg <= b:
                raise ValueError(f'{item["id"]}: span_m must contain LCG and lie inside the hull')
            boundaries.extend([a, b])
    edges = np.unique(np.r_[np.linspace(xmin, xmax, cells+1), mesh['ring_x'],
                            boundaries, [i['LCG_m'] for i in all_items]])
    # Avoid microscopic integration cells caused by round-off in user/mesh boundaries.
    edges = np.round(edges, 8)
    edges[0], edges[-1] = xmin, xmax
    edges = np.unique(edges)
    dx = np.diff(edges)
    centers = (edges[:-1]+edges[1:])/2
    sample = np.empty(len(edges)+len(centers))
    sample[::2], sample[1::2] = edges, centers
    slicer = ctx.slicer()
    sections = [slicer.section(float(x)) for x in sample]
    eqs = {c['case_id']: c['calculated'] for c in ctx.data[7]['cases']}
    kernel = ctx.hydro()
    results, rows, distribution_rows = [], [], []
    for case in weight_cases:
        cid = case['case_id']
        state = eqs[cid]
        wm = np.zeros(len(dx))
        wmz = np.zeros(len(dx))
        for item in case['lightweight_components']+case['items']:
            if item['mass_t'] == 0:
                continue
            masses = distributed_cell_masses(edges, item['mass_t'], item['LCG_m'], item.get('span_m'))
            wm += masses
            wmz += masses*item['KG_m']
            distribution_rows.append(dict(case_id=cid, id=item['id'], mass_t=item['mass_t'],
                LCG_m=item['LCG_m'], span_m=item.get('span_m'),
                distribution='nonnegative exponential' if item.get('span_m') else 'point load on adjacent cells'))
        slope = state['trim_m']/kernel.lbp
        heel = math.tan(math.radians(state['heel_deg']))
        area, moment_z = [], []
        for x, section in zip(sample, sections):
            level = state['T_m']+slope*(x-kernel.xref)
            ymin, zmin, ymax, zmax = section.bounds
            left, right = ymin-1., ymax+1.
            bottom = min(zmin-1., level+heel*left-1., level+heel*right-1.)
            wet = section.intersection(Polygon([(left, bottom), (right, bottom),
                (right, level+heel*right), (left, level+heel*left)]))
            value = float(wet.area)
            area.append(value)
            moment_z.append(value*wet.centroid.y if value else 0.)
        area, moment_z = np.array(area), np.array(moment_z)
        volume = dx*(area[::2][:-1]+4*area[1::2]+area[::2][1:])/6
        moment_x = dx*(edges[:-1]*area[::2][:-1]+4*centers*area[1::2]+edges[1:]*area[::2][1:])/6
        moment_z = dx*(moment_z[::2][:-1]+4*moment_z[1::2]+moment_z[::2][1:])/6
        factor = case['calculated']['total']['mass_t']/volume.sum()
        bm, bmx, bmz = volume*factor, moment_x*factor, moment_z*factor
        shear, bending = beam_result(edges, wm, wm*centers, wmz, bm, bmx, bmz,
                                     slope, state['normal_body'][2])
        curve = dict(x_m=edges.tolist(), SF_t=shear.tolist(), BM_tm=bending.tolist(),
                     cell_x_m=centers.tolist(), weight_t_m=(wm/dx).tolist(), buoyancy_t_m=(bm/dx).tolist())
        extrema = dict(SF_max_t=float(shear.max()), SF_min_t=float(shear.min()),
                       BM_hogging_max_tm=float(bending.max()), BM_sagging_min_tm=float(bending.min()))
        results.append(dict(case_id=cid, curve=curve, extrema=extrema,
                            model='Longitudinal beam; user spans or point loads; local CORR excluded'))
        rows.extend(dict(case_id=cid, x_m=float(x), SF_t=float(sf), BM_tm=float(bm))
                    for x, sf, bm in zip(edges, shear, bending))
    result = dict(cases=results)
    save_json(ctx.output(10)/'longitudinal.json', result)
    write_csv(ctx.output(10)/'longitudinal.csv', rows)
    write_csv(ctx.output(10)/'weight_distributions.csv', distribution_rows)
    np.savez_compressed(ctx.cache(10)/'longitudinal.npz', x_m=edges,
        case_ids=np.array([case['case_id'] for case in results]),
        SF_t=np.array([case['curve']['SF_t'] for case in results]),
        BM_tm=np.array([case['curve']['BM_tm'] for case in results]))
    return result
