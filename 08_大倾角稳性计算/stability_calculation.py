"""08 大倾角稳性：恒重量、自由纵倾、指定横倾的封闭主船体GZ。

python stability_calculation.py [--preview]
使用06载荷及07初值；不使用PDF GZ/KN作为网格计算输入。
甲板以上浮力几何缺失，封顶浸水后仍输出封闭主船体诊断值，明确标记。
"""
from pathlib import Path
from collections import defaultdict
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
sys.path.insert(0,str(PROJECT/'07_浮态与平衡求解'))
from equilibrium_calculation import load_results as load_equilibria, HydrostaticCalculator, MESH, np, digest, save_json, write_csv
from tank_capacity_calculation import pdf_lines
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq, minimize_scalar
import pymupdf

ANGLES=np.unique(np.r_[np.arange(0.,90.01,2.5),12.,np.arange(95.,180.01,5.)])
REF_ANGLES=[0.,10.,12.,20.,30.,40.,50.,60.,70.]
KN_ANGLES=[5.,10.,20.,30.,40.,50.,60.,70.,75.]


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


def extract_reference(pdf,equilibria,output):
    target=output/'reference';target.mkdir(parents=True,exist_ok=True)
    path=target/'pdf_stability.json';fingerprint=digest(pdf)
    if path.exists():
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved.get('parser_version')==1 and saved.get('source_sha256')==fingerprint:return saved
    cases=[];kn=[];identities=[]
    with pymupdf.open(pdf) as d:
        cross_note=d[67].get_text()
        for case in equilibria['cases']:
            pn=case['reference']['pdf_page']+3
            text=d[pn-1].get_text()
            head=text.split('HEEL',1)[0]
            sides=set(re.findall(r'\b(?:PS|SB)\b',head))
            if len(sides)!=1:raise ValueError(f'PDF{pn}页横倾方向不明确')
            side=sides.pop();rows=[]
            for y,words in pdf_lines(d[pn-1]):
                line=' '.join(w[4] for w in words)
                vals=re.findall(r'[-+]?\d+(?:\.\d+)?',line)
                if len(vals)!=6:continue
                values=list(map(float,vals))
                if values[0] not in REF_ANGLES:continue
                rows.append(dict(zip(['angle_deg','MS_m','GZ_m','area_mrad','FSMOM_tm','DGZ_m'],values),
                                 pdf_page=pn,source_row=line))
            if [r['angle_deg'] for r in rows]!=REF_ANGLES:raise ValueError(f'PDF{pn}页GZ表不完整')
            gm=case['reference']['GM_criteria_m'] or case['reference']['GM_m']
            for row in rows:
                ms_pred=row['GZ_m']-gm*math.sin(math.radians(row['angle_deg']))
                dgz_pred=row['FSMOM_tm']/case['load']['mass_t']
                identities.append(dict(case_id=case['case_id'],angle_deg=row['angle_deg'],pdf_page=pn,
                    MS_identity_difference_m=ms_pred-row['MS_m'],MS_within_printing=abs(ms_pred-row['MS_m'])<=.0061,
                    DGZ_identity_difference_m=dgz_pred-row['DGZ_m'],DGZ_within_printing=abs(dgz_pred-row['DGZ_m'])<=.00051))
            cases.append(dict(case_id=case['case_id'],side=side,side_sign=1 if side=='PS' else -1,pdf_page=pn,
                               rows=rows,source_text=text))
        for pn in range(69,73):
            for line in d[pn-1].get_text().splitlines():
                vals=re.findall(r'[-+]?\d+\.\d+',line)
                if len(vals)!=10:continue
                numbers=list(map(float,vals));draft=numbers[0]
                if not 2<=draft<=14.6:continue
                kn.append(dict(draft_m=draft,**{str(h):v for h,v in zip(KN_ANGLES,numbers[1:])},pdf_page=pn,source_row=line))
    drafts=sorted(set(np.round(np.arange(2.,12.0001,.05),2).tolist()+[9.83,10.27]))
    if [r['draft_m'] for r in kn]!=drafts:raise ValueError(f'KN参考表未覆盖完整203档吃水，实际{len(kn)}')
    data=dict(parser_version=1,source_pdf=str(pdf.resolve()),source_sha256=fingerprint,cases=cases,
        cross_curve_note=cross_note,kn_rows=kn,kn_angles_deg=KN_ANGLES,case_row_count=sum(len(c['rows']) for c in cases),
        reference_identity_checks=identities,MS_interpretation='数值核对支持MS=GZ-GM*sin(HEEL)，为推断关系，原列与原值独立保留',
        values_not_modified=True)
    save_json(path,data)
    write_csv(target/'pdf_GZ.csv',[dict(case_id=c['case_id'],side=c['side'],**r) for c in cases for r in c['rows']])
    write_csv(target/'pdf_KN.csv',kn)
    write_csv(target/'reference_identity_checks.csv',identities)
    return data


