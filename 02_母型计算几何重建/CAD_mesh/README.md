# 阶段02输出

- `01_展示模型/ship_display.html`：离线彩色船体与主要分舱，默认无网格。
- `01_展示模型/ship_display.obj`及MTL、`ship_compartments.dxf`：按对象/图层区分船壳及23个主要分舱。
- `02_计算网格/steel_shell_mesh.html`：真实内外钢板和顶缘网格，可旋转、切半舷或截取船长。
- `02_计算网格/steel_shell.obj/stl/vtk`：开口、空心、含顶缘封边的钢板材料域，不含舱壁和甲板。
- `02_计算网格/bulkheads.obj/stl/vtk`：独立的8mm横隔板材料域。
- `02_计算网格/longitudinal_partitions.obj/stl/vtk`：10块货舱与左右边压载舱间的8mm内舷纵隔板，含上部斜板。
- `02_计算网格/steel_structure.obj/stl/vtk`：船壳、横隔板与内舷纵隔板的完整材料装配，用于分项或合计体积、质量和重心计算。
- `02_计算网格/bulkhead_midsurface.bdf/vtk`：8mm隔板中面壳单元。
- `02_计算网格/partition_midsurface.bdf/vtk`：内舷纵隔板中面壳单元，默认厚度8mm。
- `02_计算网格/bulkheads.json`与`steel_materials.json`：位置、厚度、分项体积/质量/重心及合计。
- `02_计算网格/hydro_outer.obj/stl/vtk`：水密外包络，仅供浮力/切片/CFD表面前处理，不可当作钢材实体。
- `02_计算网格/shell_midsurface.bdf/vtk`：恒定板厚的壳单元中面。
- `02_计算网格/calculation_mesh.npz`：后续程序建议直接读取的双精度顶点、三角面及站位索引。
- `reconstruction_report.json`：来源指纹、逐站曲线匹配、网格检查和建模假设。

全部长度单位m。X向艏，Y向左舷，Z向上。源数据为母型172m垂线间长，不是34000t改型。
默认12mm板厚可用`--thickness-mm`调整；法向偏移几何误差见报告，BDF/VTK属性严格采用设定厚度。
默认8mm横隔板可用`--bulkhead-thickness-mm`调整，以图纸舱界为中面，两侧舱室各退让半个板厚。
默认8mm内舷纵隔板可用`--partition-thickness-mm`调整，左右5对，共10块；货舱和边压载舱各按法向半厚度退让。
纵隔板沿典型剖面内舷界面布置，包含垂直段和上部斜段；纵向变剖面采用局部斜率修正的离散偏移。
纵隔板止于横隔板的内表面，顶部和底部裁在原高度。双层底上方的水平内底板不在本次新增范围内。
横隔板沿真实内壁裁形，板厚区间内5处切片取交集，沿轮廓预留0.05mm数值间隙，避免与船壳重复计量。
7道横隔板位于X=7.4、25.8、53.8、82.6、111.4、140.2、165.0m，为连续平板近似，未模拟波纹、门孔或扶强材。
钢结构输出为各自闭合的材料域装配，未作焊缝布尔融合；合计质量仅含船壳、横隔板和内舷纵隔板，非整船空船重量。
默认把型值表最前站之后按总长收尖补全；该段是假设，可用`--bow-closure station`保留原站位范围。
分舱为主要空间示意，尚未复原全部油水小舱、管弄和墩座，不能作为核定舱容。
面闭合、面方向和退化面检查通过不代表已通过CFD网格验收；CFD还需流场域、体网格、边界层及几何质量核查。

后续读取示例：
```python
import numpy as np
mesh = np.load('calculation_mesh.npz')
v, f = mesh['outer_vertices'], mesh['hydro_faces']  # 03/04包络切片
steel_v, steel_f = mesh['structure_vertices'], mesh['structure_faces']  # 船壳+全部隔板材料
# solid_vertices/solid_faces: 纯船壳；bulkhead_*: 横隔板；partition_*: 内舷纵隔板
```
