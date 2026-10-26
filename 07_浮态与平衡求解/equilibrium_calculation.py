"""07：由06重量重心与02船体网格求解吃水/纵倾/小横倾，不用参考浮态作初值。

python equilibrium_calculation.py [--preview]
python equilibrium_calculation.py --case-file ../06_重量与重心系统/example_custom_case.json
"""
from pathlib import Path
from functools import lru_cache
import argparse
import json
import math
import re
import sys
import time
import webbrowser

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parent
sys.path.append(str(PROJECT.parent/'.cad_deps'))
sys.path.insert(0,str(PROJECT/'06_重量与重心系统'))
from weight_cg_calculation import load_results as load_weights, calculate_case, load_geometry_item
from hydrostatics_calculation import HydrostaticCalculator, MESH, np, digest, save_json, write_csv
from scipy.optimize import brentq, least_squares
import pymupdf

INPUT=PROJECT/'06_重量与重心系统/output/equilibrium_inputs.json'
NAMES=dict(T_m='参考点型吃水',trim_m='纵倾 TF−TA',TA_m='艉垂线型吃水',TF_m='艏垂线型吃水',
           heel_deg='横倾（左舷下沉为正）',KM_m='横稳心船体z坐标',GM0_m='未作自由液面修正GM',GM_m='修正后初稳性GM')
UNITS={k:'deg' if k=='heel_deg' else 'm' for k in NAMES}


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
        # 只用目标重量与网格正浮体积反求，不读取PDF参考T/trim/heel。
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


def extract_reference(pdf,weights,output):
    target=output/'reference';target.mkdir(parents=True,exist_ok=True)
    path=target/'pdf_equilibrium.json';fingerprint=digest(pdf)
    if path.exists():
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('parser_version')==1 and data.get('source_sha256')==fingerprint:return data
    rows=[]
    with pymupdf.open(pdf) as doc:
        for c in weights['cases']:
            pn=c['reference']['summary_page'];text=doc[pn-1].get_text()
            row=dict(c['reference']['floating_reference'])
            for key,pattern in [('TA_m',r'\bTA\s+([-+\d.]+)'),('TF_m',r'\bTF\s+([-+\d.]+)'),
                                ('heel_deg',r'Heel, PS=\+\s+([-+\d.]+)')]:
                m=re.search(pattern,text)
                if m is None:raise ValueError(f'PDF{pn}页缺少{key}')
                row[key]=float(m[1])
            precise=re.search(r'GM0\.15\s+GM > 0\.15 m\s+0\.150\s+([-+\d.]+)',text)
            row['GM_criteria_m']=float(precise[1]) if precise else None
            row.update(case_id=c['case_id'],pdf_page=pn,source_text=text)
            if abs(row['TF_m']-row['TA_m']-row['trim_m'])>.0021:raise ValueError('参考艏艉吃水与纵倾不一致')
            rows.append(row)
    data=dict(parser_version=1,source_pdf=str(pdf.resolve()),source_sha256=fingerprint,rows=rows,
        reference_basis='Detailed case summaries, not page89 overview; LD09C inconsistency retained in06')
    save_json(path,data)
    write_csv(target/'pdf_equilibrium.csv',[{k:v for k,v in r.items() if k!='source_text'} for r in rows])
    return data


def compare(results,references):
    refs={r['case_id']:r for r in references}
    rows=[]
    for item in results:
        r=item['calculated'];ref=refs[item['case_id']]
        for key in NAMES:
            value=ref['GM_criteria_m'] if key=='GM_m' and ref['GM_criteria_m'] is not None else ref[key]
            diff=r[key]-value
            rows.append(dict(case_id=item['case_id'],metric=key,calculated=r[key],reference=value,difference=diff,
                error_percent=100*diff/value if abs(value)>1e-12 else None,unit=UNITS[key],pdf_page=ref['pdf_page']))
    return rows


def load_results(output=None,verify_sources=False):
    output=Path(output) if output else HERE/'output'
    data=json.loads((output/'equilibrium_results.json').read_text(encoding='utf-8'))
    if verify_sources:
        if digest(INPUT)!=data['source_weights_sha256'] or digest(MESH)!=data['source_mesh_sha256']:
            raise ValueError('02/06输入变化，请重算07')
    return data


