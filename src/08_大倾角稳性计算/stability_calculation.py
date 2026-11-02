"""Constant-mass, free-trim large-angle stability."""
import math
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import minimize_scalar
from hydrostatics_calculation import HydrostaticCalculator
from fsgy_surf.io.files import save_json, write_csv

class RollHydrostatics:
    """将横倾船体转到计算坐标，平移z以避免04对T=0的系数奇点。

    坐标变换y'=cos(phi)y+sin(phi)z，z'=-sin(phi)y+cos(phi)z+shift。
    水面z'=a+(TR'/LBP)(x-xref)。pitch=atan(TR'/LBP)。
    """
    def __init__(self,base):self.base=base;self.angle=None

    def set_angle(self,angle):
        if self.angle==angle:return
        self.angle=float(angle)
        phi=math.radians(angle);self.c,self.s=math.cos(phi),math.sin(phi)
        v=self.base.vertices.copy()
        v[:,1]=self.c*self.base.vertices[:,1]+self.s*self.base.vertices[:,2]
        v[:,2]=-self.s*self.base.vertices[:,1]+self.c*self.base.vertices[:,2]
        self.shift=1.-float(v[:,2].min());v[:,2]+=self.shift
        self.kernel=HydrostaticCalculator(v,self.base.faces,self.base.physical,lbp=self.base.lbp,
            beam=self.base.beam,xref=self.base.xref,rho=self.base.rho,shell_thickness=self.base.shell_t)
        self.zmin=float(v[:,2].min());self.zmax=float(v[:,2].max())

    def evaluate(self,a,tr):return self.kernel.evaluate(float(a),float(tr))

    def solve_pose(self,load,angle,initial=None,max_iterations=18,mass_tolerance=.01,lever_tolerance=1e-6):
        self.set_angle(angle)
        xg=load['LCG_m'];zg=self.c*load['KG_m']-self.s*load['TCG_m']+self.shift
        if initial is None:
            # 只给定目标重量，任何横倾角均可独立括区间求起始水面。
            target=load['mass_t']/self.base.rho
            if not 0<target<self.base.full_volume:raise ValueError('目标重量不在封闭主船体可排水范围')
            ratio=target/self.base.full_volume
            a=self.zmin+(self.zmax-self.zmin)*ratio;tr=0.
        else:
            a,tr=map(float,initial)
        a=float(np.clip(a,self.zmin+.001,self.zmax-.001));tr=float(np.clip(tr,-35.,35.))
        count=0
        for iteration in range(max_iterations):
            r=self.evaluate(a,tr);count+=1
            b=tr/self.base.lbp;v=r['VOLM'];area=r['WLA']
            force=r['DISP']-load['mass_t']
            lever=(r['LCB']-xg)+b*(r['VCB']-zg)
            if abs(force)<mass_tolerance and abs(lever)<lever_tolerance:break
            xf=r['LCA'];zf=a+b*(xf-self.base.xref)
            variance_x=r['waterplane_IL_m4']/r['waterplane_area_actual_m2']/(1+b*b)
            t=(xf-r['LCB'])+b*(zf-r['VCB'])
            # 形状导数为型体积的解析导数；忽略薄外板WSA导数，以实际残差迭代收敛。
            J=np.array([[self.base.rho*area,self.base.rho*area*(xf-self.base.xref)/self.base.lbp],
                [area/v*t,((r['VCB']-zg)+area/v*((1+b*b)*variance_x+t*(xf-self.base.xref)))/self.base.lbp]])
            try:step=np.linalg.solve(J,np.array([force,lever]))
            except np.linalg.LinAlgError:raise ValueError('自由纵倾平衡雅可比奇异')
            factor=min(1.,2./max(abs(step[0]),1e-12),8./max(abs(step[1]),1e-12))
            previous=np.linalg.norm([force/max(load['mass_t'],1.),lever/self.base.lbp])
            accepted=False
            for _ in range(9):
                aa=a-factor*step[0];tt=tr-factor*step[1]
                if not self.zmin+.00001<aa<self.zmax-.00001 or abs(tt)>45.:
                    factor*=.5;continue
                candidate=self.evaluate(aa,tt);count+=1
                bb=tt/self.base.lbp
                ll=candidate['LCB']-xg+bb*(candidate['VCB']-zg)
                score=np.linalg.norm([(candidate['DISP']-load['mass_t'])/load['mass_t'],ll/self.base.lbp])
                if score<previous:
                    a,tr=aa,tt;accepted=True;break
                factor*=.5
            if not accepted:raise RuntimeError(f'横倾{angle}度的纵倾/质量平衡无法下降')
        else:raise RuntimeError(f'横倾{angle}度平衡未收敛')
        pitch=math.atan(tr/self.base.lbp)
        gz_signed=r['TCB_m']-(self.c*load['TCG_m']+self.s*load['KG_m'])
        bz_r=r['VCB']-self.shift
        return dict(heel_signed_deg=float(angle),plane_level_rotated_m=float(a-self.shift),rotated_trim_m=float(tr),
            pitch_deg=math.degrees(pitch),GZ_signed_fixed_m=float(gz_signed),
            LCB_m=r['LCB'],TCB_m=float(self.c*r['TCB_m']-self.s*bz_r),
            VCB_m=float(self.s*r['TCB_m']+self.c*bz_r),DISP_t=r['DISP'],VOLM_m3=r['VOLM'],
            WSA_m2=r['WSA'],mass_residual_t=float(force),pitch_lever_residual_m=float(lever),
            deck_immersed=bool(r['deck_limit_reached']),evaluations=count,
            numerical_converged=bool(abs(force)<.01 and abs(lever)<1e-6),
            waterline_closure_m=r['waterline_closure_m'])


