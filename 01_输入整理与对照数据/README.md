# 01 输入整理与对照数据

`read_dxf.py`只读母型源图，输出到同目录`output`。不使用34000t改型参数，不生成或拉伸船体，不修改原DXF。

从本目录运行：

```powershell
python read_dxf.py
python read_dxf.py --force
python read_dxf.py "../35000DWT散货船型线图_2010VER.dxf" --output output_lines_only
```

首次解析DXF；随后依据源文件SHA256、读取器代码指纹和ezdxf版本复用缓存。更换输入集合时建议使用独立output目录。源文件名和绝对路径均保存在manifest中。

## 输出内容

| 文件 | 用途 |
|---|---|
| `output/manifest.json` | 输入文件清单、路径、指纹、缓存位置、版本、数量统计及PDF待解析状态 |
| `output/principal_dimensions.json` | 跨图纸主尺度对照；每个数值附来源图及文字实体handle |
| `output/hull_offsets.json` | 型值表站位、毫米原文、米制列表、各单元格来源；斜杠多交点不丢失 |
| `output/hull_offsets.csv` | 便于人工核对的米制型值表；空白表示缺失，斜杠表示多值 |
| `output/quality_report.json` | 冲突、警告、已完成检查及待办 |
| `output/summary.md` | 人工阅读的提取汇总 |
| `output/drawings/图纸名/entities.json.gz` | 模型空间、块定义、插入变换、原始DXF组码、图层、头变量、审计结果 |
| `output/drawings/图纸名/texts.csv` | 模型空间及块内文字、局部坐标、来源handle |
| `output/drawings/图纸名/dimensions.csv` | 标注几何测量值、显示覆盖文字、定义点及坐标作用域 |

曲线保留圆弧参数、polyline的bulge、样条控制/拟合点、节点及权重。HATCH、REGION、OLE等保留原始组码；保留不代表已经解码为工程几何或舱容表。
块定义只存一次，INSERT保留完整变换矩阵、阵列参数及属性，不把块内坐标误当成船体坐标。Matrix44使用ezdxf的行向量约定；应用到列向量实现时应相应转置。块内图层0、ByBlock、OCS等仍需按DXF规则解释。

## 后续项目如何读取

在02或后续项目的脚本中：

```python
from pathlib import Path
import sys

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project / '01_输入整理与对照数据'))
from read_dxf import load_inputs

inputs = load_inputs()  # 不读取或解析DXF
dimensions = inputs['principal_dimensions']['dimensions']
stations = inputs['hull_offsets']['stations']
quality = inputs['quality']  # 使用前须检查errors及相关warnings

# 需要完整实体、曲线及块定义时按需加载：
# inputs = load_inputs(include_drawings=True)
# 主动核对源文件是否更新（只计算文件指纹，不解析DXF）：
# inputs = load_inputs(verify_sources=True)
```

## 此阶段还有哪些内容

1. 本程序先完成DXF读取及初步跨图校对。PDF的基准条件、目标结果表、重量数据和39个工况还需单独结构化。
2. INSUNITS未指定时，不能仅凭数值大小宣布几何单位正确；型值表毫米列转米与整个图形的单位核定是两件事。
3. 图纸二维视图可能使用不同平移基准；需要分别建立AP、BL、CL及肋位坐标变换。
4. DIMENSION的几何测量值与显示文字可能不同；角度标注的测量不是长度，不能统一除1000。
5. 型值表适配器只针对当前母型布局；识别失败会报错，不退回猜测型值。
6. 图纸数据缺失、审计修复建议、嵌入表格等须人工核对。当前尚未完成02所需的三维闭合、自交和收敛验证。
7. 后续先按PDF条件复算母型；18mm外板/20mm龙骨、密度等应从PDF基准提取，不能沿用12mm裸壳计算条件。
