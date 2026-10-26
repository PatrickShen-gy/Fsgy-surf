"""解析箱体、三角剖面、孔洞及原表提取核对：python test_bonjean.py。"""
import unittest
import json
import bonjean_calculation as c

np = c.np


def prism(yz):
    yz = np.array(yz, dtype=float)
    n = len(yz)
    vertices = np.vstack([np.column_stack([np.full(n, x), yz]) for x in (0., 10.)])
    faces = []
    for i in range(n):
        j = (i+1)%n
        faces.extend([(i, j, j+n), (i, j+n, i+n)])
    for i in range(1, n-1):
        faces.extend([(0, i+1, i), (n, n+i, n+i+1)])
    return vertices, np.array(faces, dtype=int)


class AnalyticTests(unittest.TestCase):
    def test_box_interior_and_coplanar_end_sections(self):
        v, f = prism([(-2, 0), (2, 0), (2, 3), (-2, 3)])
        slicer = c.MeshSlicer(v, f)
        for x in (0., 2.75, 5., 10.):
            section = slicer.section(x)
            wet = c.submerged_properties(section, 1.25)
            self.assertAlmostEqual(wet['area_m2'], 5., places=9)
            self.assertAlmostEqual(wet['moment_bl_m3'], 3.125, places=9)
            self.assertAlmostEqual(wet['centroid_z_m'], .625, places=9)
            self.assertAlmostEqual(wet['waterline_breadth_m'], 4., places=9)
            self.assertEqual(c.submerged_properties(section, -.1)['area_m2'], 0.)
        check = c.mesh_volume_check(slicer)
        self.assertAlmostEqual(check['volume_from_sections_m3'], 120., places=9)
        self.assertAlmostEqual(check['moment_from_sections_m4'], 180., places=9)

    def test_triangle_section(self):
        v, f = prism([(0, 0), (2, 2), (-2, 2)])
        slicer = c.MeshSlicer(v, f)
        t = 1.25
        props = c.submerged_properties(slicer.section(3.2), t)
        self.assertAlmostEqual(props['area_m2'], t*t, places=8)
        self.assertAlmostEqual(props['moment_bl_m3'], 2*t**3/3, places=8)

    def test_nested_loops_are_holes(self):
        outer, f1 = prism([(-2, 0), (2, 0), (2, 3), (-2, 3)])
        inner, f2 = prism([(-1, 1), (1, 1), (1, 2), (-1, 2)])
        slicer = c.MeshSlicer(np.vstack([outer, inner]), np.vstack([f1, f2[:, ::-1]+len(outer)]))
        wet = c.submerged_properties(slicer.section(4.), 1.5)
        self.assertAlmostEqual(wet['area_m2'], 5., places=9)
        self.assertAlmostEqual(wet['moment_bl_m3'], 3.25, places=9)
        self.assertAlmostEqual(c.mesh_volume_check(slicer)['volume_from_sections_m3'], 100., places=9)

    def test_zero_reference_has_no_relative_error(self):
        row = c.compare_record(dict(area_m2=0., moment_bl_m3=0.),
            dict(x_m=0., draft_m=1., area_m2=0., moment_bl_m3=0., pdf_page=32))
        self.assertIsNone(row['area_error_percent'])
        self.assertTrue(row['both_pass'])


class ExportTests(unittest.TestCase):
    def test_reference_values_and_anomaly_preserved(self):
        path = c.HERE/'output/reference/pdf_bonjean.json'
        rows = json.loads(path.read_text(encoding='utf-8'))['rows']
        self.assertEqual(len(rows), 650)
        row = next(r for r in rows if r['x_m'] == 172. and r['draft_m'] == 10.5)
        self.assertEqual(row['area_m2'], 39.54)
        self.assertEqual(row['moment_bl_m3'], 178.1)
        self.assertEqual(row['pdf_page'], 38)
        self.assertEqual(len(c.reference_anomalies(rows)), 2)

    def test_calculated_areas_and_moments_are_monotone(self):
        data = np.load(c.HERE/'output/bonjean_data.npz')
        self.assertEqual(data['area_m2'].shape, (31, 27))
        self.assertTrue(np.all(np.diff(data['area_m2'], axis=1) >= -1e-8))
        self.assertTrue(np.all(np.diff(data['moment_bl_m3'], axis=1) >= -1e-8))
        report = json.loads((c.HERE/'output/verification_report.json').read_text(encoding='utf-8'))
        self.assertTrue(report['numerical_cross_check']['passed'])
        self.assertEqual(report['verification']['pairs'], 650)


if __name__ == '__main__':
    unittest.main()
