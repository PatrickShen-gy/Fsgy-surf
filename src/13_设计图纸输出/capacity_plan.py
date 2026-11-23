"""13.4: 舱容图及模型满舱容积、容积重心表。"""
import numpy as np
from fsgy_surf.io.files import write_csv
from drawing_common import Sheet, DrawingSlicer, hull_deck, model_cut, BLOCK_LAYERS
from template_common import load_template, style_sheet, rectangle, title_block

PURPOSES = {'cargo':'货舱', 'ballast_water':'压载水', 'machinery':'机舱', 'unassigned':'未指定',
            'fresh_water':'淡水', 'fuel_oil':'燃油', 'lubricating_oil':'滑油', 'void':'空舱'}


def capacity_table(sheet,ctx,settings,scale):
    x0,top=np.array(settings['table_origin'])*scale
    width=settings['table_width']*scale
    blocks=ctx.data[2]['compartments']
    fractions=np.array([.22,.23,.14,.13,.13,.15])
    xs=x0+np.r_[0,np.cumsum(fractions)]*width
    row_h=3.05*scale
    bottom=top-row_h*(len(blocks)+1)
    for x in xs:sheet.polyline([(x,bottom),(x,top)],'REF_3')
    for i in range(len(blocks)+2):sheet.polyline([(x0,top-i*row_h),(x0+width,top-i*row_h)],'REF_3')
    rows=[['空间编号','容积/m³','LCG/m','TCG/m','KG/m','用途']]
    output=[]
    for b in blocks:
        cx,cy,cz=b['center_m']
        purpose=PURPOSES[b['purpose']]
        rows.append([b['id'],f'{b["volume_m3"]:.1f}',f'{cx:.3f}',f'{cy:.3f}',f'{cz:.3f}',purpose])
        output.append(dict(id=b['id'],purpose=b['purpose'],volume_m3=b['volume_m3'],LCG_m=cx,TCG_m=cy,KG_m=cz))
    for i,row in enumerate(rows):
        for j,text in enumerate(row):
            sheet.text(text,((xs[j]+xs[j+1])/2,top-(i+.68)*row_h),.62*scale,centered=True)
    sheet.text('模型满舱容积与容积重心',(x0,top+1.4*scale),.85*scale)
    write_csv(ctx.output(13)/'capacity_plan.csv',output)


def draw_capacity_plan(sheet_class,slice_class,ctx,template,plan_z):
    settings=template['capacity_plan']
    dims=ctx.data[2]['dimensions'];beam=dims['beam_m']
    scale=max(*ctx.data[2]['scale_xyz'],dims['loa_m']/ctx.data[1]['dimensions']['loa_m']);h=.72*scale
    sheet=sheet_class(h*.65);style_sheet(sheet,template)
    frame=np.array(settings['frame'])*scale
    rectangle(sheet,frame)
    rectangle(sheet,frame+[1.5*scale,1.5*scale,-1.5*scale,-1.5*scale],'REF_6')
    profile=np.array(settings['profile_origin'])*scale
    plan=np.array(settings['plan_origin'])*scale
    bottom=np.array(settings['bottom_origin'])*scale
    bottom_z=ctx.data[2]['layout']['double_bottom_m']/2
    model_cut(sheet,slice_class,ctx,1,0,profile,h,capacity=True)
    model_cut(sheet,slice_class,ctx,2,plan_z,plan,h,capacity=True)
    model_cut(sheet,slice_class,ctx,2,bottom_z,bottom,h,capacity=True)
    deck=hull_deck(ctx)
    sheet.polyline(deck[:,[0,2]]+profile,'REF_3')
    sheet.text('舱容图 / CAPACITY PLAN',(-5*scale,50*scale),h*2)
    sheet.text('中纵剖面及舱室容积投影',(-4*scale,44*scale),h*1.1)
    sheet.text(f'水平分舱图 Z={plan_z:.3f} m',(-4*scale,plan[1]+beam/2+4*scale),h*1.1)
    sheet.text(f'双层底水平剖面 Z={bottom_z:.3f} m',(-4*scale,bottom[1]+beam/2+4*scale),h*1.1)
    cargo=[b for b in ctx.data[2]['compartments'] if b['category']=='cargo']
    spaces=ctx.arrays(ctx.data[2]['compartments_file'])
    for ident,at in zip(['HOLD_3','HOLD_1'],settings['section_origins']):
        block=next(b for b in cargo if b['id']==ident)
        x=(block['start_m']+block['end_m'])/2
        outer=slice_class(ctx.geometry()['outer_vertices'],ctx.geometry()['hydro_faces']).section(x)
        at=np.array(at)*scale
        sheet.curves(outer.boundary,at,'HULL')
        for b in ctx.data[2]['compartments']:
            if b['start_m']<x<b['end_m']:
                section=slice_class(spaces[b['id']+'_vertices'],spaces[b['id']+'_faces']).section(x)
                sheet.curves(section.boundary,at,BLOCK_LAYERS[b['category']])
        sheet.text(f'{ident} X={x:.2f} m',at+[-beam/2,-2*scale],h*.85)
    capacity_table(sheet,ctx,settings,scale)
    totals=[(name,sum(b['volume_m3'] for b in ctx.data[2]['compartments'] if b['category']==category))
            for category,name in [('cargo','货舱合计'),('ballast','边舱合计'),('double_bottom','双层底空间合计')]]
    for i,(name,value) in enumerate(totals):sheet.text(f'{name}: {value:.1f} m³',(-4*scale,(-84-i*3)*scale),h)
    sheet.text('容积及重心直接取02封闭分舱模型；CSV保留未取整值。',(-4*scale,-97*scale),h*.85)
    sheet.text('未细分空间按包络列示；用途未指定的空间不自动认定为燃油舱或压载舱。',(-4*scale,-100*scale),h*.85)
    title_block(sheet,np.array(settings['title_block'])*scale,ctx.config.get('name',ctx.case_dir.name),'舱容图','FSGY-13-04')
    sheet.save(ctx.output(13)/'capacity_plan')


def run(ctx, drawing):
    draw_capacity_plan(Sheet, DrawingSlicer, ctx, drawing['template'] or load_template('mother_35000'),
                       drawing['plan_z'])
    return dict(capacity_plan_file=str(ctx.output(13)/'capacity_plan.csv'))
