"""DXF entities, offsets and mother-ship dimensions."""
from pathlib import Path
from collections import Counter
import math, re, gzip, json
import ezdxf
from ezdxf.lldxf.tagwriter import TagCollector
from fsgy_surf.io.files import digest, save_json
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
    result['counts'] = dict(modelspace_entities=len(result['modelspace']), blocks=len(result['blocks']),
                            modelspace_types=dict(Counter(e['type'] for e in result['modelspace'])),
                            block_entities=sum(len(b['entities']) for b in result['blocks']))
    return result


def run(ctx):
    mother = ctx.config['mother_ship']
    drawings, cached = [], []
    dimensions = {}
    reader_signature = digest(Path(__file__))
    for role, filename in mother['drawings'].items():
        path = Path(filename)
        target = ctx.cache(1) / f'{role}.json.gz'
        sha = digest(path)
        drawing = None
        if target.exists():
            try:
                with gzip.open(target, 'rt', encoding='utf-8') as stream:
                    candidate = json.load(stream)
                if (candidate.get('_reader_sha256') == reader_signature
                        and candidate['source']['sha256'] == sha
                        and candidate['source']['path'] == str(path)):
                    drawing = candidate
            except (OSError, ValueError, KeyError, EOFError):
                pass
        if drawing is None:
            drawing = parse_drawing(path, sha)
            drawing['role'] = role
            drawing['_reader_sha256'] = reader_signature
            with gzip.open(target, 'wt', encoding='utf-8') as stream:
                json.dump(drawing, stream, ensure_ascii=False, allow_nan=False)
        cached.append(str(target))
        drawings.append(drawing)
        if role == 'lines':
            for key, entries in principal_dimensions(drawing).items():
                values = {entry['value'] for entry in entries}
                if len(values) != 1:
                    raise ValueError(f'Ambiguous mother-ship dimension: {key}')
                dimensions[key] = values.pop()
            offsets = extract_offsets(drawing)
    dimensions.update(mother.get('dimensions', {}))
    for key in ('loa_m', 'lbp_m', 'beam_m', 'depth_m', 'design_draft_m'):
        if not dimensions.get(key) or not math.isfinite(dimensions[key]) or dimensions[key] <= 0:
            raise ValueError(f'Mother-ship dimension missing: {key}')
    if offsets is None:
        raise ValueError('No supported offset table was found in the lines DXF')
    payload = dict(dimensions=dimensions, hull_offsets=offsets,
                   drawing_caches=cached, adapter=mother['adapter'],
                   coordinates='AP=0; X forward; Y port; Z above baseline; units m')
    save_json(ctx.output(1) / 'mother_ship.json', {
        'dimensions': dimensions, 'stations': offsets['stations'],
        'drawings': {role: filename for role, filename in mother['drawings'].items()},
        'coordinates': payload['coordinates']})
    return payload


def load_drawings(stage_data):
    drawings = []
    for path in stage_data['drawing_caches']:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            drawings.append(json.load(stream))
    return dict(drawings=drawings, hull_offsets=stage_data['hull_offsets'])