def curve_point(pose,load,theta,side):
    correction=load['FSM_tm']/load['mass_t']
    fsmom=load['FSM_tm']*math.sin(math.radians(theta))
    dgz=correction*math.sin(math.radians(theta))
    fixed=side*pose['GZ_signed_fixed_m']
    return dict(angle_deg=float(theta),side='PS' if side==1 else 'SB',GZ_fixed_m=float(fixed),
        GZ_m=float(fixed-dgz),FSMOM_tm=float(fsmom),DGZ_m=float(dgz),
        full_vessel_geometry_complete=False,**pose)


def warm_start(hydro,previous,load,angle):
    hydro.set_angle(angle)
    old=math.radians(previous['heel_signed_deg'])
    height=previous['plane_level_rotated_m']+(hydro.c-math.cos(old))*load['KG_m']-(hydro.s-math.sin(old))*load['TCG_m']
    return [height+hydro.shift,previous['rotated_trim_m']]


def summarize(rows):
    angles = np.radians([row['angle_deg'] for row in rows])
    curve = PchipInterpolator(angles, [row['GZ_m'] for row in rows], extrapolate=False)
    integral = curve.antiderivative()
    # All extrema lie at an endpoint or a root of the interpolant derivative.
    candidates = [float(angles[0]), float(angles[-1])]
    candidates.extend(float(x) for x in curve.derivative().roots(extrapolate=False)
                      if angles[0] <= x <= angles[-1])
    best = max(candidates, key=lambda x: float(curve(x)))
    roots = sorted(float(x) for x in curve.roots(extrapolate=False)
                   if angles[0] < x < angles[-1])
    avs = next((x for x in roots if curve(max(angles[0], x-1e-6)) > 0
                and curve(min(angles[-1], x+1e-6)) < 0), None)
    result = dict(GZmax_m=float(curve(best)), GZmax_angle_deg=float(np.degrees(best)),
        AVS_sealed_hull_deg=None if avs is None else float(np.degrees(avs)),
        first_sampled_deck_immersion_deg=next((r['angle_deg'] for r in rows if r['deck_immersed']), None),
        GM_from_GZ_slope_m=float(curve.derivative()(0.)))
    for a, b in [(0, 30), (0, 40), (30, 40)]:
        result[f'area_{a}_{b}_mrad'] = (float(integral(math.radians(b))-integral(math.radians(a)))
                                       if rows[-1]['angle_deg'] >= b else None)
    return result


