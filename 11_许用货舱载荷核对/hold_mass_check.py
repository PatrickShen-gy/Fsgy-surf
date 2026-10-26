"""11：将PDF图片中标注的分段包络转录为数据，核对单舱/相邻双舱载荷。

严禁对图域外吃水无标记外推；读图容差只区分边界不确定度，不是许用超载。
"""
from pathlib import Path
import sys,re,argparse,webbrowser
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parent))
from pipeline_common import *

# 下列数值来自334—340页图片的标注端点，已逐图视觉核对。
# sea: 初始上限、10.27处线性上限、水平封顶；harbour: 初始/末端上限、是否平台。
DIGITIZED=[
 dict(id='H1',holds=[1],page=334,image_index=0,sea0=4108.,sea_end=9034.,sea_cap=8554.,harbour0=5391.,harbour_end=10317.,knee=None,sea_min_start=8.524,sea_min_end=837.,harbour_min_start=10.27,harbour_min_end=0.),
 dict(id='H2',holds=[2],page=334,image_index=1,sea0=5131.,sea_end=9982.,sea_cap=9982.,harbour0=6628.,harbour_end=11479.,knee=6.8809,sea_min_start=10.27,sea_min_end=0.,harbour_min_start=10.27,harbour_min_end=0.),
 dict(id='H3',holds=[3],page=335,image_index=0,sea0=6294.,sea_end=13544.,sea_cap=12824.,harbour0=8218.,harbour_end=15467.,knee=None,sea_min_start=8.524,sea_min_end=1232.,harbour_min_start=10.27,harbour_min_end=0.),
 dict(id='H4',holds=[4],page=335,image_index=1,sea0=5137.,sea_end=9994.,sea_cap=9994.,harbour0=6636.,harbour_end=11493.,knee=6.8809,sea_min_start=10.27,sea_min_end=0.,harbour_min_start=10.27,harbour_min_end=0.),
 dict(id='H5',holds=[5],page=336,image_index=0,sea0=5738.,sea_end=12215.,sea_cap=11566.,harbour0=7473.,harbour_end=13949.,knee=None,sea_min_start=8.524,sea_min_end=1101.,harbour_min_start=10.27,harbour_min_end=0.),
 dict(id='H12',holds=[1,2],page=337,image_index=0,sea0=11812.,sea_end=19963.,sea_cap=19963.,harbour0=14807.,harbour_end=22958.,knee=6.881,sea_min_start=7.703,sea_min_end=3042.,harbour_min_start=10.230,harbour_min_end=47.),
 dict(id='H23',holds=[2,3],page=338,image_index=0,sea0=10255.,sea_end=19963.,sea_cap=19963.,harbour0=13250.,harbour_end=22958.,knee=6.881,sea_min_start=7.703,sea_min_end=3622.,harbour_min_start=9.825,harbour_min_end=628.),
 dict(id='H34',holds=[3,4],page=339,image_index=0,sea0=10273.,sea_end=19987.,sea_cap=19987.,harbour0=13271.,harbour_end=22986.,knee=6.881,sea_min_start=7.703,sea_min_end=3625.,harbour_min_start=9.826,harbour_min_end=627.),
 dict(id='H45',holds=[4,5],page=340,image_index=0,sea0=10791.,sea_end=19987.,sea_cap=19987.,harbour0=13798.,harbour_end=22986.,knee=6.881,sea_min_start=7.703,sea_min_end=3431.,harbour_min_start=9.946,harbour_min_end=433.)]
SPANS={1:(140.2,165.),2:(111.4,140.2),3:(82.6,111.4),4:(53.8,82.6),5:(25.8,53.8)}


