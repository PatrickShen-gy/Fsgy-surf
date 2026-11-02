# Fsgy-surf 用户操作指南

本指南按本次 `example` 算例的实际操作顺序编写：放入母型 DXF，填写设计与装载配置，运行一次 `main.py`，查看计算结果和自动生成的 HTML 报告。

以下路径均以 `Fsgy-surf` 为项目根目录。算例输入统一使用 UTF-8 编码的 JSON，正常计算不需要 PDF。

## 1. 准备运行环境

首次使用时，在 `Fsgy-surf` 目录打开 PowerShell，安装依赖：

```powershell
python -m pip install -r requirements.txt
```

已经使用同一个 Python 环境成功运行过程序，则直接进行下一步。

## 2. 准备算例文件夹

本次使用的目录如下：

```text
Fsgy-surf/
├── main.py
├── src/
├── docs/
└── example/
    ├── 35000DWT散货船型线图_2010VER.dxf
    ├── 35000DWT散货船总布置图_2010VER.dxf
    ├── 35000DWT散货船_典型横剖面图.dxf
    ├── case.json
    └── loading.example.json
```

三份图纸分别提供型线、主分舱布置和典型横剖面信息。`case.json` 是总配置，`loading.example.json` 是装载配置。

本次实际装载文件名是 `loading.example.json`。如果自行改名为 `loadexample.json`，也需要同步修改 `case.json` 中的 `loading` 字段。

`example` 是本次操作的算例；`cases/example_ship` 是另一套示例目录。运行命令中的配置路径决定本次处理哪一套文件。

## 3. 在 case.json 中指定图纸

修改现有配置中的对应字段，保留其余配置。当前三份 DXF 与 `case.json` 位于同一目录，因此使用实际文件名：

```json
{
  "mother_ship": {
    "adapter": "mother_ship_offsets_v1",
    "drawings": {
      "lines": "35000DWT散货船型线图_2010VER.dxf",
      "general_arrangement": "35000DWT散货船总布置图_2010VER.dxf",
      "typical_section": "35000DWT散货船_典型横剖面图.dxf"
    }
  }
}
```

配置内的相对文件路径均相对于 `case.json` 所在目录。如果把图纸放进 `example/drawings`，对应路径应改为 `drawings/实际文件名.dxf`。JSON 路径建议使用 `/`。

当前 `mother_ship_offsets_v1` 适配器对应现有母型的图纸布局。更换为完全不同布局的 DXF 时，需要扩展图纸适配器。

## 4. 填写目标主尺度

编辑 `design.target_dimensions`：

```json
{
  "design": {
    "method": "affine",
    "target_dimensions": {
      "lbp_m": 172.0,
      "beam_m": 28.8,
      "depth_m": 14.6,
      "design_draft_m": 9.83,
      "scantling_draft_m": 10.27
    }
  }
}
```

| 字段 | 含义 | 单位 |
|---|---|---|
| `lbp_m` | 目标垂线间长 | m |
| `beam_m` | 目标型宽 | m |
| `depth_m` | 目标型深 | m |
| `design_draft_m` | 设计吃水，也是静水力报告中的设计参考点 | m |
| `scantling_draft_m` | 结构吃水，可选 | m |

保持以上数值，得到的是当前母型尺度。改变长、宽、深后，程序按三个方向的比例生成目标船型，钢板厚度按 `mesh` 中的设置保持。总长 LOA 默认随垂线间长同比变化。

`design_draft_m` 是指定的计算参考吃水；07 的实际平衡吃水由装载重量和重心求解，两者可以不同。

## 5. 确认水密度和板厚设置

当前示例采用：

```json
{
  "environment": {
    "water_density_t_m3": 1.025,
    "hydro_shell_thickness_mm": 0.0
  }
}
```

`water_density_t_m3` 是计算水密度，也用于简化压载输入的水重计算。

`hydro_shell_thickness_mm` 是排水量的一阶外板修正厚度。当前取 0，表示按型线包络计算；旧 `test_35000SDWT` 采用 18mm。需要与旧版对齐排水量口径时，设置为 `18.0`。

`mesh.shell_thickness_mm` 是钢结构模型的船壳板厚，当前为 12mm。这两个参数分别用于排水修正和钢材模型，不要混同。

## 6. 指定计算任务并接入装载文件

本次 `example/case.json` 已采用：

```json
{
  "loading": "loading.example.json",
  "tasks": [
    "geometry",
    "bonjean",
    "hydrostatics",
    "compartments",
    "weights",
    "equilibrium",
    "stability",
    "longitudinal",
    "report"
  ]
}
```

这会运行 01—08、10 和 12。程序自动安排任务依赖，不需要依次启动各阶段脚本。09 的开口/风压和 11 的许用货舱载荷尚需相应输入，本配置未启用。

如果只计算 01—05，可将 `tasks` 设置为：

```json
{
  "tasks": ["geometry", "bonjean", "hydrostatics", "compartments", "report"]
}
```

这时会执行 01—05 和 12，不要求装载数据。

## 7. 编辑装载工况

打开 `example/loading.example.json`，检查空船输入、货物输入和压载状态。

### 空船重量与重心

`lightweight` 中填写空船重量和重心。当前演示值为 8650t、LCG=79.3m、TCG=0、KG=10.2m。用于新设计时，应改成相应设计输入。

02 输出的钢结构质量只包含已建立的船壳和主隔板，不能自动替代整船空船重量。

### 货物等逐项载荷

工况的 `items` 中记录货物和其他载荷。当前示例在 3 号货舱加入 5000t 货物：

