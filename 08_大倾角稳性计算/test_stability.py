import json
import math
import unittest
import stability_calculation as s
from test_equilibrium import box_kernel

np=s.np


class AnalyticTests(unittest.TestCase):
    def test_wall_sided_box_righting_lever(self):
        hydro=s.RollHydrostatics(box_kernel())
        load=dict(mass_t=180.,LCG_m=7.5,TCG_m=0.,KG_m=1.,FSM_tm=0.)
        BM=6**2/(12*2.)
        GM=BM+1.-1.
        for theta in [0.,5.,10.]:
            r=hydro.solve_pose(load,theta)
            phi=math.radians(theta)
            expected=(GM+.5*BM*math.tan(phi)**2)*math.sin(phi)
            self.assertAlmostEqual(r['GZ_signed_fixed_m'],expected,places=8)
            self.assertLess(abs(r['mass_residual_t']),.01)
            self.assertLess(abs(r['pitch_lever_residual_m']),1e-6)

    def test_free_surface_is_sine_correction(self):
        hydro=s.RollHydrostatics(box_kernel())
        load=dict(mass_t=180.,LCG_m=7.5,TCG_m=0.,KG_m=1.,FSM_tm=36.)
        pose=hydro.solve_pose(load,10.)
        row=s.curve_point(pose,load,10.,1)
        self.assertAlmostEqual(row['DGZ_m'],.2*math.sin(math.radians(10.)),places=10)
        self.assertAlmostEqual(row['GZ_fixed_m']-row['GZ_m'],row['DGZ_m'],places=10)

    def test_port_starboard_symmetry(self):
        hydro=s.RollHydrostatics(box_kernel())
        load=dict(mass_t=180.,LCG_m=7.5,TCG_m=.1,KG_m=1.,FSM_tm=0.)
        a=s.curve_point(hydro.solve_pose(load,10.),load,10.,1)
        other=dict(load,TCG_m=-.1)
        b=s.curve_point(hydro.solve_pose(other,-10.),other,10.,-1)
        self.assertAlmostEqual(a['GZ_m'],b['GZ_m'],places=9)

    def test_large_angle_datum_and_deck_flag(self):
        hydro=s.RollHydrostatics(box_kernel())
        load=dict(mass_t=180.,LCG_m=7.5,TCG_m=0.,KG_m=1.,FSM_tm=0.)
        r=hydro.solve_pose(load,90.)
        self.assertAlmostEqual(r['GZ_signed_fixed_m'],1.5,places=8)
        self.assertTrue(r['deck_immersed'])
        r=hydro.solve_pose(load,180.)
        self.assertAlmostEqual(r['GZ_signed_fixed_m'],0.,places=8)

    def test_radian_area_and_vanishing_angle(self):
        rows=[dict(angle_deg=float(a),GZ_m=2*math.sin(math.radians(a)),deck_immersed=a>=15) for a in s.ANGLES]
        summary=s.summarize_curve(rows)
        self.assertAlmostEqual(summary['area_0_30_mrad'],2*(1-math.cos(math.radians(30))),delta=2e-5)
        rows=[dict(angle_deg=float(a),GZ_m=math.sin(math.radians(a))*(math.cos(math.radians(a))+.2),deck_immersed=a>=15) for a in s.ANGLES]
        summary=s.summarize_curve(rows)
        self.assertAlmostEqual(summary['AVS_sealed_hull_deg'],math.degrees(math.acos(-.2)),delta=.05)
        self.assertEqual(summary['first_sampled_deck_immersion_deg'],15.)


class ArtifactTests(unittest.TestCase):
    def test_reference_complete_and_not_reinterpreted(self):
        r=json.loads((s.HERE/'output/reference/pdf_stability.json').read_text(encoding='utf-8'))
        self.assertEqual(r['case_row_count'],351)
        self.assertEqual(len(r['kn_rows']),203)
        self.assertTrue(all(c['MS_within_printing'] and c['DGZ_within_printing'] for c in r['reference_identity_checks']))
        c=next(c for c in r['cases'] if c['case_id']=='LD04A')
        self.assertEqual(c['side'],'SB')
        self.assertEqual(c['rows'][4]['GZ_m'],1.44)
        self.assertEqual(c['rows'][4]['angle_deg'],30.)

    def test_all_equilibria_and_diagnostic_markers(self):
        data=s.load_results(verify_sources=True)
        self.assertEqual(len(data['cases']),39)
        report=json.loads((s.HERE/'output/verification_report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['all_numerical_converged'])
        self.assertFalse(report['full_vessel_geometry_complete'])
        self.assertEqual(report['mesh_point_count'],39*len(s.ANGLES))
        self.assertLess(report['max_mass_residual_t'],.01)
        self.assertLess(report['max_pitch_lever_residual_m'],1e-6)
        self.assertGreater(report['post_deck_point_count'],0)
        self.assertTrue(all(c['summary']['AVS_sealed_hull_deg'] is not None for c in data['cases']))
        with np.load(s.HERE/'output/stability_data.npz') as a:
            self.assertEqual(a['GZ_m'].shape,(39,len(s.ANGLES)))
            self.assertTrue(np.isfinite(a['GZ_m']).all())

    def test_offline_viewer_complete(self):
        html=(s.HERE/'output/stability_verification.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__',html)
        self.assertNotIn('https://',html)


if __name__=='__main__':unittest.main()