def summarize_curve(rows):
    x=np.array([r['angle_deg'] for r in rows]);y=np.array([r['GZ_m'] for r in rows])
    curve=PchipInterpolator(np.radians(x),y)
    area=curve.antiderivative()
    grid=np.linspace(0.,math.pi,len(x)*20)
    max_index=int(np.argmax(curve(grid)))
    left=max(0.,grid[max_index]-math.radians(5));right=min(math.pi,grid[max_index]+math.radians(5))
    opt=minimize_scalar(lambda a:-float(curve(a)),bounds=(left,right),method='bounded')
    roots=sorted(float(v) for v in curve.roots(extrapolate=False) if -1e-9<=v<=math.pi+1e-9)
    stable_start=None;avs=None
    for a,b in zip([0.]+roots,roots+[math.pi]):
        if b-a<1e-8:continue
        if float(curve((a+b)/2))>0 and stable_start is None:stable_start=a
        elif stable_start is not None and float(curve((a+b)/2))<0:
            avs=a;break
    deck=next((r['angle_deg'] for r in rows if r['deck_immersed']),None)
    return dict(area_0_30_mrad=float(area(math.radians(30))-area(0.)),
        area_0_40_mrad=float(area(math.radians(40))-area(0.)),
        area_30_40_mrad=float(area(math.radians(40))-area(math.radians(30))),
        GZmax_m=float(-opt.fun),GZmax_angle_deg=float(math.degrees(opt.x)),
        positive_range_start_deg=None if stable_start is None else math.degrees(stable_start),
        AVS_sealed_hull_deg=None if avs is None else math.degrees(avs),
        first_sampled_deck_immersion_deg=deck,angle_step_deg_low=2.5,angle_step_deg_high=5.,
        post_deck_values_are_sealed_hull_diagnostics=True,area_method='PCHIP analytic antiderivative; radians')


def load_results(output=None,verify_sources=False):
    output=Path(output) if output else HERE/'output'
    data=json.loads((output/'stability_results.json').read_text(encoding='utf-8'))
    if verify_sources:
        source=PROJECT/'07_浮态与平衡求解/output/stability_inputs.json'
        if digest(MESH)!=data['source_mesh_sha256'] or digest(source)!=data['source_equilibrium_sha256']:
            raise ValueError('02/07输入已变化，请重算08')
    return data


def warm_start(hydro,previous,load,angle):
    hydro.set_angle(angle)
    old=math.radians(previous['heel_signed_deg'])
    height=previous['plane_level_rotated_m']+(hydro.c-math.cos(old))*load['KG_m']-(hydro.s-math.sin(old))*load['TCG_m']
    return [height+hydro.shift,previous['rotated_trim_m']]


