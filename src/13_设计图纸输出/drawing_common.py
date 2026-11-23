"""13: Draw editable 2-D design plans from the stage-02 hull and compartments."""
import html
import os
from pathlib import Path
import numpy as np
import ezdxf
from ezdxf.enums import TextEntityAlignment
from ezdxf.addons import text2path
from ezdxf.fonts.fonts import FontFace
from ezdxf.math import Matrix44
from ezdxf.path import bbox as path_bbox
from shapely.geometry import box, LineString, Polygon
from shapely import make_valid, symmetric_difference_all
from shapely.ops import polygonize, unary_union
from bonjean_calculation import MeshSlicer


LAYERS = {
    'HULL': ('#ffffff', 35), 'INNER': ('#27e8ff', 18),
    'STATIONS': ('#79a6ff', 25), 'WATERLINES': ('#79a6ff', 25),
    'BUTTOCKS': ('#79a6ff', 25), 'GRID': ('#8390a3', 9),
    'CENTER': ('#27e8ff', 13), 'CARGO': ('#46ff83', 25),
    'BALLAST': ('#27e8ff', 25), 'DOUBLE_BOTTOM': ('#ffbd4a', 25),
    'MACHINERY': ('#e490ff', 25), 'PEAK': ('#c7d2e8', 25),
    'BULKHEAD': ('#ff64e5', 25), 'DIMENSIONS': ('#fff23b', 18),
    'TEXT': ('#fff23b', 18), 'ANNOTATION': ('#fff23b', 18),
}
SCOPE = '计算模型：主船体及主分舱；甲板室、设备及构件按母型参考图示表达。'


class DrawingSlicer(MeshSlicer):
    def section(self, x):
        key = round(float(x), 9)
        if key in self.cache:
            return self.cache[key]
        tol = self.tolerance
        selected = self.triangles[(self.xmin <= x+tol) & (self.xmax >= x-tol)]
        edges = set()
        # 共面甲板取负侧极限；最小端点取正侧极限。交点仍位于指定平面，
        # 采用半开边规则消除共面三角形的内部边，不移动真实站位或水线。
        direction = -1 if abs(x-self.vertices[:, 0].min()) < tol else 1
        for triangle in selected:
            delta = triangle[:, 0]-x
            side = direction*delta < -tol
            hits = set()
            for i in range(3):
                j = (i+1)%3
                if side[i] != side[j]:
                    t = -delta[i]/(delta[j]-delta[i])
                    point = triangle[i, 1:] + t*(triangle[j, 1:]-triangle[i, 1:])
                    hits.add(tuple(np.round(point, 8)))
            if len(hits) == 2:
                edges.add(tuple(sorted(hits)))
        section = unary_union(list(polygonize([LineString(edge) for edge in sorted(edges)])))
        if section.is_empty or not section.is_valid or section.area <= 0:
            raise ValueError(f'No closed drawing section at coordinate {x:g} m')
        self.cache[key] = section
        return section


def line_parts(geometry):
    """保留分离曲线，避免把间断水线或纵剖线跨空白区连接。"""
    if geometry.is_empty:
        return
    if geometry.geom_type in ('LineString', 'LinearRing'):
        yield np.asarray(geometry.coords)
    elif hasattr(geometry, 'geoms'):
        for part in geometry.geoms:
            yield from line_parts(part)


