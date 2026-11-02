"""Clipped-mesh hydrostatics for inclined waterplanes."""
import numpy as np
from fsgy_surf.io.files import save_json, write_csv

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
    def from_npz(cls, path, **kwargs):
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
                   
                   shell_correction_t=float(self.rho*self.shell_t*wetted),
                   waterplane_area_actual_m2=float(area), waterplane_IT_m4=float(it), waterplane_IL_m4=float(il),
                   TCB_m=float(buoyancy[1]), TCF_m=float(yc),
                   BMT_m=float(it/volume), BML_m=float(il/volume),
                   waterline_closure_m=float(np.linalg.norm((end-start).sum(axis=0))),
                   draft_AP_m=float(draft-slope*self.xref), draft_FP_m=float(draft+slope*(self.lbp-self.xref)),
                   # 水线到达/越过封顶后仍可积分，但不具备上层建筑/甲板进水的物理意义。
                   deck_limit_reached=bool(np.min(self.deck_vertices[:, 2]-draft-slope*(self.deck_vertices[:, 0]-self.xref)) <= 1e-9)
                   if len(self.deck_vertices) else bool(np.max(draft+slope*(self.vertices[:, 0]-self.xref)) >= self.zmax-1e-9))
        return row


def run(ctx):
    from bonjean_calculation import draft_grid, submerged_properties
    settings = ctx.config.get('hydrostatics', {})
    drafts = draft_grid(ctx, settings)
    trims = np.asarray(settings.get('trims_m', [0.]), float)
    if not len(trims) or not np.isfinite(trims).all():
        raise ValueError('trims_m must be nonempty and finite')
    trims = np.unique(trims)
    kernel = ctx.hydro()
    midship = ctx.slicer().section(kernel.xref)
    rows = []
    for trim in trims:
        for draft in drafts:
            amid = submerged_properties(midship, float(draft))['area_m2']
            row = kernel.evaluate(float(draft), float(trim), midship_area=amid if amid > 0 else None)
            row.pop('waterline_closure_m', None)
            rows.append(row)
    path = ctx.output(4)/'hydrostatics.csv'
    write_csv(path, rows)
    arrays = ctx.cache(4)/'hydrostatics.npz'
    np.savez_compressed(arrays, draft_m=drafts, trim_m=trims, **{
        key: np.array([r[key] if r[key] is not None else np.nan for r in rows]).reshape(len(trims), len(drafts))
        for key in rows[0] if key not in ('draft_m', 'trim_m')})
    design_draft = ctx.data[2]['dimensions']['design_draft_m']
    design_area = submerged_properties(midship, design_draft)['area_m2']
    design_point = kernel.evaluate(design_draft,
                                  midship_area=design_area if design_area > 0 else None)
    design_point.pop('waterline_closure_m', None)
    result = dict(table_file=str(path), arrays_file=str(arrays), rows=len(rows),
                  water_density_t_m3=kernel.rho, hydro_shell_thickness_m=kernel.shell_t,
                  design_point=design_point)
    save_json(ctx.output(4)/'hydrostatics.json', result)
    return result
