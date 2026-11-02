"""12: Assemble the selected calculations into an offline results report."""
import html
import os
from pathlib import Path
from fsgy_surf.io.files import read_json, save_json, write_csv
from fsgy_surf.reporting.html import table, chart, document


def run(ctx):
    out = ctx.output(12)
    sections, inventory = [], []
    for stage, data in sorted(ctx.data.items()):
        folder = ctx.output(stage)
        links = []
        for path in sorted(folder.glob('*')):
            if path.suffix in ('.csv', '.json', '.html', '.obj', '.stl', '.vtk', '.dxf'):
                relative = os.path.relpath(path, out).replace(os.sep, '/')
                links.append(f'<a href="{html.escape(relative, quote=True)}">{html.escape(path.name)}</a>')
        inventory.append(dict(stage=stage, result_files=len(links)))
        body = f'<h2>{stage:02d}</h2><p>{" · ".join(links)}</p>'
        if stage == 2:
            body += table([data['dimensions']])
            body += table([dict(structure_mass_t=data['structure_mass_t'], structure_center_m=data['structure_center_m'])])
            body += f'<p>{html.escape(data["model_scope"])}</p>'
        elif stage == 3:
            arrays = ctx.arrays(data['arrays_file'])
            body += chart('邦戎曲线', [(f'X={x:.3f} m', arrays['draft_m'], areas)
                for x, areas in zip(arrays['x_m'][::max(1, len(arrays['x_m'])//5)],
                                    arrays['area_m2'][::max(1, len(arrays['x_m'])//5)])], '吃水 / m', '面积 / m²')
        elif stage == 4:
            body += table([data['design_point']])
            arrays = ctx.arrays(data['arrays_file'])
            for field in ('VOLM', 'KMT', 'LCB'):
                body += chart(field, [(f'trim={trim:g} m', arrays['draft_m'], values)
                    for trim, values in zip(arrays['trim_m'], arrays[field])], '吃水 / m', field)
        elif stage == 5:
            body += table(data['compartments'])
        elif stage == 6:
            body += table([dict(case_id=c['case_id'], **c['calculated']['total']) for c in data['cases']])
        elif stage == 7:
            body += table([dict(case_id=c['case_id'], **{k: c['calculated'][k] for k in
                ['T_m', 'TA_m', 'TF_m', 'trim_m', 'heel_deg', 'GM_m']}) for c in data['cases']])
        elif stage == 8:
            for case in data['cases']:
                body += chart(case['curve_id']+' · GZ', [('GZ', [p['angle_deg'] for p in case['points']],
                    [p['GZ_m'] for p in case['points']])], '横倾 / °', 'GZ / m')
                body += table([case['summary']])
        elif stage == 9:
            body += table(data['cases'])
        elif stage == 10:
            for case in data['cases']:
                body += table([dict(case_id=case['case_id'], **case['extrema'])])
                for field in ('SF_t', 'BM_tm'):
                    body += chart(case['case_id']+' · '+field,
                        [(field, case['curve']['x_m'], case['curve'][field])], 'X / m', field)
        elif stage == 11:
            body += table(data['results'])
        sections.append('<section>'+body+'</section>')
    title = ctx.config.get('name', ctx.case_dir.name)+' · 计算结果'
    path = out/'report.html'
    path.write_text(document(title, sections), encoding='utf-8')
    save_json(out/'results.json', {str(stage): data for stage, data in ctx.data.items() if stage != 1})
    write_csv(out/'stages.csv', inventory)
    return dict(report_file=str(path), result_file=str(out/'results.json'), stages=list(ctx.data))