class Sheet:
    """共用一套米制绘图坐标；DXF写为毫米，SVG保持相同几何比例。"""
    def __init__(self, text_height):
        windows_dir = os.environ.get('WINDIR')
        font_path = Path(windows_dir)/'Fonts'/'Deng.ttf' if windows_dir else None
        if font_path is None or not font_path.is_file():
            raise FileNotFoundError('Drawing generation requires DengXian (Deng.ttf) in the Windows Fonts directory')
        self.doc = ezdxf.new('R2010')
        self.doc.units = 4
        self.doc.header['$MEASUREMENT'] = 1
        self.doc.styles.new('CHINESE', dxfattribs={'font': 'Deng.ttf'})
        for layer, (color, weight) in LAYERS.items():
            self.doc.layers.new(layer, dxfattribs={
                'true_color': int(color[1:], 16), 'lineweight': weight})
        self.model = self.doc.modelspace()
        self.height = text_height
        self.items = []
        self.layers = dict(LAYERS)
        self.doc.layers.new('TEXT_EDITABLE', dxfattribs={'color': 2})
        self.doc.layers.get('TEXT_EDITABLE').off()
        self.font = FontFace(filename=str(font_path))
        self.text_paths = {}

    def glyph_paths(self, value, height, centered, rotation, at):
        key = (value, height, centered)
        if key not in self.text_paths:
            align = TextEntityAlignment.BOTTOM_CENTER if centered else TextEntityAlignment.BOTTOM_LEFT
            self.text_paths[key] = text2path.make_paths_from_str(value, self.font, size=height, align=align)
        matrix = Matrix44.z_rotate(np.deg2rad(rotation)) @ Matrix44.translate(*at, 0)
        return [path.transform(matrix) for path in self.text_paths[key]]

    def curve_layer(self, name, base):
        color, weight = self.layers[base]
        self.doc.layers.new(name, dxfattribs={'true_color': int(color[1:], 16), 'lineweight': weight})
        self.layers[name] = (color, weight)
        return name

    def polyline(self, points, layer='HULL', closed=False):
        p = np.asarray(points, float)
        if len(p) < 2:
            return
        self.model.add_lwpolyline(p * 1000, close=closed, dxfattribs={'layer': layer})
        self.items.append(('polyline', p, layer, closed))

    def text(self, value, at, height=None, centered=False, rotation=0, layer='TEXT'):
        height = self.height if height is None else height
        p = np.asarray(at, float)
        value = str(value)
        if not value.strip():
            return
        entity = self.model.add_text(value, dxfattribs={
            'height': height * 1000, 'style': 'CHINESE',
            'layer': layer if value.isascii() else 'TEXT_EDITABLE', 'rotation': rotation})
        entity.set_placement(p * 1000, align=(TextEntityAlignment.BOTTOM_CENTER
                             if centered else TextEntityAlignment.BOTTOM_LEFT))
        if not value.isascii():
            # 字符包围框会相互覆盖；按真实轮廓的奇偶填充关系建立内外环，
            # 避免混排英文I、T等笔画被错误识别成其他字符的孔洞。
            paths=self.glyph_paths(value,height,centered,rotation,p)
            polygons=[make_valid(Polygon([(v.x,v.y) for v in path.flattening(height/150,segments=8)])) for path in paths]
            shape=symmetric_difference_all(polygons)
            for part in ([shape] if shape.geom_type=='Polygon' else shape.geoms):
                if part.geom_type!='Polygon':continue
                hatch=self.model.add_hatch(color=256,dxfattribs={'layer':layer})
                hatch.paths.add_polyline_path(np.asarray(part.exterior.coords)*1000,is_closed=True,flags=1)
                for ring in part.interiors:
                    hatch.paths.add_polyline_path(np.asarray(ring.coords)*1000,is_closed=True,flags=0)
        self.items.append(('text', p, value, height, centered, rotation, layer))

    def curves(self, geometry, offset=(0, 0), layer='HULL', mirror=False):
        for p in line_parts(geometry):
            p = p.copy()
            if mirror:
                p[:, 0] *= -1
            self.polyline(p + offset, layer)

    def dimension(self, a, b, distance, label, vertical=False, unit='m'):
        a, b = np.asarray(a, float), np.asarray(b, float)
        h = self.height
        base = (distance, a[1]) if vertical else (a[0], distance)
        dim = self.model.add_linear_dim(base=np.array(base) * 1000,
            p1=a * 1000, p2=b * 1000, angle=90 if vertical else 0,
            text=label, dxfattribs={'layer': 'DIMENSIONS'}, override={
                'dimlfac': 1. if unit == 'mm' else .001,
                'dimdec': 0 if unit == 'mm' else 3, 'dimtxt': h * 1000,
                'dimasz': h * 500, 'dimexo': h * 200, 'dimexe': h * 300,
                'dimtxsty': 'CHINESE', 'dimtad': 1, 'dimdsep': 46})
        dim.render()
        # SVG预览采用相同的尺寸界线和实测值，DXF保留原生DIMENSION实体。
        aa, bb = a.copy(), b.copy()
        axis = 0 if vertical else 1
        aa[axis] = bb[axis] = distance
        for p, q in ((a, aa), (b, bb), (aa, bb)):
            self.items.append(('polyline', np.array([p, q]), 'DIMENSIONS', False))
        for p in (aa, bb):
            self.items.append(('polyline', np.array([p-h*.25, p+h*.25]), 'DIMENSIONS', False))
        value = np.linalg.norm(b-a)
        label = label.replace('<>', f'{value*1000:.0f}' if unit == 'mm' else f'{value:.3f}')
        self.items.append(('text', (aa+bb)/2 + np.array([h*.5, 0] if vertical else [0, h*.4]),
                           label, h, True, 90 if vertical else 0, 'DIMENSIONS'))

    def save(self, stem):
        points = []
        for item in self.items:
            if item[0] == 'polyline':
                points.extend(item[1])
            else:
                _, p, value, height, centered, rotation, layer = item
                bounds=path_bbox(self.glyph_paths(value,height,centered,rotation,p),fast=True)
                points.extend([(bounds.extmin.x,bounds.extmin.y),(bounds.extmax.x,bounds.extmax.y)])
        p = np.asarray(points)
        low, high = p.min(axis=0)-self.height*3, p.max(axis=0)+self.height*3
        center = (low+high)/2
        self.doc.set_modelspace_vport(height=(high[1]-low[1])*1000, center=center*1000)
        self.doc.saveas(stem.with_suffix('.dxf'))
        width, height = high-low
        content = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{low[0]} {-high[1]} {width} {height}">',
                   f'<rect x="{low[0]}" y="{-high[1]}" width="{width}" height="{height}" fill="#10141c"/>']
        # 表格数字和重复站号共用字形定义，避免轮廓文字重复膨胀SVG。
        labels={}
        content.append('<defs>')
        for item in self.items:
            if item[0]!='text':continue
            _,at,value,size,centered,rotation,layer=item
            key=(value,size,centered)
            if key in labels:continue
            ident=f't{len(labels)}';labels[key]=ident
            commands=[]
            for path in self.glyph_paths(value,size,centered,0,[0,0]):
                points=list(path.flattening(distance=size/100,segments=8))
                commands.append('M '+' L '.join(f'{p.x:.5f},{-p.y:.5f}' for p in points)+' Z')
            content.append(f'<path id="{ident}" d="{" ".join(commands)}" fill-rule="evenodd"/>')
        content.append('</defs>')
        for item in self.items:
            if item[0] == 'polyline':
                _, coords, layer, closed = item
                color, weight = self.layers[layer]
                coords = np.vstack([coords, coords[0]]) if closed else coords
                vertices = ' '.join(f'{x:.6f},{-y:.6f}' for x, y in coords)
                stroke = self.height * (.10 if weight < 18 else .15 if weight < 35 else .22)
                content.append(f'<polyline points="{vertices}" fill="none" stroke="{color}" stroke-width="{stroke:.5f}"/>')
            else:
                _, at, value, size, centered, rotation, layer = item
                ident=labels[(value,size,centered)]
                content.append(f'<use href="#{ident}" transform="translate({at[0]} {-at[1]}) rotate({-rotation})" '
                               f'fill="{self.layers[layer][0]}"><title>{html.escape(value)}</title></use>')
        content.append('</svg>')
        stem.with_suffix('.svg').write_text('\n'.join(content), encoding='utf-8')


