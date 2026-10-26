import unittest
import run_pipeline as c


class ReportTests(unittest.TestCase):
    def test_stages_and_freshness(self):
        r=c.read_json(c.HERE/'output/pipeline_status.json')
        self.assertEqual(r['stages_with_programs'],12);self.assertEqual(r['case_count'],39)
        self.assertTrue(r['sources_fresh']);self.assertFalse(r['full_pdf_reproduction_complete'])
        self.assertTrue(all(row['fresh'] for row in c.freshness()))

    def test_all_case_inputs_and_report_links(self):
        rows=c.read_json(c.HERE/'output/case_results.json')
        self.assertEqual(len(rows),39);self.assertEqual(len({r['case_id'] for r in rows}),39)
        self.assertTrue(all(c.math.isfinite(r['T_m']) and c.math.isfinite(r['GM_m']) for r in rows))
        html=(c.HERE/'output/report.html').read_text(encoding='utf-8')
        self.assertNotIn('__DATA__',html)
        self.assertTrue((c.HERE/'output/report.md').exists())
        self.assertEqual(len(c.read_json(c.HERE/'output/known_issues.json')),8)


if __name__=='__main__':unittest.main()
