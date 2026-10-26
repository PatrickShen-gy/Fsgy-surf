"""解析几何检验积分算法，而不是把当前船体结果当成正确答案。"""
import json
import unittest
import hydrostatics_calculation as h

np = h.np


def box_mesh(length=10., breadth=4., depth=3.):
    yz = np.array([[-breadth/2, 0], [breadth/2, 0], [breadth/2, depth], [-breadth/2, depth]])
    vertices = np.vstack([np.column_stack([np.full(4, x), yz]) for x in (0., length)])
    faces = []
    physical = []
    for i in range(4):
        j = (i+1) % 4
        faces.extend([(i, j, j+4), (i, j+4, i+4)])
        physical.extend([i != 2]*2)
    faces.extend([(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)])
    physical.extend([True]*4)
    return vertices, np.array(faces), np.array(physical)


class AnalyticTests(unittest.TestCase):
    def setUp(self):
        v, f, p = box_mesh()
        self.calc = h.HydrostaticCalculator(v, f, p, lbp=10., beam=4., xref=5., rho=1., shell_thickness=0.)

    def test_box_and_coplanar_top(self):
        for t in [1.25, 3.]:
            r = self.calc.evaluate(t, midship_area=4*t)
            self.assertAlmostEqual(r['VOLM'], 40*t, places=10)
            self.assertAlmostEqual(r['LCB'], 5., places=10)
            self.assertAlmostEqual(r['VCB'], t/2, places=10)
            self.assertAlmostEqual(r['WLA'], 40., places=10)
            self.assertAlmostEqual(r['WSA'], 40+28*t, places=10)
            self.assertAlmostEqual(r['waterplane_IT_m4'], 10*4**3/12, places=10)
            self.assertAlmostEqual(r['waterplane_IL_m4'], 4*10**3/12, places=10)
            self.assertAlmostEqual(r['KMT'], t/2+4**2/(12*t), places=10)
            self.assertAlmostEqual(r['KML'], t/2+10**2/(12*t), places=10)
            for k in ['CB', 'CM', 'CP', 'CW']:
                self.assertAlmostEqual(r[k], 1., places=10)

    def test_trimmed_box_wedge_integrals(self):
        t, trim, length, breadth = 1.5, 1., 10., 4.
        slope = trim/length
        r = self.calc.evaluate(t, trim)
        self.assertAlmostEqual(r['VOLM'], length*breadth*t, places=10)
        self.assertAlmostEqual(r['LCB'], 5+slope*length**2/(12*t), places=10)
        self.assertAlmostEqual(r['VCB'], t/2+slope**2*length**2/(24*t), places=10)
        self.assertAlmostEqual(r['WLA'], length*breadth, places=10)
        self.assertAlmostEqual(r['waterplane_area_actual_m2'], length*breadth*np.hypot(1,slope), places=10)
        self.assertAlmostEqual(r['LCA'], 5., places=10)
        self.assertAlmostEqual(r['WSA'], 40+28*t, places=10)
        self.assertLess(r['waterline_closure_m'], 1e-10)

    def test_waterplane_derivative(self):
        for t, tr in [(1.25, 0.), (1.5, 1.), (1.5, -1.)]:
            r = self.calc.evaluate(t, tr)
            eps = 1e-5
            dv = (self.calc.evaluate(t+eps,tr)['VOLM']-self.calc.evaluate(t-eps,tr)['VOLM'])/(2*eps)
            self.assertAlmostEqual(r['WLA'], dv, places=7)

    def test_trim_cut_through_virtual_deck(self):
        r = self.calc.evaluate(2.8, 1.)
        self.assertTrue(r['deck_limit_reached'])
        self.assertAlmostEqual(r['VOLM'], 110.2, places=9)
        self.assertAlmostEqual(r['WLA'], 28., places=9)
        self.assertAlmostEqual(r['LCA'], 3.5, places=9)

    def test_rotated_triangulation_and_vertex_plane(self):
        v, f, p = box_mesh()
        # 循环改变面内顶点顺序应保持正向及结果；水线穿过端面的对角点。
        calc = h.HydrostaticCalculator(v, np.roll(f,1,axis=1), p, lbp=10., beam=4., xref=5., rho=1., shell_thickness=0.)
        for key in ['VOLM','VCB','WSA','WLA','KMT','KML']:
            self.assertAlmostEqual(calc.evaluate(1.5)[key], self.calc.evaluate(1.5)[key], places=10)


class ArtifactTests(unittest.TestCase):
    def test_reference_and_grid_completeness(self):
        data = json.loads((h.HERE/'output/reference/pdf_hydrostatics.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['even_keel']), 129)
        self.assertEqual(len(data['trimmed']), 1032)
        row = next(r for r in data['even_keel'] if r['draft_m'] == 9.83)
        self.assertEqual((row['DISP'],row['VOLM'],row['KMT'],row['KML']), (41442.3,40301.2,12.338,251.955))
        self.assertEqual((row['pages']['VOLM'],row['pages']['KMT']), (42,45))
        report = json.loads((h.HERE/'output/verification_report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['numerical_checks']['passed'])
        self.assertEqual(report['even_keel_comparison_cells'], 1935)
        a = np.load(h.HERE/'output/hydrostatics_data.npz')
        self.assertEqual(a['VOLM'].shape, (8,129))
        self.assertTrue(np.all(np.diff(a['VOLM'],axis=1) > 0))
        self.assertTrue(np.all(a['waterline_closure_m'] < 1e-6))
        html = (h.HERE/'output/hydrostatics_verification.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__',html)
        self.assertNotIn('https://',html)


if __name__ == '__main__':
    unittest.main()
