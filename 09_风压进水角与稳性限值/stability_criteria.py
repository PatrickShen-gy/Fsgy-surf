"""09：按所给历史装载手册的风压、开口、稳性判据计算；不宣称现行法规认证。"""
from pathlib import Path
import sys,re,math,argparse,webbrowser
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parent))
from pipeline_common import *
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq

CODES=['AREA30','AREA40','AREA3040','GZ0.2','MAXGZ25','GM0.15','IMOWEATH.']


def extract_reference(out):
    cache=out/'reference/manual_conditions.json';pdf=pdf_path();sha=digest(pdf)
    if cache.exists():
        r=read_json(cache)
        if r.get('sha256')==sha and r.get('version')==1:return r
    wind=[];limits=[];criteria={}
    s07=read_json(stage(7)/'output/equilibrium_results.json')
    with pymupdf.open(pdf) as doc:
        for line in page_lines(doc[72]):
            m=re.match(r'T=([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)',line)
            if m:wind.append(dict(T_m=float(m[1]),area_m2=float(m[2]),CGZ_m=float(m[3]),moment_m3=float(m[4]),pdf_page=73,source_row=line))
        for pn in [77,78]:
            for line in doc[pn-1].get_text().splitlines():
                m=re.match(r'\s*(\d+\.\d{3})\s+(0\.000)\s+([\d.]+)\s+([\d.]+)\s*(\w*)',line)
                if m:limits.append(dict(T_m=float(m[1]),trim_m=0.,MINGM_m=float(m[3]),MAXKG_m=float(m[4]),controlling=m[5] or None,pdf_page=pn))
        for case in s07['cases']:
            pn=case['reference']['pdf_page'];rows={}
            for line in page_lines(doc[pn-1]):
                first=next((code for code in sorted(CODES,key=len,reverse=True) if line.startswith(code)),None)
                if first is None:continue
                vals=re.findall(r'[-+]?\d+(?:\.\d+)?',line[len(first):])
                if len(vals)<2:raise ValueError(f'PDF{pn}页判据格式不完整')
                rows[first]=dict(required=float(vals[-2]),attained=float(vals[-1]),reported_status='NOT MET' if 'NOT MET' in line else 'OK',pdf_page=pn,source_row=line)
            if set(rows)!=set(CODES):raise ValueError(f'{case["case_id"]}判据未提取完整')
            criteria[case['case_id']]=rows
        text={str(p):doc[p-1].get_text() for p in [23,24,25,26,73,74,76,79]}
    if len(wind)!=19 or len(limits)!=85:raise ValueError('风压/限值参考点不完整')
    openings=[dict(id=f'OWIT{i}',frame=15 if i<3 else 30,x_m=float(frame_to_x(15 if i<3 else 30)),y_m=y,z_m=19.1,pdf_page=74)
              for i,y in enumerate([8.2,-8.2,9.6,-9.6],1)]
    r=dict(version=1,sha256=sha,wind=wind,zero_trim_limits=limits,openings=openings,criteria=criteria,source_text=text,
        settings=dict(P_N_m2=504.,g_m_s2=9.81,bilge_keel_area_m2=18.66,beam_m=28.8,depth_m=14.6,LWL_m=175.22),
        source_notes='P504见24页，Ak18.66见76页；LWL=172.02-(-3.20)见39页；肋号坐标由总布置和强度站位转换')
    save_json(cache,r);write_csv(out/'reference/wind_profile.csv',wind);write_csv(out/'reference/KG_limit_zero_trim.csv',limits)
    return r


def wind_profile(t,rows):
    x=np.array([r['T_m'] for r in rows]);extra=t<x[0] or t>x[-1]
    result={}
    for field in ['area_m2','CGZ_m','moment_m3']:
        y=np.array([r[field] for r in rows])
        if t<x[0]:v=y[0]+(t-x[0])*(y[1]-y[0])/(x[1]-x[0])
        elif t>x[-1]:v=y[-1]+(t-x[-1])*(y[-1]-y[-2])/(x[-1]-x[-2])
        else:v=np.interp(t,x,y)
        result[field]=float(v)
    result['extrapolated']=bool(extra)
    return result


