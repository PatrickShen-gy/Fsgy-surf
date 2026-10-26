"""阶段01：提取DXF、保留来源和几何、生成可供后续阶段复用的缓存。

python read_dxf.py                       # 默认读取Fsgy-surf根目录的全部DXF
python read_dxf.py drawing.dxf           # 指定一个或多个输入文件
python read_dxf.py --force               # 忽略缓存重新解析

只读源图；不缩放、不修复源文件、不使用34000t改型参数。
输出JSON采用UTF-8；CSV为UTF-8-BOM；原始几何坐标不转换单位。
"""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import argparse
import csv
import gzip
import hashlib
import json
import math
import re
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.append(str(PROJECT.parent/'.cad_deps'))
try:
    import ezdxf
    from ezdxf.lldxf.tagwriter import TagCollector
except ImportError as exc:
    raise SystemExit('缺少ezdxf：请运行 python -m pip install ezdxf') from exc

SCHEMA_VERSION = '1.0'
PRINCIPAL_LABELS = {
    'loa_m': 'LENGTH O.A.', 'lbp_m': 'LENGTH B.P.',
    'beam_m': 'BREADTH MLD.', 'depth_m': 'DEPTH MLD.',
    'design_draft_m': 'DESIGN DRAFT', 'scantling_draft_m': 'SCANTLING DRAFT',
}
OFFSET_NAMES = ['station', 'x', 'flat_of_bottom'] + [
    f'half_breadth_wl_{z}' for z in (1000, 2000, 3000, 4000, 5000, 6000, 7000,
                                   8000, 9000, 9830, 10300, 11000, 12000, 13000, 14000)
] + ['upper_deck_half_breadth', 'poop_half_breadth', 'forecastle_half_breadth',
     'bulwark_half_breadth', 'flat_of_side_half_breadth', 'centerline_height'] + [
    f'height_at_half_breadth_{y}' for y in (1000, 2000, 4000, 6000, 8000, 10000, 12000, 14000)
] + ['upper_deck_height', 'poop_height', 'forecastle_height', 'bulwark_height', 'station_repeat']


