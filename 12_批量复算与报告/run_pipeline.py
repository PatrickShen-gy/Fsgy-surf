"""12：按阶段批算、运行测试、检查来源指纹，汇总全工况离线报告。

python run_pipeline.py --test                # 复用01—08，重算09—11并验证
python run_pipeline.py --report-only         # 只汇总已落盘结果
python run_pipeline.py --from-stage 3 --test # 从03开始重算至12
"""
from pathlib import Path
import sys,re,argparse,subprocess,time
from datetime import datetime,timezone,timedelta
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parent))
from pipeline_common import *

PROGRAMS={1:'read_dxf.py',2:'cad_reconstruction.py',3:'bonjean_calculation.py',4:'hydrostatics_calculation.py',5:'tank_capacity_calculation.py',
    6:'weight_cg_calculation.py',7:'equilibrium_calculation.py',8:'stability_calculation.py',9:'stability_criteria.py',10:'longitudinal_strength.py',11:'hold_mass_check.py'}
TESTS={2:'test_partitions.py',3:'test_bonjean.py',4:'test_hydrostatics.py',5:'test_tank_capacity.py',6:'test_weight_cg.py',7:'test_equilibrium.py',
       8:'test_stability.py',9:'test_criteria.py',10:'test_longitudinal.py',11:'test_hold_mass.py',12:'test_pipeline_report.py'}
STATUS={1:'输入提取已落盘',2:'主船体/主要分舱已建；上部浮力几何待补',3:'邦戎计算与局部差异已记录',4:'静水力计算与对照完成',
    5:'舱容计算已跑通；实际小舱细分未齐',6:'重量/重心合成在打印舍入范围内一致',7:'39工况浮态均收敛',8:'大倾角程序完成；完整船型高角度未复现',
    9:'开口/风压/基本限值诊断完成',10:'假设分布梁模型完成；局部CORR未实现',11:'图像外包络核对完成；临界/图域外保留',12:'批算、测试与汇总报告完成'}


def utc8_now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def freshness():
    result=[]
    mesh=stage(2)/'CAD_mesh/02_计算网格/calculation_mesh.npz'
    geom=stage(2)/'CAD_mesh/01_展示模型/compartments.npz'
    expected=[(3,'mesh_sha256',mesh),(4,'source_mesh_sha256',mesh),(5,'source_mesh_sha256',geom),
        (6,'source_stage05_sha256',stage(5)/'output/loading_reference_catalog.json'),
        (7,'source_weights_sha256',stage(6)/'output/equilibrium_inputs.json'),(7,'source_mesh_sha256',mesh),
        (8,'source_equilibrium_sha256',stage(7)/'output/stability_inputs.json'),(8,'source_mesh_sha256',mesh),
        (9,'sources.stage08',stage(8)/'output/stability_results.json'),(9,'sources.stage07',stage(7)/'output/equilibrium_results.json'),
        (10,'sources.stage06',stage(6)/'output/loading_cases.json'),(10,'sources.stage07',stage(7)/'output/equilibrium_results.json'),(10,'sources.mesh',mesh),
        (11,'source_stage06_sha256',stage(6)/'output/loading_cases.json'),(11,'source_stage07_sha256',stage(7)/'output/equilibrium_results.json')]
    pdfsha=digest(pdf_path())
    reports={n:read_json(stage(n)/'output/verification_report.json') for n in range(3,12)}
    for n,key,path in expected:
        value=reports[n]
        for part in key.split('.'):value=value.get(part) if isinstance(value,dict) else None
        actual=digest(path)
        result.append(dict(stage=n,input=str(path.relative_to(PROJECT)),recorded_sha256=value,current_sha256=actual,fresh=value==actual))
    for n,r in reports.items():
        value=r.get('source_pdf_sha256') or r.get('reference_sha256') or r.get('sources',{}).get('pdf')
        if value is not None:result.append(dict(stage=n,input=pdf_path().name,recorded_sha256=value,current_sha256=pdfsha,fresh=value==pdfsha))
    return result


