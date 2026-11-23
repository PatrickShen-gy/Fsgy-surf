"""13.1: 型线三视图、型值表和横剖截线数据。"""
import numpy as np
from shapely.geometry import LineString, box
from fsgy_surf.io.files import write_csv
from fsgy_surf.config.design import longitudinal_x
from drawing_common import Sheet, heading, line_parts
from template_common import points_of, style_sheet, rectangle, title_block, leader


def half_width(section, z):
    ymin, _, ymax, _ = section.bounds
    points = points_of(section.intersection(LineString([(ymin-1, z), (ymax+1, z)])))
    return max(p[0] for p in points) if points else None


def station_names(ctx, stations):
    source = ctx.data[1]['hull_offsets']['stations']
    known = {round(float(longitudinal_x(s['x_m'],ctx.data[1]['dimensions'],ctx.data[2]['dimensions'])),7):s['station']
             for s in source}
    lbp = ctx.data[2]['dimensions']['lbp_m']
    return [known.get(round(float(x), 7), f'{20*x/lbp:g}') for x in stations]


def raised_edges(ctx):
    """型值表中已给出的艏艉楼和舷墙边线，仅作母型缩放参考。"""
    scale=np.asarray(ctx.data[2]['scale_xyz'])
    result=[]
    for key,label,layer in [('poop','尾楼甲板边线','REF_3'),
                             ('forecastle','艏楼甲板边线','REF_3'),
                             ('bulwark','舷墙顶边线','REF_6')]:
        points=[]
        for row in ctx.data[1]['hull_offsets']['stations']:
            y=row['cells'][key+'_half_breadth']['values_m']
            z=row['cells'][key+'_height']['values_m']
            if y and z:
                point=np.array([row['x_m'],y[0],z[0]])*scale
                point[0]=longitudinal_x(row['x_m'],ctx.data[1]['dimensions'],ctx.data[2]['dimensions'])
                points.append(point)
            elif points:
                if len(points)>1:result.append((label,np.array(points),layer))
                points=[]
        if len(points)>1:result.append((label,np.array(points),layer))
    return result


def offsets_table(sheet, ctx, template, body, stations, names, scale):
    settings = template['lines']
    x0, top = np.array(settings['table_origin'])*scale
    width, height = settings['table_width']*scale, settings['table_height']*scale
    dimensions = ctx.data[2]['dimensions']
    levels = np.r_[np.arange(1,10)*dimensions['depth_m']/14.6,
                   dimensions['design_draft_m'], np.arange(11,15)*dimensions['depth_m']/14.6]
    headers = ['ST.NO.', 'X', 'F.O.B.']+[f'{z*1000:.0f}' for z in levels]+['DECK Y', 'CL Z', 'DECK Z']
    col_width, row_height = width/len(headers), height/(len(stations)+2)
    bottom = top-height
    for i in range(len(headers)+1):
        sheet.polyline([(x0+i*col_width,bottom), (x0+i*col_width,top)], 'REF_4')
    for i in range(len(stations)+3):
        y = top-i*row_height
        sheet.polyline([(x0,y), (x0+width,y)], 'REF_7')
    sheet.text('型值表 / OFFSETS  (mm)', (x0+width/2,top+row_height), row_height*.75, centered=True)
    sheet.text('水线列为半宽；CL Z、DECK Z 为基线以上高度', (x0, bottom-row_height*1.5), row_height*.55)
    all_rows = [headers]
    output = []
    for name, x in zip(names, stations):
        section = body.section(x)
        deck_z = section.bounds[3]
        deck_y = half_width(section, deck_z)
        center = points_of(section.intersection(LineString([(0,-1), (0,deck_z+1)])))
        center_z = min(p[1] for p in center)
        values = [x, half_width(section,0)] + [half_width(section,z) for z in levels]+[deck_y,center_z,deck_z]
        row = [name]+['-' if value is None else f'{value*1000:.0f}' for value in values]
        all_rows.append(row)
        output.append(dict(station=name, x_m=x, flat_of_bottom_m=values[1],
            **{f'half_breadth_at_z_{z:.6f}_m':value for z,value in zip(levels,values[2:-3])},
            deck_half_breadth_m=deck_y, centerline_height_m=center_z, deck_height_m=deck_z))
    for i, row in enumerate(all_rows):
        for j, value in enumerate(row):
            size = min(row_height*.60, col_width/(max(len(value),1)*.72))
            sheet.text(value, (x0+(j+.5)*col_width,top-(i+1.25)*row_height), size, centered=True)
    write_csv(ctx.output(13)/'offsets.csv', output)


