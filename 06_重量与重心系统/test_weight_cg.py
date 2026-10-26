import json
import unittest
import weight_cg_calculation as w


def item(ident,mass,x,y,z,fsm=0.):
    return dict(id=ident,category='Test',mass_t=mass,LCG_m=x,TCG_m=y,KG_m=z,FSM_tm=fsm)


class AnalyticTests(unittest.TestCase):
    def test_mass_moments_and_virtual_KG(self):
        r=w.calculate_case([item('A',100.,100.,2.,8.,200.),item('B',300.,20.,-1.,2.)],item('LIGHT',1000.,50.,0.,5.))['total']
        self.assertEqual(r['mass_t'],1400.)
        self.assertAlmostEqual(r['LCG_m'],66000/1400,places=12)
        self.assertAlmostEqual(r['TCG_m'],-100/1400,places=12)
        self.assertAlmostEqual(r['KG_m'],6400/1400,places=12)
        self.assertAlmostEqual(r['KG_correction_m'],200/1400,places=12)
        self.assertAlmostEqual(r['KG_effective_m'],6600/1400,places=12)

    def test_relocated_weight_preserves_mass_changes_moment(self):
        light=item('L',1000.,50.,0.,5.)
        a=w.calculate_case([item('A',100.,100.,2.,8.,200.)],light)['total']
        b=w.calculate_case([item('A',100.,110.,2.,11.,200.)],light)['total']
        self.assertEqual(a['mass_t'],b['mass_t'])
        self.assertEqual(a['FSM_tm'],b['FSM_tm'])
        self.assertAlmostEqual(b['longitudinal_moment_tm']-a['longitudinal_moment_tm'],1000.)
        self.assertAlmostEqual(b['vertical_moment_tm']-a['vertical_moment_tm'],300.)

    def test_empty_mass_can_have_adopted_FSM_and_GRM_is_separate(self):
        empty=item('E',0.,0.,0.,0.,100.)
        empty['grain_heeling_moment_tm']=2000.
        r=w.calculate_case([empty],item('L',1000.,50.,0.,5.))['total']
        self.assertEqual(r['FSM_tm'],100.)
        self.assertEqual(r['KG_correction_m'],.1)
        self.assertEqual(r['KG_m'],5.)

    def test_invalid_loads_rejected(self):
        for row in [item('N',-1.,1.,0.,1.),item('N',1.,float('nan'),0.,1.),item('N',1.,1.,0.,1.,-2.)]:
            with self.assertRaises(ValueError):w.aggregate([row])

    def test_geometry_load_density_and_partial_center(self):
        spec=dict(geometry_id='WBT_3_P',fill_fraction=.5,density_t_m3=1.)
        a=w.load_geometry_item(spec)
        b=w.load_geometry_item(dict(spec,density_t_m3=2.))
        self.assertAlmostEqual(b['mass_t'],2*a['mass_t'],places=8)
        self.assertAlmostEqual(b['FSM_tm'],2*a['FSM_tm'],places=8)
        self.assertEqual(a['KG_m'],b['KG_m'])
        caps=w.load_results_05()['capacities']['compartments']
        full=next(r for r in caps if r['id']=='WBT_3_P')
        self.assertAlmostEqual(a['mass_t'],.5*full['volume_m3'],places=7)
        self.assertLess(a['KG_m'],full['CGZ_m'])


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.data=w.load_results()
        self.report=json.loads((w.HERE/'output/verification_report.json').read_text(encoding='utf-8'))

    def test_complete_cases_and_aggregation(self):
        self.assertEqual(len(self.data['cases']),39)
        self.assertEqual(self.report['item_count'],2064)
        self.assertEqual(self.report['comparison_count'],1945)
        self.assertTrue(self.report['all_aggregation_checks_rounding_consistent'])
        light=next(c for c in self.data['cases'] if c['case_id']=='LD00')['calculated']['total']
        self.assertEqual((light['mass_t'],light['LCG_m'],light['KG_m']),(8650.,79.3,10.2))
        with w.np.load(w.HERE/'output/weight_cg_data.npz') as a:
            self.assertEqual(a['case_ids'].shape,(39,))
            self.assertTrue(w.np.isfinite(a['KG_effective_m']).all())
            w.np.testing.assert_allclose(a['KG_effective_m'],a['KG_m']+a['FSM_tm']/a['mass_t'])

    def test_reference_discrepancy_preserved(self):
        c=next(c for c in self.data['cases'] if c['case_id']=='LD09C')
        self.assertEqual(c['overview']['DISP_t'],43537.1)
        self.assertEqual(c['reference']['total']['mass_t'],43347.1)
        self.assertEqual({r['case_id'] for r in self.report['reference_inconsistencies']},{'LD09C'})
        self.assertTrue(self.report['all_detailed_correction_checks_rounding_consistent'])
        self.assertEqual(sum(r['rounding_consistent'] for r in self.report['free_surface_correction_checks']),38)
        bw=next(r for r in c['items'] if r['id']=='WBT2P')
        self.assertEqual((bw['mass_t'],bw['FSM_tm']),(0.,5374.44))

    def test_source_inputs_and_loading_interfaces(self):
        self.assertEqual(len(w.load_results(verify_sources=True)['cases']),39)
        c=next(c for c in self.data['cases'] if c['case_id']=='LD08A')
        self.assertEqual(sum(r['mass_t'] for r in c['items'] if r['category']=='Steel Coil'),31980.)
        inputs=json.loads((w.HERE/'output/equilibrium_inputs.json').read_text(encoding='utf-8'))
        self.assertEqual(len(inputs['cases']),39)
        html=(w.HERE/'output/weight_cg_verification.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__',html)
        self.assertNotIn('https://',html)


if __name__=='__main__':unittest.main()
