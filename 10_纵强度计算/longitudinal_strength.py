"""10：按空船分项/载荷质量矩守恒的假设分布，积分网格浮力、剪力和弯矩。"""
from pathlib import Path
import sys,re,math,argparse,webbrowser
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parent))
from pipeline_common import *
sys.path.insert(0,str(stage(3)))
from bonjean_calculation import MeshSlicer
from shapely.geometry import Polygon
from scipy.optimize import brentq

LIGHT_SPANS=dict(STERN=(-3.2,7.4),ER1=(7.4,25.8),ER2=(7.4,25.8),HOLD5=(25.8,53.8),BHD2=(52.6,53.8),
    HOLD4=(53.8,82.6),BHD3=(81.4,82.6),HOLD3=(82.6,111.4),BHD4=(110.2,111.4),HOLD2=(111.4,140.2),
    BHD5=(139.,140.2),HOLD1=(140.2,165.),FORE=(165.,176.68),SUPER=(-3.2,25.8),PIPE=(7.4,165.),MIS=(-3.2,176.68),ADD=(-3.2,176.68))
HOLD_SPANS={1:(140.2,165.),2:(111.4,140.2),3:(82.6,111.4),4:(53.8,82.6),5:(25.8,53.8)}


def extract_reference(out):
    path=out/'reference/longitudinal_reference.json';sha=digest(pdf_path())
    if path.exists():
        data=read_json(path)
        if data.get('sha256')==sha and data.get('version')==1:return data
    weights=read_json(stage(6)/'output/loading_cases.json');refs={};limits=[]
    with pymupdf.open(pdf_path()) as d:
        t=d[332].get_text();pattern=r'\b(STERN|ER1|ER2|HOLD[1-5]|BHD[2-5]|FORE|SUPER|PIPE|MIS|ADD)\s+([\d.]+)\s+([\d.]+)\s+([-\d.]+)\s+([\d.]+)'
        light=[dict(id=m[0],mass_t=float(m[1]),LCG_m=float(m[2]),TCG_m=float(m[3]),KG_m=float(m[4]),pdf_page=333,
                    span_assumed_m=list(LIGHT_SPANS[m[0]])) for m in re.findall(pattern,t)]
        if len(light)!=17 or sum(r['mass_t'] for r in light)!=8650.:raise ValueError('空船17分项未提取完整')
        frames=[9,25,34,52,69,87,105,123,141,159,177,192,208,214,220]
        mode='intact'
        for line in page_lines(d[14]):
            m=re.match(r'(INTACT|FLOOD|CONDITION)\s+(HOGGING|SAGGING|POSITIVE|NEGATIVE)\s+(.+)',line)
            if not m:continue
            if m[1]=='INTACT':mode='intact'
            if m[1]=='FLOOD':mode='flood'
            vals=[float(v) for v in re.findall(r'-?\d+',m[3])]
            if len(vals)!=15:raise ValueError('15页强度限值列数不匹配')
            limits.append(dict(mode=mode,kind=m[2],frames=frames,values=vals,unit='kN·m' if m[2] in ['HOGGING','SAGGING'] else 'kN',pdf_page=15,source_row=line))
        if len(limits)!=8:raise ValueError('强度限值八行未提取完整')
        for c in weights['cases']:
            pn=c['reference']['summary_page']+4;rows=[]
            for line in page_lines(d[pn-1]):
                vals=re.findall(r'[-+]?\d+(?:\.\d+)?',line)
                if len(vals)!=6:continue
                v=list(map(float,vals))
                if not 0<=v[0]<=177 or not 0<=v[1]<=224:continue
                rows.append(dict(x_m=v[0],frame=v[1],BM_tm=v[2],SF_t=v[3],WD_t_m=v[4],BD_t_m=v[5],pdf_page=pn))
            if len(rows)!=16 or rows[0]['x_m']!=0.:raise ValueError(f'{c["case_id"]}强度表不完整：{len(rows)}')
            text=d[c['reference']['summary_page']-1].get_text()
            refs[c['case_id']]=dict(rows=rows,summary_SF_marked_CORR='CORR' in text,pdf_page=pn)
    result=dict(version=1,sha256=sha,lightship=light,limits=limits,cases=refs,
        source_anomalies=['15页INTACT SAGGING在FR34印作-950000，按原值保留。','15页INTACT POSITIVE在FR220印作-400，不取绝对值或改零。'])
    save_json(path,result);return result


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


