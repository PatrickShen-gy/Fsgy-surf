"""13: Dispatch four drawing substeps and assemble their shared output index."""
import html
from importlib import import_module
from pathlib import Path
import numpy as np
from drawing_common import SCOPE, grid_values, slicing_axes
from template_common import load_template
from fsgy_surf.io.files import save_json, write_csv
from fsgy_surf.config.design import principal_summary
from fsgy_surf.reporting.html import table

DRAWINGS = [('lines_plan', '型线图'), ('typical_section', '典型横剖面图'),
            ('general_arrangement', '总布置图'), ('capacity_plan', '舱容图')]


def lines_grids(settings, template, mesh, dimensions, bounds):
    xmin, xmax, zmax = bounds
    depth = dimensions['depth_m']
    beam = dimensions['beam_m']
    draft = dimensions['design_draft_m']
    default_waterlines = np.r_[np.linspace(depth/8, depth, 8), draft]
    default_buttocks = np.arange(4)*beam/8
    if template:
        default_waterlines = np.r_[np.array(template['lines']['waterlines_m'])*depth/14.6,
                                  draft, dimensions['scantling_draft_m']]
        default_buttocks = np.array(template['lines']['buttocks_m'])*beam/28.8
    default_waterlines = default_waterlines[default_waterlines < zmax]
    return (
        grid_values(settings, 'stations_m', mesh['source_station_x'], xmin, xmax),
        grid_values(settings, 'waterlines_m', default_waterlines, 0, zmax, True),
        grid_values(settings, 'buttocks_m', default_buttocks, 0, beam/2, True),
    )


def configured_coordinate(settings, key, default, lower, upper):
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError) as error:
        raise ValueError(f'drawings.{key} must be a number') from error
    if not lower < value < upper:
        raise ValueError(f'drawings.{key} must be in ({lower:g}, {upper:g}) m')
    return value


