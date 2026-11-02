"""Read-only, dedicated report for geometry and performance stages 01-05."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import html
import math
import os

from fsgy_surf.io.files import read_json
from fsgy_surf.reporting.html import chart, table

DIMENSIONS = [('loa_m', '总长 LOA / m'), ('lbp_m', '垂线间长 LBP / m'),
              ('beam_m', '型宽 B / m'), ('depth_m', '型深 D / m'),
              ('design_draft_m', '设计吃水 / m'), ('scantling_draft_m', '结构吃水 / m')]
PURPOSES = {'cargo': '货舱', 'ballast_water': '压载水', 'fresh_water': '淡水',
            'fuel_oil': '燃油', 'lubricating_oil': '滑油', 'machinery': '机舱',
            'void': '空舱', 'unassigned': '未指定用途'}
HYDRO = [('DISP', '排水量', 't'), ('VOLM', '型排水体积', 'm³'),
         ('WLA', '水线面投影面积', 'm²'), ('WSA', '湿表面积', 'm²'),
         ('LCB', '纵向浮心 LCB', 'm'), ('VCB', '垂向浮心 VCB', 'm'),
         ('LCA', '纵向漂心 LCF', 'm'), ('KMT', '横稳心高度 KMT', 'm'),
         ('KML', '纵稳心高度 KML', 'm'), ('TCP', '每厘米吃水吨数 TPC', 't/cm'),
         ('MCT', '每厘米纵倾力矩 MCT', 't·m/cm'), ('CB', '方形系数 CB', '—'),
         ('CM', '中横剖面系数 CM', '—'), ('CP', '棱形系数 CP', '—'),
         ('CW', '水线面系数 CW', '—')]


def escape(value):
    return html.escape(str(value), quote=True)


def required(path):
    if not path.is_file():
        raise FileNotFoundError(f'缺少计算产物：{path}。请先通过 main.py 完成 01—05 计算。')
    return path


def read_table(path):
    def number(value):
        if value == '':
            return None
        if value in ('True', 'False'):
            return value == 'True'
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except ValueError:
            return value
    with required(path).open(encoding='utf-8-sig', newline='') as stream:
        return [{key: number(value) for key, value in row.items()} for row in csv.DictReader(stream)]


def select_panels(group, items, selected=0):
    options, panels = [], []
    for i, (label, body) in enumerate(items):
        options.append(f'<option value="{i}"{" selected" if i == selected else ""}>{escape(label)}</option>')
        panels.append(f'<div data-panel="{group}-{i}"{" hidden" if i != selected else ""}>{body}</div>')
    return (f'<label class="selector">选择结果 <select data-switch="{group}">'
            + ''.join(options) + '</select></label>' + ''.join(panels))


def plot(title, xs, ys, xlabel, ylabel):
    return '<div class="plot">'+chart(title, [(title, xs, ys)], xlabel, ylabel)+'</div>'


def generate_report(case_path, output=None):
    case_path = Path(case_path).resolve()
    if case_path.is_dir():
        case_path /= 'case.json'
    config = read_json(required(case_path))
    results = (case_path.parent/config.get('results_dir', 'results')).resolve()
    output = Path(output).resolve() if output is not None else results/'report_01_05.html'
    mother = read_json(required(results/'01/mother_ship.json'))
    geometry = read_json(required(results/'02/geometry.json'))
    hydro = read_json(required(results/'04/hydrostatics.json'))
    capacities = read_json(required(results/'05/capacities.json'))['compartments']
    bonjean = read_table(results/'03/bonjean.csv')
    hydro_rows = read_table(results/'04/hydrostatics.csv')
    filling = read_table(results/'05/filling_curves.csv')
    levels = read_table(results/'05/level_curves.csv')

    def link(path, label):
        relative = os.path.relpath(required(path), output.parent).replace(os.sep, '/')
        return f'<a href="{escape(relative)}">{escape(label)}</a>'

    def section(number, title, body):
        return f'<section id="stage-{number:02d}"><div class="section-title"><span>{number:02d}</span><h2>{title}</h2></div>{body}</section>'

    sections = []
    source_rows = []
    role_names = {'lines': '型线图', 'general_arrangement': '总布置图', 'typical_section': '典型横剖面图'}
    for role, filename in mother['drawings'].items():
        source_rows.append({'图纸用途': role_names.get(role, role), '文件': Path(filename).name})
    dimensions = table([{'参数': label, '母型': mother['dimensions'].get(key),
                         '设计船型': geometry['dimensions'].get(key)} for key, label in DIMENSIONS])
    body = '<h3>输入图纸</h3>'+table(source_rows)+'<h3>主尺度</h3>'+dimensions
    body += '<p class="note">主尺度取自已保存的计算结果。坐标原点为 AP／CL／BL；X 向艏、Y 向左舷、Z 自基线向上。长度单位为 m。</p>'
    body += '<p>'+link(results/'01/mother_ship.json', '母型输入数据 JSON')+'</p>'
    sections.append(section(1, '输入与主尺度', body))

    sx, sy, sz = geometry['scale_xyz']
    body = table([{'纵向缩放': sx, '横向缩放': sy, '垂向缩放': sz,
                   '空间区块数': len(geometry['compartments']),
                   '已建钢结构质量 / t': geometry['structure_mass_t']}])
    viewer = link(results/'02/ship.html', '独立打开三维模型')
    viewer_url = escape(os.path.relpath(results/'02/ship.html', output.parent).replace(os.sep, '/'))
    body += f'<p>{viewer}</p><iframe class="model" src="{viewer_url}" title="三维船体与空间分舱" loading="lazy"></iframe>'
    space_rows = []
    for block in geometry['compartments']:
        center = block['center_m']
        space_rows.append({'编号': block['id'], '名称': block['name'],
            '用途': PURPOSES.get(block['purpose'], block['purpose']),
            '起点 X / m': block['start_m'], '终点 X / m': block['end_m'],
            '容积 / m³': block['volume_m3'], 'CGX / m': center[0], 'CGY / m': center[1], 'CGZ / m': center[2]})
    body += '<h3>空间区块</h3>'+table(space_rows)
    body += '<p class="note">空间边界和用途继承 02 的重建模型。已建钢结构质量包含船壳及主隔板，作为所建材料域的质量输出。</p>'
    body += '<p>'+link(results/'02/spatial_blocks.csv', '空间区块 CSV')+' · '+link(results/'02/steel_materials.csv', '钢结构材料 CSV')+'</p>'
    sections.append(section(2, '三维船型与空间划分', body))

    by_station = defaultdict(list)
    for row in bonjean:
        by_station[row['x_m']].append(row)
    items = []
    for x, rows in sorted(by_station.items()):
        rows.sort(key=lambda row: row['draft_m'])
        drafts = [row['draft_m'] for row in rows]
        body = '<div class="plots">'+plot('横剖面积', drafts, [r['area_m2'] for r in rows], '吃水 / m', '面积 / m²')
        body += plot('对基线的一次面积矩', drafts, [r['moment_bl_m3'] for r in rows], '吃水 / m', '面积矩 / m³')+'</div>'
        body += table([{'吃水 / m': r['draft_m'], '面积 / m²': r['area_m2'],
            '基线面积矩 / m³': r['moment_bl_m3'], '剖面中心 Z / m': r['centroid_z_m'],
            '水线宽 / m': r['waterline_breadth_m']} for r in rows])
        items.append((f'X = {x:g} m', body))
    stations = sorted(by_station)
    selected = min(range(len(stations)), key=lambda i: abs(stations[i]-geometry['dimensions']['lbp_m']/2))
    body = f'<p>共 {len(stations)} 个站位、{len(bonjean)} 组面积和面积矩结果。</p>'
    body += select_panels('bonjean', items, selected)+'<p>'+link(results/'03/bonjean.csv', '完整邦戎 CSV')+'</p>'
    sections.append(section(3, '邦戎曲线', body))

    point = hydro['design_point']
    body = f'<p>设计吃水 {point["draft_m"]:g} m，纵倾 {point["trim_m"]:g} m；水密度 {hydro["water_density_t_m3"]:g} t/m³，排水量一阶外板修正厚度 {hydro["hydro_shell_thickness_m"]*1000:g} mm。</p>'
    body += table([{'指标': label, '设计吃水计算值': point.get(key), '单位': unit} for key, label, unit in HYDRO])
    by_trim = defaultdict(list)
    for row in hydro_rows:
        by_trim[row['trim_m']].append(row)
    items = []
    plot_keys = {'DISP', 'VOLM', 'KMT', 'KML', 'LCB', 'WSA'}
    for trim, rows in sorted(by_trim.items()):
        rows.sort(key=lambda row: row['draft_m'])
        body_trim = '<div class="plots">'
        for key, label, unit in HYDRO:
            if key in plot_keys:
                body_trim += plot(label, [r['draft_m'] for r in rows], [r[key] for r in rows], '吃水 / m', unit)
        body_trim += '</div>'+table([dict({'吃水 / m': row['draft_m']}, **{
            f'{label} / {unit}': row.get(key) for key, label, unit in HYDRO},
            **{'水面到达封顶': '是' if row.get('deck_limit_reached') else '否'}) for row in rows])
        items.append((f'纵倾 TF−TA = {trim:g} m', body_trim))
    body += '<h3>各吃水曲线与结果表</h3>'+select_panels('hydro', items)
    body += '<p>'+link(results/'04/hydrostatics.csv', '完整静水力 CSV')+'</p>'
    sections.append(section(4, '静水力', body))

    by_fill, by_level = defaultdict(list), defaultdict(list)
    for row in filling:
        by_fill[row['geometry_id']].append(row)
    for row in levels:
        by_level[row['geometry_id']].append(row)
    body = table([{'编号': c['id'], '名称': c['name'], '容积 / m³': c['volume_m3'],
        'CGX / m': c['CGX_m'], 'CGY / m': c['CGY_m'], 'CGZ / m': c['CGZ_m'],
        '底高 / m': c['bottom_m'], '顶高 / m': c['top_m'],
        '最大自由液面 IT / m⁴': c['maximum_free_surface_IT_m4']} for c in capacities])
    items = []
    for capacity in capacities:
        ident = capacity['id']
        fills = sorted(by_fill[ident], key=lambda row: row['requested_fill_fraction'])
        liquid = sorted(by_level[ident], key=lambda row: row['level_BL_m'])
        detail = '<div class="plots">'+plot('液位与舱容', [r['level_BL_m'] for r in liquid],
            [r['volume_m3'] for r in liquid], '液位 Z / m', '容积 / m³')
        detail += plot('装载率与重心高度', [r['requested_fill_fraction']*100 for r in fills],
            [r['CGZ_m'] for r in fills], '装载率 / %', 'CGZ / m')
        detail += plot('自由液面惯性矩', [r['level_BL_m'] for r in liquid],
            [r['free_surface_IT_m4'] for r in liquid], '液位 Z / m', 'IT / m⁴')+'</div>'
        detail += table([{'装载率 / %': r['requested_fill_fraction']*100, '液位 / m': r['level_BL_m'],
            '容积 / m³': r['volume_m3'], 'CGX / m': r['CGX_m'], 'CGY / m': r['CGY_m'], 'CGZ / m': r['CGZ_m'],
            '自由液面 IT / m⁴': r['free_surface_IT_m4']} for r in fills])
        items.append((f'{ident} · {capacity["name"]}', detail))
    body += '<h3>逐区块装载曲线</h3>'+select_panels('capacity', items)
    body += '<p class="note">装载曲线按水平液面计算；自由液面惯性矩表示区块装液体时的几何属性。空舱的重心栏留空。</p>'
    body += '<p>'+link(results/'05/capacities.csv', '舱容 CSV')+' · '+link(results/'05/filling_curves.csv', '装载曲线 CSV')+' · '+link(results/'05/level_curves.csv', '液位曲线 CSV')+'</p>'
    sections.append(section(5, '舱容、重心与自由液面', body))

    title = f'{config.get("name", case_path.parent.name)} · 01—05 计算报告'
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    nav = ''.join(f'<a href="#stage-{i:02d}">{i:02d} {name}</a>' for i, name in enumerate(
        ['输入', '三维分舱', '邦戎', '静水力', '舱容'], 1))
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>' + STYLE + '</style></head><body>'
        f'<header><div class="eyebrow">FSGY-SURF · CALCULATION RESULTS</div><h1>{escape(title)}</h1>'
        f'<p>母型输入 → 船型与空间 → 邦戎 → 静水力 → 舱容</p><small>根据已有计算结果生成 · {timestamp}（Asia/Shanghai）</small></header>'
        f'<nav>{nav}</nav><main>{"".join(sections)}</main>'
        '<footer>长度 m · 质量 t · 容积 m³ · 本报告独立汇总 01—05 结果。</footer>'
        '<script>document.querySelectorAll("[data-switch]").forEach(select=>select.addEventListener("change",()=>{'
        'const group=select.dataset.switch;document.querySelectorAll("[data-panel]").forEach(panel=>{'
        'if(panel.dataset.panel.startsWith(group+"-"))panel.hidden=panel.dataset.panel!==group+"-"+select.value;});}));</script>'
        '</body></html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding='utf-8')
    return output


STYLE = '''
*{box-sizing:border-box}html{scroll-padding-top:80px}
body{margin:0;background:#edf2f5;color:#193744;font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}
header{background:#132f41;color:#f4fafc;padding:42px max(24px,calc((100% - 1280px)/2)) 34px}
h1{font-size:30px;margin:8px 0 10px;line-height:1.35}header p{color:#bcd3df;margin:8px 0}
.eyebrow{font-size:12px;letter-spacing:2px;color:#80d3ce}small{color:#9db9c8}
nav{position:sticky;top:0;z-index:2;background:#fff;border-bottom:1px solid #d2dfe5;display:flex;gap:24px;padding:14px max(24px,calc((100% - 1280px)/2));overflow:auto;white-space:nowrap}
a{color:#087b83;text-decoration:none}a:hover{text-decoration:underline}
main{max-width:1328px;margin:28px auto;padding:0 24px}section{background:white;border:1px solid #dce5e9;border-radius:12px;padding:26px;margin:0 0 28px}
.section-title{display:flex;align-items:center;gap:14px;border-bottom:1px solid #e1eaee;margin-bottom:20px;padding-bottom:14px}
.section-title span{background:#e0f2f0;color:#087b83;font-weight:700;padding:5px 12px;border-radius:6px}
h2{font-size:22px;margin:0}h3{font-size:17px;margin:22px 0 12px}.note{color:#607784;font-size:13px}
.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e1e9ed;white-space:nowrap}th{background:#f0f6f8;color:#375868}tbody tr:hover{background:#f6fafb}
.model{width:100%;height:620px;border:0;border-radius:8px;background:#0d1825}.selector{display:flex;align-items:center;gap:12px;margin:20px 0}
select{font:inherit;padding:8px 14px;border:1px solid #b5cbd4;border-radius:6px;background:white;max-width:100%;color:#193744}
.plots{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}.plot{min-width:0;background:#f8fbfc;border:1px solid #e3ecef;border-radius:8px;padding:0 12px 8px}.plot p{font-size:12px;margin:0}
svg{width:100%;display:block}svg text{font:12px system-ui,sans-serif;fill:#536e7c}[hidden]{display:none!important}
footer{max-width:1280px;margin:0 auto 32px;padding:0 24px;color:#6b818d;font-size:13px}
@media(max-width:760px){header{padding:26px 18px}h1{font-size:24px}nav{gap:18px;padding:12px 18px}main{padding:0 12px;margin-top:16px}section{padding:16px}.plots{grid-template-columns:1fr}.model{height:440px}.selector{display:block}select{display:block;margin-top:8px}}
@media print{nav,.model,.selector{display:none}header{background:white;color:#193744;padding:0 0 20px}header p,small{color:#526e7b}main{padding:0;max-width:none}section{break-inside:auto;border:0;padding:0;margin:20px 0}.plot{break-inside:avoid}[data-panel][hidden]{display:none!important}}
'''