def make_plots(output,cases):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,3,figsize=(16,8.5),layout='constrained')
    x=np.arange(len(cases))
    for ax,key in zip(axes.flat,['T_m','trim_m','TA_m','TF_m','heel_deg','GM_m']):
        ax.plot(x,[c['calculated'][key] for c in cases],'o-',ms=3,lw=1,label='Mesh equilibrium')
        ax.plot(x,[c['reference'].get('GM_criteria_m') if key=='GM_m' and c['reference'].get('GM_criteria_m') is not None else c['reference'][key] for c in cases],'x',ms=4,label='PDF detailed')
        ax.set(ylabel=f'{key} ({UNITS[key]})',xticks=x[::3],xticklabels=[c['case_id'] for c in cases[::3]])
        ax.tick_params(axis='x',rotation=65,labelsize=8)
        ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(output/'equilibrium_comparison.png',dpi=150)
    plt.close(fig)


def run_custom(path,kernel,output):
    spec=json.loads(Path(path).read_text(encoding='utf-8'))
    if 'items' in spec:
        items=[]
        for raw in spec['items']:
            row=load_geometry_item(raw) if 'geometry_id' in raw else dict(raw)
            row.setdefault('FSM_tm',0.);row.setdefault('category','Custom Load')
            items.append(row)
        light=dict(spec['lightweight']);light.setdefault('FSM_tm',0.)
        load=calculate_case(items,light)['total']
    else:load=spec
    result=EquilibriumSolver(kernel).solve(load)
    cid=spec.get('case_id','CUSTOM')
    target=output/'custom_cases'/re.sub(r'[^A-Za-z0-9_-]','_',cid)
    target.mkdir(parents=True,exist_ok=True)
    save_json(target/'equilibrium.json',dict(case_id=cid,load=load,calculated=result,
        rho_t_m3=kernel.rho,shell_thickness_m=kernel.shell_t))
    print(json.dumps({k:v for k,v in result.items() if k!='history'},ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--case-file',type=Path)
    parser.add_argument('--shell-thickness-mm',type=float,default=18.,help='排水量一阶外板修正，默认遵循PDF18mm；不改变02钢材')
    parser.add_argument('--rho',type=float,default=1.025,help='水密度t/m3')
    args=parser.parse_args()
    if not 0<=args.shell_thickness_mm<=30 or not .9<=args.rho<=1.1:raise ValueError('外板厚度/水密度超出允许范围')
    output=HERE/'output';output.mkdir(exist_ok=True)
    kernel=HydrostaticCalculator.from_npz(rho=args.rho,shell_thickness=args.shell_thickness_mm/1000)
    if args.case_file:
        run_custom(args.case_file,kernel,output);return
    started=time.perf_counter()
    weights=load_weights(verify_sources=True)
    inputs=json.loads(INPUT.read_text(encoding='utf-8'))
    pdfs=list(PROJECT.glob('*.pdf'))
    if len(pdfs)!=1 or digest(pdfs[0])!=inputs['source_pdf_sha256']:raise ValueError('PDF与06输入不一致')
    reference=extract_reference(pdfs[0],weights,output)
    refs={r['case_id']:r for r in reference['rows']}
    solver=EquilibriumSolver(kernel)
    cases,flat,histories,pose_diagnostics=[],[],[],[]
    for load in inputs['cases']:
        cid=load['case_id'];r=solver.solve(load)
        ref={k:v for k,v in refs[cid].items() if k!='source_text'}
        cases.append(dict(case_id=cid,description=load['description'],load=load,calculated=r,reference=ref))
        flat.append(dict(case_id=cid,description=load['description'],**{k:v for k,v in r.items() if k not in ('history','normal_body','initial_guess')}))
        histories.extend(dict(case_id=cid,evaluation_index=i,**row) for i,row in enumerate(r['history']))
        pose=solver.hydro.evaluate(ref['T_m'],ref['trim_m'],ref['heel_deg'])
        s=ref['trim_m']/kernel.lbp;h=math.tan(math.radians(ref['heel_deg']))
        pose_diagnostics.append(dict(case_id=cid,reference_T_m=ref['T_m'],reference_trim_m=ref['trim_m'],reference_heel_deg=ref['heel_deg'],
            mesh_DISP_at_reference_t=pose['DISP_t'],target_mass_t=load['mass_t'],mass_difference_t=pose['DISP_t']-load['mass_t'],
            longitudinal_lever_m=pose['LCB_m']-load['LCG_m']+s*(pose['VCB_m']-load['KG_m']),
            transverse_lever_m=pose['TCB_m']-load['TCG_m']+h*(pose['VCB_m']-load['KG_effective_m']),pdf_page=ref['pdf_page']))
        print(f"{cid}: T={r['T_m']:.5f}, TR={r['trim_m']:.5f}, heel={r['heel_deg']:.5f}, GM={r['GM_m']:.5f}; converged={r['numerical_converged']}",flush=True)
    comparisons=compare(cases,reference['rows'])
    metrics={key:dict(unit=UNITS[key],mean_absolute_difference=float(np.mean([abs(r['difference']) for r in comparisons if r['metric']==key])),
        max_absolute_difference=max(abs(r['difference']) for r in comparisons if r['metric']==key),
        worst=max((r for r in comparisons if r['metric']==key),key=lambda r:abs(r['difference']))) for key in NAMES}
    sensitivity=[]
    for thickness in [0.,.012,.018]:
        diagnostic_solver=solver if abs(kernel.shell_t-thickness)<1e-12 else EquilibriumSolver(
            HydrostaticCalculator(kernel.vertices,kernel.faces,kernel.physical,lbp=kernel.lbp,beam=kernel.beam,xref=kernel.xref,rho=kernel.rho,shell_thickness=thickness))
        for cid in ['LD00','LD01A','LD04A']:
            case=next(c for c in cases if c['case_id']==cid)
            r=case['calculated'] if diagnostic_solver is solver else diagnostic_solver.solve(case['load'])
            sensitivity.append(dict(case_id=cid,shell_thickness_mm=thickness*1000,**{k:r[k] for k in ['T_m','trim_m','heel_deg','TA_m','TF_m','GM_m']}))
    # 沿X的上下包络只用于HTML纵剖示意，不参与求解。
    xx,inverse=np.unique(kernel.vertices[:,0],return_inverse=True)
    lower=np.full(len(xx),np.inf);upper=np.full(len(xx),-np.inf)
    np.minimum.at(lower,inverse,kernel.vertices[:,2]);np.maximum.at(upper,inverse,kernel.vertices[:,2])
    profile=dict(x_m=xx.tolist(),lower_z_m=lower.tolist(),upper_z_m=upper.tolist())
    notes=[
        '输入重量、LCG/TCG/KG及FSM来自06；参考T/纵倾/横倾不参与初值、残差或参数拟合。正浮体积先反求吃水，再用网格初稳性估计初值。',
        '水面：z=T+(trim/172)*(x-86)+tan(phi)*y。trim=TF-TA，phi正值为左舷下沉；TA/TF为中线处垂线型吃水。',
        '求解rho*(VOLM+t*WSA)=W及两个水面切向力臂为零，纵向方程包含trim/LBP*(VCB-KG)，不简单令LCB=LCG。',
        '通过旋转坐标复用04三角面裁切积分，再把浮心旋回原船体坐标；旋转不改变体积/面积。',
        '06只有横向FSM；小横倾方程用KG+FSM/W，纵倾方程用原KG。未把横向FSM虚升错误地当成纵向自由液面修正。',
        '横倾限制在±5度。液体随姿态变化的真实舱内重心未重算，采用固定工况重心和线性自由液面等效项；大倾角稳性属于08。',
        '默认rho=1.025t/m3、外板18mm一阶排水体积修正，与04/PDF一致；02钢材仍为12mm。另列0/12/18mm敏感性，不改变母型几何。',
        '一阶外板修正仅用于排水量；浮心和惯性矩保留型线网格口径，未做完整外偏置积分。这是当前平衡计算的几何近似，不是精确18mm外表面模型。',
        'GM0=IT/V+dot(B-G,n)，GM=GM0-FSM/W；KM导出横稳心的船体z坐标。倾斜时GM不必严格等于KM_bodyZ-KG，差异随倾角消失。',
        '参考采用PDF详细工况表，修正GM优先用同页GM0.15判据栏的三位小数值。LD09C继续沿用详细表，保留06发现的总览差异。',
        '收敛要求质量残差<0.01t、纵横两个切向力臂残差各<1e-6m。数值收敛与PDF几何复现是分别报告的量，不通过调整输入消除模型差异。',
        '触及虚拟封顶或GM非正的结果不标为可用小倾角平衡；未计风、缆绳、海底接触和船体挠曲。',
    ]
    report=dict(stage='07_浮态与平衡求解',source_weights_sha256=digest(INPUT),source_mesh_sha256=digest(MESH),
        source_pdf_sha256=reference['source_sha256'],source_pdf=reference['source_pdf'],
        source_hydrostatics_code_sha256=digest(PROJECT/'04_静水力计算/hydrostatics_calculation.py'),
        case_count=len(cases),comparison_count=len(comparisons),rho_t_m3=kernel.rho,shell_thickness_m=kernel.shell_t,
        all_numerical_converged=all(c['calculated']['numerical_converged'] for c in cases),
        usable_small_angle_cases=sum(c['calculated']['usable_small_angle_equilibrium'] for c in cases),
        deck_limit_cases=[c['case_id'] for c in cases if not c['calculated']['below_deck']],
        nonpositive_GM_cases=[c['case_id'] for c in cases if not c['calculated']['positive_GM']],
        max_mass_residual_t=max(abs(c['calculated']['mass_residual_t']) for c in cases),
        max_longitudinal_lever_residual_m=max(abs(c['calculated']['longitudinal_lever_residual_m']) for c in cases),
        max_transverse_lever_residual_m=max(abs(c['calculated']['transverse_lever_residual_m']) for c in cases),
        metric_summary=metrics,notes=notes,elapsed_seconds=time.perf_counter()-started)
    data=dict(source_weights_sha256=report['source_weights_sha256'],source_mesh_sha256=report['source_mesh_sha256'],
              source_pdf_sha256=reference['source_sha256'],conditions=dict(rho_t_m3=kernel.rho,shell_thickness_m=kernel.shell_t),cases=cases)
    save_json(output/'equilibrium_results.json',data)
    save_json(output/'verification_report.json',report)
    write_csv(output/'equilibrium_summary.csv',flat)
    write_csv(output/'verification.csv',comparisons)
    write_csv(output/'solver_history.csv',histories)
    write_csv(output/'reference_pose_diagnostic.csv',pose_diagnostics)
    write_csv(output/'shell_thickness_sensitivity.csv',sensitivity)
    np.savez_compressed(output/'equilibrium_data.npz',case_ids=np.array([c['case_id'] for c in cases]),
        **{k:np.array([c['calculated'][k] for c in cases]) for k in list(NAMES)+['LCB_m','TCB_m','VCB_m','DISP_t','VOLM_m3','KG_correction_m','usable_small_angle_equilibrium']})
    save_json(output/'stability_inputs.json',dict(purpose='08及后续稳性程序输入；GM为当前小倾角近似',
        conditions=data['conditions'],source_mesh_sha256=report['source_mesh_sha256'],source_weights_sha256=report['source_weights_sha256'],
        cases=[dict(case_id=c['case_id'],load=c['load'],equilibrium={k:v for k,v in c['calculated'].items() if k not in ('history','initial_guess')}) for c in cases]))
    template=(HERE/'equilibrium_viewer.html').read_text(encoding='utf-8')
    (output/'equilibrium_verification.html').write_text(template.replace('__DATA__',json.dumps(dict(report=report,cases=cases,profile=profile,names=NAMES,units=UNITS),ensure_ascii=False,allow_nan=False)),encoding='utf-8')
    make_plots(output,cases)
    print(json.dumps(dict(cases=len(cases),converged=report['all_numerical_converged'],usable=report['usable_small_angle_cases'],
        max_mass_residual_t=report['max_mass_residual_t'],max_differences={k:v['max_absolute_difference'] for k,v in metrics.items()}),ensure_ascii=False,indent=2))
    if not report['all_numerical_converged']:raise RuntimeError('部分工况平衡未收敛，见报告')
    if args.preview:webbrowser.open((output/'equilibrium_verification.html').as_uri())


if __name__=='__main__':main()
