# 07 浮态与平衡求解

由06的重量/重心/FSM与02船体网格联立求解型吃水、纵倾和小横倾，复用04三角网格积分器。39个详细工况均数值收敛，水线未触及虚拟封顶，计算GM均为正；参考浮态没有作为初值或拟合目标。

```powershell
python equilibrium_calculation.py
python equilibrium_calculation.py --preview
python test_equilibrium.py
# 自定义载荷，沿用06的输入格式：
python equilibrium_calculation.py --case-file ../06_重量与重心系统/example_custom_case.json
# 使用12mm排水量修正的自定义工况：
python equilibrium_calculation.py --case-file ../06_重量与重心系统/example_custom_case.json --shell-thickness-mm 12
```

脚本相对于自身位置定位输入，可从任意目录启动；命令行自定义文件的相对路径则相对于启动目录。需保留02—06的目录结构；依赖见`requirements.txt`，PDF库也从项目根目录`.cad_deps`查找。

## 输入及独立求解范围

- `../06_重量与重心系统/output/equilibrium_inputs.json`：39个工况的总重量、LCG/TCG/KG和FSM。重量重心由06采用的手册逐项载荷合成，本阶段不重新改变载荷设置。
- `../02_母型计算几何重建/CAD_mesh/02_计算网格/calculation_mesh.npz`：外包络顶点、封闭排水面、物理外板面标记。
- 直接从倾斜水面裁切船体网格积分，不从PDF静水力表或04纵倾表插值反解，因此可处理LD09E约−4.6m等超出04参考表纵倾范围的工况。
- PDF详细工况页只作验证：提取T/TR/TA/TF/横倾、KM、GM0和GM。GM优先用同页GM0.15判据栏的三位小数值；其它多数稳性栏只有两位小数。

## 方程与符号

AP/CL/BL为坐标基准，x向艏，y正为左舷，z向上。T为x=86m处中线型吃水，trim=TF−TA，phi为横倾角，正值表示左舷下沉。水面为：

```text
s = trim / LBP
h = tan(phi)
z = T + s*(x-xref) + h*y
n = (-s, -h, 1) / sqrt(1+s²+h²)
TA = T - s*xref
TF = T + s*(LBP-xref)
```

以横倾角旋转网格坐标，使水面转成04支持的仅纵倾水面，调用同一裁切积分器，再将浮心旋回船体坐标。该旋转保持体积和面积，不改变船体几何。

联立以下三个方程：

```text
rho*(VOLM + shell_thickness*WSA) - W = 0
(LCB-LCG) + s*(VCB-KG) = 0
(TCB-TCG) + h*(VCB-KG-FSM/W) = 0
```

重力方向随倾斜水面变化，故不能只令LCB=LCG。06只有横向自由液面矩，等效KG虚升仅用于小横倾方程；纵倾方程不误用这个横向修正。没有重新求解每个液舱在实际浮态下的液位与重心，纵向自由液面效应尚未单独建模。

先按目标重量反求正浮吃水，再用当前网格的纵横初稳性估计纵倾/横倾初值，最后使用带边界的最小二乘法联立求解，并用中心差分求雅可比矩阵。**初值、残差和收敛判定均不读取PDF参考浮态。**

数值收敛要求质量残差小于0.01t，两个水面切向力臂残差分别小于10⁻⁶m。实际最大质量残差约2.64×10⁻⁸t，纵向力臂约1.97×10⁻¹¹m，横向约1.08×10⁻¹⁴m。这些表明方程已解平衡，不代表船型与参考完全相同。

## 初稳性及外板口径

```text
BM_T = IT / VOLM
GM0 = BM_T + dot(B-G, n)
GM = GM0 - FSM/W
KM_bodyZ = VCB + BM_T*n_z
```

GM为当前小倾角姿态附近的近似初稳性量，KM导出船体z坐标；有纵横倾时，GM不必严格等于`KM_bodyZ-KG`，倾角趋零时退化为常用公式。

默认海水密度1.025t/m³，按04/PDF的18mm进行排水体积一阶修正，02钢材仍为12mm。**浮心和水线惯性矩仍采用型线网格口径**，没有完整外偏置后的几何/矩积分，因此这不是精确18mm外壳的全部静水力计算。

