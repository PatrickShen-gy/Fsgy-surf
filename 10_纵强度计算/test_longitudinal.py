import unittest
import longitudinal_strength as c


class BeamTests(unittest.TestCase):
    def test_nonnegative_distribution_preserves_mass_and_center(self):
        edges=c.np.linspace(0,10,101);x=(edges[:-1]+edges[1:])/2
        for cg in [2.,5.,8.]:
            m=c.distributed_cell_masses(edges,100.,cg,(0.,10.))
            self.assertTrue((m>=0).all());self.assertAlmostEqual(m.sum(),100.,places=10)
            self.assertAlmostEqual(m@x,100*cg,places=8)
        m=c.distributed_cell_masses(edges,7.,4.57,None)
        self.assertAlmostEqual(m@x,7*4.57,places=10)

    def test_tilted_gravity_bending_closure(self):
        x=c.np.array([-1.,0.,1.]);wm=c.np.array([1.,1.]);bm=wm.copy()
        sf,bm_result=c.beam_result(x,wm,c.np.array([-.5,.5]),c.np.array([1.,1.]),bm,c.np.array([-.4,.6]),c.np.array([.5,.5]),.2)
        self.assertAlmostEqual(sf[-1],0.);self.assertAlmostEqual(bm_result[-1],0.,places=10)

    def test_geometry_and_source(self):
        r=c.read_json(c.HERE/'output/verification_report.json')
        self.assertEqual(r['station_comparisons'],624)
        self.assertLess(r['max_end_SF_t'],1e-6);self.assertLess(r['max_end_BM_tm'],3.)
        self.assertLess(r['max_weight_LCG_error_m'],1e-7);self.assertLess(r['max_slice_volume_relative_error'],1e-5)
        with c.np.load(c.HERE/'output/longitudinal_data.npz') as a:
            self.assertTrue((c.np.diff(a['x_m'])>1e-8).all())
            self.assertEqual(a['SF_t'].shape[0],39)
        source=c.read_json(c.HERE/'output/reference/longitudinal_reference.json')
        self.assertEqual(len(source['lightship']),17)
        row=next(r for r in source['limits'] if r['mode']=='intact' and r['kind']=='POSITIVE')
        self.assertEqual(row['values'][-1],-400.)


if __name__=='__main__':unittest.main()
