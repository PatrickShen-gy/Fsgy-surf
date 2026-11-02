# 用户算例格式

## 单位与坐标

几何及配置长度 m，重量 t，密度 t/m³，自由液面矩 t·m。AP 为 x=0，X 向艏，Y 向左舷，Z 从基线向上；纵倾为 TF−TA，左舷下沉横倾为正。图纸适配器负责从原始绘图坐标转入母型坐标。

## 母型与目标尺度

`mother_ship.drawings` 明确指定 `lines`、`general_arrangement`、`typical_section`。当前 `mother_ship_offsets_v1` 适配器支持现有母型型值表和布置布局；改成其他名称的图纸文件仍可读取，任意新布局需要新的适配器。`mother_ship.dimensions` 可补充图中缺失的母型主尺度。

`design.target_dimensions` 必填 `lbp_m`、`beam_m`、`depth_m`、`design_draft_m`。可选 `scantling_draft_m` 和 `loa_m`；指定 LOA 时须与 LBP 保持相同纵向缩放比例。几何在目标尺度下重新生成钢板及隔板材料域，板厚按配置保持，不随船型比例缩放。

当前分舱沿用原模型的 5 货舱、10 边舱、5 双层底空间、机舱及艏艉空间近似；其余小舱、甲板以上储备浮力、内底板和完整骨架尚不包含。钢结构质量仅包含已生成的船壳和主隔板，不能自动作为整船空船重量。高角度结果保留虚拟甲板浸没标记。

`compartments` 可选覆盖目标船上的 `double_bottom_m`、`side_tank_m`、`inner_half_breadth_m`、`upper_half_breadth_m`、`top_slope_start_m`、`top_slope_end_m`；也可指定 `spans`，按 ENGINE_ROOM、HOLD_5 至 HOLD_1 顺序给出连续的 `id/name/start_m/end_m`。未指定时从母型布置提取并同比缩放。

## 计算设置

- `environment.water_density_t_m3` 默认 1.025。
- `environment.hydro_shell_thickness_mm` 默认 0，表示采用型线包络；非零时采用 `rho*(VOLM+t*WSA)` 一阶排水修正，浮心保持型线口径。该参数独立于钢结构板厚。
- `mesh` 配置横剖采样数、纵向细分数、船壳/横隔板/内舷纵隔板厚度和导出格式。
- `bonjean` 配置 `stations_m`、`drafts_m`，或用 `draft_step_m` 自动生成吃水。
- `hydrostatics` 配置 `drafts_m` / `draft_step_m` 和 `trims_m`。
- `capacity` 配置 `fill_fractions`（0—1）和 `level_samples`。
- `stability` 配置 `angles_deg`（从 0 开始，最多 180°）和 `sides`（`port`、`starboard`）。默认计算完整 0—180°曲线；面积使用弧度积分。
- `longitudinal.cells` 默认 450，控制纵向积分初始网格，随后加入船体站位及载荷边界。

## 装载输入

02 在重建时即生成 `results/02/spatial_blocks.json` 和 CSV：每个独立闭合区块记录编号、用途、左右舷、三维边界、容积、容积中心及网格数组位置。05 复用这些满舱属性，仅继续求部分装载液位和自由液面；06 引用区块，不要求用户重复填写舱容和重心。

压载状态可直接在工况中填写：

```json
"ballast": {
  "WBT_3_P": 1,
  "WBT_3_S": 1,
  "WBT_2_P": 0,
  "WBT_2_S": 0
}
```

0 为空、1 为满；也支持 0—1 之间的装载率。密度自动采用 `environment.water_density_t_m3`。可在 `loading.ballast` 定义所有工况的公共状态，并在各工况的 `ballast` 中覆盖。未指定的压载区块不添加水重。相同区块不得同时通过 `items` 和 `ballast` 重复输入。