def grid_values(settings, key, default, lower, upper, upper_open=False):
    end = ')' if upper_open else ']'
    message = f'drawings.{key} must be a nonempty list in [{lower:g}, {upper:g}{end} m'
    try:
        values = np.asarray(settings.get(key, default), float)
    except (TypeError, ValueError) as error:
        raise ValueError(message) from error
    if (values.ndim != 1 or not len(values) or not np.isfinite(values).all()
            or np.any(values < lower) or np.any(values >= upper if upper_open else values > upper)):
        raise ValueError(message)
    return np.unique(values)


def slicing_axes(vertices, faces):
    # MeshSlicer输出平面内的两个坐标，分别得到YZ、XY和XZ截线。
    return (DrawingSlicer(vertices, faces), DrawingSlicer(vertices[:, [2, 0, 1]], faces),
            DrawingSlicer(vertices[:, [1, 0, 2]], faces))


def heading(sheet, title, name, dimensions, at):
    x, z = at
    h = sheet.height
    sheet.text(title, (x, z), h*1.7)
    sheet.text(name, (x, z-h*2.6))
    sheet.text('LBP={lbp_m:.3f} m   B={beam_m:.3f} m   D={depth_m:.3f} m   T={design_draft_m:.3f} m'.format(**dimensions),
               (x, z-h*5))


