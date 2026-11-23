"""13: 模板读取、配色、图框及共用标注。"""
from pathlib import Path
import numpy as np
from fsgy_surf.io.files import read_json

TEMPLATES = Path(__file__).with_name('templates')


def load_template(name):
    if name == 'simple':
        return None
    if name != 'mother_35000':
        raise ValueError('drawings.template accepts mother_35000 or simple')
    return read_json(TEMPLATES/name/'template.json')


def style_sheet(sheet, template):
    styles = {name: (color, 13) for name, color in template['palette'].items()}
    styles.update(template['styles'])
    for layer, (color, weight) in styles.items():
        entry = sheet.doc.layers.get(layer) if layer in sheet.doc.layers else sheet.doc.layers.new(layer)
        if color == '#ffffff':
            entry.dxf.discard('true_color')
            entry.dxf.color = 7
        else:
            entry.dxf.true_color = int(color[1:], 16)
        entry.dxf.lineweight = weight
        sheet.layers[layer] = (color, weight)


def points_of(geometry):
    if geometry.is_empty:
        return []
    if hasattr(geometry, 'geoms'):
        return [p for part in geometry.geoms for p in points_of(part)]
    return list(geometry.coords)


def leader(sheet, point, elbow, label, height, layer='ANNOTATION'):
    sheet.polyline([point,elbow],layer)
    sheet.text(label,np.asarray(elbow)+[height*.4,height*.25],height,layer=layer)


def rectangle(sheet, bounds, layer='REF_7'):
    x0, y0, x1, y1 = bounds
    sheet.polyline([(x0,y0), (x1,y0), (x1,y1), (x0,y1)], layer, closed=True)


def title_block(sheet, bounds, name, title, number):
    x0, y0, x1, y1 = bounds
    width, height = x1-x0, y1-y0
    rectangle(sheet, bounds, 'REF_6')
    for fraction in (.25, .5, .75):
        y = y0+height*fraction
        sheet.polyline([(x0,y), (x1,y)], 'REF_6')
    for text, row, size in ((title, 3, .1), (name, 2, .065),
                            (f'图号 {number}    MODEL 1:1', 1, .055), ('单位 mm    FSGY-SURF', 0, .055)):
        sheet.text(text, (x0+width*.04, y0+height*(row+.2)/4), height*size)



