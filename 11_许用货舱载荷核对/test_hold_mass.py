import unittest
import hold_mass_check as c


class EnvelopeTests(unittest.TestCase):
    def test_printed_endpoints_and_breakpoints(self):
        a=c.DIGITIZED[0];self.assertEqual(c.envelope(a,10.27,'seagoing'),(837.,8554.))
        b=c.DIGITIZED[1];self.assertEqual(c.envelope(b,6.8809,'seagoing'),(0.,9982.))
        pair=next(r for r in c.DIGITIZED if r['id']=='H23')
        lo,hi=c.envelope(pair,10.27,'harbour');self.assertAlmostEqual(lo,628.);self.assertAlmostEqual(hi,22958.)

    def test_domain_and_boundary_band_are_not_pass(self):
        self.assertEqual(c.envelope(c.DIGITIZED[0],10.3,'seagoing'),(None,None))
        self.assertEqual(c.classify(101.,0.,100.),'boundary_digitization_band')
        self.assertEqual(c.classify(110.,0.,100.),'outside_digitized_envelope')
        self.assertEqual(c.classify(0.,None,None),'outside_chart_domain')

    def test_all_checks_and_source_images(self):
        r=c.read_json(c.HERE/'output/verification_report.json')
        self.assertEqual(r['checks'],702);self.assertEqual(sum(r['status_counts'].values()),702)
        self.assertGreater(r['status_counts']['outside_chart_domain'],0)
        source=c.read_json(c.HERE/'output/reference/digitized_envelopes.json')
        self.assertEqual(len(source['curves']),9)
        for row in source['curves']:
            p=c.HERE/'output/reference'/row['source_image']
            self.assertEqual(c.digest(p),row['source_image_sha256'])


if __name__=='__main__':unittest.main()