def plain(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('DXF包含非有限数值。')
        return value
    if isinstance(value, bytes):
        return {'encoding': 'hex', 'data': value.hex()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    try:
        return [plain(v) for v in value]
    except TypeError as exc:
        raise TypeError(f'尚未支持的DXF值类型：{type(value).__name__}') from exc


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def serialize_entity(entity, version):
    kind = entity.dxftype()
    result = dict(type=kind, handle=entity.dxf.get('handle'),
                  layer=entity.dxf.get('layer', '0'),
                  dxf=plain(entity.dxf.all_existing_dxf_attribs()))
    # 原始组码保留HATCH内外环、OCS、ACIS、扩展数据等，不将曲线离散为折线。
    collector = TagCollector(dxfversion=version)
    entity.export_dxf(collector)
    result['tags'] = [[tag.code, plain(tag.value)] for tag in collector.tags]
    if kind in ('TEXT', 'ATTRIB', 'ATTDEF'):
        result['text'] = entity.dxf.text
    elif kind == 'MTEXT':
        result.update(text=entity.text, plain_text=entity.plain_text())
    elif kind == 'LWPOLYLINE':
        result.update(points_xyseb=plain(list(entity.get_points('xyseb'))), closed=bool(entity.closed))
    elif kind == 'POLYLINE':
        result['vertices'] = [plain(v.dxf.all_existing_dxf_attribs()) for v in entity.vertices]
        result['closed'] = bool(entity.is_closed)
    elif kind == 'SPLINE':
        result.update(control_points=plain(entity.control_points), fit_points=plain(entity.fit_points),
                      knots=plain(entity.knots), weights=plain(entity.weights))
    elif kind == 'INSERT':
        result.update(block_name=entity.dxf.name,
                      matrix44_rows=plain(list(entity.matrix44().rows())),
                      attribs=[serialize_entity(a, version) for a in entity.attribs])
    elif kind == 'DIMENSION':
        result['geometric_measurement'] = plain(entity.get_measurement())
    return result


def text_records(drawing):
    for scope, entities in [('modelspace', drawing['modelspace'])] + [
        ('block:'+block['name'], block['entities']) for block in drawing['blocks']
    ]:
        for e in entities:
            if 'text' in e:
                yield dict(scope=scope, handle=e['handle'], layer=e['layer'], type=e['type'],
                           position=e['dxf'].get('insert'), height=e['dxf'].get('height', e['dxf'].get('char_height')),
                           text=e.get('plain_text', e['text']), raw_text=e['text'])


def principal_dimensions(drawing):
    texts = [r for r in text_records(drawing) if r['scope'] == 'modelspace' and r['position']]
    numbers = [(t, re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*m\s*', t['text'], re.I)) for t in texts]
    found = {}
    for key, label in PRINCIPAL_LABELS.items():
        labels = [t for t in texts if label in t['text'].upper()]
        evidence = []
        for t in labels:
            x, y = t['position'][:2]
            height = t['height'] or 300
            candidates = [(n, match) for n, match in numbers if match
                          and 0 < n['position'][0]-x < 30000
                          and abs(n['position'][1]-y) < height*.65]
            if candidates:
                n, match = min(candidates, key=lambda v: (abs(v[0]['position'][1]-y), v[0]['position'][0]-x))
                evidence.append(dict(value=float(match.group(1)), unit='m', label_handle=t['handle'],
                                     value_handle=n['handle'], raw_value=n['raw_text'], drawing=drawing['source']['name']))
        if evidence:
            found[key] = evidence
    return found


def extract_offsets(drawing):
    """此母型型值表专用适配器；图纸平移可适配，任意表格缩放需重新核定。"""
    texts = [e for e in drawing['modelspace'] if e['type'] == 'TEXT']
    anchors = [e for e in texts if e['text'].strip() == 'ST.NO.']
    if not anchors:
        return None
    anchor = min(anchors, key=lambda e: e['dxf']['insert'][0])
    ax, ay = anchor['dxf']['insert'][:2]
    left, first_y = ax-565.2519356105, ay-930.7461336752
    pitch_x, pitch_y = 1536., 358.4
    rows = {}
    for e in texts:
        x, y = e['dxf']['insert'][:2]
        row, col = round((first_y-y)/pitch_y), math.floor((x-left)/pitch_x)
        if 0 <= row <= 30 and 0 <= col <= 36 and abs(y-(first_y-row*pitch_y)) < 3:
            cells = rows.setdefault(row, {})
            if col in cells:
                raise ValueError(f'型值表单元格重复：{row}, {col}')
            raw = e['text'].strip()
            numeric = re.fullmatch(r'-?\d+(?:\.\d+)?(?:/-?\d+(?:\.\d+)?)*', raw)
            values = [float(v)/1000 for v in raw.split('/')] if numeric and 1 <= col <= 35 else None
            cells[col] = dict(raw=raw, values_m=values, source_handle=e['handle'])
    stations = []
    for row, cells in sorted(rows.items()):
        if 0 in cells and 1 in cells and cells[1]['values_m']:
            stations.append(dict(table_row=row, station=cells[0]['raw'], x_m=cells[1]['values_m'][0],
                                 cells={OFFSET_NAMES[k]: v for k, v in sorted(cells.items())}))
    if len(stations) < 20 or any('upper_deck_height' not in r['cells'] for r in stations):
        raise ValueError('型值表布局识别未通过完整性检查。')
    return dict(source=drawing['source']['name'], source_sha256=drawing['source']['sha256'], adapter='mother_ship_offsets_v1',
                length_unit='m', raw_numeric_unit='mm', missing_value=None,
                coordinate_system='X从AP向艏；半宽为到中线距离；Z自基线向上',
                table_layout=dict(left=left, first_row_y=first_y, column_pitch=pitch_x, row_pitch=pitch_y),
                stations=stations)


def parse_drawing(path, fingerprint):
    doc = ezdxf.readfile(path)
    result = dict(schema_version=SCHEMA_VERSION, source=dict(name=path.name, path=str(path), sha256=fingerprint),
                  metadata=dict(dxf_version=doc.dxfversion, encoding=doc.encoding, insunits=int(doc.units),
                                geometry_units='mm' if doc.units == 4 else 'unspecified' if doc.units == 0 else f'INSUNITS={doc.units}',
                                coordinates='原始图纸坐标，未转换为船体坐标；块实体坐标属于块的局部坐标系'),
                  header=plain({name: doc.header.get(name) for name in doc.header.varnames()}),
                  layers=[plain(e.dxf.all_existing_dxf_attribs()) for e in doc.layers],
                  modelspace=[serialize_entity(e, doc.dxfversion) for e in doc.modelspace()],
                  blocks=[dict(name=b.name, base_point=plain(b.block.dxf.base_point),
                               flags=b.block.dxf.flags, entities=[serialize_entity(e, doc.dxfversion) for e in b])
                          for b in doc.blocks if not b.name.upper().startswith('*MODEL_SPACE')],
                  layouts=[dict(name=l.name, block_name=l.block_record.dxf.name) for l in doc.layouts])
    # 先保存原始解析结果，再审计；ezdxf的内存修复不会回写源图或缓存实体。
    audit = doc.audit()
    def issue(item):
        return dict(code=int(item.code), message=item.message,
                    entity_handle=item.entity.dxf.get('handle') if item.entity is not None else None)
    result['audit'] = dict(errors=[issue(e) for e in audit.errors],
                           suggested_fixes=[issue(e) for e in audit.fixes],
                           source_modified=False, cache_entities_before_audit=True)
    result['counts'] = dict(modelspace_entities=len(result['modelspace']), blocks=len(result['blocks']),
                            modelspace_types=dict(Counter(e['type'] for e in result['modelspace'])),
                            block_entities=sum(len(b['entities']) for b in result['blocks']))
    return result


def write_tables(out, drawing):
    with (out/'texts.csv').open('w', newline='', encoding='utf-8-sig') as f:
        rows = list(text_records(drawing))
        fields = ['scope', 'handle', 'layer', 'type', 'position', 'height', 'text', 'raw_text']
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerows(rows)
    with (out/'dimensions.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['scope', 'handle', 'layer', 'measurement_raw_units', 'display_text', 'definition_points'])
        scopes = [('modelspace', drawing['modelspace'])]+[('block:'+b['name'], b['entities']) for b in drawing['blocks']]
        for scope, entities in scopes:
            for e in entities:
                if e['type'] == 'DIMENSION':
                    points = {k: v for k, v in e['dxf'].items() if k.startswith('defpoint')}
                    writer.writerow([scope, e['handle'], e['layer'], e['geometric_measurement'],
                                     e['dxf'].get('text', '<>'), json.dumps(points, ensure_ascii=False)])


def build_outputs(drawings, out, entries):
    all_principal = {d['source']['name']: principal_dimensions(d) for d in drawings}
    warnings, errors, consensus = [], [], {}
    for key in PRINCIPAL_LABELS:
        evidence = [v for params in all_principal.values() for v in params.get(key, [])]
        values = sorted(set(round(e['value'], 6) for e in evidence))
        consensus[key] = dict(value_m=values[0] if len(values) == 1 else None,
                              status='consistent' if len(values) == 1 else 'conflict' if values else 'missing', evidence=evidence)
        if len(values) > 1:
            errors.append(f'主尺度冲突：{key}={values}')
        elif not values:
            warnings.append(f'未提取到主尺度：{key}')
    offsets = []
    for d in drawings:
        if d['metadata']['insunits'] == 0:
            warnings.append(f"{d['source']['name']} 的INSUNITS未指定；原始几何单位需核定。型值表按表中毫米数值转换，未自动缩放图形。")
        for kind in ('REGION', 'OLE2FRAME'):
            if d['counts']['modelspace_types'].get(kind):
                warnings.append(f"{d['source']['name']} 包含{kind}：保留组码，尚未解码其中的实体/嵌入表格。")
        if d['audit']['errors']:
            errors.append(f"{d['source']['name']} 存在{len(d['audit']['errors'])}项DXF审计错误，见缓存audit。")
        if d['audit']['suggested_fixes']:
            warnings.append(f"{d['source']['name']} 审计提出{len(d['audit']['suggested_fixes'])}项内存修复；未改动源文件或缓存原始实体。")
        table = extract_offsets(d)
        if table:
            offsets.append(table)
    if len(offsets) == 1:
        write_json(out/'hull_offsets.json', offsets[0])
        with (out/'hull_offsets.csv').open('w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['station', 'x_m']+OFFSET_NAMES[2:36])
            for row in offsets[0]['stations']:
                writer.writerow([row['station'], row['x_m']]+[
                    '/'.join(str(v) for v in row['cells'][name]['values_m'])
                    if name in row['cells'] and row['cells'][name]['values_m'] is not None else ''
                    for name in OFFSET_NAMES[2:36]])
        warnings.extend(['型值表的10.3m水线与结构吃水10.27m不同，未混用或替换。',
                         '型值表站位范围不等于完整总长，不能为凑总长而拉伸艏端型线。',
                         '斜杠分隔的多交点已保留为列表；缺失值是null，不是0。'])
    elif len(offsets) > 1:
        errors.append('发现多个型值表；没有自动选择，请按源图核定。')
    else:
        warnings.append('输入中没有识别到此母型型值表。')
    write_json(out/'principal_dimensions.json', dict(schema_version=SCHEMA_VERSION, dimensions=consensus, per_drawing=all_principal))
    write_json(out/'quality_report.json', dict(errors=errors, warnings=warnings,
        checks=dict(cross_drawing_principal_dimensions=True, source_dxf_audit=True,
                    hull_geometry_closed=False, pdf_baseline_compared=False),
        pending=['提取PDF的基准条件、目标结果表与39个工况；本程序只解析DXF。',
                 '建立各视图图纸坐标到船体坐标的变换，核对AP/BL/CL和肋距分区。',
                 '人工确认图纸版本、型线原始单位、标注覆盖值及舱容扣除口径。',
                 '将舱容图文字表格结构化；当前所有文字和标注均已保留，尚未认定其工程含义。',
                 '在02阶段重建和核验船体，不将原始组码缓存视为已通过几何质量检查。']))
    write_json(out/'manifest.json', dict(schema_version=SCHEMA_VERSION,
        generated_at_utc=datetime.now(timezone.utc).isoformat(), reader_sha256=sha256(Path(__file__)),
        ezdxf_version=ezdxf.__version__, drawings=entries,
        derived_outputs=['principal_dimensions.json', 'quality_report.json', 'summary.md']+
                        (['hull_offsets.json', 'hull_offsets.csv'] if len(offsets) == 1 else []),
        pdf_sources=[dict(name=p.name, path=str(p.resolve()), status='not_parsed') for p in PROJECT.glob('*.pdf')]))
    summary = ['# 01 DXF读取与校对结果', '', f'读取图纸：{len(drawings)}份。错误：{len(errors)}项；提示：{len(warnings)}项。', '',
               '| 图纸 | 模型空间实体 | 块定义 | 块内实体 | 原始单位 |', '|---|---:|---:|---:|---|']
    for d in drawings:
        c = d['counts']
        summary.append(f"| {d['source']['name']} | {c['modelspace_entities']} | {c['blocks']} | {c['block_entities']} | {d['metadata']['geometry_units']} |")
    summary += ['', '## 图纸主尺度（米）', '']+[f"- {k}: {v['value_m']} ({v['status']})" for k, v in consensus.items()]
    summary += ['', '## 需要核对', '']+['- '+s for s in errors+warnings]
    summary += ['', '详细待办见quality_report.json。本次未修改任何源图，也未建立船体几何。']
    (out/'summary.md').write_text('\n'.join(summary)+'\n', encoding='utf-8')
    return errors


def load_inputs(output=None, include_drawings=False, verify_sources=False):
    """供02及后续阶段使用；默认只读JSON缓存，不打开任何DXF。

    verify_sources=True时只核对源文件SHA256，不重新解析DXF。
    include_drawings=True时加载全部原始实体/块缓存，可能占用较多内存。
    """
    out = Path(output) if output is not None else HERE/'output'
    def read(name):
        return json.loads((out/name).read_text(encoding='utf-8'))
    manifest = read('manifest.json')
    if manifest['schema_version'] != SCHEMA_VERSION:
        raise ValueError('缓存结构版本不匹配，请重新运行read_dxf.py。')
    if verify_sources:
        for entry in manifest['drawings']:
            path = Path(entry['source_path'])
            if not path.exists() or sha256(path) != entry['sha256']:
                raise ValueError(f"源图已变更或缺失：{path}，请重新提取。")
    result = dict(manifest=manifest, principal_dimensions=read('principal_dimensions.json'),
                  quality=read('quality_report.json'))
    if 'hull_offsets.json' in manifest.get('derived_outputs', []):
        offsets = read('hull_offsets.json')
        active = {e['source_name']: e['sha256'] for e in manifest['drawings']}
        if active.get(offsets['source']) != offsets['source_sha256']:
            raise ValueError('型值表缓存与源清单不匹配，请重新提取。')
        result['hull_offsets'] = offsets
    if include_drawings:
        result['drawings'] = []
        for entry in manifest['drawings']:
            with gzip.open(out/entry['cache_path'], 'rt', encoding='utf-8') as f:
                drawing = json.load(f)
            if drawing['source']['sha256'] != entry['sha256']:
                raise ValueError('实体缓存与源清单不匹配，请重新提取。')
            result['drawings'].append(drawing)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inputs', type=Path, nargs='*')
    parser.add_argument('--output', type=Path, default=HERE/'output')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    paths = [p.resolve() for p in (args.inputs or sorted(PROJECT.glob('*.dxf')))]
    if not paths or any(not p.is_file() or p.suffix.lower() != '.dxf' for p in paths):
        parser.error('请提供存在的DXF文件。')
    if len({p.stem for p in paths}) != len(paths):
        parser.error('输入文件名重复，会导致缓存冲突；请分别运行。')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    old = json.loads((out/'manifest.json').read_text(encoding='utf-8')) if (out/'manifest.json').exists() else {}
    previous = {d['source_path']: d for d in old.get('drawings', [])}
    reader_hash = sha256(Path(__file__))
    drawings, entries = [], []
    for path in paths:
        fingerprint = sha256(path)
        folder = out/'drawings'/path.stem
        folder.mkdir(parents=True, exist_ok=True)
        cache = folder/'entities.json.gz'
        entry = previous.get(str(path), {})
        reuse = (not args.force and cache.exists() and entry.get('sha256') == fingerprint
                 and old.get('reader_sha256') == reader_hash and old.get('ezdxf_version') == ezdxf.__version__)
        if reuse:
            with gzip.open(cache, 'rt', encoding='utf-8') as f:
                drawing = json.load(f)
        else:
            drawing = parse_drawing(path, fingerprint)
            with gzip.open(cache, 'wt', encoding='utf-8') as f:
                json.dump(drawing, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        write_tables(folder, drawing)
        drawings.append(drawing)
        entries.append(dict(source_path=str(path), source_name=path.name, sha256=fingerprint,
                            size_bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns,
                            cache_path=cache.relative_to(out).as_posix(), reused_cache=reuse,
                            counts=drawing['counts'], metadata=drawing['metadata']))
        print(f"{'CACHED' if reuse else 'PARSED'} {path.name}: {drawing['counts']['modelspace_entities']} entities")
    errors = build_outputs(drawings, out, entries)
    print(f'Output: {out}')
    print(f'Quality errors: {len(errors)}; see quality_report.json')
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