def flooding(case,openings):
    points=[p for p in case['points'] if p['angle_deg']<=90]
    x=np.array([p['angle_deg'] for p in points]);side=1 if case['side']=='PS' else -1
    rows=[]
    for op in openings:
        margins=[]
        for p in points:
            phi=math.radians(side*p['angle_deg'])
            opening_z=-math.sin(phi)*op['y_m']+math.cos(phi)*op['z_m']
            wl=p['plane_level_rotated_m']+p['rotated_trim_m']/172*(op['x_m']-86.)
            margins.append(opening_z-wl)
        curve=PchipInterpolator(x,margins)
        roots=[float(r) for r in curve.roots(extrapolate=False) if 0<=r<=90]
        angle=0. if margins[0]<=0 else min(roots) if roots else None
        rows.append(dict(**op,flooding_angle_deg=angle,margin_at_upright_m=margins[0],method='08 sealed-hull pose + opening geometry'))
    finite=[r for r in rows if r['flooding_angle_deg'] is not None]
    first=min(finite,key=lambda r:r['flooding_angle_deg']) if finite else None
    return rows,None if first is None else first['flooding_angle_deg'],None if first is None else first['id']


def roots_level(fn,level,a,b):
    xs=np.linspace(a,b,801);ys=np.array([fn(x)-level for x in xs]);roots=[]
    for i in range(len(xs)-1):
        if ys[i]==0:roots.append(float(xs[i]))
        if ys[i]*ys[i+1]<0:roots.append(float(brentq(lambda t:fn(t)-level,xs[i],xs[i+1])))
    return sorted(set(round(r,10) for r in roots))


def weather(curve,GM,KG,t,mass,wp,deck_angle,fa,settings):
    B=settings['beam_m'];L=settings['LWL_m'];g=settings['g_m_s2']
    lw1=settings['P_N_m2']*wp['moment_m3']/(1000*g*mass);lw2=1.5*lw1
    CB=mass/(1.025*L*B*t)
    x1=float(np.interp(B/t,[2.4,2.5,2.6,2.7,2.8,2.9,3,3.1,3.2,3.3,3.4,3.5],[1,.98,.96,.95,.93,.91,.9,.88,.86,.84,.82,.8]))
    x2=float(np.interp(CB,[.45,.5,.55,.6,.65,.7],[.75,.82,.89,.95,.97,1]))
    k=float(np.interp(100*settings['bilge_keel_area_m2']/(L*B),[0,1,1.5,2,2.5,3,3.5,4],[1,.98,.95,.88,.79,.74,.72,.7]))
    period=2*B/math.sqrt(max(GM,1e-9))*(.373+.023*B/t-.043*L/100)
    sf=float(np.interp(period,[6,7,8,12,14,16,18,20],[.1,.098,.093,.065,.053,.044,.038,.035]))
    raw_r=.73+.6*(KG-t)/t
    rf=min(1.,raw_r) if settings.get('cap_r_at_one',True) else raw_r
    roll=109*k*x1*x2*math.sqrt(max(rf*sf,0.))
    def right(angle):
        a=abs(angle);base=float(curve(math.radians(a)))
        return base if angle>=0 else -base+2*float(curve(0.))*math.cos(math.radians(a))
    first=roots_level(right,lw1,0.,70.)
    gust=roots_level(right,lw2,0.,90.)
    theta0=first[0] if first else None
    theta_a=gust[0] if gust else None
    theta_c=gust[1] if len(gust)>1 else None
    theta2=min(50.,fa if fa is not None else 90.,theta_c if theta_c is not None else 90.)
    area_a=area_b=ratio=None
    if theta0 is not None and theta_a is not None and theta2>theta_a:
        from scipy.integrate import quad
        theta_r=theta0-roll
        area_a=quad(lambda a:lw2-right(math.degrees(a)),math.radians(theta_r),math.radians(theta_a),epsabs=1e-8,limit=150)[0]
        area_b=quad(lambda a:right(math.degrees(a))-lw2,math.radians(theta_a),math.radians(theta2),epsabs=1e-8,limit=150)[0]
        ratio=area_b/area_a if area_a>0 else None
    allowable=min(16.,.8*deck_angle) if deck_angle is not None else 16.
    return dict(lw1_m=lw1,lw2_m=lw2,roll_period_s=period,x1=x1,x2=x2,k=k,r=rf,r_uncapped=raw_r,r_cap_applied=rf!=raw_r,s=sf,roll_to_windward_deg=roll,
        steady_angle_deg=theta0,steady_limit_deg=allowable,steady_angle_met=theta0 is not None and theta0<=allowable,
        gust_first_intersection_deg=theta_a,gust_second_intersection_deg=theta_c,integration_limit_deg=theta2,
        area_a_mrad=area_a,area_b_mrad=area_b,ratio_b_a=ratio,ratio_met=ratio is not None and ratio>=1.,
        moment_profile_zero_trim_approx=True,negative_heel_extension='mirror with TCG cosine offset')