def load_span(item,catalog):
    ident=item['id']
    m=re.match(r'CH([1-5])$',ident)
    if m:return HOLD_SPANS[int(m[1])]
    m=re.match(r'WBT([1-5])[PS]$',ident)
    if m:return HOLD_SPANS[int(m[1])]
    if ident=='APT':return (-3.2,7.4)
    if ident=='FPT':return (165.,176.68)
    if ident in catalog:
        r=catalog[ident];return (max(-3.2,float(frame_to_x(r['frame_aft']))),min(176.68,float(frame_to_x(r['frame_fore']))))
    return None


def beam_result(edges,wm,wmx,wmz,bm,bmx,bmz,slope,nz=1.):
    q=np.r_[0.,np.cumsum(wm-bm)]
    mx=np.r_[0.,np.cumsum(wmx-bmx)];mz=np.r_[0.,np.cumsum(wmz-bmz)]
    # 沿倾斜重力方向的轴向力也对不同高度产生纵向弯矩，保证端部力矩可闭合。
    return nz*q,nz*(edges*q-mx-slope*mz)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--preview',action='store_true');args=parser.parse_args()
    out=HERE/'output';out.mkdir(exist_ok=True);(out/'reference').mkdir(exist_ok=True)
    ref=extract_reference(out);weights=read_json(stage(6)/'output/loading_cases.json');eq=read_json(stage(7)/'output/equilibrium_results.json')
    eqmap={c['case_id']:c['calculated'] for c in eq['cases']}
    catalog={r['id']:r for r in read_json(stage(5)/'output/loading_reference_catalog.json')['tanks']}
    meshfile=stage(2)/'CAD_mesh/02_计算网格/calculation_mesh.npz'
    with np.load(meshfile) as m:vertices=m['outer_vertices'];faces=m['hydro_faces'];ring_x=m['ring_x']
    xmin,xmax=float(vertices[:,0].min()),float(vertices[:,0].max())
    boundaries=[v for span in LIGHT_SPANS.values() for v in span]
    for r in catalog.values():boundaries.extend([max(xmin,float(frame_to_x(r['frame_aft']))),min(xmax,float(frame_to_x(r['frame_fore'])))])
    centers_CG=[i['LCG_m'] for c in weights['cases'] for i in c['items'] if i['mass_t']>0]
    edges=np.unique(np.round(np.r_[np.linspace(xmin,xmax,451),ring_x,boundaries,centers_CG,FRAME_X[(FRAME_X>=xmin)&(FRAME_X<=xmax)]],8))
    edges=edges[(edges>=xmin)&(edges<=xmax)];centers=(edges[:-1]+edges[1:])/2;dx=np.diff(edges)
    sample=np.empty(len(edges)+len(centers));sample[::2]=edges;sample[1::2]=centers
    slicer=MeshSlicer(vertices,faces);sections=[slicer.section(float(x)) for x in sample]
    shift=79.30-sum(r['mass_t']*r['LCG_m'] for r in ref['lightship'])/8650.
    wm_light=np.zeros(len(dx));wz_light=np.zeros(len(dx))
    component_rows=[]
    for r in ref['lightship']:
        cg=r['LCG_m']+shift;mass=distributed_cell_masses(edges,r['mass_t'],cg,r['span_assumed_m'])
        wm_light+=mass;wz_light+=mass*r['KG_m']
        component_rows.append(dict(case_id='ALL',id=r['id'],mass_t=r['mass_t'],input_LCG_m=r['LCG_m'],adopted_LCG_m=cg,
            distribution_span_m=r['span_assumed_m'],source='PDF333 mass/CG + assumed span',mass_residual_t=float(mass.sum()-r['mass_t']),moment_residual_tm=float(mass@centers-r['mass_t']*cg)))
    results=[];comparisons=[];checks=[];dash=[]
    intact={r['kind']:r for r in ref['limits'] if r['mode']=='intact'}
    limit_x=frame_to_x(np.array(intact['HOGGING']['frames']));g=9.81
    for case in weights['cases']:
        cid=case['case_id'];state=eqmap[cid];slope=state['trim_m']/172;heel=math.tan(math.radians(state['heel_deg']));nz=state['normal_body'][2]
        wm=wm_light.copy();wmz=wz_light.copy()
        for item in case['items']:
            if item['mass_t']==0:continue
            span=load_span(item,catalog);mass=distributed_cell_masses(edges,item['mass_t'],item['LCG_m'],span)
            wm+=mass;wmz+=mass*item['KG_m']
            component_rows.append(dict(case_id=cid,id=item['id'],mass_t=item['mass_t'],input_LCG_m=item['LCG_m'],adopted_LCG_m=item['LCG_m'],
                distribution_span_m=span,source='PDF case mass/CG + assumed span or point load',mass_residual_t=float(mass.sum()-item['mass_t']),moment_residual_tm=float(mass@centers-item['mass_t']*item['LCG_m'])))
        area=[];mz=[]
        for x,section in zip(sample,sections):
            level=state['T_m']+slope*(x-86.)
            clip=Polygon([(-100.,-100.),(100.,-100.),(100.,level+heel*100.),(-100.,level-heel*100.)])
            wet=section.intersection(clip);a=float(wet.area)
            area.append(a);mz.append(a*wet.centroid.y if a else 0.)
        area=np.array(area);mz=np.array(mz)
        vol=dx*(area[::2][:-1]+4*area[1::2]+area[::2][1:])/6
        bx=dx*(sample[::2][:-1]*area[::2][:-1]+4*sample[1::2]*area[1::2]+sample[::2][1:]*area[::2][1:])/6
        bz=dx*(mz[::2][:-1]+4*mz[1::2]+mz[::2][1:])/6
        # 07用一阶外板排水量而浮心保持型线口径；使用同一总体质量因子，不拟合参考SF/BM。
        factor=case['calculated']['total']['mass_t']/vol.sum()
        bm=vol*factor;bmx=bx*factor;bmz=bz*factor
        SF,BM=beam_result(edges,wm,wm*centers,wmz,bm,bmx,bmz,slope,nz)
        closure=dict(case_id=cid,end_SF_t=float(SF[-1]),end_BM_tm=float(BM[-1]),
            weight_mass_t=float(wm.sum()),target_mass_t=case['calculated']['total']['mass_t'],
            weight_LCG_m=float(wm@centers/wm.sum()),target_LCG_m=case['calculated']['total']['LCG_m'],
            sliced_Bx_m=float(bx.sum()/vol.sum()),mesh_Bx_m=state['LCB_m'],
            sliced_Bz_m=float(bz.sum()/vol.sum()),mesh_Bz_m=state['VCB_m'],
            relative_slice_volume_difference=float((vol.sum()-state['VOLM_m3'])/state['VOLM_m3']),
            buoyancy_mass_factor_t_m3=float(factor),lightship_rounding_LCG_shift_m=shift)
        checks.append(closure)
        table=[]
        for r in ref['cases'][cid]['rows']:
            x=r['x_m'];calcSF=float(np.interp(x,edges,SF));calcBM=float(np.interp(x,edges,BM))
            row=dict(x_m=x,SF_t=calcSF,reference_SF_t=r['SF_t'],SF_difference_t=calcSF-r['SF_t'],BM_tm=calcBM,reference_BM_tm=r['BM_tm'],BM_difference_tm=calcBM-r['BM_tm'],pdf_page=r['pdf_page'])
            if limit_x[0]<=x<=limit_x[-1]:
                loS=float(np.interp(x,limit_x,intact['NEGATIVE']['values']));hiS=float(np.interp(x,limit_x,intact['POSITIVE']['values']))
                loB=float(np.interp(x,limit_x,intact['SAGGING']['values']));hiB=float(np.interp(x,limit_x,intact['HOGGING']['values']))
                row.update(SF_min_kN=loS,SF_max_kN=hiS,BM_min_kNm=loB,BM_max_kNm=hiB,
                    current_model_SF_within_limit=loS<=calcSF*g<=hiS,current_model_BM_within_limit=loB<=calcBM*g<=hiB)
            table.append(row);comparisons.append(dict(case_id=cid,**row))
        curve=dict(x_m=edges.tolist(),SF_t=SF.tolist(),BM_tm=BM.tolist(),cell_x_m=centers.tolist(),weight_t_m=(wm/dx).tolist(),buoyancy_t_m=(bm/dx).tolist())
        result=dict(case_id=cid,description=case['description'],curve=curve,verification=table,closure=closure,
            extrema=dict(SF_max_t=float(SF.max()),SF_min_t=float(SF.min()),BM_hogging_max_tm=float(BM.max()),BM_sagging_min_tm=float(BM.min())),
            weight_distribution_assumed=True,reference_summary_SF_marked_CORR=ref['cases'][cid]['summary_SF_marked_CORR'])
        results.append(result)
        rr=ref['cases'][cid]['rows']
        charts=[dict(title='纵向载荷密度',x_label='X / m',y_label='t/m',series=[dict(name='假设重量分布',x=centers,y=wm/dx),dict(name='网格浮力分布',x=centers,y=bm/dx)]),
            dict(title='剪力 SF',x_label='X / m',y_label='t',series=[dict(name='梁模型',x=edges,y=SF),dict(name='PDF',x=[r['x_m'] for r in rr],y=[r['SF_t'] for r in rr],dots=True)]),
            dict(title='弯矩 BM',x_label='X / m',y_label='t·m',series=[dict(name='梁模型',x=edges,y=BM),dict(name='PDF',x=[r['x_m'] for r in rr],y=[r['BM_tm'] for r in rr],dots=True)])]
        dash.append(dict(case_id=cid,description=case['description'],metrics=result['extrema'],notes=['重量分布形状为假设，逐分项守恒重量和LCG。未计算手册标注的局部CORR剪力修正。'],tables={'16站对照':table,'端部闭合':[closure]},charts=charts))
        print(f'{cid}: SF end={SF[-1]:.3g} t; BM end={BM[-1]:.3g} tm',flush=True)
    report=dict(stage=10,case_count=len(results),station_comparisons=len(comparisons),grid_cells=len(dx),
        max_end_SF_t=max(abs(c['end_SF_t']) for c in checks),max_end_BM_tm=max(abs(c['end_BM_tm']) for c in checks),
        max_weight_LCG_error_m=max(abs(c['weight_LCG_m']-c['target_LCG_m']) for c in checks),
        max_slice_volume_relative_error=max(abs(c['relative_slice_volume_difference']) for c in checks),
        SF_difference=curve_summary(comparisons,'SF_difference_t'),BM_difference=curve_summary(comparisons,'BM_difference_tm'),
        source_anomalies=ref['source_anomalies'],weight_distribution_assumed=True,local_shear_correction_implemented=False,
        sources=dict(pdf=ref['sha256'],mesh=digest(meshfile),stage06=digest(stage(6)/'output/loading_cases.json'),stage07=digest(stage(7)/'output/equilibrium_results.json')))
    notes=['空船17项质量和重心来自333页，分布区间/非负指数形状为明确假设；油水舱区间由81—83页肋号转换。无区间的物资按点载荷近似。',
        f'333页打印分项合成LCG为79.296432m，为与06采用79.30m一致，所有空船分项统一平移{shift:.8f}m，只作舍入对齐，不拟合剪力弯矩。',
        '浮力来自02网格切片与07平衡水面，使用每格端点/中点Simpson积分。总体浮力因子沿用07外板一阶修正且保持型线浮心，不从PDF BD曲线反推。',
        '弯矩包括倾斜重力轴向分量与重量/浮力高度差的作用，端部闭合残差独立报告，不以线性修正抹平。',
        '输出是未作局部舱壁/斜板CORR修正的纵向梁模型；参考表/摘要部分有CORR标注，现有重量分布假设也会造成差异，不能宣称已精确复现纵强度。',
        '限值按手册15页完好状态逐站线性插值，单位kN/kN·m，计算t/t·m以g=9.81转换；仅在限值肋号范围内核对。原表疑点保留。']
    save_json(out/'longitudinal_results.json',dict(cases=results,report=report,notes=notes));save_json(out/'verification_report.json',report)
    write_csv(out/'station_verification.csv',comparisons);write_csv(out/'closure_checks.csv',checks);write_csv(out/'distribution_assumptions.csv',component_rows)
    write_csv(out/'reference/lightship_components.csv',ref['lightship']);write_csv(out/'reference/strength_limits.csv',ref['limits'])
    np.savez_compressed(out/'longitudinal_data.npz',case_ids=np.array([c['case_id'] for c in results]),x_m=edges,SF_t=np.array([c['curve']['SF_t'] for c in results]),BM_tm=np.array([c['curve']['BM_tm'] for c in results]))
    write_dashboard(out/'longitudinal_verification.html','10 · 纵强度与分布假设','网格浮力、分项质量矩守恒、剪力与弯矩；参考输出不参与分布拟合。',dash,notes,
        stats={'工况':39,'站位对照':len(comparisons),'空船分项':17},links={'CSV':'station_verification.csv','端部闭合':'closure_checks.csv','分布假设':'distribution_assumptions.csv','报告':'verification_report.json'})
    print('10 completed',report)
    if args.preview:webbrowser.open((out/'longitudinal_verification.html').as_uri())


if __name__=='__main__':main()
