"""06 重量与重心：读取PDF逐项工况输入，独立汇总重量/重心/自由液面修正。

python weight_cg_calculation.py [--preview]
python weight_cg_calculation.py --case-file example_custom_case.json
PDF汇总结果仅作验证；几何舱室也可作为自定义载荷输入。
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
sys.path.insert(0,str(PROJECT/'05_舱室与舱容计算'))
from tank_capacity_calculation import pdf_lines, load_results as load_results_05, TankCalculator, DISPLAY, np, digest, save_json, write_csv
import pymupdf

CATEGORIES=['Ballast Water','Diesel Oil','Fresh Water','Grain Load','Steel Coil','Heavy Fuel Oil','Lubricating Oil','Mass Load','Miscellaneous']
PURPOSE={'BW':'Ballast Water','DO':'Diesel Oil','FW':'Fresh Water','GRAIN':'Grain Load','HFO':'Heavy Fuel Oil','LO':'Lubricating Oil','MASS':'Mass Load','MIS':'Miscellaneous'}
METRICS=['mass_t','LCG_m','TCG_m','KG_m','FSM_tm','KG_correction_m','KG_effective_m']
NAMES=dict(mass_t='总重量',LCG_m='纵向重心',TCG_m='横向重心',KG_m='垂向重心',FSM_tm='自由液面矩合计',
           KG_correction_m='自由液面虚升量',KG_effective_m='修正后等效KG')
UNITS=dict(mass_t='t',LCG_m='m',TCG_m='m',KG_m='m',FSM_tm='t·m',KG_correction_m='m',KG_effective_m='m')


def tokens_at_right(words, threshold):
    text=' '.join(w[4] for w in words if w[0]>=threshold)
    return re.findall(r'[-+]?\d+(?:\.\d+)?',text)


def canonical_id(description, category, y, counters, catalog):
    if description.startswith('PROVISION'):return 'PROVISION'
    match=re.match(r'NO\.(\d) CARGO HOLD',description)
    if match:return 'CH'+match[1]
    if description.startswith('AFT PEAK'):return 'APT'
    if description.startswith('FORE PEAK'):return 'FPT'
    match=re.match(r'NO\.(\d) (?:WATER BALLAST|W\.B\.T\.)',description)
    if match:
        number=match[1]
        pair=counters[('WBT',number)];counters[('WBT',number)]+=1
        side='P' if '(P)' in description or y>0 else 'S' if '(S)' in description or y<0 else ('P' if pair==0 else 'S')
        return f'WBT{number}{side}'
    aliases={'M.D.O. SERV. TK.':'MDOSERVTK','M.D.O. SETTL. TK.':'MDOSETTLTK',
             'NO.1 H.F.O. SETTL. T.':'NO1_HFO_SETTL_TK','NO.2 H.F.O. SETTL. T.':'NO2_HFO_SETTL_TK',
             'CREW & EFFECT':'CREW','PROVISION 100%':'PROVISION','SPARE':'SPARE'}
    if description in aliases:return aliases[description]
    def normal(s):return re.sub(r'[^A-Z0-9]','',s.upper())
    normalized=normal(description)
    found=[r['id'] for r in catalog if normal(r['description'])==normalized]
    if len(found)==1:return found[0]
    # 所有未识别名称应触发回查，而不是静默丢失一项载荷。
    raise ValueError(f'无法映射载荷名称：{category}: {description}')


def parse_details(document,pages,catalog):
    rows,subtotals=[],[]
    category,density,last_kind=None,None,None
    counters=defaultdict(int)
    for pn in pages:
        for y,words in pdf_lines(document[pn-1]):
            line=' '.join(w[4] for w in words)
            if 'MASS' in line and 'FILL' in line:
                last_kind='GRM' if 'GRM' in line else 'FRSM'
                continue
            header=re.match(r'^(.*?)\s*,?\s*\(Dens\s*=\s*([\d.]+)\)',line)
            if header:
                category=header[1].rstrip(' ,') or 'Mass Load'
                density=float(header[2]);continue
            if line=='Steel Coil':category,density='Steel Coil',None;continue
            if not words or words[0][0]>90:continue
            description=' '.join(w[4] for w in words if w[0]<190)
            numeric=tokens_at_right(words,190)
            if description in ('SUBTOTAL','TOTAL'):
                if len(numeric)==5:
                    subtotals.append(dict(category=category,label=description,pdf_page=pn,source_row=line,
                        **dict(zip(['mass_t','LCG_m','TCG_m','KG_m','last_moment_tm'],map(float,numeric)))))
                continue
            if not numeric:continue
            # 页眉/单位/分隔行不能成为载荷。
            if description.startswith(('34800','Page','DES','---')) or not description:continue
            if len(numeric)!=6:
                raise ValueError(f'PDF{pn}页载荷列数异常：{line}')
            mass,fill,x,yy,z,last=map(float,numeric)
            if description in ('CREW & EFFECT','SPARE') or description.startswith('PROVISION'):
                category,density='Mass Load',1.
            actual_category=category
            if actual_category not in CATEGORIES:
                raise ValueError(f'PDF{pn}页载荷类别未识别：{line}, {actual_category}')
            ident=canonical_id(description,actual_category,yy,counters,catalog)
            row=dict(id=ident,description=description,category=actual_category,mass_t=mass,fill_percent=fill,
                     LCG_m=x,TCG_m=yy,KG_m=z,density_t_m3=1. if actual_category=='Mass Load' else density,
                     FSM_tm=last if last_kind=='FRSM' else 0.,grain_heeling_moment_tm=last if last_kind=='GRM' else 0.,
                     last_column_kind=last_kind,pdf_page=pn,source_row=line,numeric_tokens=numeric,
                     data_source='pdf_case_item',position_source='PDF actual loading CG, not full-tank CG')
            rows.append(row)
    if len({(r['category'],r['id']) for r in rows})!=len(rows):
        raise ValueError(f'载荷项重复：{pages}')
    if not 50<=len(rows)<=56:raise ValueError(f'工况载荷项数量异常：{pages}: {len(rows)}')
    return rows,subtotals


def parse_summary(document,pn):
    groups,light,total,deadweight=[],None,None,None
    for y,words in pdf_lines(document[pn-1]):
        line=' '.join(w[4] for w in words)
        if line.startswith('F L O A T'):break
        numeric=tokens_at_right(words,200)
        values=list(map(float,numeric))
        left=' '.join(w[4] for w in words if w[0]<200)
        if left.startswith('Lightweight') and len(values)==4:
            light=dict(id='LIGHTSHIP',description='Lightweight',category='Lightweight',
                       **dict(zip(['mass_t','LCG_m','TCG_m','KG_m'],values)),FSM_tm=0.,
                       data_source='pdf_lightship_input',pdf_page=pn,source_row=line,numeric_tokens=numeric)
        elif left.startswith('Total weight') and len(values)==4:
            total=dict(zip(['mass_t','LCG_m','TCG_m','KG_m'],values))
        elif left.startswith('Deadweight') and len(values)==5:
            deadweight=dict(zip(['mass_t','LCG_m','TCG_m','KG_m','FSM_tm'],values))
        elif len(values)==5:
            # PURPOSE在左侧；钢卷没有PURP时用类别名称识别。
            label=next((PURPOSE[w[4]] for w in words if w[4] in PURPOSE),None)
            if 'Steel Coil' in line:label='Steel Coil'
            if label is not None:
                groups.append(dict(category=label,**dict(zip(['mass_t','LCG_m','TCG_m','KG_m','FSM_tm'],values)),pdf_page=pn))
    if light is None or total is None or deadweight is None:
        raise ValueError(f'PDF{pn}页汇总识别不完整')
    text=document[pn-1].get_text()
    def value(pattern):
        m=re.search(pattern,text)
        if m is None:raise ValueError(f'PDF{pn}页缺少{pattern}')
        return float(m[1])
    floating=dict(T_m=value(r'Draught moulded\s+([-\d.]+)'),trim_m=value(r'\bTrim\s+([-\d.]+)'),
                  KM_m=value(r'\bKM\s+([-\d.]+)'),KG_m=value(r'\bKG\s+([-\d.]+)'),
                  GM0_m=value(r'\bGM0\s+([-\d.]+)'),GMCORR_signed_m=value(r'\bGMCORR\s+([-\d.]+)'),
                  GM_m=value(r'\bGM\s+([-\d.]+)'))
    total['FSM_tm']=deadweight['FSM_tm']
    return dict(lightweight=light,total=total,deadweight=deadweight,groups=groups,
                floating_reference=floating,summary_page=pn)


def extract_reference(pdf,output,catalog):
    target=output/'reference';target.mkdir(parents=True,exist_ok=True)
    path=target/'pdf_loading_cases.json';fingerprint=digest(pdf)
    if path.exists():
        data=json.loads(path.read_text(encoding='utf-8'))
        if data.get('parser_version')==2 and data.get('source_sha256')==fingerprint:return data
    cases=[]
    with pymupdf.open(pdf) as d:
        for i in range(98,len(d)):
            text=d[i].get_text()
            match=re.search(r"LOADING CASE\s+'?(LD\d+[A-Z]?)\s*--\s*([^\n]+)",text)
            if not match:continue
            pn=i+1
            summary=parse_summary(d,pn+1)
            items,subtotals=parse_details(d,[pn+2,pn+3],catalog)
            cases.append(dict(case_id=match[1],description=match[2].strip(),figure_page=pn,
                              detail_pages=[pn+2,pn+3],items=items,detail_subtotals=subtotals,**summary))
        overview=[]
        for y,words in pdf_lines(d[88]):
            if not re.match(r'^LD\d+[A-Z]?$',words[0][4]):continue
            vals=tokens_at_right(words,120)
            if len(vals)!=8:raise ValueError('PDF89页总览列数异常')
            overview.append(dict(case_id=words[0][4],**dict(zip(['DISP_t','DWIT_t','cargo_t','bunker_t','T_m','trim_m','GM_m','GMCORR_m'],map(float,vals))),pdf_page=89))
    if len(cases)!=39 or len(set(c['case_id'] for c in cases))!=39 or {r['case_id'] for r in overview}!={c['case_id'] for c in cases}:
        raise ValueError(f'工况不完整：{len(cases)}个详细工况、{len(overview)}个总览工况')
    data=dict(parser_version=2,source_pdf=str(pdf.resolve()),source_sha256=fingerprint,
              input_definition='各工况逐项MASS/CG/FRSM与空船为采用的输入；分类、总计、浮态栏为独立验证参考',
              case_count=len(cases),item_count=sum(len(c['items']) for c in cases),cases=cases,overview=overview,
              values_not_modified=True)
    save_json(path,data)
    return data


def aggregate(items):
    if any(not math.isfinite(float(r[k])) for r in items for k in ['mass_t','LCG_m','TCG_m','KG_m','FSM_tm']):
        raise ValueError('载荷有缺失或非有限重量/坐标/FSM')
    if any(r['mass_t']<0 or r['FSM_tm']<0 for r in items):raise ValueError('重量及FSM不能为负')
    mass=math.fsum(r['mass_t'] for r in items)
    moment={key:math.fsum(r['mass_t']*r[key] for r in items) for key in ['LCG_m','TCG_m','KG_m']}
    fsm=math.fsum(r['FSM_tm'] for r in items)
    result=dict(mass_t=mass,**{k:moment[k]/mass if mass>0 else 0. for k in moment},FSM_tm=fsm,
                longitudinal_moment_tm=moment['LCG_m'],transverse_moment_tm=moment['TCG_m'],vertical_moment_tm=moment['KG_m'],
                item_count=len(items),positive_mass_items=sum(r['mass_t']>0 for r in items))
    result['KG_correction_m']=fsm/mass if mass>0 else 0.
    result['KG_effective_m']=result['KG_m']+result['KG_correction_m']
    return result


def calculate_case(items,lightweight):
    deadweight=aggregate(items)
    total=aggregate([lightweight]+items)
    if total['mass_t']<=0:raise ValueError('总重量必须大于0')
    groups=[dict(category=cat,**aggregate([r for r in items if r['category']==cat]))
            for cat in dict.fromkeys(r['category'] for r in items)]
    return dict(total=total,deadweight=deadweight,groups=groups,lightweight=lightweight)


def rounding_envelope(items):
    """按实际印刷小数位传播输入舍入范围；不修改数值去强行匹配总计。"""
    total=aggregate(items)
    dw=0.;dm={k:0. for k in ['LCG_m','TCG_m','KG_m']};df=0.
    for r in items:
        tok=r.get('numeric_tokens')
        if tok is None:raise ValueError('舍入传播需要原始打印字符')
        resolution=lambda s:.5*10**(-len(s.partition('.')[2])) if '.' in s else .5
        # 手册部分零重量舱仍指定非零FRSM，FSM的打印精度与质量是否为零无关。
        if r.get('last_column_kind')=='FRSM':df+=resolution(tok[-1])
        if r['mass_t']==0:continue  # 空舱不虚构隐藏的重量/质量矩。
        positions=[2,3,4] if len(tok)==6 else [1,2,3]
        e=resolution(tok[0]);dw+=e
        for key,j in zip(dm,positions):
            ec=resolution(tok[j]);dm[key]+=abs(r[key])*e+r['mass_t']*ec+e*ec
    result=dict(mass_t=dw,FSM_tm=df)
    for key in dm:
        # 位置可为负，四个端点覆盖分子/分母独立舍入范围。
        moment=total[dict(LCG_m='longitudinal_moment_tm',TCG_m='transverse_moment_tm',KG_m='vertical_moment_tm')[key]]
        if total['mass_t']>dw:
            endpoints=[(moment+sign*dm[key])/(total['mass_t']+sign2*dw) for sign in (-1,1) for sign2 in (-1,1)]
            result[key]=max(abs(v-total[key]) for v in endpoints)
        else:result[key]=0.
    return result


def comparisons(calculated,reference,items,case_id,level,page):
    bound=rounding_envelope(items)
    rows=[]
    for key in ['mass_t','LCG_m','TCG_m','KG_m','FSM_tm']:
        err=calculated[key]-reference[key]
        print_half=.05 if key=='mass_t' else .005
        if key=='FSM_tm':print_half=.005
        tol=bound[key]+print_half+1e-9
        rows.append(dict(case_id=case_id,level=level,metric=key,calculated=calculated[key],reference=reference[key],
                         difference=err,unit=UNITS[key],rounding_bound=tol,rounding_consistent=abs(err)<=tol,pdf_page=page))
    return rows


def load_geometry_item(spec):
    ident,fill,rho=spec['geometry_id'],float(spec['fill_fraction']),float(spec['density_t_m3'])
    if not math.isfinite(rho) or rho<=0:raise ValueError('密度须为正有限值')
    with np.load(DISPLAY/'compartments.npz') as mesh:
        calc=TankCalculator(mesh[ident+'_vertices'],mesh[ident+'_faces'])
    r=calc.at_fill(fill)
    return dict(id=spec.get('id',ident),description=spec.get('description',ident),category=spec.get('category','Geometry Load'),
                mass_t=r['volume_m3']*rho,LCG_m=r['CGX_m'] or 0.,TCG_m=r['CGY_m'] or 0.,KG_m=r['CGZ_m'] or 0.,
                FSM_tm=r['free_surface_IT_m4']*rho,data_source='geometry_05',geometry_id=ident,fill_fraction=fill,
                density_t_m3=rho,note='采用现有几何空间，边舱/双层底/端部包络不自动等同真实独立水舱')


def run_custom(path,output):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    items=[]
    for spec in data['items']:
        row=load_geometry_item(spec) if 'geometry_id' in spec else dict(spec)
        row.setdefault('FSM_tm',0.);row.setdefault('category','Custom Load');row.setdefault('data_source','user_case_input')
        items.append(row)
    light=dict(data['lightweight']);light.setdefault('FSM_tm',0.);light.setdefault('category','Lightweight')
    result=calculate_case(items,light)
    case_id=data.get('case_id','CUSTOM')
    target=output/'custom_cases'/re.sub(r'[^A-Za-z0-9_-]','_',case_id)
    target.mkdir(parents=True,exist_ok=True)
    save_json(target/'result.json',dict(case_id=case_id,items=items,**result))
    print(json.dumps(result['total'],ensure_ascii=False,indent=2))
    return target


def load_results(output=None,verify_sources=False):
    output=Path(output) if output else HERE/'output'
    data=json.loads((output/'loading_cases.json').read_text(encoding='utf-8'))
    if verify_sources:
        stage05=load_results_05()
        if digest(PROJECT/'05_舱室与舱容计算/output/loading_reference_catalog.json')!=data['source_stage05_sha256']:
            raise ValueError('05舱表已变化，请重算06')
        pdfs=list(PROJECT.glob('*.pdf'))
        if len(pdfs)!=1 or digest(pdfs[0])!=data['source_pdf_sha256']:raise ValueError('PDF已变化，请重算06')
    return data


def make_plots(output,cases):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(15,8),layout='constrained')
    x=np.arange(len(cases))
    for ax,key in zip(axes.flat,['mass_t','LCG_m','KG_m','KG_correction_m']):
        ax.plot(x,[c['calculated']['total'][key] for c in cases],'o-',ms=3,lw=1,label='Item summation')
        if key!='KG_correction_m':
            ax.plot(x,[c['reference']['total'][key] for c in cases],'x',ms=4,label='PDF summary')
        else:ax.plot(x,[c['overview']['GMCORR_m'] for c in cases],'x',ms=4,label='PDF overview')
        ax.set(ylabel=f'{key} ({UNITS[key]})',xticks=x[::2],xticklabels=[c['case_id'] for c in cases[::2]])
        ax.tick_params(axis='x',rotation=70,labelsize=8)
        ax.grid(alpha=.2)
        ax.legend(fontsize=8)
    fig.savefig(output/'weight_cg_comparison.png',dpi=150)
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--case-file',type=Path)
    args=parser.parse_args()
    output=HERE/'output';output.mkdir(exist_ok=True)
    if args.case_file:
        run_custom(args.case_file,output);return
    started=time.perf_counter()
    previous=load_results_05(verify_sources=True)
    pdfs=list(PROJECT.glob('*.pdf'))
    if len(pdfs)!=1:raise ValueError('Fsgy-surf根目录须有唯一参考PDF')
    ref=extract_reference(pdfs[0],output,previous['reference_catalog']['tanks'])
    overview={r['case_id']:r for r in ref['overview']}
    results,rows,group_rows,comparison_rows,correction_rows,full_fsm_rows=[],[],[],[],[],[]
    overview_checks=[]
    for c in ref['cases']:
        cid=c['case_id'];r=calculate_case(c['items'],c['lightweight']);o=overview[cid]
        checks=comparisons(r['total'],c['total'],[c['lightweight']]+c['items'],cid,'total',c['summary_page'])
        checks+=comparisons(r['deadweight'],c['deadweight'],c['items'],cid,'deadweight',c['summary_page'])
        for gr in c['groups']:
            included=[gr['category']]
            # 手册将钢卷计入PURP=MASS；显示仍单列钢卷，校核按原表组合。
            if gr['category']=='Mass Load' and any(i['category']=='Steel Coil' for i in c['items']):included.append('Steel Coil')
            group_items=[i for i in c['items'] if i['category'] in included]
            calculated=aggregate(group_items)
            label='Mass Load incl. Steel Coil' if len(included)>1 else gr['category']
            checks+=comparisons(calculated,gr,group_items,cid,label,c['summary_page'])
        comparison_rows.extend(checks)
        correction=r['total']['KG_correction_m']
        env=rounding_envelope([c['lightweight']]+c['items'])
        w,fsm=r['total']['mass_t'],r['total']['FSM_tm']
        correction_bound=max(abs((fsm+sf*env['FSM_tm'])/(w+sw*env['mass_t'])-correction) for sf in (-1,1) for sw in (-1,1))
        # PDF89页GMCORR为正的减稳性量；详细浮态栏则打印其负值。
        correction_rows.append(dict(case_id=cid,calculated_KG_correction_m=correction,
            reference_overview_m=o['GMCORR_m'],difference_m=correction-o['GMCORR_m'],
            printed_half_resolution_m=.0005,rounding_bound_m=correction_bound+.0005,
            rounding_consistent=abs(correction-o['GMCORR_m'])<=correction_bound+.0005+1e-9,
            reference_floating_signed_m=c['floating_reference']['GMCORR_signed_m'],
            detailed_floating_rounding_consistent=abs(correction+c['floating_reference']['GMCORR_signed_m'])<=correction_bound+.005+1e-9,pdf_page=89))
        floating=c['floating_reference']
        for key,a,b,tolerance in [('DISP_t',o['DISP_t'],c['total']['mass_t'],.100001),
                                   ('DWIT_t',o['DWIT_t'],c['deadweight']['mass_t'],.100001),
                                   ('T_m',o['T_m'],floating['T_m'],.001001),
                                   ('trim_m',o['trim_m'],floating['trim_m'],.001001),
                                   ('GM_m',o['GM_m'],floating['GM_m'],.005501),
                                   ('GMCORR_m',o['GMCORR_m'],-floating['GMCORR_signed_m'],.005501)]:
            overview_checks.append(dict(case_id=cid,metric=key,overview=a,detailed=b,difference=a-b,
                                        print_rounding_bound=tolerance,consistent=abs(a-b)<=tolerance,
                                        overview_page=89,detailed_page=c['summary_page']))
        for item in c['items']:
            enriched=dict(case_id=cid,**item,
                          longitudinal_moment_tm=item['mass_t']*item['LCG_m'],
                          transverse_moment_tm=item['mass_t']*item['TCG_m'],vertical_moment_tm=item['mass_t']*item['KG_m'])
            rows.append(enriched)
            if item['fill_percent']>=100 and item['FSM_tm']>0:full_fsm_rows.append(dict(case_id=cid,**item))
        group_rows.extend(dict(case_id=cid,**g) for g in r['groups'])
        results.append(dict(case_id=cid,description=c['description'],items=c['items'],calculated=r,
                            reference=dict(total=c['total'],deadweight=c['deadweight'],groups=c['groups'],
                                           floating_reference=c['floating_reference'],summary_page=c['summary_page']),
                            overview=o,rounding_consistent=all(v['rounding_consistent'] for v in checks)))
    steel_path=PROJECT/'02_母型计算几何重建/CAD_mesh/02_计算网格/steel_materials.json'
    steel=json.loads(steel_path.read_text(encoding='utf-8'))
    material=dict(data_source='02 partial steel material mesh; diagnostic only',source_sha256=digest(steel_path),
                  modeled_steel_mass_t=steel['total_mass_t'],modeled_steel_CG_m=steel['total_cg_m'],
                  adopted_lightship=results[0]['calculated']['lightweight'],
                  modeled_fraction_of_adopted_lightship=steel['total_mass_t']/results[0]['calculated']['lightweight']['mass_t'],
                  note='02仅计部分船壳/隔板；未计完整甲板、骨架、机械、上建等，不替代空船重量，且不再加到手册空船中。')
    save_json(output/'steel_weight_diagnostic.json',material)
    notes=[
        '采用PDF各工况逐项重量、该工况重心和FRSM作为输入；独立计算其加权合成与自由液面虚升。不是独立从舱室几何重新求全部小舱重量/重心。',
        '各工况的分类汇总、Deadweight、Total weight及PDF89页GMCORR只用于验证，不参与合成，不重复计入。',
        '空船采用PDF给出的8650t、LCG79.30m、TCG0m、KG10.20m；包含了手册空船的全部项目。02局部钢材重量只作覆盖诊断，不再次叠加。',
        '坐标AP/CL/BL，x向艏、Y正左舷、z向上。W=sum(w)，CG=sum(w*cg)/W，KGcorr=sum(FSM)/W，KG_effective=KG+KGcorr。',
        'FRSM为已选择的工况输入。部分FILL=100%的舱在手册仍保留非零FRSM，本复算照录；05几何模式的压满舱FSM=0，两种口径分开。',
        '部分零重量/空舱也被手册指定非零FRSM；它作为所采用的保守修正输入保留，不解释为当前空舱的物理液面。钢卷在原汇总计入PURP=MASS。',
        'GRM是粮食横倾矩，独立保存，绝不自动加进液体FSM。钢卷为按重量给定的载荷，其FILL=0不代表空舱。',
        'PDF重量通常只印至0.1t、坐标至0.01m，逐项合成与原软件未舍入总计可有小差异；校核传播打印舍入界限，不调整输入强制匹配。',
        'GMCORR在89页以正值表示减稳性量，详细浮态栏以负值打印；本程序KG_correction使用正值。尚未计算平衡吃水/纵倾或独立GM，这些属于07及以后。',
        '自定义几何载荷可调用05舱室积分，但几何范围仍继承05已接受的近似；实际散货堆形须另设，不能自动把水平液面当成散货堆形。',
    ]
    total_checks=[r for r in comparison_rows if r['level']=='total']
    metric_summary={k:dict(max_absolute_difference=max(abs(r['difference']) for r in total_checks if r['metric']==k),
                           unit=UNITS[k],count=sum(r['metric']==k for r in total_checks)) for k in ['mass_t','LCG_m','TCG_m','KG_m','FSM_tm']}
    report=dict(stage='06_重量与重心系统',source_pdf=ref['source_pdf'],source_pdf_sha256=ref['source_sha256'],
                source_stage05_sha256=digest(PROJECT/'05_舱室与舱容计算/output/loading_reference_catalog.json'),
                case_count=len(results),item_count=len(rows),comparison_count=len(comparison_rows),
                rounding_consistent_comparison_count=sum(r['rounding_consistent'] for r in comparison_rows),
                failed_comparisons=[r for r in comparison_rows if not r['rounding_consistent']],
                all_aggregation_checks_rounding_consistent=all(r['rounding_consistent'] for r in comparison_rows),
                free_surface_correction_checks=correction_rows,
                all_total_checks_rounding_consistent=all(r['rounding_consistent'] for r in total_checks),
                all_correction_checks_rounding_consistent=all(r['rounding_consistent'] for r in correction_rows),
                all_detailed_correction_checks_rounding_consistent=all(r['detailed_floating_rounding_consistent'] for r in correction_rows),
                overview_detailed_consistency_checks=overview_checks,
                reference_inconsistencies=[r for r in overview_checks if not r['consistent']],
                total_metric_summary=metric_summary,
                full_fill_nonzero_FSM_count=len(full_fsm_rows),full_fill_nonzero_FSM_rows=full_fsm_rows,
                zero_mass_nonzero_FSM_count=sum(r['mass_t']==0 and r['FSM_tm']>0 for r in rows),
                geometry_limitations_accepted=True,notes=notes,steel_diagnostic=material,
                elapsed_seconds=time.perf_counter()-started)
    data=dict(source_pdf=ref['source_pdf'],source_pdf_sha256=ref['source_sha256'],source_stage05_sha256=report['source_stage05_sha256'],
              data_source='pdf_case_inputs + independent mass moment aggregation',cases=results)
    save_json(output/'loading_cases.json',data)
    save_json(output/'verification_report.json',report)
    keys=['case_id','id','description','category','mass_t','fill_percent','density_t_m3','LCG_m','TCG_m','KG_m','FSM_tm',
          'grain_heeling_moment_tm','longitudinal_moment_tm','transverse_moment_tm','vertical_moment_tm','pdf_page','data_source']
    write_csv(output/'weight_items.csv',[{k:r.get(k) for k in keys} for r in rows])
    write_csv(output/'category_summary.csv',group_rows)
    write_csv(output/'case_summary.csv',[dict(case_id=c['case_id'],description=c['description'],**c['calculated']['total'],
                                              deadweight_t=c['calculated']['deadweight']['mass_t'],summary_pdf_page=c['reference']['summary_page']) for c in results])
    write_csv(output/'verification.csv',comparison_rows)
    write_csv(output/'free_surface_verification.csv',correction_rows)
    write_csv(output/'reference_overview_consistency.csv',overview_checks)
    np.savez_compressed(output/'weight_cg_data.npz',case_ids=np.array([c['case_id'] for c in results]),
                        **{k:np.array([c['calculated']['total'][k] for c in results]) for k in METRICS},
                        deadweight_t=np.array([c['calculated']['deadweight']['mass_t'] for c in results]))
    save_json(output/'equilibrium_inputs.json',dict(purpose='07浮态求解输入，参考浮态不参与重量计算',
        reference_basis='PDF detailed case tables; overview inconsistencies preserved in06 verification_report',
        data_source=data['data_source'],source_pdf_sha256=ref['source_sha256'],
        cases=[dict(case_id=c['case_id'],description=c['description'],**{k:c['calculated']['total'][k] for k in METRICS},
                    source_summary_page=c['reference']['summary_page'],reference_floating=c['reference']['floating_reference']) for c in results]))
    template=(HERE/'weight_cg_viewer.html').read_text(encoding='utf-8')
    (output/'weight_cg_verification.html').write_text(template.replace('__DATA__',json.dumps(dict(report=report,cases=results,names=NAMES,units=UNITS),ensure_ascii=False,allow_nan=False)),encoding='utf-8')
    make_plots(output,results)
    print(json.dumps(dict(cases=len(results),items=len(rows),rounding_consistent=f"{report['rounding_consistent_comparison_count']}/{len(comparison_rows)}",
                          max_total_differences=metric_summary,detail_correction_checks_passed=report['all_detailed_correction_checks_rounding_consistent'],
                          overview_correction_matches=sum(r['rounding_consistent'] for r in correction_rows),
                          reference_inconsistency_cases=sorted(set(r['case_id'] for r in report['reference_inconsistencies']))),ensure_ascii=False,indent=2))
    if args.preview:webbrowser.open((output/'weight_cg_verification.html').as_uri())


if __name__=='__main__':main()