def compute_curves(base,equilibria,reference,output):
    refs={r['case_id']:r for r in reference['cases']}
    checkpoint=output/'checkpoints/curves.json';checkpoint.parent.mkdir(exist_ok=True)
    signature=dict(mesh=digest(MESH),input=digest(PROJECT/'07_浮态与平衡求解/output/stability_inputs.json'),
                   code=digest(Path(__file__)),angles=ANGLES.tolist(),rho=base.rho,thickness=base.shell_t)
    saved={}
    if checkpoint.exists():
        old=json.loads(checkpoint.read_text(encoding='utf-8'))
        if old['signature']==signature:saved=old['points']
    points={c['case_id']:[] for c in equilibria['cases']}
    hydros={1:RollHydrostatics(base),-1:RollHydrostatics(base)}
    previous={}
    for theta in ANGLES:
        key=str(float(theta))
        if key in saved:
            for cid,row in saved[key].items():points[cid].append(row);previous[cid]=row
            continue
        batch={}
        for side in [1,-1]:
            hydro=hydros[side]
            angle=float(side*theta);hydro.set_angle(angle)
            for c in equilibria['cases']:
                cid=c['case_id'];load=c['load']
                if refs[cid]['side_sign']!=side:continue
                if cid in previous:initial=warm_start(hydro,previous[cid],load,angle)
                else:initial=[c['calculated']['T_m']+hydro.shift,c['calculated']['trim_m']]
                try:pose=hydro.solve_pose(load,angle,initial)
                except (ValueError,RuntimeError):pose=hydro.solve_pose(load,angle,None,max_iterations=28)
                point=curve_point(pose,load,float(theta),side)
                points[cid].append(point);previous[cid]=point;batch[cid]=point
        saved[key]=batch
        save_json(checkpoint,dict(signature=signature,points=saved))
        print(f'Heel {theta:g} deg: {len(batch)} cases completed',flush=True)
    # 独立小角度斜率检验，使用更严格的质量/纵倾容差。
    gm={}
    for side in [1,-1]:
        hydro=hydros[side]
        slopes=defaultdict(dict)
        for theta in [-.1,.1]:
            for c in equilibria['cases']:
                cid=c['case_id']
                if refs[cid]['side_sign']!=side:continue
                seed=warm_start(hydro,points[cid][0],c['load'],side*theta)
                pose=hydro.solve_pose(c['load'],side*theta,seed,mass_tolerance=1e-6,lever_tolerance=1e-10)
                slopes[cid][theta]=curve_point(pose,c['load'],theta,side)['GZ_m']
        for cid,s in slopes.items():gm[cid]=(s[.1]-s[-.1])/math.radians(.2)
    cases=[]
    for c in equilibria['cases']:
        cid=c['case_id'];rows=points[cid]
        curve=PchipInterpolator(np.radians([r['angle_deg'] for r in rows]),[r['GZ_m'] for r in rows])
        integral=curve.antiderivative()
        for r in rows:
            r['area_mrad']=float(integral(math.radians(r['angle_deg']))-integral(0.))
            r['MS_m']=r['GZ_m']-gm[cid]*math.sin(math.radians(r['angle_deg']))
        summary=summarize_curve(rows)
        summary.update(GM_from_GZ_slope_m=gm[cid],GM_stage07_m=c['calculated']['GM_m'],
                       GM_slope_difference_m=gm[cid]-c['calculated']['GM_m'])
        cases.append(dict(case_id=cid,description=c['description'],load=c['load'],side=refs[cid]['side'],
                          points=rows,summary=summary,reference=refs[cid]))
    return cases


def kn_benchmark(base,reference):
    rows=[]
    lookup={r['draft_m']:r for r in reference['kn_rows']}
    targets={t:base.evaluate(t,0.)['DISP'] for t in [2.,4.,6.,8.,9.83,10.27,12.]}
    hydro=RollHydrostatics(base)
    for angle in KN_ANGLES:
        hydro.set_angle(angle)
        for draft,mass in targets.items():
            fn=lambda a:hydro.evaluate(a,0.)['DISP']-mass
            a=brentq(fn,hydro.zmin+.0001,hydro.zmax-.0001,xtol=1e-8)
            r=hydro.evaluate(a,0.);ref=lookup[draft][str(angle)]
            rows.append(dict(draft_m=draft,angle_deg=angle,KN_mesh_m=r['TCB_m'],KN_reference_m=ref,
                difference_m=r['TCB_m']-ref,deck_immersed=r['deck_limit_reached'],pdf_page=lookup[draft]['pdf_page']))
    return rows


def compare(cases):
    rows=[]
    fields=dict(GZ_m='m',area_mrad='m·rad',MS_m='m',FSMOM_tm='t·m',DGZ_m='m')
    for case in cases:
        points={r['angle_deg']:r for r in case['points']}
        for ref in case['reference']['rows']:
            r=points[ref['angle_deg']]
            for key,unit in fields.items():
                rows.append(dict(case_id=case['case_id'],angle_deg=ref['angle_deg'],side=case['side'],metric=key,
                    calculated=r[key],reference=ref[key],difference=r[key]-ref[key],unit=unit,pdf_page=ref['pdf_page'],
                    deck_immersed=r['deck_immersed'],comparison_geometry='sealed main hull; upper structures missing'))
    return rows