def execute(n,filename,log_dir):
    script=stage(n)/filename
    if not script.exists():raise FileNotFoundError(script)
    print(f'[{n:02d}] {filename} ...',flush=True);start=time.perf_counter()
    command=[sys.executable,'-X','utf8',str(script)]
    r=subprocess.run(command,cwd=stage(n),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    log=log_dir/f'{n:02d}_{script.stem}.log';log.write_text(r.stdout,encoding='utf-8')
    match=re.search(r'Ran (\d+) tests?',r.stdout)
    outcome=dict(stage=n,script=filename,returncode=r.returncode,passed=r.returncode==0,
        test_count=int(match[1]) if match else None,elapsed_seconds=time.perf_counter()-start,log=str(log.relative_to(HERE/'output')),
        command=command)
    print(f'[{n:02d}] {"OK" if r.returncode==0 else "FAILED"} / {outcome["elapsed_seconds"]:.2f}s',flush=True)
    if r.returncode:print(r.stdout[-6000:],flush=True)
    return outcome


def build_report(test_results=None,run_results=None):
    out=HERE/'output';out.mkdir(exist_ok=True)
    fresh=freshness()
    reports={n:read_json(stage(n)/'output/verification_report.json') for n in range(3,12)}
    s06=read_json(stage(6)/'output/loading_cases.json');s07=read_json(stage(7)/'output/equilibrium_results.json')
    s08=read_json(stage(8)/'output/stability_results.json');s09=read_json(stage(9)/'output/criteria_results.json')
    s10=read_json(stage(10)/'output/longitudinal_results.json');s11=read_json(stage(11)/'output/hold_mass_results.json')
    indexes={n:{c['case_id']:c for c in data['cases']} for n,data in [(6,s06),(7,s07),(8,s08),(9,s09),(10,s10),(11,s11)]}
    ids=[c['case_id'] for c in s06['cases']]
    if any(set(index)!=set(ids) for index in indexes.values()):raise ValueError('阶段06—11工况集合不一致')
    tests=test_results if test_results is not None else read_json(out/'test_results.json') if (out/'test_results.json').exists() else []
    runs=run_results if run_results is not None else read_json(out/'run_results.json') if (out/'run_results.json').exists() else []
    summaries=[];case_views=[]
    for cid in ids:
        a,b,c,d,e,f=[indexes[n][cid] for n in [6,7,8,9,10,11]]
        pose=b['calculated'];ref=b['reference'];load=a['calculated']['total'];sr=c['summary'];weather=d['wind']
        row=dict(case_id=cid,description=a['description'],mass_t=load['mass_t'],LCG_m=load['LCG_m'],KG_m=load['KG_m'],
            T_m=pose['T_m'],T_difference_m=pose['T_m']-ref['T_m'],trim_m=pose['trim_m'],trim_difference_m=pose['trim_m']-ref['trim_m'],
            heel_deg=pose['heel_deg'],GM_m=pose['GM_m'],GZmax_sealed_hull_m=sr['GZmax_m'],AVS_sealed_hull_deg=sr['AVS_sealed_hull_deg'],
            first_deck_contact_sample_deg=sr['first_sampled_deck_immersion_deg'],flooding_diagnostic_deg=d['flooding_angle_deg'],
            weather_ratio_diagnostic=weather['ratio_b_a'],weather_ratio_reference=next(r['reference'] for r in d['checks'] if r['code']=='IMOWEATH.'),
            SF_max_t=e['extrema']['SF_max_t'],SF_min_t=e['extrema']['SF_min_t'],BM_hog_max_tm=e['extrema']['BM_hogging_max_tm'],BM_sag_min_tm=e['extrema']['BM_sagging_min_tm'],
            **{f'hold_{key}':value for key,value in f['status_counts'].items()},full_vessel_geometry_complete=False)
        summaries.append(row)
        pts=[p for p in c['points'] if p['angle_deg']<=75]
        case_views.append(dict(case_id=cid,description=a['description'],metrics={'总重量 / t':load['mass_t'],'吃水 / m':pose['T_m'],'纵倾 / m':pose['trim_m'],'GM / m':pose['GM_m']},
            notes=['计算链条已运行至12；GZ/天气为主船体诊断，纵强度含分布假设，许用载荷保留图域外与临界情况。'],
            tables={'跨阶段结果':[row],'基本稳性和天气':d['checks'],'纵强度站位':e['verification'],'许用货舱载荷':f['checks']},
            charts=[dict(title='GZ对照',x_label='横倾 / °',y_label='m',series=[dict(name='主船体诊断',x=[p['angle_deg'] for p in pts],y=[p['GZ_m'] for p in pts]),dict(name='PDF完整船型',x=[p['angle_deg'] for p in c['reference']['rows']],y=[p['GZ_m'] for p in c['reference']['rows']],dots=True)]),
                dict(title='纵向弯矩',x_label='X / m',y_label='t·m',series=[dict(name='假设分布梁模型',x=e['curve']['x_m'],y=e['curve']['BM_tm']),dict(name='PDF',x=[r['x_m'] for r in e['verification']],y=[r['reference_BM_tm'] for r in e['verification']],dots=True)])]))
    issues=[dict(id='UPPER_BUOYANCY',stages=[2,8,9],state='open',detail='缺少完整艉楼/艏楼/舱口围板浮力几何；高角度曲线、天气与限值仍是主船体诊断。'),
        dict(id='COMPARTMENT_DETAILS',stages=[2,5,6],state='accepted_approximation',detail='23主要空间不等于84实际舱室；06采用手册逐项工况重心，未冒充从小舱几何独立推得。'),
        dict(id='PLATING',stages=[4,7,8],state='open',detail='18mm仅作一阶排水体积修正；浮心/惯性矩未作完整外偏置。02钢材仍12mm。'),
        dict(id='LD09C_REFERENCE',stages=[6,7,8,9,10,11],state='preserved',detail='总览与详细表的重量差190t等不一致；后续采用详细载荷与浮态，原值保留。'),
        dict(id='WEATHER_R',stages=[9],state='sensitivity_reported',detail='按手册文字r≤1；LD00天气b/a约1.746，取消r上限敏感性约0.992，参考0.979。主假设未按结果拟合。'),
        dict(id='LONGITUDINAL_DISTRIBUTION',stages=[10],state='assumed',detail='空船/舱载荷分布形状不完整，采用守恒质量矩的明确假设，未实现局部CORR剪力修正。'),
        dict(id='HOLD_IMAGE_LIMITS',stages=[11],state='digitized_diagnostic',detail='许用曲线来自图片标注，外包络读图、双层底耦合和双舱吃水位置约定保留；92项图域外、3项临界带未判为合格。'),
        dict(id='LIMIT_TABLE_ANOMALIES',stages=[10],state='preserved',detail='15页FR34负弯矩限值和FR220负的“POSITIVE”剪力限值均照原值保存。')]
    inventory=[]
    for n in range(1,13):
        entry=stage(n)/(PROGRAMS[n] if n<12 else 'run_pipeline.py')
        inventory.append(dict(stage=n,directory=stage(n).name,entry=str(entry.relative_to(PROJECT)),program_exists=entry.exists(),status=STATUS[n]))
    manifest=dict(generated_at=utc8_now(),timezone='Asia/Shanghai',stages=inventory,sources_fresh=all(r['fresh'] for r in fresh),freshness_checks=fresh,
        programs=[dict(stage=n,path=str((stage(n)/(PROGRAMS[n] if n<12 else 'run_pipeline.py')).relative_to(PROJECT)),sha256=digest(stage(n)/(PROGRAMS[n] if n<12 else 'run_pipeline.py'))) for n in range(1,13)],
        common_sha256=digest(PROJECT/'pipeline_common.py'),source_pdf_sha256=digest(pdf_path()),python_version=sys.version,
        tests=tests,runs=runs,case_ids=ids,case_count=len(ids),full_pdf_reproduction_complete=False)
    status=dict(stages_with_programs=sum(r['program_exists'] for r in inventory),case_count=39,sources_fresh=manifest['sources_fresh'],
        tests_passed=all(r['passed'] for r in tests) if tests else None,test_count=sum(r['test_count'] or 0 for r in tests),
        computation_pipeline_complete=True,full_pdf_reproduction_complete=False,known_issue_count=len(issues))
    save_json(out/'case_results.json',summaries);write_csv(out/'case_comparison.csv',summaries)
    save_json(out/'known_issues.json',issues);save_json(out/'reproducibility_manifest.json',manifest);save_json(out/'pipeline_status.json',status)
    write_csv(out/'stage_inventory.csv',inventory);write_csv(out/'freshness_checks.csv',fresh)
    notes=['“计算链条跑通”与“完整复现PDF全部结果”分开记录，当前后者仍为false。总览没有把接受的近似或缺失几何改成校核通过。',
        '默认一键运行09—11并重建12报告，复用01—08缓存；--from-stage 3可以重算03以后，--report-only只汇总。--test运行02—12测试并保存日志。',
        '12会核对各阶段来源SHA256，过期输入不会被标为最新结果。没有从结果反推重量分布或缩放几何来消除误差。']+[i['detail'] for i in issues]
    overview=dict(case_id='OVERVIEW',description='12阶段与来源状态',metrics={'工况数':39,'程序阶段':status['stages_with_programs'],'测试项':status['test_count'],'来源新鲜':status['sources_fresh']},
        notes=['现有程序已连续运行至12；完整PDF复现仍受列出的几何和输入假设限制。'],
        tables={'阶段状态':inventory,'已知限制':issues,'来源检查':fresh,'测试记录':tests,'全部工况汇总':summaries},
        charts=[dict(title='型吃水对照',x_label='工况序号',y_label='m',series=[dict(name='网格求解',x=list(range(39)),y=[r['T_m'] for r in summaries]),dict(name='PDF详细工况',x=list(range(39)),y=[indexes[7][cid]['reference']['T_m'] for cid in ids],dots=True)])])
    links={'跨阶段CSV':'case_comparison.csv','可复现清单':'reproducibility_manifest.json','已知限制':'known_issues.json','报告Markdown':'report.md'}
    filenames={3:'bonjean_verification.html',4:'hydrostatics_verification.html',5:'tank_capacity_verification.html',6:'weight_cg_verification.html',7:'equilibrium_verification.html',8:'stability_verification.html',9:'criteria_verification.html',10:'longitudinal_verification.html',11:'hold_mass_verification.html'}
    for n,name in filenames.items():links[f'{n:02d}详情']=f'../../{stage(n).name}/output/{name}'
    links['02展示模型']=f'../../{stage(2).name}/CAD_mesh/01_展示模型/ship_display.html'
    write_dashboard(out/'report.html','01—12 · 母型船计算总报告','重量、静水力、浮态、稳性、纵强度与局部载荷的全工况计算链条。',[overview]+case_views,notes,
        stats={'工况':39,'阶段程序':12,'测试项':status['test_count'],'完整PDF复现':'尚未完成'},links=links)
    md=['# 母型船01—12计算总报告','',f'生成时间：{manifest["generated_at"]}（Asia/Shanghai）','',
        '**计算程序与批算链条已运行至12；完整PDF全部数值尚未复现。** 原因和范围均在下表及已知限制中保留。','',
        f'共39个工况；当前来源指纹检查：{"全部一致" if status["sources_fresh"] else "存在过期数据"}。自动测试：{status["test_count"]}项，状态{status["tests_passed"]}。','',
        '| 阶段 | 状态 |','|---|---|']+[f'| {r["stage"]:02d} | {r["status"]} |' for r in inventory]
    md+=['','## 主要量化结果','',
        f'- 06：1945项重量/重心/FSM对照在打印舍入传播范围内一致。',
        f'- 07：39工况均收敛；吃水最大差{reports[7]["metric_summary"]["T_m"]["max_absolute_difference"]:.5f}m，纵倾最大差{reports[7]["metric_summary"]["trim_m"]["max_absolute_difference"]:.5f}m。',
        f'- 08：2184个姿态；未触封顶的146个参考点GZ平均绝对差{reports[8]["GZ_before_first_deck_contact"]["mean_absolute_difference_m"]:.5f}m，全部参考点为{reports[8]["GZ_all_reference_points"]["mean_absolute_difference_m"]:.5f}m。',
        '- 09：273项基本稳性/天气对照，四个开口的进水角及85点零纵倾参考限值均已保存；结果为主船体/风侧近似诊断。',
        f'- 10：624站位对照；剪力平均绝对差{reports[10]["SF_difference"]["mean_abs"]:.2f}t，弯矩平均绝对差{reports[10]["BM_difference"]["mean_abs"]:.2f}t·m。端部弯矩最大闭合残差{reports[10]["max_end_BM_tm"]:.3f}t·m。',
        f'- 11：702项检查，状态计数：{reports[11]["status_counts"]}。','',
        '## 已知限制与源表差异','']+[f'- **{i["id"]}**：{i["detail"]}' for i in issues]
    md+=['','## 使用','',
        '从12目录运行：`python run_pipeline.py --test`。只更新报告：`python run_pipeline.py --report-only`。从03开始重算：`python run_pipeline.py --from-stage 3 --test`。','',
        '打开`output/report.html`选择工况查看各阶段结果；`case_comparison.csv`为跨阶段数值汇总，`reproducibility_manifest.json`记录程序/来源指纹和运行日志。','',
        '本项目核对的是所提供历史装载手册，不构成现行法规认证、结构审批或完整船型稳性校核结论。']
    (out/'report.md').write_text('\n'.join(md),encoding='utf-8')
    return status


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--from-stage',type=int,default=9);p.add_argument('--to-stage',type=int,default=12)
    p.add_argument('--report-only',action='store_true');p.add_argument('--test',action='store_true');args=p.parse_args()
    if not 1<=args.from_stage<=args.to_stage<=12:raise ValueError('阶段范围必须在01—12内')
    out=HERE/'output';out.mkdir(exist_ok=True);logs=out/'logs';logs.mkdir(exist_ok=True)
    runs=[]
    if not args.report_only:
        for n in range(args.from_stage,min(args.to_stage,11)+1):
            r=execute(n,PROGRAMS[n],logs);runs.append(r);save_json(out/'run_results.json',runs)
            if not r['passed']:raise RuntimeError(f'阶段{n}失败，详见日志')
    tests=[]
    if args.test:
        for n,filename in TESTS.items():
            if n==12:continue
            tests.append(execute(n,filename,logs))
        save_json(out/'test_results.json',tests)
    status=build_report(tests if args.test else None,runs if not args.report_only else None)
    if args.test:
        tests.append(execute(12,TESTS[12],logs));save_json(out/'test_results.json',tests)
        status=build_report(tests,runs if not args.report_only else None)
        if not all(r['passed'] for r in tests):raise RuntimeError('存在失败测试，详见test_results和日志')
    print(json.dumps(status,ensure_ascii=False,indent=2))
    print('Report:',out/'report.html')


if __name__=='__main__':main()