def evaluate(case,eq,reference):
    pts=case['points'];angles=np.radians([r['angle_deg'] for r in pts]);gz=[r['GZ_m'] for r in pts]
    curve=PchipInterpolator(angles,gz);integral=curve.antiderivative()
    openings,fa,first=flooding(case,reference['openings']);end=min(40.,fa if fa is not None else 40.)
    area30=float(integral(math.radians(30))-integral(0.))
    area40=float(integral(math.radians(end))-integral(0.))
    area3040=float(integral(math.radians(end))-integral(math.radians(30))) if end>=30 else 0.
    grid=np.linspace(0.,90.,1801);vals=curve(np.radians(grid));imax=int(np.argmax(vals))
    max_after30=float(np.max(vals[grid>=30]))
    GM=case['summary']['GM_from_GZ_slope_m'];KG=case['load']['KG_m'];mass=case['load']['mass_t']
    t=eq['T_m'];wp=wind_profile(t,reference['wind'])
    wet=case['summary']['first_sampled_deck_immersion_deg']
    dry=max((p['angle_deg'] for p in pts if wet is not None and p['angle_deg']<wet),default=0.)
    deck=(wet+dry)/2 if wet is not None else None
    wind=weather(curve,GM,KG,t,mass,wp,deck,fa,reference['settings'])
    alternate=weather(curve,GM,KG,t,mass,wp,deck,fa,dict(reference['settings'],cap_r_at_one=False))
    wind['ratio_without_r_cap_sensitivity']=alternate['ratio_b_a']
    wind['roll_without_r_cap_deg']=alternate['roll_to_windward_deg']
    attained=dict(AREA30=area30,AREA40=area40,AREA3040=area3040,**{'GZ0.2':max_after30,'MAXGZ25':float(grid[imax]),'GM0.15':GM,'IMOWEATH.':wind['ratio_b_a']})
    req=dict(AREA30=.055,AREA40=.09,AREA3040=.03,**{'GZ0.2':.2,'MAXGZ25':25.,'GM0.15':.15,'IMOWEATH.':1.})
    checks=[]
    for code in CODES:
        actual=attained[code];rr=reference['criteria'][case['case_id']][code]
        checks.append(dict(code=code,required=req[code],calculated=actual,reference=rr['attained'],
            difference=None if actual is None else actual-rr['attained'],criterion_met_current_model=actual is not None and actual>=req[code] and (code!='IMOWEATH.' or wind['steady_angle_met']),
            reference_status=rr['reported_status'],pdf_page=rr['pdf_page'],full_vessel_geometry_complete=False))
    # 固定当前浮心曲线，改变KG的基本判据上限；不冒充零纵倾/含风浪的完整KG限值。
    maxkg={
        'AREA30':KG+(area30-.055)/(1-math.cos(math.radians(30))),
        'AREA40':KG+(area40-.09)/max(1-math.cos(math.radians(end)),1e-9),
        'AREA3040':KG+(area3040-.03)/max(math.cos(math.radians(30))-math.cos(math.radians(end)),1e-9),
        'GM0.15':KG+GM-.15,
        'GZ0.2':max(KG+(float(curve(a))-.2)/math.sin(a) for a in np.radians(np.linspace(30,90,1201)))}
    # 峰值位置限制按冻结浮力曲线扫描，未以参考MAXKG拟合。
    candidates=np.linspace(-5.,35.,801);peak_safe=[]
    sin=np.sin(np.radians(grid))
    for ktest in candidates:
        best=grid[int(np.argmax(vals-(ktest-KG)*sin))]
        if best>=25:peak_safe.append(float(ktest))
    maxkg['MAXGZ25']=max(peak_safe) if peak_safe else -5.
    controlling=min(maxkg,key=maxkg.get)
    return dict(case_id=case['case_id'],side=case['side'],T_m=t,checks=checks,openings=openings,flooding_angle_deg=fa,
        first_opening=first,deck_contact_bracket_deg=[dry,wet],wind=wind,wind_profile=wp,
        KG_limit_basic_diagnostic_m=maxkg[controlling],KG_limit_controlling=controlling,KG_limits_by_criterion=maxkg,
        KG_limit_assumption='冻结该工况浮力曲线；不含天气限制，不等于零纵倾完整船型MAXKG',curve=case)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--preview',action='store_true');args=parser.parse_args()
    out=HERE/'output';out.mkdir(exist_ok=True);(out/'reference').mkdir(exist_ok=True)
    ref=extract_reference(out);s08=read_json(stage(8)/'output/stability_results.json');s07=read_json(stage(7)/'output/equilibrium_results.json')
    eq={c['case_id']:c['calculated'] for c in s07['cases']}
    cases=[evaluate(c,eq[c['case_id']],ref) for c in s08['cases']]
    rows=[dict(case_id=c['case_id'],**r) for c in cases for r in c['checks']]
    report=dict(stage=9,case_count=len(cases),criterion_comparisons=len(rows),full_vessel_geometry_complete=False,
        geometry_mode='08 sealed main hull diagnostic',wind_profile_extrapolated_cases=[c['case_id'] for c in cases if c['wind_profile']['extrapolated']],
        criterion_difference_summary={code:curve_summary([r for r in rows if r['code']==code and r['difference'] is not None]) for code in CODES},
        sources=dict(stage08=digest(stage(8)/'output/stability_results.json'),stage07=digest(stage(7)/'output/equilibrium_results.json'),pdf=ref['sha256']))
    notes=['沿用08封闭主船体曲线，甲板以上浮力几何不完整；输出的判据数值及满足与否只针对当前模型，不作为完整船型判据复现或现行法规认证。',
        '按所给手册23—26、76、79页的历史参数计算，不外加新法规。进水角由74页开口坐标与08自由纵倾姿态求交，封顶接水角与进水角分别保存。',
        '风侧面积和面积矩采用73页零纵倾表：按吃水插值，超过10.27m时沿末段外推并标记。未独立重建上层建筑风侧轮廓或纵倾修正。',
        '天气衡准使用P504N/m²、Ak18.66m²及手册滚动系数。负角度GZ由对侧镜像加TCG余弦偏置近似，未新增反向全曲线。',
        'r按25页文字取不超过1；另列不截断r的敏感性，但不为匹配参考值自动更改主假设。轻载天气结果对该约定敏感。',
        'KG诊断上限冻结当前浮心曲线，包含六项基本判据、不含天气限制；与手册零纵倾限值不是相同工况，原85点限值表独立保留。',
        '只有差值具有相同量纲时才比较；汇总的跨指标绝对差不构成统一验收分数。高角度和风侧几何完善后应重算。']
    save_json(out/'criteria_results.json',dict(cases=cases,report=report,notes=notes))
    save_json(out/'verification_report.json',report);write_csv(out/'criteria_verification.csv',rows)
    write_csv(out/'flooding_angles.csv',[dict(case_id=c['case_id'],**r) for c in cases for r in c['openings']])
    write_csv(out/'weather_results.csv',[dict(case_id=c['case_id'],**c['wind']) for c in cases])
    write_csv(out/'case_KG_limits.csv',[dict(case_id=c['case_id'],KG_limit_basic_diagnostic_m=c['KG_limit_basic_diagnostic_m'],controlling=c['KG_limit_controlling'],**c['KG_limits_by_criterion']) for c in cases])
    view=[]
    for c in cases:
        pts=[p for p in c['curve']['points'] if p['angle_deg']<=90];x=[p['angle_deg'] for p in pts]
        view.append(dict(case_id=c['case_id'],description='进水、天气与基本稳性诊断',
            metrics={'首个开口':c['first_opening'],'进水角 / °':c['flooding_angle_deg'],'天气 b/a':c['wind']['ratio_b_a'],'基本KG上限 / m':c['KG_limit_basic_diagnostic_m']},
            notes=['主船体诊断；上部浮力体积缺失。风侧面积采用PDF零纵倾条件。'],
            tables={'稳性判据对照':c['checks'],'开口进水角':c['openings'],'天气参数':[c['wind']],'KG基本限值':[c['KG_limits_by_criterion']]},
            charts=[dict(title='GZ与风倾力臂',x_label='横倾 / °',y_label='力臂 / m',series=[
                dict(name='08主船体GZ',x=x,y=[p['GZ_m'] for p in pts]),dict(name='稳风 lw1',x=x,y=[c['wind']['lw1_m']]*len(x),dash=True),dict(name='阵风 lw2',x=x,y=[c['wind']['lw2_m']]*len(x),dash=True)])]))
    write_dashboard(out/'criteria_verification.html','09 · 风压、进水角与稳性限值','39个工况；采用手册条件与08主船体诊断曲线。',view,notes,
        stats={'工况':39,'判据对照':len(rows),'零纵倾参考限值点':85},links={'CSV':'criteria_verification.csv','报告':'verification_report.json','天气参数':'weather_results.csv'})
    print(f'09 completed: {len(cases)} cases / {len(rows)} criterion comparisons; geometry diagnostic')
    if args.preview:webbrowser.open((out/'criteria_verification.html').as_uri())


if __name__=='__main__':main()
