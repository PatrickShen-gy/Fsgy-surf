# 06 重量与重心系统

已按用户接受的05分舱近似继续工作。本阶段采用装载手册逐项工况输入，完成39个工况、2064条载荷的独立重量矩合成与自由液面虚升计算，供07浮态求解读取。没有修改02几何。

```powershell
python weight_cg_calculation.py
python weight_cg_calculation.py --preview
python test_weight_cg.py
# 自定义点载荷与05几何载荷演示：
python weight_cg_calculation.py --case-file example_custom_case.json
```

输入按脚本位置定位，启动工作目录不受限制。保留02—05相对目录结构；依赖见`requirements.txt`，PDF库也从项目根目录`.cad_deps`查找。

## 输入和验证边界

- 05的`loading_reference_catalog.json`提供统一舱名与数据来源；06不直接将近似边舱替代完整实际液舱。
- 默认模式从PDF各工况的两页明细提取重量、装载率、该工况的LCG/TCG/KG、FRSM以及密度。采用的是部分装载时的重心，不用满舱重心冒充。
- 空船采用各详细工况给出的8650t、LCG=79.30m、TCG=0m、KG=10.20m。
- 分类、Deadweight、Total weight汇总，以及第89页总览，只作验证，不参与计算输入。
- 因而本阶段独立验证重量合成和修正算法，**没有从近似舱室几何独立重建全部小舱的载荷重心**。
- 原始数值、字符、物理页码及PDF指纹均保留。按页面坐标重组同行，并处理负横向坐标粘连、跨页及钢卷归类等情况。

## 计算方法

坐标AP/CL/BL，x向艏，Y正为左舷，z向上。单位为t、m和t·m。

```text
W = sum(w_i)
LCG = sum(w_i*x_i)/W
TCG = sum(w_i*y_i)/W
KG  = sum(w_i*z_i)/W
FSM = sum(FRSM_i)
KG_correction = FSM/W
KG_effective = KG + KG_correction
```

分别汇总载重量、空船加全部载荷的总重量及各类别。使用稳定浮点求和；分类合计、明细页SUBTOTAL/TOTAL不重复计重。

KG_correction取正值，表示自由液面导致的等效重心虚升。实际GM要在07求得浮态和KM后再计算；06不使用PDF的KM来冒充独立稳性结果。第89页GMCORR为正的减稳性量，详细浮态栏则以负值打印，已分别保存。

### 手册特殊口径

- 部分100%装满舱、甚至零重量舱仍被指定非零FRSM。默认复算保留这个所采用的工况/保守修正输入，不把它解释为空舱或压满舱的真实液面。
- 05几何模式对空舱/封闭压满舱采用实际FSM=0。自定义几何载荷沿用该口径，与PDF复算分开。
- GRM是粮食横倾矩，独立存储，不并入液体FSM。
- 钢卷的FILL=0不等于零重量。钢卷明细单列，手册汇总将其与人员物资一并计入PURP=MASS，校核按同一组合进行。
- 02局部船壳/隔板模型质量约1274.36t，仅约为采用空船重量的14.73%；尚缺完整甲板、骨架、机械、上建等。它只作诊断，不替代8650t空船，也不再叠加到空船中。

## 当前校核结果

分类、载重量、全船合计共1945项重量/重心/FSM对照均在打印舍入传播范围内一致。39个详细工况的自由液面虚升与详细浮态栏也一致。

| 全船总计指标 | 最大绝对差异 |
|---|---:|
| 总重量 | 0.400t |
| LCG | 0.006064m |
| TCG | 0.004627m |
| KG | 0.006107m |
| FSM | 0.010t·m |

例如LD01A：逐项合成总重量23697.1t，PDF详细汇总23697.4t；LCG=88.075742m，KG=7.714714m，FSM=1529.75t·m，KG虚升0.064554m，等效KG=7.779268m。

