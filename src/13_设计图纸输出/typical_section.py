"""13.2: 典型横剖面、结构引线及局部放大图。"""
import numpy as np
import ezdxf
from shapely.geometry import LineString, box
from drawing_common import Sheet, DrawingSlicer, heading, block_layer, SCOPE
from template_common import TEMPLATES, style_sheet, rectangle, leader


def draw_section(sheet_class, slice_class, ctx, template, x):
    geometry, mesh = ctx.data[2], ctx.geometry()
    dimensions, layout = geometry['dimensions'], geometry['layout']
    beam, depth = dimensions['beam_m'], dimensions['depth_m']
    source = template['source_dimensions']
    sy, sz = beam/source['beam_m'], depth/source['depth_m']
    settings = template['section']
    scale = max(sy,sz)
    h = settings['text_height']*scale
    sheet = sheet_class(settings['dimension_text_height']*scale)
    style_sheet(sheet,template)
    section = slice_class(mesh['outer_vertices'],mesh['hydro_faces']).section(x)
    inner = slice_class(mesh['inner_vertices'],mesh['hydro_faces']).section(x)
    active = [b for b in geometry['compartments'] if b['start_m']<x<b['end_m']]
    # 母型结构示意适用于全宽平行货舱段；其他剖面直接表达当前计算模型。
    details = (ctx.config.get('drawings',{}).get('structure_details',True)
               and any(b['category']=='cargo' for b in active)
               and np.isclose(section.bounds[2]-section.bounds[0],beam,atol=.001,rtol=0))
    if details:
        # 参考构件随舱壁/内底/甲板控制位置分段映射，板厚和结构质量仍由02独立计算。
        target_y = [0.,layout['upper_half_breadth_m'],layout['inner_half_breadth_m'],beam/2,18*sy]
        camber = settings['deck_camber_m']*sz
        coaming = settings['coaming_above_camber_m']*sz
        target_z = [-3*sz,0.,layout['double_bottom_m'],5.75*sz,layout['top_slope_start_m'],
                    layout['top_slope_end_m'],depth,depth+camber,depth+camber+coaming,
                    depth+(settings['source_z_knots_m'][-1]-source['depth_m'])*sz]
        detail_doc = ezdxf.readfile(TEMPLATES/template['name']/settings['details_file'])
        detail_paths=[]
        for entity in detail_doc.modelspace().query('LWPOLYLINE'):
            p = np.array(entity.get_points('xy'))
            p[:,0] = np.sign(p[:,0])*np.interp(np.abs(p[:,0]),settings['source_y_knots_m'],target_y)
            p[:,1] = np.interp(p[:,1],settings['source_z_knots_m'],target_z)
            sheet.polyline(p,entity.dxf.layer,closed=entity.closed)
            detail_paths.append((p,entity.dxf.layer))
    else:
        spaces = ctx.arrays(geometry['compartments_file'])
        for block in active:
            ident = block['id']
            cut = slice_class(spaces[ident+'_vertices'],spaces[ident+'_faces']).section(x)
            sheet.curves(cut.boundary,layer='REF_6')
    if details:
        # 去除计算模型的虚拟甲板封口，图面改由参考梁拱及舱口轮廓表达。
        sheet.curves(section.boundary.intersection(box(-beam,-1,beam,section.bounds[3]-1e-7)),layer='HULL')
        sheet.curves(inner.boundary.intersection(box(-beam,-1,beam,inner.bounds[3]-1e-7)),layer='INNER')
    else:
        sheet.curves(section.boundary,layer='HULL')
        sheet.curves(inner.boundary,layer='INNER')
    sheet.polyline([(0,-1.1*sz),(0,depth+3*sz)],'CENTER')
    sheet.text('CL',(0,-1.35*sz),h*1.4,centered=True)
    sheet.polyline([(-beam/2-1.5*sy,0),(beam/2+2.2*sy,0)],'CENTER')
    sheet.text('BL',(beam/2+1.7*sy,.12*sz),h*1.4)
    levels = [(depth,'UPPER DECK')]
    if details:
        levels = [(layout['double_bottom_m'],'INNER BOTTOM'),(5.75*sz,'NO.2 STR'),
                  (layout['top_slope_start_m'],'NO.1 STR')]+levels
    elif any(b['category']=='double_bottom' for b in active):
        levels.insert(0,(layout['double_bottom_m'],'INNER BOTTOM'))
    for z,label in levels:
        sheet.polyline([(beam/2-.4*sy,z),(beam/2+1.9*sy,z)],'REF_3')
        sheet.text(label,(beam/2+.5*sy,z+.08*sz),h)
        sheet.text(f'{z*1000:.0f} A.B.L.',(beam/2+.5*sy,z-.25*sz),h*.75)
    sheet.dimension((section.bounds[0],layout['double_bottom_m']),
                    (section.bounds[2],layout['double_bottom_m']),-1.6*sz,'<>',unit='mm')
    sheet.dimension((-beam/2,0),(-beam/2,depth),-beam/2-1.1*sy,'<>',vertical=True,unit='mm')
    if any(b['category']=='double_bottom' for b in active):
        sheet.dimension((0,0),(0,layout['double_bottom_m']),2.2*sy,'<>',vertical=True,unit='mm')
    if details:
        inner_y,upper_y = layout['inner_half_breadth_m'],layout['upper_half_breadth_m']
        zside = 5.75*sz
        sheet.dimension((-beam/2,zside),(-inner_y,zside),zside-.5*sz,'<>',unit='mm')
        sheet.dimension((-inner_y,zside),(inner_y,zside),zside-.5*sz,'<>',unit='mm')
        sheet.dimension((inner_y,zside),(beam/2,zside),zside-.5*sz,'<>',unit='mm')
        sheet.dimension((-upper_y,layout['top_slope_end_m']),(upper_y,layout['top_slope_end_m']),
                        layout['top_slope_start_m']-.55*sz,'<>',unit='mm')
        sheet.dimension((-beam/2,0),(-beam/2,depth+camber+coaming),-beam/2-1.9*sy,'<>',vertical=True,unit='mm')
        sheet.dimension((0,depth),(0,depth+camber),.7*sy,'<>',vertical=True,unit='mm')
        sheet.dimension((0,depth+camber),(0,depth+camber+coaming),.7*sy,'<>',vertical=True,unit='mm')
        spaces=ctx.arrays(geometry['compartments_file'])
        cargo=next(b for b in active if b['category']=='cargo')
        cut=slice_class(spaces[cargo['id']+'_vertices'],spaces[cargo['id']+'_faces']).section(x)
        sheet.text('货舱 / CARGO HOLD',(0,8.2*sz),h*1.6,centered=True)
        sheet.text(f'净剖面积 A={cut.area:.2f} m²',(0,7.4*sz),h*1.2,centered=True)
        sheet.text(f'模型外板 {float(mesh["thickness_m"])*1000:g} mm；主纵隔板 '
                   f'{layout["longitudinal_partition_thickness_m"]*1000:g} mm',
                   (0,3.7*sz),h,centered=True)
        leader(sheet,(beam/2-.2*sy,7.5*sz),(beam/2+1.3*sy,8.5*sz),'舷侧纵骨',h)
        leader(sheet,(-inner_y,11.0*sz),(-beam/2-3*sy,12.4*sz),'顶边舱斜壁',h)
        leader(sheet,(5*sy,layout['double_bottom_m']),(6*sy,2.8*sz),'内底板',h)
        insets=[('A 舭部连接  2:1',[-beam/2,0,-beam/2+3.5*sy,layout['double_bottom_m']+.45*sz],-beam/2-sy,2.),
                ('B 双层底中桁  2:1',[-1.7*sy,0,1.7*sy,layout['double_bottom_m']],-4*sy,2.),
                ('C 顶边舱结构  1.5:1',[upper_y-.1*sy,layout['top_slope_start_m']-.3*sz,
                     beam/2,depth+camber+.15*sz],beam/2-6.8*sy,1.5)]
        for label,bounds,left,factor in insets:
            lower=np.array(bounds[:2]);upper=np.array(bounds[2:])
            origin=np.array([left,-7*sz-(upper[1]-lower[1])*factor])
            for points,layer in detail_paths:
                if points[:,0].max()<bounds[0] or points[:,0].min()>bounds[2] or points[:,1].max()<bounds[1] or points[:,1].min()>bounds[3]:continue
                part=LineString(points).intersection(box(*bounds))
                for item in (part.geoms if hasattr(part,'geoms') else [part]):
                    if item.geom_type=='LineString':sheet.polyline((np.array(item.coords)-lower)*factor+origin,layer)
            rectangle(sheet,[left,origin[1],left+(upper[0]-lower[0])*factor,-7*sz],'GRID')
            sheet.text(label,(left,-6.5*sz),h)
    title_y = depth+(4.2 if details else 2.2)*sz
    sheet.text('典型横剖面图 / TYPICAL SECTION',(-beam/2-1.5*sy,title_y),h*2)
    sheet.text(f'{ctx.config.get("name",ctx.case_dir.name)}    X={x:.3f} m    B={beam:.3f} m    D={depth:.3f} m',
               (-beam/2-1.5*sy,title_y-.8*sz),h*1.1)
    note = '纵骨、开孔、梁拱及围板参考母型，仅作结构示意，不计入02质量。' if details else '仅绘制计算模型的船壳及主分舱边界。'
    sheet.text(note,(-beam/2-1.5*sy,-2.6*sz),h)
    sheet.text('主图1:1；局部图按注明比例放大；标注单位mm。参考构件不写回计算模型。',
               (-beam/2-1.5*sy,-3.1*sz),h)
    sheet.save(ctx.output(13)/'typical_section')
    return [b['id'] for b in active], bool(details)