| 字段 | 含义 |
|---|---|
| `id` | 载荷编号，同一工况内唯一 |
| `hold_id` | 所属货舱，如 `HOLD_3` |
| `mass_t` | 重量，t |
| `LCG_m` / `TCG_m` / `KG_m` | 纵向、横向和垂向重心，m |
| `FSM_tm` | 采用的自由液面矩，t·m；当前散货示例为 0 |
| `span_m` | 纵向分布范围，供纵强度计算使用 |

坐标原点为 AP／CL／BL，X 向艏、Y 向左舷、Z 向上。用户指定的重量、重心和分布区间均采用目标船坐标，修改主尺度后不会自动缩放这些输入。

### 压载水舱空／满状态

在每个工况的 `ballast` 中指定区块状态：

```json
{
  "ballast": {
    "WBT_3_P": 1,
    "WBT_3_S": 1,
    "WBT_2_P": 0,
    "WBT_2_S": 0
  }
}
```

`1` 表示满舱，`0` 表示空舱；也支持 0—1 之间的部分装载率。`P` 为左舷，`S` 为右舷。未指定的压载区块不添加水重。

程序从 02 的空间区块和 05 的舱容数据读取几何属性，自动计算压载水重量、重心及自由液面，不需要用户重复输入这些舱的容积和重心。同一区块应通过 `ballast` 或 `items` 中的一种方式输入，避免重复。

可用的空间编号与用途保存在首次计算生成的 `results/02/spatial_blocks.json`。当前边舱默认用于压载，双层底和艏艉包络默认用途未指定；用途设置和现有几何范围详见 [配置字段说明](case_format.md)。

如需多个装载工况，在 `cases` 数组中增加工况对象，并为每个工况设置不同的 `case_id`。程序会批量计算。

## 8. 运行一次 main.py

如果终端当前位于 `Fsgy-surf`：

```powershell
python main.py example/case.json
```

如果终端当前位于 `Fsgy-surf/example`，即本次操作使用的目录：

```powershell
python ..\main.py .\case.json
```

运行时，终端会依次显示各任务及其状态：`RUN` 表示正在计算，`CACHE` 表示复用已有结果。首次运行会创建 `cache` 和 `results` 文件夹。

按当前配置完成后，终端会输出结果目录和 `01-05 report` 路径。**01—05 的 HTML 报告已接入 main.py，无需单独运行报告脚本。**

## 9. 查看结果

```text
example/
├── cache/                       # 自动保存的中间数据
└── results/
    ├── report_01_05.html         # 01—05 专用报告，main.py 自动生成
    ├── run.json                 # 本次运行的阶段记录与结果摘要
    ├── 01/                      # 母型输入与主尺度
    ├── 02/                      # 三维模型、空间目录和钢结构材料
    ├── 03/                      # 邦戎结果
    ├── 04/                      # 静水力结果
    ├── 05/                      # 舱容、重心和自由液面结果
    ├── 06/                      # 装载重量与重心
    ├── 07/                      # 平衡吃水、纵倾、横倾及 GM
    ├── 08/                      # GZ 曲线
    ├── 10/                      # 重量/浮力分布、剪力和弯矩
    └── 12/report.html           # 本次已选任务的综合报告
```

首先用浏览器打开 `results/report_01_05.html`，依次查看主尺度、三维分舱、邦戎曲线、静水力和舱容结果。页面可以切换站位、纵倾和空间区块。

查看装载、浮态、GZ 和纵强度时，打开 `results/12/report.html`。需要完整数值时，读取各阶段的 CSV 或 JSON。

三维模型也可通过 `results/02/ship.html` 独立打开。分享或移动报告时，保留 `results` 的目录结构，以便三维模型和数据链接继续使用。`run.json` 和各结果文件由程序生成，不作为用户配置编辑。

只有本次任务包含完整 01—05 时，main.py 才自动生成或更新 `report_01_05.html`；上述两种示例任务列表均满足此条件。

## 10. 修改方案后再次运行

修改主尺度、装载或其他配置后，保存 JSON，再执行同一条 `main.py` 命令。程序会按依赖更新受影响的结果，并复用其他缓存。例如只修改载荷时，可以复用已有几何和舱容数据。

需要强制重新计算当前所选任务及其依赖时，在 `example` 目录执行：

```powershell
python ..\main.py .\case.json --force
```

正常调整方案无需手动删除缓存，也无需手动运行报告程序。

## 11. 本次示例结果的理解

目标主尺度不变，说明计算仍基于原母型尺度；装载工况由 `loading.example.json` 决定。当前工况编号为 `USER_DEMO`，包含演示货物及选定压载舱，不会自动对应旧项目的某个 35000DWT 历史工况。

“设计吃水 9.83m”与“该演示装载的平衡吃水”分别对应设计参考点和实际载荷求解结果。比较不同运行结果时，应同时确认主尺度、装载和排水修正设置。

## 12. 常见情况

| 情况 | 处理方式 |
|---|---|
| 提示找不到 DXF | 核对文件名及其相对于 `case.json` 的路径 |
| 提示找不到装载文件 | 核对 `loading` 字段是否与 `loading.example.json` 的实际名称一致 |
| JSON 读取失败 | 使用双引号；不要加入注释、中文标点或末项之后的多余逗号 |
| 压载编号无法识别 | 从 `results/02/spatial_blocks.json` 中选取用途为 `ballast_water` 的编号 |
| 日志多为 `CACHE` | 表示复用有效结果，程序仍会自动更新 01—05 报告 |
| 未生成 01—05 报告 | 确认计算已成功结束，且本次任务包含完整 01—05 |

更多字段、任务参数及当前模型范围见 [case_format.md](case_format.md)。
