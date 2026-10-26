import json
import unittest
import equilibrium_calculation as e

np=e.np


def box_kernel():
    yz=np.array([[-3.,0.],[3.,0.],[3.,5.],[-3.,5.]])
    v=np.vstack([np.column_stack([np.full(4,x),yz]) for x in (0.,15.)])
    f=[];physical=[]
    for i in range(4):
        j=(i+1)%4;f.extend([(i,j,j+4),(i,j+4,i+4)]);physical.extend([i!=2]*2)
    f.extend([(0,2,1),(0,3,2),(4,5,6),(4,6,7)]);physical.extend([True]*4)
    return e.HydrostaticCalculator(v,np.array(f),physical,lbp=15.,beam=6.,xref=7.5,rho=1.,shell_thickness=0.)


class AnalyticTests(unittest.TestCase):
    def test_box_with_oblique_waterplane(self):
        hydro=e.InclinedHydrostatics(box_kernel())
        T,trim,heel=2.,1.2,1.2;s=trim/15;h=np.tan(np.radians(heel))
        r=hydro.evaluate(T,trim,heel)
        self.assertAlmostEqual(r['VOLM_m3'],15*6*T,places=9)
        self.assertAlmostEqual(r['LCB_m'],7.5+s*15**2/(12*T),places=9)
        self.assertAlmostEqual(r['TCB_m'],h*6**2/(12*T),places=9)
        self.assertAlmostEqual(r['VCB_m'],T/2+(s*s*15**2+h*h*6**2)/(24*T),places=9)
        self.assertAlmostEqual(r['WLA_horizontal_m2'],90.,places=9)
        eps=1e-5
        derivative=(hydro.evaluate(T+eps,trim,heel)['VOLM_m3']-hydro.evaluate(T-eps,trim,heel)['VOLM_m3'])/(2*eps)
        self.assertAlmostEqual(derivative,90.,places=7)

    def test_upright_box_equilibrium_and_GM(self):
        r=e.EquilibriumSolver(box_kernel()).solve(dict(mass_t=180.,LCG_m=7.5,TCG_m=0.,KG_m=1.,FSM_tm=0.))
        self.assertAlmostEqual(r['T_m'],2.,places=8)
        self.assertAlmostEqual(r['trim_m'],0.,places=8)
        self.assertAlmostEqual(r['heel_deg'],0.,places=8)
        self.assertAlmostEqual(r['GM_m'],1.5,places=8)
        self.assertTrue(r['usable_small_angle_equilibrium'])

    def test_recover_analytic_trim_and_heel_with_free_surface(self):
        T,trim,heel,KG,corr=2.,1.2,1.2,.8,.2
        s=trim/15;h=np.tan(np.radians(heel))
        bx=7.5+s*15**2/(12*T);by=h*6**2/(12*T)
        bz=T/2+(s*s*15**2+h*h*6**2)/(24*T)
        # 解析楔形体浮心，重心投影与浮心重合；横向附加虚升corr。
        load=dict(mass_t=180.,LCG_m=bx+s*(bz-KG),TCG_m=by+h*(bz-KG-corr),KG_m=KG,FSM_tm=180*corr)
        r=e.EquilibriumSolver(box_kernel()).solve(load)
        np.testing.assert_allclose([r['T_m'],r['trim_m'],r['heel_deg']],[T,trim,heel],atol=1e-7)
        self.assertTrue(r['numerical_converged'])

    def test_reference_fields_do_not_control_solution(self):
        load=dict(mass_t=180.,LCG_m=7.5,TCG_m=0.,KG_m=1.,FSM_tm=0.,reference_T_m=100.,reference_trim_m=-99.)
        solver=e.EquilibriumSolver(box_kernel())
        a=solver.solve(load)
        b=solver.solve(dict(load,FSM_tm=36.))
        self.assertAlmostEqual(a['T_m'],2.,places=8)
        self.assertAlmostEqual(a['GM_m']-b['GM_m'],.2,places=8)
        # 相同几何姿态不同载荷的结果不应被水静力缓存相互改写。
        self.assertAlmostEqual(a['KG_correction_m'],0.,places=12)

    def test_negative_GM_is_not_marked_usable(self):
        r=e.EquilibriumSolver(box_kernel()).solve(dict(mass_t=180.,LCG_m=7.5,TCG_m=0.,KG_m=10.,FSM_tm=0.))
        self.assertTrue(r['numerical_converged'])
        self.assertFalse(r['positive_GM'])
        self.assertFalse(r['usable_small_angle_equilibrium'])

    def test_out_of_range_displacement_rejected(self):
        with self.assertRaises(ValueError):
            e.EquilibriumSolver(box_kernel()).solve(dict(mass_t=1000.,LCG_m=7.5,TCG_m=0.,KG_m=1.,FSM_tm=0.))


class ArtifactTests(unittest.TestCase):
    def test_full_ship_equilibria_and_interfaces(self):
        data=e.load_results(verify_sources=True)
        self.assertEqual(len(data['cases']),39)
        report=json.loads((e.HERE/'output/verification_report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['all_numerical_converged'])
        self.assertEqual(report['usable_small_angle_cases'],39)
        self.assertLess(report['max_mass_residual_t'],.01)
        self.assertLess(report['max_longitudinal_lever_residual_m'],1e-6)
        self.assertLess(report['max_transverse_lever_residual_m'],1e-6)
        for c in data['cases']:
            r=c['calculated']
            self.assertAlmostEqual(r['TF_m']-r['TA_m'],r['trim_m'],places=10)
        with np.load(e.HERE/'output/equilibrium_data.npz') as a:
            self.assertEqual(a['case_ids'].shape,(39,))
            self.assertTrue(np.isfinite(a['GM_m']).all())

    def test_reference_extraction_and_source_choice(self):
        data=e.load_results()
        case=next(c for c in data['cases'] if c['case_id']=='LD09C')
        self.assertEqual(case['reference']['T_m'],10.224)
        self.assertEqual(case['reference']['trim_m'],-.763)
        self.assertEqual(case['load']['mass_t'],43347.1)
        self.assertTrue(all(c['reference']['GM_criteria_m'] is not None for c in data['cases']))
        html=(e.HERE/'output/equilibrium_verification.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__',html)
        self.assertNotIn('https://',html)


if __name__=='__main__':unittest.main()