重量通常印到0.1t，位置通常印到0.01m，FSM按实际打印小数位识别。校核传播每项的半个末位误差，得到保守的质量矩和重心区间，再计入参考打印精度。这是舍入一致性检查，不代表船级社验收阈值，不通过调整输入强制匹配。

### LD09C源表不一致

第89页总览和第274页详细汇总存在以下差异，均已保留：

| 指标 | 第89页总览 | 第274页详细表 |
|---|---:|---:|
| 总重量 / t | 43537.1 | 43347.1 |
| 载重量 / t | 34887.1 | 34697.1 |
| 型吃水 / m | 10.268 | 10.224 |
| 纵倾 / m | −0.496 | −0.763 |
| GM / m | 6.929 | 6.94 |
| GMCORR减稳性量 / m | 0.273 | 0.26（原栏打印−0.26） |

逐项载荷采用第275—276页，合成重量43347.1t，与详细表43347.1t一致；KG虚升约0.262804m，与详细栏0.26一致，与总览0.273不一致。39个工况中只有这个工况的总览/详细表发生上述超出舍入范围的差异。

后续07采用详细载荷和详细浮态作对照，**不把总览43537.1t替换进当前载荷合成结果**。差异本身不是通过修改算法消除的目标。

## 输出

全部位于`output`，离线打开`weight_cg_verification.html`查看工况切换、重量组成、载荷位置图、逐项明细及对照结果。

| 文件 | 用途 |
|---|---|
| `loading_cases.json` | 39个工况输入、计算结果、独立参考结果及来源 |
| `weight_items.csv` | 2064条载荷及纵横垂向重量矩，不含重复分类总计 |
| `category_summary.csv` | 逐工况分类合计；钢卷显示单列 |
| `case_summary.csv` | 全船重量、重心、重量矩、自由液面修正及载重量 |
| `weight_cg_data.npz` | 以`case_ids`为轴的39个工况数组，字段含总质量、LCG/TCG/KG、FSM、KG虚升与等效KG |
| `equilibrium_inputs.json` | 07浮态求解接口；注明详细表来源，参考浮态独立保存 |
| `verification.csv` | 1945项分类/载重量/全船逐项差异及舍入界限 |
| `free_surface_verification.csv` | 与总览/详细栏的自由液面虚升对照 |
| `reference_overview_consistency.csv` | PDF总览与详细汇总的跨表一致性检查 |
| `verification_report.json` | 统计、源表差异、特殊FSM工况及计算限制 |
| `steel_weight_diagnostic.json` | 02部分钢材质量与采用空船重量的覆盖诊断 |
| `reference/pdf_loading_cases.json` | PDF原始逐项输入、汇总、原行、物理页码与指纹 |
| `weight_cg_comparison.png` | 全工况对比总览 |

## 后续接口与自定义载荷

```python
from weight_cg_calculation import load_results, calculate_case
data = load_results()  # 只读取06结果，不重新解析PDF
data = load_results(verify_sources=True)  # 另核对05/02/PDF指纹
for case in data['cases']:
    print(case['case_id'], case['calculated']['total'])
```

`calculate_case(items, lightweight)`支持独立调用，项目用`mass_t`、`LCG_m`、`TCG_m`、`KG_m`、`FSM_tm`等字段。不需要PDF汇总作为输入。提供负重量、负FSM或非有限坐标会报错。

`--case-file`支持指定重量及坐标的点载荷，也支持`geometry_id`、`fill_fraction`、`density_t_m3`给定的05几何载荷。后者调用05的精确体积反求，而不是插值满舱重心；几何范围仍继承05的近似，不自动视为完整真实液舱。示例输出到`output/custom_cases/CUSTOM_DEMO/result.json`，不覆盖手册复算结果。

8项测试覆盖解析重量矩、载荷移位、GRM/FSM区分、手册零重量保留FSM、输入有效性、05几何接口、源表不一致保留与结果完整性。已通过。
