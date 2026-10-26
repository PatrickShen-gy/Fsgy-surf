"""隔板几何核对：python test_partitions.py（实际模型测试需先生成CAD_mesh）。"""
from pathlib import Path
import json
import unittest

import cad_reconstruction as c

np = c.np


class AnalyticPartitionTests(unittest.TestCase):
    def test_normal_thickness_and_volume_for_inclined_plane(self):
        xs, zs = np.array([0., 5., 10.]), np.array([0., 2., 4.])
        y = 3+.2*xs[:, None]-.5*zs[None, :]
        inner, outer = c.partition_profiles(xs, zs, y, .008)
        normal = np.array([-.2, 1., .5])/np.sqrt(1+.2**2+.5**2)
        rings = []
        for i, x in enumerate(xs):
            a = np.column_stack([np.full(3, x), outer[i]])
            b = np.column_stack([np.full(3, x), inner[i]])
            self.assertTrue(np.allclose((a-b)@normal, .008, atol=1e-12))
            rings.append(np.vstack([a, b[::-1]]))
        v, f = c.partition_mesh(rings)
        c.mu.validate(v, f)
        volume, cg = c.mu.volume_centroid(v, f)
        expected = 10*4*.008*np.sqrt(1+.2**2+.5**2)
        self.assertAlmostEqual(volume, expected, places=10)
        np.testing.assert_allclose(cg, [5., 3., 2.], atol=1e-9)


def triangle_keys(vertices, faces):
    return {tuple(sorted(tuple(p) for p in np.round(tri, 8))) for tri in vertices[faces]}


class ExportedPartitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = c.HERE/'CAD_mesh'
        cls.meshes = np.load(out/'01_展示模型/compartments.npz')
        cls.report = json.loads((out/'02_计算网格/longitudinal_partitions.json').read_text(encoding='utf-8'))
        cls.materials = json.loads((out/'02_计算网格/steel_materials.json').read_text(encoding='utf-8'))

    def test_count_volume_and_symmetry(self):
        parts = self.report['partitions']
        self.assertEqual(len(parts), 10)
        self.assertEqual(self.report['thickness_m'], .008)
        for num in range(1, 6):
            pair = [p for p in parts if p['source']['hold'] == f'HOLD_{num}']
            self.assertEqual(len(pair), 2)
            self.assertAlmostEqual(pair[0]['material_volume_m3'], pair[1]['material_volume_m3'], places=8)
            for p in pair:
                self.assertTrue(p['checks']['closed_edges'])
                self.assertLess(abs(p['material_volume_m3']/p['thin_plate_volume_m3']-1), .02)
        self.assertEqual(len(self.materials['materials']), 18)
        self.assertAlmostEqual(self.materials['total_material_volume_m3'],
                               sum(p['material_volume_m3'] for p in self.materials['materials']), places=9)

    def test_compartment_interfaces_match_partition_faces(self):
        for num in range(1, 6):
            cargo_v = self.meshes[f'HOLD_{num}_vertices']
            cargo_f = self.meshes[f'HOLD_{num}_faces']
            cargo_keys = triangle_keys(cargo_v, cargo_f)
            for side in ('P', 'S'):
                wall_v = self.meshes[f'PARTITION_{num}_{side}_vertices']
                wall_f = self.meshes[f'PARTITION_{num}_{side}_faces']
                n = len(wall_v)//2
                nx = n//7
                count = 2*(nx-1)*6
                ballast_v = self.meshes[f'WBT_{num}_{side}_vertices']
                ballast_f = self.meshes[f'WBT_{num}_{side}_faces']
                positive = triangle_keys(wall_v, wall_f[:count])
                negative = triangle_keys(wall_v, wall_f[count:2*count])
                cargo_side = negative if side == 'P' else positive
                ballast_side = positive if side == 'P' else negative
                self.assertTrue(cargo_side <= cargo_keys, f'货舱{num}{side}界面不贴合')
                self.assertTrue(ballast_side <= triangle_keys(ballast_v, ballast_f), f'压载舱{num}{side}界面不贴合')

    def test_section_areas_have_no_gaps_or_overlaps(self):
        for num in range(1, 6):
            cargo = self.meshes[f'HOLD_{num}_vertices'].reshape(-1, 14, 3)
            left = self.meshes[f'WBT_{num}_P_vertices'].reshape(-1, 14, 3)
            right = self.meshes[f'WBT_{num}_S_vertices'].reshape(-1, 14, 3)
            walls = []
            for side in ('P', 'S'):
                v = self.meshes[f'PARTITION_{num}_{side}_vertices']
                n = len(v)//2
                walls.append((v[:n].reshape(-1, 7, 3), v[n:].reshape(-1, 7, 3)))
            for i in range(len(cargo)):
                polygons = [c.Polygon(p[i, :, 1:]) for p in (cargo, left, right)]
                polygons += [c.Polygon(np.vstack([a[i, :, 1:], b[i, ::-1, 1:]])) for a, b in walls]
                whole = c.Polygon(np.vstack([left[i, :7, 1:], right[i, 7:, 1:]]))
                self.assertTrue(all(p.is_valid for p in polygons))
                for j, p in enumerate(polygons):
                    for q in polygons[j+1:]:
                        self.assertLess(p.intersection(q).area, 1e-7)
                self.assertAlmostEqual(sum(p.area for p in polygons), whole.area, places=6)


if __name__ == '__main__':
    unittest.main()
