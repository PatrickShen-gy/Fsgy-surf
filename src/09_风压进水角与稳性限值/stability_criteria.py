"""Opening immersion, wind response and stability limits."""
import math
import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import brentq
from fsgy_surf.io.files import save_json, write_csv

def wind_profile(t,rows):
    x=np.array([r['T_m'] for r in rows]);extra=t<x[0] or t>x[-1]
    result={}
    for field in ['area_m2','CGZ_m','moment_m3']:
        y=np.array([r[field] for r in rows])
        if t<x[0]:v=y[0]+(t-x[0])*(y[1]-y[0])/(x[1]-x[0])
        elif t>x[-1]:v=y[-1]+(t-x[-1])*(y[-1]-y[-2])/(x[-1]-x[-2])
        else:v=np.interp(t,x,y)
        result[field]=float(v)
    result['extrapolated']=bool(extra)
    return result


def flooding(case,openings,lbp,xref):
    points=[p for p in case['points'] if p['angle_deg']<=90]
    x=np.array([p['angle_deg'] for p in points]);side=1 if case['side']=='PS' else -1
    rows=[]
    for op in openings:
        margins=[]
        for p in points:
            phi=math.radians(side*p['angle_deg'])
            opening_z=-math.sin(phi)*op['y_m']+math.cos(phi)*op['z_m']
            wl=p['plane_level_rotated_m']+p['rotated_trim_m']/lbp*(op['x_m']-xref)
            margins.append(opening_z-wl)
        curve=PchipInterpolator(x,margins)
        roots=[float(r) for r in curve.roots(extrapolate=False) if 0<=r<=90]
        angle=0. if margins[0]<=0 else min(roots) if roots else None
        rows.append(dict(**op,flooding_angle_deg=angle,margin_at_upright_m=margins[0],method='08 sealed-hull pose + opening geometry'))
    finite=[r for r in rows if r['flooding_angle_deg'] is not None]
    first=min(finite,key=lambda r:r['flooding_angle_deg']) if finite else None
    return rows,None if first is None else first['flooding_angle_deg'],None if first is None else first['id']


def roots_level(fn,level,a,b):
    xs=np.linspace(a,b,801);ys=np.array([fn(x)-level for x in xs]);roots=[]
    for i in range(len(xs)-1):
        if ys[i]==0:roots.append(float(xs[i]))
        if ys[i]*ys[i+1]<0:roots.append(float(brentq(lambda t:fn(t)-level,xs[i],xs[i+1])))
    return sorted(set(round(r,10) for r in roots))


def weather(curve,GM,KG,t,mass,wp,deck_angle,fa,settings):
    B=settings['beam_m'];L=settings['LWL_m'];g=settings['g_m_s2']
    lw1=settings['P_N_m2']*wp['moment_m3']/(1000*g*mass);lw2=1.5*lw1
    CB=mass/(settings['rho_t_m3']*L*B*t)
    x1=float(np.interp(B/t,[2.4,2.5,2.6,2.7,2.8,2.9,3,3.1,3.2,3.3,3.4,3.5],[1,.98,.96,.95,.93,.91,.9,.88,.86,.84,.82,.8]))
    x2=float(np.interp(CB,[.45,.5,.55,.6,.65,.7],[.75,.82,.89,.95,.97,1]))
    k=float(np.interp(100*settings['bilge_keel_area_m2']/(L*B),[0,1,1.5,2,2.5,3,3.5,4],[1,.98,.95,.88,.79,.74,.72,.7]))
    period=2*B/math.sqrt(max(GM,1e-9))*(.373+.023*B/t-.043*L/100)
    sf=float(np.interp(period,[6,7,8,12,14,16,18,20],[.1,.098,.093,.065,.053,.044,.038,.035]))
    raw_r=.73+.6*(KG-t)/t
    rf=min(1.,raw_r) if settings.get('cap_r_at_one',True) else raw_r
    roll=109*k*x1*x2*math.sqrt(max(rf*sf,0.))
    def right(angle):
        a=abs(angle);base=float(curve(math.radians(a)))
        return base if angle>=0 else -base+2*float(curve(0.))*math.cos(math.radians(a))
    first=roots_level(right,lw1,0.,70.)
    gust=roots_level(right,lw2,0.,90.)
    theta0=first[0] if first else None
    theta_a=gust[0] if gust else None
    theta_c=gust[1] if len(gust)>1 else None
    theta2=min(50.,fa if fa is not None else 90.,theta_c if theta_c is not None else 90.)
    area_a=area_b=ratio=None
    if theta0 is not None and theta_a is not None and theta2>theta_a:
        from scipy.integrate import quad
        theta_r=theta0-roll
        area_a=quad(lambda a:lw2-right(math.degrees(a)),math.radians(theta_r),math.radians(theta_a),epsabs=1e-8,limit=150)[0]
        area_b=quad(lambda a:right(math.degrees(a))-lw2,math.radians(theta_a),math.radians(theta2),epsabs=1e-8,limit=150)[0]
        ratio=area_b/area_a if area_a>0 else None
    allowable=min(16.,.8*deck_angle) if deck_angle is not None else 16.
    return dict(lw1_m=lw1,lw2_m=lw2,roll_period_s=period,x1=x1,x2=x2,k=k,r=rf,r_uncapped=raw_r,r_cap_applied=rf!=raw_r,s=sf,roll_to_windward_deg=roll,
        steady_angle_deg=theta0,steady_limit_deg=allowable,steady_angle_met=theta0 is not None and theta0<=allowable,
        gust_first_intersection_deg=theta_a,gust_second_intersection_deg=theta_c,integration_limit_deg=theta2,
        area_a_mrad=area_a,area_b_mrad=area_b,ratio_b_a=ratio,ratio_met=ratio is not None and ratio>=1.,
        moment_profile_zero_trim_approx=True,negative_heel_extension='mirror with TCG cosine offset')


