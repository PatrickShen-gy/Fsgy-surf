import json
import unittest
import tank_capacity_calculation as t

np=t.np


def prism(yz,length=10.):
    yz=np.asarray(yz,float);n=len(yz)
    v=np.vstack([np.column_stack([np.full(n,x),yz]) for x in (0.,length)])
    faces=[]
    for i in range(n):
        j=(i+1)%n;faces.extend([(i,j,j+n),(i,j+n,i+n)])
    for i in range(1,n-1):faces.extend([(0,i+1,i),(n,n+i,n+i+1)])
    return v,np.array(faces)


class AnalyticTests(unittest.TestCase):
    def test_offset_box_centroid_and_free_surface(self):
        v,f=prism([(-2,0),(2,0),(2,3),(-2,3)])
        v+=np.array([100.,7.,4.])
        calc=t.TankCalculator(v,f)
        self.assertAlmostEqual(calc.volume,120.,places=9)
        np.testing.assert_allclose(calc.center,[105.,7.,5.5],atol=1e-10)
        r=calc.at_fill(.4)
        self.assertAlmostEqual(r['volume_m3'],48.,places=9)
        self.assertAlmostEqual(r['level_BL_m'],5.2,places=9)
        self.assertAlmostEqual(r['CGZ_m'],4.6,places=9)
        self.assertAlmostEqual(r['free_surface_area_m2'],40.,places=9)
        self.assertAlmostEqual(r['free_surface_IT_m4'],10*4**3/12,places=9)
        self.assertAlmostEqual(r['free_surface_IL_m4'],4*10**3/12,places=8)

    def test_triangular_hopper_volume_and_fill_inverse(self):
        v,f=prism([(0,0),(2,2),(-2,2)])
        calc=t.TankCalculator(v,f)
        r=calc.at_fill(.25)
        self.assertAlmostEqual(calc.volume,40.,places=9)
        self.assertAlmostEqual(r['volume_m3'],10.,places=8)
        self.assertAlmostEqual(r['level_BL_m'],1.,places=8)
        self.assertAlmostEqual(r['CGZ_m'],2/3,places=8)
        self.assertAlmostEqual(r['free_surface_IT_m4'],10*2**3/12,places=8)

    def test_empty_full_and_maximum_are_distinct(self):
        v,f=prism([(-2,0),(2,0),(2,3),(-2,3)])
        calc=t.TankCalculator(v,f)
        self.assertIsNone(calc.at_fill(0.)['CGZ_m'])
        self.assertEqual(calc.at_fill(0.)['free_surface_IT_m4'],0.)
        self.assertEqual(calc.at_fill(1.)['free_surface_IT_m4'],0.)
        _,maximum=calc.scan(samples=21)
        self.assertAlmostEqual(maximum['free_surface_IT_m4'],10*4**3/12,places=9)
        with self.assertRaises(ValueError):calc.at_fill(1.1)

    def test_thickness_not_subtracted_again(self):
        # 输入已代表可填充空间，程序不能再无条件扣一个外板或隔板体积。
        v,f=prism([(-2,0),(2,0),(2,3),(-2,3)])
        calc=t.TankCalculator(v,f)
        self.assertAlmostEqual(calc.at_fill(1.)['volume_m3'],120.,places=10)


class ArtifactTests(unittest.TestCase):
    def test_reference_preservation_and_coverage(self):
        ref=json.loads((t.HERE/'output/reference/pdf_tank_capacity.json').read_text(encoding='utf-8'))
        self.assertEqual((ref['location_count'],ref['capacity_count']),(84,83))
        catalog={r['id']:r for r in ref['catalog']}
        self.assertIsNone(catalog['CLS']['VOLM'])
        self.assertEqual(catalog['CH3']['VOLM'],9994.1)
        self.assertEqual(catalog['WBT2S']['CGY'],-10.47)
        self.assertEqual(catalog['MDOSTORTKP']['location_density_t_m3'],.86)
        self.assertEqual(catalog['MDOSTORTKP']['density_t_m3'],.85)
        self.assertEqual(catalog['CH1']['reduction_header'],1.)
        self.assertEqual(catalog['CH1']['VNET'],6652.8)
        # 原始体积只印到0.1m3，0.998是约数；不强制修改原表实现精确比例。
        self.assertAlmostEqual(catalog['CH1']['VNET']/catalog['CH1']['VOLM'],.998,delta=2e-5)
        self.assertTrue(all(c[k]['passed'] for c in ref['total_checks'] for k in ['VOLM','VNET','FSM']))
        mapping=t.geometry_mapping(ref)
        self.assertEqual(next(m for m in mapping if m['reference_id']=='WBT1P')['geometry_status'],'partial_geometry')

    def test_results_are_monotone_and_self_checked(self):
        output=t.HERE/'output'
        r=json.loads((output/'verification_report.json').read_text(encoding='utf-8'))
        self.assertTrue(r['numerical_checks']['passed'])
        self.assertEqual((r['geometry_count'],r['fill_rows']),(23,1173))
        a=np.load(output/'tank_capacity_data.npz')
        self.assertEqual(a['volume_m3'].shape,(23,51))
        self.assertEqual(a['fill_fraction'].shape,(51,))
        np.testing.assert_allclose(a['achieved_fill_fraction'],np.broadcast_to(a['fill_fraction'],(23,51)),atol=1e-7)
        self.assertTrue(np.all(np.diff(a['volume_m3'],axis=1)>0))
        self.assertTrue(np.isnan(a['CGZ_m'][:,0]).all())
        self.assertTrue(np.isfinite(a['CGZ_m'][:,1:]).all())
        self.assertTrue((a['free_surface_IT_m4'][:,[0,-1]]==0).all())
        html=(output/'tank_capacity_verification.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__',html)
        self.assertNotIn('https://',html)
        loaded=t.load_results()
        self.assertEqual(loaded['arrays']['capacity_m3'].shape,(23,))
        self.assertEqual(len(loaded['reference_catalog']['tanks']),84)


if __name__=='__main__':unittest.main()