def run(ctx):
    settings = ctx.config.get('drawings', {})
    if not isinstance(settings, dict):
        raise ValueError('drawings must be a JSON object')
    types = settings.get('types', [name for name, _ in DRAWINGS])
    valid_types = [name for name, _ in DRAWINGS]
    if not isinstance(types, list) or not types or any(name not in valid_types for name in types):
        raise ValueError('drawings.types must select lines_plan, typical_section, general_arrangement or capacity_plan')
    selected = [(i, name, title) for i, (name, title) in enumerate(DRAWINGS, 1) if name in types]
    template = load_template(settings.get('template', 'mother_35000'))
    geometry, mesh = ctx.data[2], ctx.geometry()
    dimensions = geometry['dimensions']
    vertices, faces = mesh['outer_vertices'], mesh['hydro_faces']
    xmin, xmax = vertices[:, 0].min(), vertices[:, 0].max()
    bounds = xmin, xmax, vertices[:, 2].max()
    depth = dimensions['depth_m']
    stations = waterlines = buttocks = np.empty(0)
    if 'lines_plan' in types:
        stations, waterlines, buttocks = lines_grids(settings, template, mesh, dimensions, bounds)
    section_x = None
    if 'typical_section' in types:
        cargo = next(b for b in geometry['compartments'] if b['id'] == 'HOLD_3')
        midpoint = (cargo['start_m']+cargo['end_m'])/2
        section_x = configured_coordinate(settings, 'section_x_m', midpoint, xmin, xmax)
        if any(abs(section_x-x) < 1e-6 for x in geometry['layout']['bulkhead_centers_x_m']):
            raise ValueError('drawings.section_x_m must lie inside a compartment, away from a transverse bulkhead')
    plan_z = None
    if 'general_arrangement' in types or 'capacity_plan' in types:
        plan_z = configured_coordinate(settings, 'plan_z_m', depth/2, 0, depth)
    arrangement_z = None
    if 'general_arrangement' in types:
        arrangement_z = (geometry['layout']['double_bottom_m']+.1
                         if template and 'plan_z_m' not in settings else plan_z)

    out, name = ctx.output(13), ctx.config.get('name', ctx.case_dir.name)
    point = ctx.data[4]['scantling_point'] if 4 in ctx.data else None
    ctx.principal_particulars = principal_summary(ctx, point)
    particulars_file = out/'principal_particulars.json'
    particulars_csv = out/'principal_particulars.csv'
    save_json(particulars_file, ctx.principal_particulars)
    write_csv(particulars_csv, ctx.principal_particulars['rows'])
    # 字高随图幅长度调整，不改变模型空间的真实几何比例。
    drawing = dict(template=template, name=name, slicers=slicing_axes(vertices, faces),
                   stations=stations, waterlines=waterlines, buttocks=buttocks,
                   section_x=section_x, plan_z=plan_z, arrangement_z=arrangement_z, bounds=bounds,
                   height=dimensions['lbp_m']/180)
    details = dict(reference_structure_details=False, section_blocks=[], offsets_file=None, stations_file=None,
                   section_points_file=None, capacity_plan_file=None)
    files = []
    for number, module_name, title in selected:
        print(f'[13.{number}] {module_name}', flush=True)
        details.update(import_module(module_name).run(ctx, drawing))
        files.append(dict(title=title, dxf=str(out/f'{module_name}.dxf'), svg=str(out/f'{module_name}.svg'),
                          dimension_unit='mm' if template or module_name == 'capacity_plan' else 'm'))

    print('[13.5] drawing_index', flush=True)
    index_file = out/'index.html'
    metadata_file = out/'drawings.json'
    artifact_files = [str(path) for path in (index_file, metadata_file, particulars_file, particulars_csv)]
    artifact_files.extend(item[ext] for item in files for ext in ('dxf', 'svg'))
    artifact_files.extend(path for key, path in details.items() if key.endswith('_file') and path)
    result = dict(dimensions=dimensions, dxf_version='R2010', dxf_unit='mm', model_scale='1:1',
        coordinate_unit='m', coordinates=geometry['coordinates'], model_scope=SCOPE,
        template=template['name'] if template else 'simple',
        drawing_types=[module_name for _, module_name, _ in selected],
        dimension_units={Path(item['dxf']).stem: item['dimension_unit'] for item in files},
        principal_particulars_file=str(particulars_file),
        font_rendering='DengXian TTF; CJK glyph contours in DXF; all glyph contours in SVG',
        stations_m=stations.tolist(), waterlines_m=waterlines.tolist(), buttocks_m=buttocks.tolist(),
        section_x_m=section_x, plan_z_m=plan_z, general_arrangement_plan_z_m=arrangement_z,
        files=files, index_file=str(index_file),
        artifact_files=artifact_files, **details)
    cards = ''.join(f'<section><h2>{item["title"]}</h2><p><a href="{Path(item["dxf"]).name}">下载 DXF</a> · '
                    f'<a href="{Path(item["svg"]).name}">打开 SVG</a></p>'
                    f'<img src="{Path(item["svg"]).name}" alt="{item["title"]}"></section>' for item in files)
    links = [('drawings.json', '图纸参数'), ('principal_particulars.csv', '主参数核对CSV')]
    if details['offsets_file']:
        links.append(('offsets.csv', '型值表CSV'))
    if details['section_points_file']:
        links.extend([('stations.csv', '站位表'), ('section_points.csv', '目标横剖型值')])
    if details['capacity_plan_file']:
        links.append(('capacity_plan.csv', '舱容数据CSV'))
    data_links = ' · '.join(f'<a href="{filename}">{label}</a>' for filename, label in links)
    comparison = table([dict(参数=r['label'], 单位=r['unit'], 设计输入=r['input_value'],
                             复算或采用值=r['calculated_value'], 差值=r['difference'])
                        for r in ctx.principal_particulars['rows']])
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>设计图纸</title>' \
           '<style>body{max-width:1500px;margin:32px auto;padding:0 24px;font-family:system-ui;color:#233b49}' \
           'section{margin:32px 0;border-top:1px solid #ccc}img{width:100%}a{color:#147e9b}' \
           'table{border-collapse:collapse;width:100%}th,td{padding:6px 10px;border-bottom:1px solid #ccc;text-align:right}' \
           'th:first-child,td:first-child{text-align:left}</style>' \
           f'<h1>{html.escape(name)} · 设计图纸</h1><p>DXF R2010，主图1:1，局部比例见图，单位mm；' \
           '母型版式标注单位mm，简化版式标注单位m；舱容图标注单位mm，舱容表采用m³和m。</p>' \
           f'<p>{SCOPE} 母型模板中的纵骨、开孔、梁拱及围板为参考图示，不计入计算模型。</p>' \
           '<p>图纸采用CAD深色背景配色。中文显示为轮廓，原文保留在关闭的TEXT_EDITABLE图层。</p>' \
           '<p>在CAD中打开DXF，使用“另存为”保存为DWG。</p>' \
           f'<p>{data_links}</p>' \
           '<h2>设计输入与复算</h2><p>性能参数按结构吃水Ts核对；输入值不覆盖网格静水力。LW0为指定或装载配置中的采用值。</p>' \
           + comparison + cards + '</html>'
    index_file.write_text(page, encoding='utf-8')
    save_json(metadata_file, result)
    print(f'Design drawings: {index_file}', flush=True)
    return result
