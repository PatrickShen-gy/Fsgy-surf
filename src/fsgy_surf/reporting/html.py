"""Offline result tables and SVG curves; no reference data or comparison layer."""
import html
import math
from pathlib import Path


def table(rows):
    if not rows:
        return '<p>No rows.</p>'
    keys = list(dict.fromkeys(k for row in rows for k in row))
    def cell(value):
        if isinstance(value, float):
            return f'{value:.6g}'
        return '' if value is None else str(value)
    return '<div class="scroll"><table><thead><tr>' + ''.join(
        f'<th>{html.escape(k)}</th>' for k in keys) + '</tr></thead><tbody>' + ''.join(
        '<tr>' + ''.join(f'<td>{html.escape(cell(row.get(k)))}</td>' for k in keys) + '</tr>'
        for row in rows) + '</tbody></table></div>'


def chart(title, series, xlabel, ylabel):
    points = [(float(x), float(y)) for _, xs, ys in series for x, y in zip(xs, ys)
              if y is not None and math.isfinite(float(y))]
    if not points:
        return ''
    xmin, xmax = min(x for x, _ in points), max(x for x, _ in points)
    ymin, ymax = min(y for _, y in points), max(y for _, y in points)
    dx, dy = max(xmax-xmin, 1e-9), max(ymax-ymin, 1e-9)
    colors = ['#087f8c', '#d47a24', '#7058a3', '#b04b65']
    paths, legend, labels = [], [], []
    for i, (name, xs, ys) in enumerate(series):
        coords = ' '.join(f'{65+650*(x-xmin)/dx:.2f},{270-225*(y-ymin)/dy:.2f}'
                          for x, y in zip(xs, ys) if y is not None and math.isfinite(float(y)))
        color = colors[i % len(colors)]
        paths.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"/>')
        legend.append(f'<span style="color:{color}">{html.escape(name)}</span>')
    for t in [0, .25, .5, .75, 1]:
        labels.append(f'<text x="{65+650*t}" y="292" text-anchor="middle">{xmin+dx*t:.4g}</text>')
        labels.append(f'<text x="58" y="{274-225*t}" text-anchor="end">{ymin+dy*t:.4g}</text>')
    return f'<h3>{html.escape(title)}</h3><p>{" · ".join(legend)}</p><svg viewBox="0 0 760 330" role="img" aria-label="{html.escape(title)}"><path d="M65 35V270H725" fill="none" stroke="#7d9099"/>{"".join(paths+labels)}<text x="390" y="322" text-anchor="middle">{html.escape(xlabel)}</text><text x="65" y="24">{html.escape(ylabel)}</text></svg>'


def document(title, sections):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>'
            'body{font:15px/1.6 system-ui,sans-serif;background:#eef3f5;color:#173442;max-width:1200px;margin:32px auto;padding:0 20px}'
            'section{background:white;padding:24px;margin:20px 0;border-radius:10px}h1,h2{line-height:1.3}'
            '.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #dce6ea;white-space:nowrap}'
            'th{background:#e9f2f5}a{color:#087f8c}svg{width:100%;max-height:420px}svg text{font:12px system-ui}'
            f'</style><h1>{html.escape(title)}</h1>{"".join(sections)}</html>')