def compute_points(hydro, equilibria, angles, side):
    """Share one rotated hull kernel across all loading cases at each roll angle."""
    points = {case['case_id']: [] for case in equilibria}
    for theta in angles:
        signed = float(theta*side)
        hydro.set_angle(signed)
        for case in equilibria:
            load = case['load']
            history = points[case['case_id']]
            if history:
                initial = warm_start(hydro, history[-1], load, signed)
            else:
                phi = math.radians(signed)
                initial = [case['calculated']['T_m']*math.cos(phi)+hydro.shift,
                           case['calculated']['trim_m']*math.cos(phi)]
            try:
                pose = hydro.solve_pose(load, signed, initial)
            except (ValueError, RuntimeError):
                pose = hydro.solve_pose(load, signed, None, max_iterations=35)
            if not pose['numerical_converged']:
                raise RuntimeError(f'{case["case_id"]}: large-angle pose failed at {theta} degrees')
            history.append(curve_point(pose, load, float(theta), side))
    return points


def run(ctx):
    settings = ctx.config.get('stability', {})
    default_angles = np.unique(np.r_[np.arange(0., 90.01, 2.5), np.arange(95., 180.01, 5.)])
    angles = np.unique(np.asarray(settings.get('angles_deg', default_angles), float))
    if len(angles) < 3 or not np.isfinite(angles).all() or angles[0] != 0 or angles[-1] > 180:
        raise ValueError('stability.angles_deg needs at least 3 points, starting at 0, ending <= 180')
    sides = settings.get('sides', ['port'])
    if not sides or any(side not in ('port', 'starboard') for side in sides):
        raise ValueError('stability.sides accepts port, starboard')
    base = ctx.hydro()
    cases, flat = [], []
    for side_name in dict.fromkeys(sides):
        side = 1 if side_name == 'port' else -1
        hydro = RollHydrostatics(base)
        point_sets = compute_points(hydro, ctx.data[7]['cases'], angles, side)
        for case in ctx.data[7]['cases']:
            points = point_sets[case['case_id']]
            load = case['load']
            summary = summarize(points)
            # Initial GM is the derivative of GZ with respect to roll in radians.
            small_angle = {}
            for theta in (-.1, .1):
                seed = warm_start(hydro, points[0], load, side*theta)
                pose = hydro.solve_pose(load, side*theta, seed,
                                        mass_tolerance=1e-6, lever_tolerance=1e-10)
                small_angle[theta] = curve_point(pose, load, theta, side)['GZ_m']
            summary['GM_from_GZ_slope_m'] = (small_angle[.1]-small_angle[-.1])/math.radians(.2)
            curve = PchipInterpolator(np.radians(angles), [row['GZ_m'] for row in points])
            integral = curve.antiderivative()
            for point in points:
                point['area_mrad'] = float(integral(math.radians(point['angle_deg']))-integral(0.))
                for key in ('mass_residual_t', 'pitch_lever_residual_m', 'waterline_closure_m'):
                    point.pop(key, None)
            entry = dict(case_id=case['case_id'], curve_id=f'{case["case_id"]}_{side_name}',
                description=case['description'], load=load, side='PS' if side == 1 else 'SB',
                points=points, summary=summary)
            cases.append(entry)
            flat.extend(dict(case_id=case['case_id'], curve_id=entry['curve_id'], **point) for point in points)
    result = dict(cases=cases, model_scope='Closed main-hull envelope; deck immersion is marked')
    save_json(ctx.output(8)/'stability.json', result)
    write_csv(ctx.output(8)/'gz_curves.csv', flat)
    return result
