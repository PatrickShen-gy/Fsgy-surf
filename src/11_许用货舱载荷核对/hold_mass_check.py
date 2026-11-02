"""User-defined hold-load envelopes and available margins."""
import numpy as np
from fsgy_surf.io.files import save_json, write_csv


def envelope(curve, draft):
    x = np.asarray(curve['draft_m'], float)
    low = np.asarray(curve.get('minimum_t', [0.]*len(x)), float)
    high = np.asarray(curve['maximum_t'], float)
    if x.ndim != 1 or len(x) < 2 or low.shape != x.shape or high.shape != x.shape:
        raise ValueError('Each envelope needs matching draft_m, minimum_t, maximum_t arrays')
    if not np.isfinite(np.r_[x, low, high]).all() or np.any(np.diff(x) <= 0) or np.any(low < 0) or np.any(high < low):
        raise ValueError('Envelope coordinates must be finite, drafts increasing, 0 <= minimum <= maximum')
    if draft < x[0] or draft > x[-1]:
        return None, None
    return float(np.interp(draft, x, low)), float(np.interp(draft, x, high))


def run(ctx):
    curves = ctx.config['hold_loads']['envelopes']
    spaces = {space['id']: space for space in ctx.data[2]['compartments']}
    eqs = {case['case_id']: case['calculated'] for case in ctx.data[7]['cases']}
    lbp = ctx.data[2]['dimensions']['lbp_m']
    rows = []
    for curve in curves:
        holds = curve['holds']
        if not holds or len(set(holds)) != len(holds):
            raise ValueError('Envelope holds must be a nonempty list of unique compartment IDs')
        if any(ident not in spaces or spaces[ident]['category'] != 'cargo' for ident in holds):
            raise ValueError('Envelope holds must refer to cargo compartment IDs')
        ordered = sorted((spaces[ident] for ident in holds), key=lambda p: p['start_m'])
        all_cargo = sorted((p for p in spaces.values() if p['category'] == 'cargo'), key=lambda p: p['start_m'])
        indices = [all_cargo.index(p) for p in ordered]
        if len(holds) > 2 or (len(indices) == 2 and indices[1]-indices[0] != 1):
            raise ValueError('Each envelope supports one hold or two adjacent holds')
        x = float(curve.get('evaluation_x_m', (ordered[0]['start_m']+ordered[-1]['end_m'])/2))
        if not np.isfinite(x):
            raise ValueError('evaluation_x_m must be finite')
        for case in ctx.data[6]['cases']:
            eq = eqs[case['case_id']]
            draft = eq['T_m']+eq['trim_m']/lbp*(x-lbp/2)
            mass = 0.
            for item in case['items']:
                hold = item.get('hold_id', item.get('geometry_id'))
                if item['mass_t'] > 0 and hold is None and item.get('category') == 'Cargo':
                    raise ValueError(f'{item["id"]}: Cargo items require hold_id')
                if hold in holds:
                    mass += item['mass_t']
            low, high = envelope(curve, draft)
            rows.append(dict(case_id=case['case_id'], envelope_id=curve['id'],
                mode=curve.get('mode', 'user_defined'), holds=holds, evaluation_x_m=x,
                local_draft_m=draft, hold_payload_t=mass, minimum_t=low, maximum_t=high,
                lower_margin_t=None if low is None else mass-low,
                upper_margin_t=None if high is None else high-mass,
                in_input_domain=low is not None))
    result = dict(results=rows)
    save_json(ctx.output(11)/'hold_loads.json', result)
    write_csv(ctx.output(11)/'hold_loads.csv', rows)
    return result
