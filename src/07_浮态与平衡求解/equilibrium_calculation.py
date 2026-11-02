"""Draft, trim and small-angle heel equilibrium."""
from functools import lru_cache
import math
import numpy as np
from scipy.optimize import brentq, least_squares
from hydrostatics_calculation import HydrostaticCalculator
from fsgy_surf.io.files import save_json, write_csv

class InclinedHydrostatics:
    """把横倾水面旋至04支持的纵倾水面；旋转只改变坐标，不改网格几何。

    原船体水面z=T+(trim/LBP)*(x-xref)+tan(phi)*y，phi>0为左舷下沉。
    浸没体积、面积不变；浮心旋回原坐标；正交水线轴为投影船长轴及其横轴。
    """
    def __init__(self,kernel):
        self.base=kernel

    @lru_cache(maxsize=3)
    def rotated_kernel(self,heel_deg):
        if heel_deg==0.:return self.base
        phi=np.radians(heel_deg);c,s=float(np.cos(phi)),float(np.sin(phi))
        vertices=self.base.vertices.copy()
        vertices[:,1]=c*self.base.vertices[:,1]+s*self.base.vertices[:,2]
        vertices[:,2]=-s*self.base.vertices[:,1]+c*self.base.vertices[:,2]
        return HydrostaticCalculator(vertices,self.base.faces,self.base.physical,lbp=self.base.lbp,
            beam=self.base.beam,xref=self.base.xref,rho=self.base.rho,shell_thickness=self.base.shell_t)

    @lru_cache(maxsize=256)
    def evaluate(self,T,trim,heel_deg):
        phi=np.radians(heel_deg);c,s=float(np.cos(phi)),float(np.sin(phi))
        kernel=self.rotated_kernel(float(heel_deg))
        r=kernel.evaluate(float(T*c),float(trim*c))
        by,bz=c*r['TCB_m']-s*r['VCB'],s*r['TCB_m']+c*r['VCB']
        slope=trim/self.base.lbp;transverse=math.tan(phi)
        normal=np.array([-slope,-transverse,1.]);normal/=np.linalg.norm(normal)
        # 04 WLA是对旋转后的z求导；原坐标T对应T*cos(phi)。
        return dict(T_m=float(T),trim_m=float(trim),heel_deg=float(heel_deg),
            TA_m=float(T-slope*self.base.xref),TF_m=float(T+slope*(self.base.lbp-self.base.xref)),
            pitch_deg=float(np.degrees(np.arctan(slope*c))),
            DISP_t=r['DISP'],VOLM_m3=r['VOLM'],WSA_m2=r['WSA'],
            WLA_horizontal_m2=float(r['WLA']*c),waterplane_area_m2=r['waterplane_area_actual_m2'],
            LCB_m=r['LCB'],TCB_m=float(by),VCB_m=float(bz),
            IT_m4=r['waterplane_IT_m4'],IL_m4=r['waterplane_IL_m4'],
            BMT_m=r['BMT_m'],BML_m=r['BML_m'],
            KM_m=float(bz+r['BMT_m']*normal[2]),KML_bodyZ_m=float(bz+r['BML_m']*normal[2]),
            normal_body=normal.tolist(),deck_limit_reached=r['deck_limit_reached'],
            waterline_closure_m=r['waterline_closure_m'])


