import unittest,math
import stability_criteria as c


class CriteriaTests(unittest.TestCase):
    def test_opening_plane_intersection(self):
        case=dict(side='PS',points=[dict(angle_deg=float(a),plane_level_rotated_m=5.,rotated_trim_m=0.) for a in c.np.arange(0,90.01,2.5)])
        opening=dict(id='T',x_m=86.,y_m=8.,z_m=15.)
        rows,angle,name=c.flooding(case,[opening])
        exact=math.degrees(math.acos(5/17)-math.atan(8/15))
        self.assertAlmostEqual(angle,exact,delta=.005)
        self.assertEqual(name,'T')

    def test_wind_units_and_intersection(self):
        curve=lambda rad:2*math.sin(rad)
        settings=dict(beam_m=28.8,LWL_m=175.22,g_m_s2=9.81,P_N_m2=504.,bilge_keel_area_m2=18.66)
        r=c.weather(curve,2.,5.,6.,20000.,dict(moment_m3=10000.),20.,60.,settings)
        expected=504*10000/(1000*9.81*20000)
        self.assertAlmostEqual(r['lw1_m'],expected)
        self.assertAlmostEqual(r['steady_angle_deg'],math.degrees(math.asin(expected/2)),places=7)
        self.assertAlmostEqual(r['lw2_m'],1.5*expected)

    def test_reference_and_report(self):
        r=c.read_json(c.HERE/'output/reference/manual_conditions.json')
        self.assertEqual(len(r['wind']),19);self.assertEqual(len(r['zero_trim_limits']),85)
        self.assertEqual(len(r['criteria']),39)
        data=c.read_json(c.HERE/'output/criteria_results.json')
        self.assertEqual(len(data['cases']),39)
        self.assertTrue(all(case['wind']['ratio_b_a'] is not None for case in data['cases']))
        self.assertFalse(data['report']['full_vessel_geometry_complete'])
        light=data['cases'][0]['wind']
        self.assertTrue(light['r_cap_applied'])
        self.assertLess(light['ratio_without_r_cap_sensitivity'],light['ratio_b_a'])


if __name__=='__main__':unittest.main()
