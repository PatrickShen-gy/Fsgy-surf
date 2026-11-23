"""13.3: 总布置图及母型甲板室、设备参考布置。"""
import numpy as np
import ezdxf
from drawing_common import Sheet, DrawingSlicer, heading, block_layer, hull_deck, model_cut, SCOPE
from template_common import TEMPLATES, style_sheet, rectangle, title_block


def reference_paths(ctx,template):
    settings=template['general_arrangement']
    doc=ezdxf.readfile(TEMPLATES/template['name']/settings['details_file'])
    paths={name:[] for name in settings['views']}
    for entity in doc.modelspace().query('LWPOLYLINE'):
        view,color=entity.dxf.layer.rsplit('_',1)
        paths[view].append((np.array(entity.get_points('xy')),f'REF_{color}'))
    return paths


def map_x(ctx,x,template):
    mesh=ctx.geometry()
    original=np.r_[-3.2,0,template['general_arrangement']['source_boundaries_m'],
                   template['source_dimensions']['lbp_m'],176.68]
    target=np.r_[mesh['ring_x'][0],0,ctx.data[2]['layout']['bulkhead_centers_x_m'],
                 ctx.data[2]['dimensions']['lbp_m'],mesh['ring_x'][-1]]
    result=np.interp(x,original,target)
    sx=ctx.data[2]['longitudinal_mapping']['overhang_scale']
    return np.where(x<original[0],target[0]+(x-original[0])*sx,
                    np.where(x>original[-1],target[-1]+(x-original[-1])*sx,result))


def draw_reference(sheet,ctx,template,paths,view,offset):
    sx,sy,sz=ctx.data[2]['scale_xyz']
    for points,layer in paths[view]:
        if view=='profile' and points[:,1].max()>template['general_arrangement']['views']['profile']['bounds'][3]:
            continue
        p=points.copy()
        p[:,0]=p[:,0]*sx if view=='accommodation' else map_x(ctx,p[:,0],template)
        p[:,1]*=sz if view=='profile' else sy
        sheet.polyline(p+offset,layer)


def draw_general_arrangement(sheet_class,slice_class,ctx,template,tank_z):
    dims=ctx.data[2]['dimensions'];beam=dims['beam_m'];depth=dims['depth_m']
    settings=template['general_arrangement']
    scale=max(*ctx.data[2]['scale_xyz'],dims['loa_m']/ctx.data[1]['dimensions']['loa_m']);h=.72*scale
    sheet=sheet_class(h*.65);style_sheet(sheet,template)
    rectangle(sheet,np.array(settings['frame'])*scale)
    frame=np.array(settings['frame'])*scale
    rectangle(sheet,frame+[1.5*scale,1.5*scale,-1.5*scale,-1.5*scale],'REF_6')
    paths=reference_paths(ctx,template)
    profile=np.array(settings['profile_origin'])*scale
    accommodation=np.array(settings['accommodation_origin'])*scale
    deck_at=np.array(settings['deck_origin'])*scale
    tank_at=np.array(settings['tank_origin'])*scale
    for view,offset in [('profile',profile),('accommodation',accommodation),('deck',deck_at)]:
        draw_reference(sheet,ctx,template,paths,view,offset)
    for item in settings['labels']:
        at=np.array(item['at'])*np.array(ctx.data[2]['scale_xyz'][:2])+accommodation
        sheet.text(item['text'],at,item['height']*scale,layer='REF_2')
    model_cut(sheet,slice_class,ctx,1,0,profile,h)
    deck=hull_deck(ctx)
    outline=np.vstack([deck[:,:2],deck[::-1,:2]*[1,-1]])
    sheet.polyline(outline+deck_at,'HULL',closed=True)
    for x in ctx.data[2]['layout']['bulkhead_centers_x_m']:
        width=np.interp(x,deck[:,0],deck[:,1])
        sheet.polyline([(x,deck_at[1]-width),(x,deck_at[1]+width)],'BULKHEAD')
    for block in ctx.data[2]['compartments']:
        if block['category']=='cargo':
            mid=(block['start_m']+block['end_m'])/2
            sheet.text(block['id'],(mid,deck_at[1]),h,centered=True,layer='CARGO')
    model_cut(sheet,slice_class,ctx,2,tank_z,tank_at,h)
    sheet.text('总布置图 / GENERAL ARRANGEMENT',(-5*scale,65*scale),h*2)
    sheet.text('侧视及中纵剖面：甲板室、起货设备参考母型',(-4*scale,59*scale),h)
    sheet.text('居住甲板及机舱平台参考布置', (28*scale,-6*scale),h)
    sheet.text('上甲板布置 / UPPER DECK',(-4*scale,deck_at[1]+beam/2+3*scale),h*1.2)
    tank_label = '内底以上水平剖面' if tank_z > ctx.data[2]['layout']['double_bottom_m'] else '水平剖面'
    sheet.text(f'{tank_label} Z={tank_z:.3f} m',(-4*scale,tank_at[1]+beam/2+3*scale),h*1.2)
    x,y=187*scale,56*scale
    sheet.text(ctx.config.get('name',ctx.case_dir.name),(x,y),h*1.3)
    for i,(key,label) in enumerate([('lbp_m','LBP'),('loa_m','LOA'),('beam_m','B'),('depth_m','D'),('design_draft_m','T')]):
        sheet.text(f'{label} = {dims[key]:.3f} m',(x,y-(i+1)*2.2*scale),h)
    sheet.dimension((0,0),(dims['lbp_m'],0),-2.4*scale,'LBP = <>',unit='mm')
    sheet.text('舱壁及分舱边界按当前模型更新；甲板室、设备和居住舱室为母型参考布置。',(-5*scale,-121*scale),h*.8)
    sheet.text('参考布置不写回静水力、舱容和结构质量模型。模型空间1:1，标注单位mm。',(-5*scale,-124*scale),h*.8)
    title_block(sheet,np.array(settings['title_block'])*scale,ctx.config.get('name',ctx.case_dir.name),'总布置图','FSGY-13-03')
    sheet.save(ctx.output(13)/'general_arrangement')


