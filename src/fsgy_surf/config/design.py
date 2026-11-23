"""Resolve target dimensions and compare design inputs with mesh calculations."""
import math

import numpy as np


DIMENSIONS = [
    ('loa_m', '总长 LOA', 'm'),
    ('lbp_m', '垂线间长 LBP', 'm'),
    ('beam_m', '型宽 B', 'm'),
    ('depth_m', '型深 D', 'm'),
    ('design_draft_m', '设计吃水 T', 'm'),
    ('scantling_draft_m', '结构吃水 Ts', 'm'),
]
PARAMETERS = [
    ('displacement_t', 'Ts排水量', 't'),
    ('lightweight_t', '空船重量 LW0', 't'),
    ('deadweight_t', 'Ts载重量 DW', 't'),
    ('block_coefficient', 'Ts方形系数 Cb', '—'),
]


def resolve_dimensions(source, requested):
    target = dict(requested)
    for key in ('lbp_m', 'beam_m', 'depth_m'):
        target[key] = float(requested.get(key, source[key]))
    length_scale = target['lbp_m'] / source['lbp_m']
    depth_scale = target['depth_m'] / source['depth_m']
    target['loa_m'] = float(requested.get('loa_m', source['loa_m'] * length_scale))
    target['design_draft_m'] = float(requested.get('design_draft_m', source['design_draft_m'] * depth_scale))
    source_scantling = source.get('scantling_draft_m', source['design_draft_m'])
    target['scantling_draft_m'] = float(requested.get('scantling_draft_m', source_scantling * depth_scale))
    if source['loa_m'] <= source['lbp_m']:
        raise ValueError('The mother-hull mapping requires mother_ship.dimensions.loa_m > lbp_m')
    if target['loa_m'] <= target['lbp_m']:
        raise ValueError('The mother-hull mapping requires target loa_m > lbp_m')
    if max(target['design_draft_m'], target['scantling_draft_m']) > target['depth_m']:
        raise ValueError('Design and scantling drafts must not exceed depth_m')
    return target


def longitudinal_scales(source, target):
    between_perpendiculars = target['lbp_m'] / source['lbp_m']
    overhang = (target['loa_m'] - target['lbp_m']) / (source['loa_m'] - source['lbp_m'])
    return between_perpendiculars, overhang


def longitudinal_x(x, source, target):
    """Scale station coordinates between AP=0 and FP=LBP, and scale the overhangs separately."""
    x = np.asarray(x, dtype=float)
    between_perpendiculars, overhang = longitudinal_scales(source, target)
    if math.isclose(between_perpendiculars, overhang, rel_tol=1e-12):
        return x * between_perpendiculars
    fore = target['lbp_m'] + (x - source['lbp_m']) * overhang
    return np.where(x < 0, x * overhang,
                    np.where(x > source['lbp_m'], fore, x * between_perpendiculars))


def requested_lightweight(config):
    values = config.get('design', {}).get('target_parameters', {})
    if 'lightweight_t' in values:
        return float(values['lightweight_t'])
    if 'displacement_t' in values and 'deadweight_t' in values:
        mass = float(values['displacement_t'] - values['deadweight_t'])
        if mass <= 0:
            raise ValueError('displacement_t - deadweight_t must be positive for LW0')
        return mass
    return None


def lightweight_value(config):
    value = requested_lightweight(config)
    if value is not None:
        return value, 'design.target_parameters'
    loading = config.get('loading') or {}
    components = loading.get('lightweight_components')
    if components:
        if all('mass_t' in item and 'geometry_id' not in item for item in components):
            return math.fsum(float(item['mass_t']) for item in components), 'loading.lightweight_components'
    else:
        item = loading.get('lightweight')
        if item and 'mass_t' in item and 'geometry_id' not in item:
            return float(item['mass_t']), 'loading.lightweight'
    return None, 'not_supplied'


def principal_summary(ctx, scantling_point=None):
    dimensions = ctx.data[2]['dimensions']
    ts = dimensions['scantling_draft_m']
    design = ctx.config['design']
    requested_dimensions = design.get('target_dimensions', {})
    requested_parameters = design.get('target_parameters', {})
    point = scantling_point if scantling_point is not None else ctx.hydro().evaluate(ts)
    light, light_source = lightweight_value(ctx.config)
    calculated = dict(displacement_t=point['DISP'], block_coefficient=point['CB'],
                      lightweight_t=light, deadweight_t=None if light is None else point['DISP'] - light)
    rows = []
    for key, label, unit in DIMENSIONS + PARAMETERS:
        if key in calculated:
            supplied, value = requested_parameters.get(key), calculated[key]
        else:
            supplied, value = requested_dimensions.get(key), dimensions[key]
        rows.append(dict(parameter=key, label=label, unit=unit, input_value=supplied,
                         calculated_value=value, adopted_value=value if supplied is None else supplied,
                         difference=None if supplied is None or value is None else value - supplied))
    rho = ctx.config['environment']['water_density_t_m3']
    displacement = requested_parameters.get('displacement_t')
    deadweight = requested_parameters.get('deadweight_t')
    lightweight = requested_parameters.get('lightweight_t')
    cb = requested_parameters.get('block_coefficient')
    cb_displacement = None if cb is None else rho * dimensions['lbp_m'] * dimensions['beam_m'] * ts * cb
    mass_error = None
    if displacement is not None and lightweight is not None and deadweight is not None:
        mass_error = displacement - lightweight - deadweight
    return dict(reference_draft_m=ts, water_density_t_m3=rho, inputs=requested_parameters,
                calculated=calculated, lightweight_source=light_source, rows=rows,
                input_mass_balance_error_t=mass_error, displacement_implied_by_input_cb_t=cb_displacement,
                input_displacement_minus_cb_implied_t=None if displacement is None or cb_displacement is None
                else displacement - cb_displacement,
                cb_basis='Moulded volume / (LBP * B * Ts)')