def envelope(curve,draft,mode):
    if not 0<=draft<=10.27:return None,None
    if mode=='seagoing':
        end=curve['knee'] or 10.27
        maximum=min(curve['sea_cap'],curve['sea0']+(curve['sea_end']-curve['sea0'])*min(draft,end)/end)
        start=curve['sea_min_start'];last=curve['sea_min_end']
    elif mode=='harbour':
        end=curve['knee'] or 10.27
        maximum=curve['harbour0']+(curve['harbour_end']-curve['harbour0'])*min(draft,end)/end
        start=curve['harbour_min_start'];last=curve['harbour_min_end']
    else:raise ValueError('mode必须是seagoing或harbour')
    minimum=0. if draft<=start or last==0 else last*(draft-start)/(10.27-start)
    return float(minimum),float(maximum)


def classify(mass,minimum,maximum,uncertainty=5.):
    if minimum is None:return 'outside_chart_domain'
    if minimum<=mass<=maximum:return 'within_digitized_envelope'
    if minimum-uncertainty<=mass<=maximum+uncertainty:return 'boundary_digitization_band'
    return 'outside_digitized_envelope'


def source_audit(out):
    ref=out/'reference';ref.mkdir(exist_ok=True);records=[]
    with pymupdf.open(pdf_path()) as d:
        for curve in DIGITIZED:
            info=d[curve['page']-1].get_image_info(xrefs=True)[curve['image_index']]
            image=d.extract_image(info['xref']);name=f'source_p{curve["page"]}_{curve["image_index"]+1}.{image["ext"]}'
            (ref/name).write_bytes(image['image'])
            records.append(dict(**curve,source_image=name,source_image_sha256=digest(ref/name),
                digitization_method='manual transcription of printed breakpoints + visual line interpretation',
                domain_draft_m=[0.,10.27],mass_unit='t',uncertainty_band_t=5.,
                harbour_minimum_single_holds='zero from visible hatched region; not separately printed' if len(curve['holds'])==1 else 'printed green endpoint and onset',
                unresolved_issue='figure families/double-bottom ballast effects not independently reconstructed'))
    result=dict(source_pdf_sha256=digest(pdf_path()),curves=records,
        note='转录图片标注；5t仅用于识别读图/打印临界带，不改变原包络，不表示可超载5t。')
    save_json(ref/'digitized_envelopes.json',result)
    body=['<!doctype html><meta charset="utf-8"><title>许用载荷曲线读图审计</title><style>body{font:15px/1.6 system-ui;max-width:1150px;margin:30px auto;background:#f2f6f7}section{background:white;padding:20px;margin:20px 0}img{max-width:100%}pre{white-space:pre-wrap}</style><h1>PDF许用载荷曲线 · 原图与转录端点</h1>']
    for r in records:
        body.append(f'<section><h2>{r["id"]} · PDF第{r["page"]}页</h2><img src="{r["source_image"]}"><pre>{json.dumps(r,ensure_ascii=False,indent=2)}</pre></section>')
    (ref/'digitization_audit.html').write_text('\n'.join(body),encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preview',action='store_true');args=p.parse_args()
    out=HERE/'output';out.mkdir(exist_ok=True);source=source_audit(out)
    loads=read_json(stage(6)/'output/loading_cases.json');eq=read_json(stage(7)/'output/equilibrium_results.json');poses={c['case_id']:c['calculated'] for c in eq['cases']}
    all_rows=[];cases=[];dash=[]
    for c in loads['cases']:
        cid=c['case_id'];pose=poses[cid];rows=[];charts=[]
        for curve in DIGITIZED:
            hs=curve['holds'];a=min(SPANS[h][0] for h in hs);b=max(SPANS[h][1] for h in hs);x=(a+b)/2
            draft=pose['T_m']+pose['trim_m']/172*(x-86)
            payload=sum(i['mass_t'] for i in c['items'] if i['id'] in [f'CH{h}' for h in hs])
            ballast=sum(i['mass_t'] for i in c['items'] if i['id'] in [f'WBT{h}{s}' for h in hs for s in ['P','S']])
            for mode in ['seagoing','harbour']:
                lo,hi=envelope(curve,draft,mode)
                rows.append(dict(curve_id=curve['id'],holds=hs,mode=mode,evaluation_x_m=x,local_draft_m=draft,hold_payload_t=payload,
                    associated_WBT_mass_t=ballast,minimum_t=lo,maximum_t=hi,
                    lower_margin_t=None if lo is None else payload-lo,upper_margin_t=None if hi is None else hi-payload,
                    status=classify(payload,lo,hi),pdf_page=curve['page'],uncertainty_band_t=5.,
                    scope='cargo-hold contents; WBT not silently added',pair_midpoint_assumed=len(hs)>1,
                    ballast_dependence_needs_review=ballast>0))
            xx=np.unique(np.r_[np.linspace(0,10.27,100),curve['knee'] or [],curve['sea_min_start'],curve['harbour_min_start']])
            series=[]
            for mode,color in [('seagoing','#088e7f'),('harbour','#5e86b0')]:
                bounds=[envelope(curve,float(t),mode) for t in xx]
                series.extend([dict(name=mode+' upper',x=xx,y=[v[1] for v in bounds],color=color),dict(name=mode+' lower',x=xx,y=[v[0] for v in bounds],color=color,dash=True)])
            series.append(dict(name='current hold contents',x=[draft],y=[payload],dots=True,color='#bb7546'))
            charts.append(dict(title=curve['id']+' 许用包络',x_label='局部吃水 / m',y_label='货舱载荷 / t',series=series))
        count={s:sum(r['status']==s for r in rows) for s in ['within_digitized_envelope','boundary_digitization_band','outside_digitized_envelope','outside_chart_domain']}
        cases.append(dict(case_id=cid,checks=rows,status_counts=count))
        dash.append(dict(case_id=cid,description=c['description'],metrics=count,
            notes=['同时给出海上/港内两种包络，不按工况名称自动认定运行模式。局部吃水超过10.27m时标为图域外，不外推。水舱/双层底载荷耦合尚需复核。'],tables={'全部检查':rows},charts=charts))
        all_rows.extend(dict(case_id=cid,**r) for r in rows)
    counts={s:sum(r['status']==s for r in all_rows) for s in ['within_digitized_envelope','boundary_digitization_band','outside_digitized_envelope','outside_chart_domain']}
    report=dict(stage=11,case_count=39,curves=9,checks=len(all_rows),status_counts=counts,
        source_pdf_sha256=source['source_pdf_sha256'],source_stage06_sha256=digest(stage(6)/'output/loading_cases.json'),source_stage07_sha256=digest(stage(7)/'output/equilibrium_results.json'),
        digitized_image_bounds=True,independent_structural_derivation=False,
        notes=['端点来自图片标注，不是结构强度理论反推；港内单舱最小值按可见区域取0并记录解释。','5t临界带仅反映读图/舍入，不能把超出包络的临界点宣布合格。',
               '按16页要求采用舱段中点局部吃水；双舱采用合并纵向范围中点，此为明确约定。','货舱内所有CH载荷均计入，包括以货舱装水的手册工况；边压载舱质量另列，不因归类将水舱重量漏计或混同。',
               '图内灰色曲线族及双层底压载依赖没有独立推导，当前主要核对外包络；有相关压载时标明需复核。','海上和港内同时计算，超图域、临界带、明确图内/图外分别报告，不用截断吃水伪造可核对结论。'])
    save_json(out/'hold_mass_results.json',dict(cases=cases,report=report));save_json(out/'verification_report.json',report)
    write_csv(out/'hold_mass_checks.csv',all_rows)
    write_dashboard(out/'hold_mass_verification.html','11 · 单舱与相邻双舱许用载荷','9条图像包络 × 海上/港内 × 39工况；边界值和图域外情况显式保留。',dash,report['notes'],stats={'工况':39,'曲线':9,'检查项':len(all_rows)},
        links={'CSV':'hold_mass_checks.csv','原图读图审计':'reference/digitization_audit.html','报告':'verification_report.json'})
    print('11 completed',counts)
    if args.preview:webbrowser.open((out/'hold_mass_verification.html').as_uri())


if __name__=='__main__':main()