def general_arrangement(out, name, dimensions, geometry, mesh, compartments, slicers, plan_z, h):
    sheet = Sheet(h)
    vertices = mesh['outer_vertices']
    xmin, xmax = vertices[:, 0].min(), vertices[:, 0].max()
    zmax = vertices[:, 2].max()
    beam = dimensions['beam_m']
    deck_y, plan_y = -beam/2-16*h, -beam*1.5-29*h
    heading(sheet, '总布置图（主分舱） / GENERAL ARRANGEMENT', name, dimensions, (xmin, zmax+15*h))
    sheet.text('中纵剖面 / Y = 0', (xmin, zmax+3*h))
    sheet.text('上甲板轮廓及主横舱壁投影', (xmin, deck_y+beam/2+3*h))
    sheet.text(f'水平剖面 / Z = {plan_z:.3f} m', (xmin, plan_y+beam/2+3*h))
    sheet.curves(slicers[2].section(0).boundary)
    sheet.curves(slicers[1].section(plan_z).boundary, (0, plan_y))
    ring_x = mesh['ring_x']
    n = len(vertices)//len(ring_x)
    rings = vertices[:len(ring_x)*n].reshape(len(ring_x), n, 3)
    deck = rings[:, (n-1)//2, :2]
    deck_outline = np.vstack([deck, deck[::-1]*[1, -1]])
    sheet.polyline(deck_outline+[0, deck_y], closed=True)
    for block in geometry['compartments']:
        ident = block['id']
        v, f = compartments[ident+'_vertices'], compartments[ident+'_faces']
        for axis, coordinate, offset in ((1, 0., (0, 0)), (2, plan_z, (0, plan_y))):
            if not v[:, axis].min() < coordinate < v[:, axis].max():
                continue
            axes = [1, 0, 2] if axis == 1 else [2, 0, 1]
            section = DrawingSlicer(v[:, axes], f).section(coordinate)
            sheet.curves(section.boundary, offset, block_layer(block))
            point = np.array(section.representative_point().coords[0])+offset
            if block['category'] == 'ballast':
                sign = 1 if point[1] > plan_y else -1
                anchor = np.array([(block['start_m']+block['end_m'])/2,
                                   plan_y+sign*(beam/2+2*h)])
                sheet.polyline([point, anchor], 'DIMENSIONS')
                sheet.text(ident, anchor, h*.65, centered=True)
            else:
                sheet.text(ident, point, h*.9, centered=True)
        if block['category'] in ('cargo', 'machinery', 'peak'):
            x = (block['start_m']+block['end_m'])/2
            sheet.text(ident, (x, deck_y+h), h*.9, centered=True)
    for x in geometry['layout']['bulkhead_centers_x_m']:
        width = np.interp(x, deck[:, 0], deck[:, 1])
        sheet.polyline([(x, deck_y-width), (x, deck_y+width)], 'BULKHEAD')
        sheet.text(f'{x:.2f}', (x, deck_y-width-2*h), h*.7, centered=True)
    for y in (deck_y, plan_y):
        sheet.polyline([(xmin, y), (xmax, y)], 'CENTER')
    draft = dimensions['design_draft_m']
    sheet.polyline([(xmin, draft), (xmax, draft)], 'WATERLINES')
    sheet.text(f'T={draft:.3f} m', (xmax+2*h, draft), h*.8)
    sheet.dimension((0, 0), (dimensions['lbp_m'], 0), -6*h, 'LBP = <> m')
    sheet.text('X自AP向艏；平面图上方为左舷P。甲板图中的舱段名称表示下方空间投影。', (xmin, plan_y-beam/2-5*h))
    sheet.text('HOLD：货舱；WBT：边舱；DB：双层底；ENGINE_ROOM：机舱包络；PEAK：艏艉空间。',
               (xmin, plan_y-beam/2-7.5*h))
    sheet.text(SCOPE, (xmin, plan_y-beam/2-10*h))
    sheet.text('DXF模型空间1:1，单位mm；标注值单位m。', (xmin, plan_y-beam/2-12.5*h))
    sheet.save(out/'general_arrangement')


def run(ctx, drawing):
    if drawing['template']:
        draw_general_arrangement(Sheet, DrawingSlicer, ctx, drawing['template'], drawing['arrangement_z'])
    else:
        geometry = ctx.data[2]
        spaces = ctx.arrays(geometry['compartments_file'])
        general_arrangement(ctx.output(13), drawing['name'], geometry['dimensions'], geometry,
                            ctx.geometry(), spaces, drawing['slicers'], drawing['arrangement_z'], drawing['height'])
    return {}