def typical_section(out, name, dimensions, geometry, mesh, compartments, x, h):
    sheet = Sheet(h)
    beam, depth = dimensions['beam_m'], dimensions['depth_m']
    outer = DrawingSlicer(mesh['outer_vertices'], mesh['hydro_faces']).section(x)
    inner = DrawingSlicer(mesh['inner_vertices'], mesh['hydro_faces']).section(x)
    zmax = outer.bounds[3]
    heading(sheet, '典型横剖面图 / TYPICAL SECTION', name, dimensions, (-beam/2-10*h, zmax+17*h))
    sheet.text(f'剖切位置 X = {x:.3f} m（自AP向艏）', (-beam/2, zmax+3*h))
    sheet.curves(outer.boundary)
    sheet.curves(inner.boundary, layer='INNER')
    active = []
    for block in geometry['compartments']:
        if not block['start_m'] < x < block['end_m']:
            continue
        ident = block['id']
        section = DrawingSlicer(compartments[ident+'_vertices'], compartments[ident+'_faces']).section(x)
        sheet.curves(section.boundary, layer=block_layer(block))
        point = np.array(section.representative_point().coords[0])
        if block['category'] == 'ballast':
            sign = 1 if point[0] > 0 else -1
            anchor = np.array([sign*(beam/2+3*h), depth*.55])
            sheet.polyline([point, anchor], 'DIMENSIONS')
            sheet.text(ident, anchor + ([h, 0] if sign > 0 else [-10*h, 0]), h*.8)
        else:
            sheet.text(ident, point, h*.85, centered=True)
        active.append(ident)
    draft = dimensions['design_draft_m']
    for z, label in ((0, 'BL'), (draft, f'T={draft:.3f} m'), (depth, f'D={depth:.3f} m')):
        sheet.polyline([(-beam/2-2*h, z), (beam/2+2*h, z)], 'CENTER')
        sheet.text(label, (beam/2+3*h, z+h*.2), h*.8)
    sheet.polyline([(0, -2*h), (0, zmax+2*h)], 'CENTER')
    sheet.text('CL', (h*.3, -2*h))
    sheet.dimension((outer.bounds[0], 0), (outer.bounds[2], 0), -5*h, 'B(x) = <> m')
    sheet.dimension((0, 0), (0, depth), -beam/2-5*h, 'D = <> m', vertical=True)
    layout = geometry['layout']
    sheet.text(f'双层底高 {layout["double_bottom_m"]:.3f} m；舷侧舱及货舱斜壁采用02分舱几何。',
               (-beam/2-10*h, -10*h))
    sheet.text(f'外板 {float(mesh["thickness_m"])*1000:g} mm；横舱壁 {layout["bulkhead_thickness_m"]*1000:g} mm；'
               f'纵隔板 {layout["longitudinal_partition_thickness_m"]*1000:g} mm。', (-beam/2-10*h, -12.5*h))
    sheet.text(SCOPE, (-beam/2-10*h, -15*h))
    sheet.text('DXF模型空间1:1，单位mm；标注值单位m。P为左舷，S为右舷。', (-beam/2-10*h, -17.5*h))
    sheet.save(out/'typical_section')
    return active


def run(ctx, drawing):
    x = drawing['section_x']
    if drawing['template']:
        active, details = draw_section(Sheet, DrawingSlicer, ctx, drawing['template'], x)
    else:
        geometry = ctx.data[2]
        spaces = ctx.arrays(geometry['compartments_file'])
        active = typical_section(ctx.output(13), drawing['name'], geometry['dimensions'], geometry,
                                 ctx.geometry(), spaces, x, drawing['height']*.65)
        details = False
    return dict(section_blocks=active, reference_structure_details=details)