class EquilibriumSolver:
    def __init__(self,kernel):
        self.hydro=InclinedHydrostatics(kernel)
        self.kernel=kernel

    def initial_guess(self,load):
        # 起始吃水由目标重量和网格正浮排水量反求。
        zmin=max(.02,float(self.kernel.vertices[:,2].min())+.02)
        zmax=float(self.kernel.deck_vertices[:,2].min() if len(self.kernel.deck_vertices) else self.kernel.zmax)-.01
        def force(t):return self.hydro.evaluate(float(t),0.,0.)['DISP_t']-load['mass_t']
        low,high=force(zmin),force(zmax)
        if low>0 or high<0:raise ValueError('目标重量超出当前封顶以下网格的正浮排水范围')
        t=brentq(force,zmin,zmax,xtol=1e-8)
        r=self.hydro.evaluate(float(t),0.,0.)
        gml=r['BML_m']+r['VCB_m']-load['KG_m']
        trim=(load['LCG_m']-r['LCB_m'])*self.kernel.lbp/gml if abs(gml)>.01 else 0.
        gmt=r['BMT_m']+r['VCB_m']-load['KG_m']-load.get('KG_correction_m',0.)
        heel=np.degrees(np.arctan((load.get('TCG_m',0.)-r['TCB_m'])/gmt)) if abs(gmt)>.01 else 0.
        return np.array([t,np.clip(trim,-8.,8.),np.clip(heel,-3.,3.)]),(zmin,zmax)

    def solve(self,load):
        for key in ['mass_t','LCG_m','TCG_m','KG_m','FSM_tm']:
            if key not in load or not math.isfinite(load[key]):raise ValueError(f'无效载荷字段{key}')
        if load['mass_t']<=0 or load['FSM_tm']<0:raise ValueError('目标重量须为正，FSM不能为负')
        load=dict(load)
        load['KG_correction_m']=load['FSM_tm']/load['mass_t']
        load['KG_effective_m']=load['KG_m']+load['KG_correction_m']
        guess,bounds=self.initial_guess(load)
        history=[]
        calls=0
        def residual(q):
            nonlocal calls
            calls+=1
            r=self.hydro.evaluate(*map(float,q))
            slope=q[1]/self.kernel.lbp;h=math.tan(math.radians(q[2]))
            # 同时对倾斜重力方向投影；不能仅令LCB=LCG。
            longitudinal=r['LCB_m']-load['LCG_m']+slope*(r['VCB_m']-load['KG_m'])
            # 06只有横向FSM：等效重心虚升只用于小横倾，不误加到纵倾方程。
            transverse=r['TCB_m']-load['TCG_m']+h*(r['VCB_m']-load['KG_effective_m'])
            result=np.array([(r['DISP_t']-load['mass_t'])/load['mass_t'],longitudinal/self.kernel.lbp,transverse/self.kernel.beam])
            history.append(dict(T_m=float(q[0]),trim_m=float(q[1]),heel_deg=float(q[2]),residual_norm=float(np.linalg.norm(result))))
            return result
        def jacobian(q):
            columns=[]
            for i,step in enumerate([2e-4,2e-3,2e-3]):
                delta=np.zeros(3);delta[i]=step
                columns.append((residual(q+delta)-residual(q-delta))/(2*step))
            return np.column_stack(columns)
        opt=least_squares(residual,guess,jac=jacobian,bounds=([bounds[0],-10.,-5.],[bounds[1],10.,5.]),
                          x_scale=[5.,2.,.2],xtol=1e-10,ftol=1e-10,gtol=1e-12,max_nfev=35)
        r=dict(self.hydro.evaluate(*map(float,opt.x)))
        final=residual(opt.x)
        bg=np.array([r['LCB_m']-load['LCG_m'],r['TCB_m']-load['TCG_m'],r['VCB_m']-load['KG_m']])
        # GM为当前倾斜姿态附近、重力方向上的近似初稳性量。
        gm0=r['BMT_m']+float(bg@np.array(r['normal_body']))
        r.update(GM0_m=gm0,GM_m=gm0-load['KG_correction_m'],
            KG_m=load['KG_m'],KG_correction_m=load['KG_correction_m'],KG_effective_m=load['KG_effective_m'],
            target_mass_t=load['mass_t'],target_LCG_m=load['LCG_m'],target_TCG_m=load['TCG_m'],
            mass_residual_t=r['DISP_t']-load['mass_t'],
            longitudinal_lever_residual_m=float(final[1]*self.kernel.lbp),
            transverse_lever_residual_m=float(final[2]*self.kernel.beam),
            normalized_residual_norm=float(np.linalg.norm(final)),
            numerical_converged=bool(abs(r['DISP_t']-load['mass_t'])<.01 and abs(final[1]*self.kernel.lbp)<1e-6 and abs(final[2]*self.kernel.beam)<1e-6),
            below_deck=not r['deck_limit_reached'],positive_GM=bool(gm0-load['KG_correction_m']>0),
            evaluations=calls,solver_status=int(opt.status),solver_message=str(opt.message),
            initial_guess=guess.tolist(),history=history)
        r['usable_small_angle_equilibrium']=r['numerical_converged'] and r['below_deck'] and r['positive_GM']
        return r


def run(ctx):
    solver = EquilibriumSolver(ctx.hydro())
    cases, rows = [], []
    for case in ctx.data[6]['cases']:
        load = case['calculated']['total']
        calculated = solver.solve(load)
        if not calculated['numerical_converged']:
            raise RuntimeError(f'{case["case_id"]}: equilibrium solver did not converge')
        calculated = {k: v for k, v in calculated.items() if k not in (
            'history', 'initial_guess', 'solver_status', 'solver_message',
            'normalized_residual_norm', 'mass_residual_t',
            'longitudinal_lever_residual_m', 'transverse_lever_residual_m', 'waterline_closure_m')}
        cases.append(dict(case_id=case['case_id'], description=case['description'],
                          load=load, calculated=calculated))
        rows.append(dict(case_id=case['case_id'], **{k: v for k, v in calculated.items() if k != 'normal_body'}))
    result = dict(cases=cases)
    save_json(ctx.output(7)/'equilibrium.json', result)
    write_csv(ctx.output(7)/'equilibrium.csv', rows)
    return result