现有边舱默认用途为 `ballast_water`，货舱为 `cargo`。双层底和艏艉包络默认用途 `unassigned`，因为其完整水密细分尚未建出；它们仍具有独立空间编号和几何属性。可通过 `compartments.purposes` 将已经定义好的区块指定为 `ballast_water`、`fresh_water`、`fuel_oil`、`lubricating_oil`、`cargo`、`machinery`、`void` 或 `unassigned`。用途指定不会创建新的舱壁，也不会自动合并边舱和双层底。

`loading` 可为内联对象，也可为独立 JSON 路径。包括公共 `lightweight` 或 `lightweight_components`，以及 `cases`。各工况也可单独覆盖空船输入。

每个工况包含唯一 `case_id`、`description`、`items`。载荷可采用：

1. 指定重量重心：`id`、`mass_t`、`LCG_m`、`KG_m`，可选 `TCG_m` 和 `FSM_tm`（默认 0）。
2. 几何装载：`id`、`geometry_id`、`fill_fraction`、`density_t_m3`；体积、重心和自由液面由对应舱室求得。液体 `free_surface=true`；散货设为 `false`，填充几何仍采用水平面近似。

纵强度使用可选 `span_m: [aft_x, fore_x]` 构造非负且守恒质量/纵向质量矩的指数分布；无区间时按相邻网格上的点载荷分配。重量分布和局部 CORR 方法沿用旧模型范围。

货舱内容物用 `hold_id: "HOLD_3"` 或对应货舱 `geometry_id` 关联许用载荷任务；边压载舱质量独立处理。

## 开口与风压输入

启用 `criteria` 时提供 `criteria` 对象。示例结构：

```json
{
  "openings": [{"id": "VENT_P", "x_m": 20.0, "y_m": 8.0, "z_m": 19.0}],
  "weather": {
    "pressure_N_m2": 504.0,
    "bilge_keel_area_m2": 18.0,
    "lwl_m": 175.0,
    "wind_profile": [
      {"T_m": 2.0, "area_m2": 1600.0, "CGZ_m": 19.0},
      {"T_m": 12.0, "area_m2": 900.0, "CGZ_m": 21.0}
    ]
  }
}
```

这些数字仅演示字段。`wind_profile` 可直接给出 `moment_m3`；省略时按 `area_m2*(CGZ_m−T_m/2)` 估计。未提供 `weather` 时只计算开口和基本 KG 限值。`thresholds` 可设置 AREA30、AREA40、AREA3040、GZ、GM、MAXGZ；默认沿用旧计算公式的数值要求，不做 PDF 对照。KG 限值采用冻结浮力曲线近似，峰值角限制在 `kg_scan_m` 范围内扫描，默认 [-5,35,801]。

## 许用货舱载荷输入

启用 `hold_loads` 时提供 `hold_loads.envelopes`，每条曲线包含：

```json
{
  "id": "H3_SEA",
  "mode": "seagoing",
  "holds": ["HOLD_3"],
  "draft_m": [0.0, 10.0],
  "minimum_t": [0.0, 1000.0],
  "maximum_t": [6000.0, 12000.0]
}
```

数字仅演示格式，实际许用数据由设计者提供。数组采用分段线性插值。输出上下限和剩余裕量；超出曲线吃水范围时返回空值，不外推。双舱输入两个相邻货舱 ID，默认在合并舱段中点求局部吃水，可用 `evaluation_x_m` 指定。程序不从船体几何推导结构许用曲线，也不自动补充双层底压载耦合。

## 中间数据与结果

`cache/01` 保存 DXF 解析，`cache/02` 保存几何网格，其他阶段缓存表格数组与阶段数据。缓存签名包括相关配置、来源内容及程序内容，产物缺失或变化会重算。修改载荷复用几何；修改主尺度更新几何及其下游。`results/01` 至 `results/12` 输出 JSON、CSV、三维模型和离线结果页面。`results/run.json` 记录实际执行的任务与缓存使用情况。

无需 PDF、PyMuPDF 或旧项目计算输出。程序仅保留避免无效输入、非法几何和数值不收敛所必需的错误处理。