输出对LD00、LD01A、LD04A的0/12/18mm修正敏感性，各3组，不据此拟合PDF。非默认的`--rho`或`--shell-thickness-mm`也会影响全表复算结果，条件写入报告。

## 与详细工况表的差异

39工况、8项指标共312项对照，按绝对差异统计，避免对接近零的纵倾/横倾使用误导性的相对误差。

| 指标 | 平均绝对差异 | 最大绝对差异 |
|---|---:|---:|
| 参考点型吃水 T | 0.01088m | 0.01492m |
| 纵倾 TF−TA | 0.03100m | 0.04504m |
| 艉垂线型吃水 TA | 0.00886m | 0.03744m |
| 艏垂线型吃水 TF | 0.02380m | 0.03227m |
| 横倾 | 0.01675° | 0.05060° |
| 修正GM | 0.02103m | 0.24834m |

GM最大差异发生于LD00空船：计算19.52534m，手册判据栏19.277m，约+1.29%。后续分析需结合低吃水型线及完整外板修正等限制，不能仅凭力平衡残差很小认定几何误差已经消除。

例如LD04A：计算T=10.27834m、trim=−1.60045m、TA=11.07857m、TF=9.47812m、横倾−0.05750°、修正GM=3.09287m；手册详细工况T=10.267m、trim=−1.640m。

LD09C继续采用06的详细载荷表口径，参考T=10.224m、trim=−0.763m。PDF总览该工况的重量/浮态不一致，相关原值保留于06报告；本阶段不改用总览浮态拟合。

## 输出及后续接口

所有输出位于`output`，其中`equilibrium_verification.html`可离线查看工况切换、平衡水线纵剖示意、浮心/重心、逐项对照及残差。

| 文件 | 内容 |
|---|---|
| `equilibrium_results.json` | 全部载荷输入、平衡结果、评估记录和独立参考 |
| `equilibrium_summary.csv` | 39个工况的浮态、静水力、GM和收敛/适用标记 |
| `equilibrium_data.npz` | 以`case_ids`为轴的数组，含T/TR/TA/TF/横倾、GM、浮心与排水体积 |
| `verification.csv` | 312项计算与参考差异 |
| `solver_history.csv` | 全部残差评估点，包含中心差分点，不全部是接受的迭代步 |
| `reference_pose_diagnostic.csv` | 在PDF参考姿态下重新积分的质量/力臂差异，帮助区分几何误差与求解误差 |
| `shell_thickness_sensitivity.csv` | 0/12/18mm一阶排水修正敏感性 |
| `verification_report.json` | 输入指纹、条件、残差、差异统计与近似说明 |
| `reference/pdf_equilibrium.json/csv` | 详细工况原值、页码，JSON另保留整页文字 |
| `stability_inputs.json` | 08及以后所需的载荷、平衡姿态与初稳性数据 |
| `equilibrium_comparison.png` | 全工况对照图 |

```python
from equilibrium_calculation import load_results, EquilibriumSolver
from hydrostatics_calculation import HydrostaticCalculator
data = load_results(verify_sources=True)
solver = EquilibriumSolver(HydrostaticCalculator.from_npz())
# 输入至少包含mass_t, LCG_m, TCG_m, KG_m, FSM_tm
result = solver.solve(data['cases'][0]['load'])
```

自定义输入既可为上述总载荷字段，也可使用06的空船+逐项载荷格式，含05几何载荷。自定义结果写到`output/custom_cases/<case_id>/equilibrium.json`，不覆盖手册全表复算结果。

## 验证及适用限制

8项测试已通过：解析箱体斜水面体积/浮心/体积导数、正浮箱体GM、含自由液面虚升的解析纵横倾反求、初值不受参考字段控制、缓存结果隔离、负GM标记、超排水范围拒绝，以及39工况输出完整性。

程序将数值收敛、未触顶、GM为正分别标记，只有三项满足才标为`usable_small_angle_equilibrium`。横倾搜索限制±5°；大倾角、真实舱内液体重分布、进水、风/缆绳/海底约束、船体挠曲等不在本阶段。当前39个工况均满足这些程序内适用条件，不等于完成了后续大倾角稳性或全部判据校核。