def block_layer(block):
    return {'cargo': 'CARGO', 'ballast': 'BALLAST', 'double_bottom': 'DOUBLE_BOTTOM',
            'machinery': 'MACHINERY', 'peak': 'PEAK'}[block['category']]


BLOCK_LAYERS = {'cargo': 'CARGO', 'ballast': 'BALLAST', 'double_bottom': 'DOUBLE_BOTTOM',
                'machinery': 'MACHINERY', 'peak': 'PEAK'}


def hull_deck(ctx):
    mesh=ctx.geometry()
    vertices=mesh['outer_vertices']
    count=len(mesh['ring_x'])
    n=len(vertices)//count
    return vertices[:count*n].reshape(count,n,3)[:,(n-1)//2]


def model_cut(sheet,slice_class,ctx,axis,coordinate,offset,h,capacity=False):
    mesh=ctx.geometry()
    axes=[1,0,2] if axis==1 else [2,0,1]
    outer=slice_class(mesh['outer_vertices'][:,axes],mesh['hydro_faces']).section(coordinate)
    sheet.curves(outer.boundary,offset,'HULL')
    spaces=ctx.arrays(ctx.data[2]['compartments_file'])
    beam=ctx.data[2]['dimensions']['beam_m']
    for block in ctx.data[2]['compartments']:
        ident=block['id'];v=spaces[ident+'_vertices'];f=spaces[ident+'_faces']
        if not v[:,axis].min()<coordinate<v[:,axis].max():continue
        section=slice_class(v[:,axes],f).section(coordinate)
        layer=BLOCK_LAYERS[block['category']]
        sheet.curves(section.boundary,offset,layer)
        point=np.array(section.representative_point().coords[0])+offset
        text_height=h*.8
        if block['category']=='ballast' and axis==2:
            sign=1 if point[1]>offset[1] else -1
            anchor=np.array([offset[0]+(block['start_m']+block['end_m'])/2,
                             offset[1]+sign*(beam/2+2*h)])
            sheet.polyline([point,anchor],layer)
            sheet.text(ident,anchor,text_height,centered=True,layer=layer)
            if capacity:sheet.text(f'{block["volume_m3"]:.0f} m³',anchor+[0,sign*1.2*h],text_height*.9,centered=True,layer=layer)
        else:
            if capacity and block['category']=='cargo':
                xmin,zmin,xmax,zmax=section.bounds
                mask=box(point[0]-offset[0]-8*h,point[1]-offset[1]-2*h,
                         point[0]-offset[0]+8*h,point[1]-offset[1]+2*h)
                for line in [LineString([(xmin,zmin),(xmax,zmax)]),LineString([(xmin,zmax),(xmax,zmin)])]:
                    sheet.curves(line.intersection(section).difference(mask),offset,layer)
            sheet.text(ident,point,text_height,centered=True,layer=layer)
            if capacity:sheet.text(f'V={block["volume_m3"]:.1f} m³',point+[0,-1.25*h],text_height*.85,centered=True,layer=layer)
    sheet.polyline([(outer.bounds[0]+offset[0],offset[1]),(outer.bounds[2]+offset[0],offset[1])],'CENTER')