def run(ctx):
    config = ctx.config['criteria']
    dimensions = ctx.data[2]['dimensions']
    lbp = dimensions['lbp_m']
    eqs = {case['case_id']: case['calculated'] for case in ctx.data[7]['cases']}
    openings = config.get('openings', [])
    for opening in openings:
        for key in ('x_m', 'y_m', 'z_m'):
            if not np.isfinite(opening[key]):
                raise ValueError('Opening coordinates must be finite')
    thresholds = dict(AREA30=.055, AREA40=.09, AREA3040=.03, GZ=.2, GM=.15, MAXGZ=25.)
    thresholds.update(config.get('thresholds', {}))
    if any(not np.isfinite(v) or v < 0 for v in thresholds.values()):
        raise ValueError('criteria.thresholds must be finite and nonnegative')
    results = []
    for case in ctx.data[8]['cases']:
        pts = [p for p in case['points'] if p['angle_deg'] <= 90.]
        if pts[-1]['angle_deg'] < 90.:
            raise ValueError('criteria requires stability angles covering 0 to 90 degrees')
        angles = np.radians([p['angle_deg'] for p in pts])
        curve = PchipInterpolator(angles, [p['GZ_m'] for p in pts], extrapolate=False)
        integral = curve.antiderivative()
        opening_results, fa, first = flooding(case, openings, lbp, lbp/2)
        end = min(40., fa if fa is not None else 40.)
        a30 = float(integral(math.radians(min(30., end)))-integral(0.))
        a40 = float(integral(math.radians(end))-integral(0.))
        a3040 = float(integral(math.radians(end))-integral(math.radians(30))) if end >= 30 else 0.
        grid = np.linspace(0., 90., 1801)
        vals = curve(np.radians(grid))
        gm = case['summary']['GM_from_GZ_slope_m']
        kg = case['load']['KG_m']
        limits = dict(
            AREA30=kg+(a30-thresholds['AREA30'])/max(1-math.cos(math.radians(min(30., end))), 1e-9),
            AREA40=kg+(a40-thresholds['AREA40'])/max(1-math.cos(math.radians(end)), 1e-9),
            AREA3040=kg+(a3040-thresholds['AREA3040'])/max(math.cos(math.radians(30))-math.cos(math.radians(end)), 1e-9),
            GM=kg+gm-thresholds['GM'],
            GZ=max(kg+(float(curve(a))-thresholds['GZ'])/math.sin(a) for a in np.radians(np.linspace(30, 90, 1201))))
        kg_min, kg_max, kg_count = config.get('kg_scan_m', [-5., 35., 801])
        if not np.isfinite([kg_min, kg_max]).all() or kg_min >= kg_max or int(kg_count) < 2:
            raise ValueError('criteria.kg_scan_m requires [minimum, maximum, point_count]')
        admissible = [float(k) for k in np.linspace(kg_min, kg_max, int(kg_count))
                      if grid[int(np.argmax(vals-(k-kg)*np.sin(np.radians(grid))))] >= thresholds['MAXGZ']]
        # A missing interval has no finite upper KG, rather than an invented boundary value.
        limits['MAXGZ'] = max(admissible) if admissible else None
        finite_limits = {k: v for k, v in limits.items() if v is not None}
        controlling = min(finite_limits, key=finite_limits.get)
        wind = None
        weather_input = config.get('weather')
        if weather_input:
            if gm <= 0:
                raise ValueError('Weather calculation requires positive initial GM')
            profile = [dict(row) for row in weather_input['wind_profile']]
            if len(profile) < 2 or any(b['T_m'] <= a['T_m'] for a, b in zip(profile, profile[1:])):
                raise ValueError('weather.wind_profile requires at least two increasing T_m values')
            for row in profile:
                row.setdefault('moment_m3', row['area_m2']*(row['CGZ_m']-row['T_m']/2))
            t = eqs[case['case_id']]['T_m']
            wp = wind_profile(t, profile)
            settings = dict(P_N_m2=float(weather_input.get('pressure_N_m2', 504.)),
                g_m_s2=9.81, bilge_keel_area_m2=float(weather_input['bilge_keel_area_m2']),
                beam_m=dimensions['beam_m'], LWL_m=float(weather_input['lwl_m']),
                rho_t_m3=ctx.config['environment']['water_density_t_m3'],
                cap_r_at_one=weather_input.get('cap_r_at_one', True))
            deck = case['summary']['first_sampled_deck_immersion_deg']
            wind = weather(curve, gm, kg, t, case['load']['mass_t'], wp, deck, fa, settings)
            wind.pop('steady_angle_met', None)
            wind.pop('ratio_met', None)
            wind['profile'] = wp
        results.append(dict(case_id=case['case_id'], curve_id=case['curve_id'],
            area_0_30_mrad=a30, area_0_40_or_flooding_mrad=a40, area_30_40_or_flooding_mrad=a3040,
            GZmax_m=float(vals.max()), GZmax_angle_deg=float(grid[int(np.argmax(vals))]),
            GM_m=gm, openings=opening_results, flooding_angle_deg=fa, first_opening=first,
            wind=wind, KG_limits_by_criterion=limits, KG_limit_basic_m=finite_limits[controlling],
            KG_limit_controlling=controlling,
            KG_limit_model='Frozen buoyancy curve at this case; weather excluded',
            MAXGZ_limit_scan_range_m=[kg_min, kg_max]))
    result = dict(cases=results)
    save_json(ctx.output(9)/'criteria.json', result)
    write_csv(ctx.output(9)/'criteria.csv', results)
    return result