def draw_lines(sheet_class, ctx, template, slicers, stations, waterlines, buttocks):
    dimensions = ctx.data[2]['dimensions']
    lbp, beam, depth = (dimensions[k] for k in ('lbp_m','beam_m','depth_m'))
    source = template['source_dimensions']
    scale = max(lbp/source['lbp_m'],beam/source['beam_m'],depth/source['depth_m'],
                dimensions['loa_m']/ctx.data[1]['dimensions']['loa_m'])
    settings = template['lines']
    h = settings['view_text_height']*scale
    small = settings['grid_text_height']*scale
    sheet = sheet_class(h*.65)
    style_sheet(sheet, template)
    body, water, buttock = slicers
    vertices = ctx.geometry()['outer_vertices']
    xmin, xmax = vertices[:,0].min(), vertices[:,0].max()
    edges=raised_edges(ctx)
    zmax = max([vertices[:,2].max()]+[p[:,2].max() for _,p,_ in edges])
    zmax = np.ceil(zmax)
    profile = np.array(settings['profile_origin'])*scale
    plan = np.array(settings['plan_origin'])*scale
    body_at = np.array(settings['body_origin'])*scale
    frame = np.array(settings['frame'])*scale
    rectangle(sheet, frame)
    rectangle(sheet, frame+[1.8*scale,1.8*scale,-1.8*scale,-1.8*scale], 'REF_6')
    names = station_names(ctx, stations)
    body_labels = []
    for index, (x, name) in enumerate(zip(stations, names)):
        for offset, height in ((profile,zmax), (plan,beam/2)):
            sheet.polyline(np.array([(x,0),(x,height)])+offset, 'GRID')
            sheet.text(name, (x+offset[0],offset[1]-small*2), small, centered=True)
        half = body.section(x).boundary.intersection(box(0,-1,beam,zmax+1))
        layer = sheet.curve_layer(f'ST_{index:02d}_X_{x:.3f}_m', 'STATIONS')
        sheet.curves(half, body_at, layer, mirror=x<lbp/2)
        group = next((g for g in body_labels if g[1] == (x<lbp/2) and g[0].equals(half)),None)
        if group is None:
            body_labels.append((half,x<lbp/2,[name]))
        else:
            group[2].append(name)
    for index,(half,mirror,labels) in enumerate(body_labels):
        point = np.array(half.interpolate(.35+.09*(index%6), normalized=True).coords[0])
        point[0] *= -1 if mirror else 1
        label = labels[0] if len(labels)==1 else f'{labels[0]}-{labels[-1]}'
        sheet.text(label, point+body_at+[0,small*.4], small*.85, centered=True)
    grid_z = np.unique(np.r_[np.arange(0,zmax,depth/14.6), depth,
                             dimensions['design_draft_m'], dimensions['scantling_draft_m']])
    for z in grid_z[grid_z<=zmax]:
        special = np.isclose(z, dimensions['design_draft_m']) or np.isclose(z,dimensions['scantling_draft_m'])
        layer = 'REF_1' if special else 'GRID'
        sheet.polyline(np.array([(xmin,z),(xmax,z)])+profile,layer)
        sheet.polyline(np.array([(-beam/2-1,z),(beam/2+1,z)])+body_at,layer)
        sheet.text(f'WL{z*1000:.0f}', profile+[xmin-2.8*scale,z], small, centered=True)
        if abs(z/(depth/14.6)-round(z/(depth/14.6)))<1e-6 and round(z/(depth/14.6))%2==1:
            sheet.text(f'WL{z*1000:.0f}',body_at+[beam/2+1.5*scale,z],small,layer='ANNOTATION')
    for i, y in enumerate(buttocks):
        layer = sheet.curve_layer(f'BT_{i:02d}_Y_{y:.3f}_m','BUTTOCKS')
        sheet.curves(buttock.section(y).boundary,profile,layer)
        sheet.polyline(np.array([(xmin,y),(xmax,y)])+plan,'GRID')
        for sign in (-1,1):
            sheet.polyline(np.array([(sign*y,0),(sign*y,zmax)])+body_at,'GRID')
    for i,z in enumerate(waterlines):
        layer = sheet.curve_layer(f'WL_{i:02d}_Z_{z:.3f}_m','WATERLINES')
        curve = water.section(z).boundary.intersection(box(xmin-1,0,xmax+1,beam))
        sheet.curves(curve,plan,layer)
        label_x=lbp*.1+i*1.5*scale
        hits=points_of(water.section(z).intersection(LineString([(label_x,-beam),(label_x,beam)])))
        if hits:
            label_y=max(p[1] for p in hits)
            sheet.text(f'WL{z*1000:.0f}',plan+[label_x,label_y+.18*scale],small*1.05,layer='ANNOTATION')
    sheet.curves(buttock.section(0).boundary,profile,'HULL')
    ring_x = ctx.geometry()['ring_x']
    n = len(vertices)//len(ring_x)
    rings = vertices[:len(ring_x)*n].reshape(len(ring_x),n,3)
    deck = rings[:,(n-1)//2]
    sheet.polyline(deck[:,[0,2]]+profile,'REF_3')
    sheet.polyline(deck[:,:2]+plan,'REF_3')
    for label,p,layer in edges:
        sheet.polyline(p[:,[0,2]]+profile,layer)
        sheet.polyline(p[:,:2]+plan,layer)
        for x,y,z in p:
            section=body.section(x)
            yz=[half_width(section,section.bounds[3]),section.bounds[3]]
            sign=-1 if x<lbp/2 else 1
            sheet.polyline(np.array([[sign*yz[0],yz[1]],[sign*y,z]])+body_at,layer)
        anchor=p[len(p)//2,[0,2]]+profile
        elbow=anchor+[2*scale,1.5*scale]
        leader(sheet,anchor,elbow,label,h*.8)
    anchor=deck[len(deck)//2,[0,2]]+profile
    leader(sheet,anchor,anchor+[9*scale,1.1*scale],'上甲板边线',h*.9)
    plan_anchor=deck[len(deck)//2,:2]+plan
    leader(sheet,plan_anchor,plan_anchor+[9*scale,1.0*scale],'上甲板边线',h*.9)
    for offset in (profile,plan):
        sheet.polyline(np.array([(xmin,0),(xmax,0)])+offset,'CENTER')
    sheet.polyline(np.array([(0,-1),(0,zmax+1)])+body_at,'CENTER')
    sheet.text('横剖线图 / BODY PLAN', body_at+[-beam/2,zmax+.6*scale],h)
    sheet.text('艉部在左 / 艏部在右', body_at+[-beam/2,-1.6*scale],small*1.2)
    sheet.text('纵剖线图 / SHEER PLAN', profile+[lbp*.42,zmax+.6*scale],h)
    sheet.text('半宽水线图 / HALF BREADTH PLAN', plan+[xmin,-2.4*scale],h)
    sheet.dimension(profile+[0,0],profile+[lbp,0],profile[1]-1.5*scale,'LBP = <>',unit='mm')
    offsets_table(sheet,ctx,template,body,stations,names,scale)
    x,y = np.array(settings['particulars_origin'])*scale
    sheet.text('主要尺度 / MAIN PARTICULARS',(x,y),h*1.1)
    for i,(key,label) in enumerate([('loa_m','LENGTH O.A.'),('lbp_m','LENGTH B.P.'),('beam_m','BREADTH MLD.'),
                                  ('depth_m','DEPTH MLD.'),('design_draft_m','DESIGN DRAFT'),('scantling_draft_m','SCANTLING DRAFT')]):
        sheet.text(f'{label:18s} {dimensions[key]:.3f} m',(x,y-(i+1)*1.3*scale),h*.8)
    for i,(key,label,unit,decimals) in enumerate([
        ('displacement_t','Delta(Ts)','t',2),('lightweight_t','LW0','t',2),
        ('deadweight_t','DW(Ts)','t',2),('block_coefficient','Cb(Ts)','',4)]):
        row=next(r for r in ctx.principal_particulars['rows'] if r['parameter']==key)
        given='-' if row['input_value'] is None else f'{row["input_value"]:.{decimals}f}'
        actual='-' if row['calculated_value'] is None else f'{row["calculated_value"]:.{decimals}f}'
        sheet.text(f'{label} {given} / {actual} {unit}',(x,y-(i+7)*1.3*scale),h*.75)
    sheet.text('性能列：设计输入 / 复算或采用值',(x,y-11.5*1.3*scale),h*.72)
    sheet.text('纵剖半宽 Y / mm：'+', '.join(f'{v*1000:.0f}' for v in buttocks),(110*scale,25*scale),h*.75)
    sheet.text('水线高度 Z / mm：'+', '.join(f'{v*1000:.0f}' for v in waterlines[:8]),(110*scale,23*scale),h*.75)
    sheet.text('续：'+', '.join(f'{v*1000:.0f}' for v in waterlines[8:]),(110*scale,21*scale),h*.75)
    sheet.text('说明：型值和型线由目标船计算网格生成，图框与版式参考母型图。',(-5*scale,-27*scale),h*.9)
    sheet.text('船体型线取02网格；艏艉楼及舷墙边线参考母型型值表，不计入静水力。',(-5*scale,-29*scale),h*.9)
    sheet.text('型值表及尺寸单位mm；CSV坐标单位m；模型空间1:1。',(-5*scale,-31*scale),h*.9)
    title_block(sheet,np.array(settings['title_block'])*scale,ctx.config.get('name',ctx.case_dir.name),
                '型线图 / LINES PLAN','FSGY-13-01')
    sheet.save(ctx.output(13)/'lines_plan')


def lines_plan(out, name, dimensions, slicers, stations, waterlines, buttocks, bounds, h):
    body, water, buttock = slicers
    xmin, xmax, zmax = bounds
    lbp, beam = dimensions['lbp_m'], dimensions['beam_m']
    half_beam = beam/2
    sheet = Sheet(h)
    plan_y = -half_beam-14*h
    body_x = xmax+half_beam+15*h
    heading(sheet, '型线图 / LINES PLAN', name, dimensions, (xmin, zmax+15*h))
    sheet.text('纵剖线图 / X-Z', (xmin, zmax+3*h))
    sheet.text('半宽水线图 / X-Y', (xmin, plan_y+half_beam+3*h))
    sheet.text('横剖线图 / Y-Z', (body_x-half_beam, zmax+3*h))
    sheet.text('艉部在左；艏部在右', (body_x-half_beam, -4*h))
    body_labels = []
    for i, x in enumerate(stations):
        sheet.polyline([(x, 0), (x, zmax)], 'GRID')
        sheet.polyline([(x, plan_y), (x, plan_y+half_beam)], 'GRID')
        sheet.text(str(i), (x, -2*h), h*.65, centered=True)
        section = body.section(x)
        half = section.boundary.intersection(box(0, -1, beam, zmax+1))
        mirror = x < lbp/2
        layer = sheet.curve_layer(f'ST_{i:02d}_X_{x:.3f}_m', 'STATIONS')
        sheet.curves(half, (body_x, 0), layer, mirror=mirror)
        group = next((g for g in body_labels if g[1] == mirror and g[0].equals(half)), None)
        if group is None:
            body_labels.append((half, mirror, [i]))
        else:
            group[2].append(i)
    for j, (half, mirror, ids) in enumerate(body_labels):
        point = np.array(half.interpolate(.35+.09*(j % 6), normalized=True).coords[0])
        point[0] *= -1 if mirror else 1
        label = str(ids[0]) if len(ids) == 1 else f'{ids[0]}-{ids[-1]}'
        sheet.text(label, point+[body_x, h*.25], h*.55, centered=True)
    for i, z in enumerate(waterlines):
        sheet.polyline([(xmin, z), (xmax, z)], 'GRID')
        sheet.polyline([(body_x-half_beam, z), (body_x+half_beam, z)], 'GRID')
        layer = sheet.curve_layer(f'WL_{i:02d}_Z_{z:.3f}_m', 'WATERLINES')
        sheet.curves(water.section(z).boundary.intersection(box(xmin-1, 0, xmax+1, beam)),
                     (0, plan_y), layer)
        sheet.text(f'{z:.2f}', (xmin-5*h, z), h*.7)
    for i, y in enumerate(buttocks):
        layer = sheet.curve_layer(f'BT_{i:02d}_Y_{y:.3f}_m', 'BUTTOCKS')
        sheet.curves(buttock.section(y).boundary, layer=layer)
        sheet.polyline([(xmin, plan_y+y), (xmax, plan_y+y)], 'GRID')
    sheet.curves(buttock.section(0).boundary)
    sheet.polyline([(xmin, 0), (xmax, 0)], 'CENTER')
    sheet.polyline([(xmin, plan_y), (xmax, plan_y)], 'CENTER')
    sheet.polyline([(body_x, 0), (body_x, zmax)], 'CENTER')
    sheet.dimension((0, 0), (lbp, 0), -6*h, 'LBP = <> m')
    note_y = plan_y-5*h
    sheet.text('站位编号与X坐标见 stations.csv；横剖线、水平水线和纵剖线均由目标船网格切取。', (xmin, note_y))
    sheet.text('纵剖线半宽 y / m：'+', '.join(f'{y:.3f}' for y in buttocks), (xmin, note_y-2.5*h))
    sheet.text('DXF模型空间1:1，单位mm；标注值单位m。艏端及上甲板封口沿用02模型的近似。', (xmin, note_y-5*h))
    sheet.save(out/'lines_plan')


def run(ctx, drawing):
    dimensions = ctx.data[2]['dimensions']
    stations, waterlines, buttocks = (drawing[key] for key in ('stations', 'waterlines', 'buttocks'))
    out = ctx.output(13)
    if drawing['template']:
        draw_lines(Sheet, ctx, drawing['template'], drawing['slicers'], stations, waterlines, buttocks)
    else:
        lines_plan(out, drawing['name'], dimensions, drawing['slicers'], stations, waterlines,
                   buttocks, drawing['bounds'], drawing['height'])
    write_csv(out/'stations.csv', [dict(station_id=i, x_m=x, side='aft' if x < dimensions['lbp_m']/2 else 'fore')
                                  for i, x in enumerate(stations)])
    rows = []
    for i, x in enumerate(stations):
        section = drawing['slicers'][0].section(x)
        for loop_id, points in enumerate(line_parts(section.boundary)):
            rows.extend(dict(station_id=i, x_m=x, loop_id=loop_id, point_id=j, y_m=y, z_m=z)
                        for j, (y, z) in enumerate(points))
    write_csv(out/'section_points.csv', rows)
    return dict(offsets_file=str(out/'offsets.csv') if drawing['template'] else None,
                stations_file=str(out/'stations.csv'), section_points_file=str(out/'section_points.csv'))
