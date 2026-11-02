"""User loading weights, moments and free-surface correction."""
import math
from fsgy_surf.io.files import save_json, write_csv

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


def load_item(ctx, spec):
    row = dict(spec)
    row.setdefault('category', 'Mass Load')
    if 'hold_id' in row and row['hold_id'] not in {
            space['id'] for space in ctx.data[2]['compartments'] if space['category'] == 'cargo'}:
        raise ValueError(f'Unknown cargo hold_id: {row["hold_id"]}')
    if 'geometry_id' in row:
        density = float(row['density_t_m3'])
        if not math.isfinite(density) or density <= 0:
            raise ValueError('density_t_m3 must be positive')
        fill = float(row['fill_fraction'])
        attributes = ctx.fill(row['geometry_id'], fill)
        if type(row.get('free_surface', True)) is not bool:
            raise ValueError('free_surface must be true or false')
        row.update(mass_t=attributes['volume_m3']*density,
                   LCG_m=attributes['CGX_m'] if attributes['CGX_m'] is not None else 0.,
                   TCG_m=attributes['CGY_m'] if attributes['CGY_m'] is not None else 0.,
                   KG_m=attributes['CGZ_m'] if attributes['CGZ_m'] is not None else 0.,
                   FSM_tm=attributes['free_surface_IT_m4']*density if row.get('free_surface', True) else 0.)
        space = next(p for p in ctx.data[2]['compartments'] if p['id'] == row['geometry_id'])
        row.setdefault('span_m', [space['start_m'], space['end_m']])
    else:
        for key in ('mass_t', 'LCG_m', 'KG_m'):
            row[key] = float(row[key])
        row.setdefault('TCG_m', 0.)
        row.setdefault('FSM_tm', 0.)
    row['TCG_m'] = float(row['TCG_m'])
    row['FSM_tm'] = float(row['FSM_tm'])
    if 'span_m' in row:
        if len(row['span_m']) != 2:
            raise ValueError('span_m requires [aft_x_m, fore_x_m]')
        a, b = map(float, row['span_m'])
        if not (math.isfinite(a) and math.isfinite(b) and a < b):
            raise ValueError('span_m requires finite aft_x_m < fore_x_m')
        row['span_m'] = [a, b]
    return row


def ballast_items(ctx, states, existing_items):
    """Expand region ID -> fill fraction; all geometric properties come from stage 02/05."""
    if not isinstance(states, dict):
        raise ValueError('ballast must map spatial block IDs to fill fractions')
    blocks = {block['id']: block for block in ctx.data[2]['compartments']}
    existing = {item['geometry_id'] for item in existing_items if 'geometry_id' in item}
    density = ctx.config['environment']['water_density_t_m3']
    result = []
    for ident, fill in states.items():
        if ident not in blocks or blocks[ident]['purpose'] != 'ballast_water':
            raise ValueError(f'{ident}: select a ballast_water region from results/02/spatial_blocks.json')
        if ident in existing:
            raise ValueError(f'{ident}: specify ballast state either in ballast or items, not both')
        if isinstance(fill, bool):
            fill = int(fill)
        if not isinstance(fill, (int, float)) or not math.isfinite(fill) or not 0 <= fill <= 1:
            raise ValueError(f'{ident}: ballast fill fraction must be between 0 and 1')
        result.append(load_item(ctx, dict(id='BALLAST_'+ident, category='Ballast Water',
            geometry_id=ident, fill_fraction=float(fill), density_t_m3=density, free_surface=True)))
    return result


def run(ctx):
    loading = ctx.config['loading']
    cases, totals, item_rows = [], [], []
    for case in loading['cases']:
        cid = case['case_id']
        raw_light = case.get('lightweight', loading.get('lightweight'))
        components = case.get('lightweight_components', loading.get('lightweight_components'))
        if components:
            light_items = [load_item(ctx, dict(item, category='Lightweight')) for item in components]
            light = dict(id='LIGHTSHIP', category='Lightweight', **aggregate(light_items))
        elif raw_light:
            light = load_item(ctx, dict(raw_light, category='Lightweight'))
            light.setdefault('id', 'LIGHTSHIP')
            light_items = [light]
        else:
            raise ValueError(f'{cid}: provide lightweight or lightweight_components')
        items = [load_item(ctx, item) for item in case.get('items', [])]
        states = dict(loading.get('ballast', {}))
        states.update(case.get('ballast', {}))
        items.extend(ballast_items(ctx, states, items))
        if len({row['id'] for row in items}) != len(items):
            raise ValueError(f'{cid}: item IDs must be unique')
        calculated = calculate_case(items, light)
        entry = dict(case_id=cid, description=case.get('description', cid),
                     items=items, lightweight_components=light_items,
                     calculated=calculated)
        cases.append(entry)
        totals.append(dict(case_id=cid, **calculated['total']))
        item_rows.extend(dict(case_id=cid, **item) for item in light_items + items)
    out = ctx.output(6)
    result = dict(cases=cases)
    save_json(out/'weights.json', result)
    write_csv(out/'weights.csv', totals)
    write_csv(out/'items.csv', item_rows)
    return result