def make_plots(output,cases):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(13,8),layout='constrained')
    for ax,cid in zip(axes.flat,['LD00','LD01A','LD04A','LD05A']):
        case=next(c for c in cases if c['case_id']==cid)
        points=[r for r in case['points'] if r['angle_deg']<=75]
        refs=case['reference']['rows']
        ax.plot([r['angle_deg'] for r in points],[r['GZ_m'] for r in points],label='Sealed main hull',lw=1.8)
        ax.plot([r['angle_deg'] for r in refs],[r['GZ_m'] for r in refs],'o--',label='PDF complete vessel',ms=4)
        deck=case['summary']['first_sampled_deck_immersion_deg']
        if deck is not None and deck<75:ax.axvspan(deck,75,color='#e5bc81',alpha=.18,label='Upper geometry needed')
        ax.axhline(0,color='#8da2ab',lw=.7)
        ax.set(title=cid+' / '+case['side'],xlabel='Heel (deg)',ylabel='GZ (m)',xlim=(0,75))
        ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(output/'stability_comparison.png',dpi=150);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    args=parser.parse_args()
    output=HERE/'output';output.mkdir(exist_ok=True)
    started=time.perf_counter()
    equilibria=load_equilibria(verify_sources=True)
    source=PROJECT/'07_浮态与平衡求解/output/stability_inputs.json'
    condition=equilibria['conditions']
    base=HydrostaticCalculator.from_npz(rho=condition['rho_t_m3'],shell_thickness=condition['shell_thickness_m'])
    pdfs=list(PROJECT.glob('*.pdf'))
    if len(pdfs)!=1 or digest(pdfs[0])!=equilibria['source_pdf_sha256']:raise ValueError('PDF与07来源不一致')
    reference=extract_reference(pdfs[0],equilibria,output)
    cases=compute_curves(base,equilibria,reference,output)
    kn=kn_benchmark(base,reference)
    comparisons=compare(cases)
    flat=[dict(case_id=c['case_id'],**r) for c in cases for r in c['points']]
    summaries=[dict(case_id=c['case_id'],side=c['side'],**c['summary']) for c in cases]
    allgz=[r for r in comparisons if r['metric']=='GZ_m']
    below=[r for r in allgz if not r['deck_immersed']]
    def stats(rows):
        return dict(count=len(rows),mean_absolute_difference_m=float(np.mean([abs(r['difference']) for r in rows])),
                    max_absolute_difference_m=max(abs(r['difference']) for r in rows),
                    worst=max(rows,key=lambda r:abs(r['difference'])))
    notes=[
        '指定横倾、恒定总重量并自由求纵倾；使用02网格、06载荷和07姿态初值，不将PDF GZ/KN作为网格计算输入或拟合目标。',
        '按各PDF图中PS/SB方向计算，PS为左舷下沉。GZ为沿该横倾方向的有符号恢复力臂，包含该工况TCG。',
        '绕x旋转后同时求水面高度和纵倾，令浮力等于重量且世界水平纵向重心投影与浮心重合；不能固定中吃水直接旋转，也不能在大横倾时仅令LCB=LCG。',
        '自由液面采用手册同一等效虚升口径：FSMOM=FSM*sin(theta)，DGZ=FSMOM/W，GZ=GZ_fixed-DGZ；未重建各液舱随角度变化的真实液面。',
        'PDF68页明确横稳性包括艉楼、艏楼和舱口围板。当前02外包络不含这些完整浮力体积，故封顶触水后继续计算的是封闭主船体假设的诊断曲线。',
        '封顶开始浸水不是船舶进水角；它标记缺失上部浮力几何开始影响本模型，09再处理实际进水点与限制。',
        '0—90度每2.5度（另加12度），90—180度每5度。大于70度的结果用于封闭主船体稳性范围诊断，PDF工况表没有对应点，不外插其参考曲线。',
        'GZmax、AVS和面积均来自本模型；封顶浸水后的峰值、AVS及面积不能当成已复现完整母型船的数值，也不用于宣布判据合格。',
        '面积用弧度积分，采用PCHIP解析原函数。输出0—30/40度和30—40度原始面积，未按实际进水角截断。',
        'MS列经全部351行数值核对支持MS=GZ-GM*sin(theta)，这是对符号的数值推断，保留原列和原值；GM取GZ在0度附近的中心差分斜率。',
        'KN参考表仅涵盖2—12m、203档吃水和9档角度，共1827个KN。另用7档吃水、9档角度做零纵倾恒排水网格诊断，不把KN插值当成独立几何计算结果。',
        '沿用07的水密度及18mm一阶排水体积修正；浮心仍为型线网格口径，未做全部外板偏置积分。02钢材厚度保持12mm。',
        '收敛只代表指定姿态下的重量及纵倾平衡；大角度受强制横倾，GZ通常非零，不要求横向力矩为零。',
    ]
    report=dict(stage='08_大倾角稳性计算',case_count=len(cases),angle_count=len(ANGLES),mesh_point_count=len(flat),
        comparison_count=len(comparisons),reference_curve_rows=reference['case_row_count'],reference_KN_cells=len(reference['kn_rows'])*9,
        conditions=condition,source_mesh_sha256=digest(MESH),source_equilibrium_sha256=digest(source),
        source_pdf=reference['source_pdf'],source_pdf_sha256=reference['source_sha256'],
        all_numerical_converged=all(r['numerical_converged'] for r in flat),
        max_mass_residual_t=max(abs(r['mass_residual_t']) for r in flat),
        max_pitch_lever_residual_m=max(abs(r['pitch_lever_residual_m']) for r in flat),
        max_GM_slope_difference_from07_m=max(abs(c['summary']['GM_slope_difference_m']) for c in cases),
        GZ_all_reference_points=stats(allgz),GZ_before_first_deck_contact=stats(below),
        post_deck_point_count=sum(r['deck_immersed'] for r in flat),
        full_vessel_geometry_complete=False,reference_identity_checks_passed=all(
            r['MS_within_printing'] and r['DGZ_within_printing'] for r in reference['reference_identity_checks']),
        notes=notes,elapsed_seconds=time.perf_counter()-started)
    data=dict(conditions=condition,source_mesh_sha256=report['source_mesh_sha256'],source_equilibrium_sha256=report['source_equilibrium_sha256'],
              full_vessel_geometry_complete=False,cases=cases)
    save_json(output/'stability_results.json',data)
    save_json(output/'verification_report.json',report)
    write_csv(output/'GZ_curves.csv',flat)
    write_csv(output/'stability_summary.csv',summaries)
    write_csv(output/'verification.csv',comparisons)
    write_csv(output/'KN_benchmark.csv',kn)
    np.savez_compressed(output/'stability_data.npz',case_ids=np.array([c['case_id'] for c in cases]),angle_deg=ANGLES,
        **{key:np.array([[r[key] for r in c['points']] for c in cases]) for key in
           ['GZ_m','GZ_fixed_m','area_mrad','MS_m','FSMOM_tm','DGZ_m','deck_immersed','numerical_converged']})
    save_json(output/'criteria_inputs.json',dict(purpose='09判据/进水角处理输入；当前缺失上部浮力几何，不能直接宣称完整船型判据复现',
        full_vessel_geometry_complete=False,source_mesh_sha256=report['source_mesh_sha256'],cases=cases))
    template=(HERE/'stability_viewer.html').read_text(encoding='utf-8')
    (output/'stability_verification.html').write_text(template.replace('__DATA__',json.dumps(dict(report=report,cases=cases),ensure_ascii=False,allow_nan=False)),encoding='utf-8')
    make_plots(output,cases)
    print(json.dumps(dict(cases=len(cases),points=len(flat),converged=report['all_numerical_converged'],
        GZ_MAE_all=report['GZ_all_reference_points']['mean_absolute_difference_m'],GZ_MAE_before_deck=report['GZ_before_first_deck_contact']['mean_absolute_difference_m'],
        elapsed_seconds=report['elapsed_seconds']),ensure_ascii=False,indent=2))
    if not report['all_numerical_converged']:raise RuntimeError('部分大倾角纵倾/重量平衡未收敛')
    if args.preview:webbrowser.open((output/'stability_verification.html').as_uri())


if __name__=='__main__':main()
