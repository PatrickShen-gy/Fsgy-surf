# 04 静水力计算

本阶段已使用02的船体外包络网格完成积分，与装载手册第39—67页对照；不修改02模型。

```powershell
python hydrostatics_calculation.py
python hydrostatics_calculation.py --preview
python test_hydrostatics.py
```

程序按自身文件位置定位输入，与启动时工作目录无关。保留02、03及参考PDF的目录结构即可。依赖见`requirements.txt`，PDF库也会从项目根目录`.cad_deps`查找。

## 输入与计算范围

- 网格：`../02_母型计算几何重建/CAD_mesh/02_计算网格/calculation_mesh.npz`。
- 使用`outer_vertices/hydro_faces`进行体积积分，`plate_faces`标记物理湿表面。内部隔板和船壳材料体积不能从排水体积中扣除。
- 复用03的网格切片和文件工具，用中横剖面切片计算CM/CP、用纵向剖面积分进行独立校验；主静水力积分不依赖PDF结果。
- PDF第39页设置：LBP=172m，B=28.8m，xref=86m，海水密度1.025t/m³，外板18mm，龙骨板20mm，横倾0°。
- 129档型吃水：2—14.6m，间隔0.1m，加9.83/10.27m。8档纵倾：−4、−3、−2、−1.5、−1、−0.5、0、0.5m，共1032工况。
- 坐标原点AP/CL/BL，x向艏、z向上。纵倾定义为TF−TA，水面为`z=T+trim*(x−86)/172`；T是参考点型吃水。

## 积分与口径

用水面裁切每个三角面，以水面上的共同顶点构造有向四面体，积分体积和一次体积矩，得到VOLM/LCB/VCB。将同一组水线交线用于格林公式，积分面积、漂心及横纵惯性矩；不依赖栅格采样面积。水线穿过顶点/边、与封顶共面时也作单独处理。

零纵倾时：

```text
KMT = VCB + IT/VOLM
KML = VCB + IL/VOLM
TCP = rho*WLA/100                    # 手册称TCP，也常写TPC
MCT = rho*IL/(100*LBP)               # 未知KG时的静水力近似
CB  = VOLM/(LBP*B*T)
CM  = Amid/(B*T)
CP  = VOLM/(LBP*Amid)
CW  = WLA/(LBP*B)
```

IT为对纵向轴的水线面二次矩，IL为对横向轴的二次矩，均过漂心。纵倾时先在实际斜水面内计算面积、漂心和惯性矩，再将稳心位置投影到船体z方向；WLA为水平投影面积，使`dVOLM/dT=WLA`。相关惯性矩、BM与实际斜水面面积另有导出。

02的钢板材料模型仍为12mm。为与PDF的总排水量口径比较，单列固定18mm的**一阶估计**：

```text
displacement_moulded_t = rho*VOLM
DISP = rho*(VOLM + 0.018*WSA)
displacement_12mm_estimate_t = rho*(VOLM + 0.012*WSA)
```

这不是实际外偏置船壳的精确排水量，也不是把钢材重量加到排水量中。浮心、惯性矩和稳心保持原型线几何口径。报告还列出PDF反推的等效板厚作为诊断，不用它拟合参数。龙骨条宽未知，未额外叠加20mm全底板修正。

KMT/KML是稳心距基线高度；本阶段没有船舶KG，不能把它们当作GM，也不作稳性合格判定。06、07、08再引入重量重心、自由液面及实际浮态。

## 输出

`output/hydrostatics_verification.html`为离线交互页面，可选择指标、吃水与纵倾，查看曲线、误差和逐项对照。

| 文件 | 用途 |
|---|---|
| `hydrostatics_even_keel.csv` | 129档正浮计算结果 |
| `hydrostatics_trimmed.csv` | 1032个工况的计算结果 |
| `hydrostatics_data.npz` | 供后续程序读取的数组，各指标维度为`[trim, draft]`，形状8×129 |
| `verification_even_keel.csv` | 1935项正浮参考值对照 |
| `verification_trimmed.csv` | 7224项纵倾对照，包含903项零纵倾重复值 |
| `verification_report.json` | 输入SHA256、条件、数值自检、逐指标误差统计、限制及关键吃水 |
| `reference/pdf_hydrostatics.json` | 原始参考值、原行文字、字段物理页码、条件及PDF指纹 |
| `reference/pdf_even_keel.csv` / `pdf_trimmed.csv` | 可直接复核的PDF提取表 |
| `hydrostatic_comparison.png` | 正浮曲线总览 |

参考提取会核对每行列数、全部吃水点、纵倾列头，并检查两类PDF表中903个零纵倾重复数值完全一致。原值不改写。PDF指纹变化会重新提取。

## 当前结果

设计吃水9.83m，零纵倾：

| 指标 | 网格计算 | PDF | 相对差异 |
|---|---:|---:|---:|
| 型排水体积 VOLM / m³ | 40252.155 | 40301.2 | −0.122% |
| 总排水量 DISP / t（18mm一阶修正） | 41393.520 | 41442.3 | −0.118% |
| 纵向浮心 LCB / m | 90.3265 | 90.388 | −0.068% |
| 垂向浮心 VCB / m | 5.15685 | 5.143 | +0.269% |
| 横稳心高度 KMT / m | 12.3462 | 12.338 | +0.067% |
| 纵稳心高度 KML / m | 253.4557 | 251.955 | +0.596% |
| 湿表面积 WSA / m² | 7320.385 | 7250 | +0.971% |

结构吃水10.27m的型排水体积为42275.950m³（PDF42323.0m³，−0.111%），KMT为12.27736m（PDF12.273m，+0.036%）。

全吃水正浮对比中，型排水体积最大相对差异约0.601%，KMT约1.262%，KML约1.877%，湿表面积约2.331%；这些是当前模型差异，不是程序验收阈值。

**55个工况触及虚拟封顶**，已在CSV/NPZ/HTML标记`deck_limit_reached`。模型封顶局部高度14.6—15.1m，存在艏部起升，不能只用全船最大z判断是否触顶。触顶后水线面积、纵稳心等可能出现很大偏差，例如14.6m/−1m纵倾的KML差异约84%；这些结果只供诊断。后续须补充甲板以上几何及进水边界，不能直接当作有效浮态使用。未触顶的非零纵倾工况中，VOLM最大差异约0.654%，KMT约1.602%，KML约1.977%。

## 验证及后续接口

6项测试覆盖解析箱体、斜水面楔形积分、虚拟甲板裁切、面内顶点顺序、体积对吃水导数、参考表与导出完整性。数值自检通过：9.83m的独立剖面积分与网格体积相对差约9.28×10⁻⁸，水线面与体积数值导数相对差小于1.2×10⁻¹¹。

后续程序可按`draft_m`、`trim_m`读取NPZ轴及指标。浮态迭代更适合直接调用本文件的`HydrostaticCalculator.from_npz()`和`evaluate(draft, trim)`，无需对表格外插；返回`deck_limit_reached`时应停止当作当前模型的有效浮态。若需要CP/CM，可给`evaluate`传入相应参考点的中横剖面积`midship_area`。

当前完成的是几何静水力计算与对照，未消除02已知的艏部局部差异，也没有通过修改型线拟合PDF。
