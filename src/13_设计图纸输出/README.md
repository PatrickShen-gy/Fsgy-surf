# 13 设计图纸输出

由 `main.py` 统一调度，单独选择 `drawings` 时依赖01、02。四类图纸各有独立模块，共用船型、截线工具、DXF/SVG画布和母型模板。

| 子工序 | 文件 | 工作与主要输出 |
|---|---|---|
| 13.1 | `lines_plan.py` | 型线三视图、型值表；`lines_plan.dxf/svg`、`offsets.csv`、`stations.csv`、`section_points.csv` |
| 13.2 | `typical_section.py` | 典型横剖面及局部构件；`typical_section.dxf/svg` |
| 13.3 | `general_arrangement.py` | 总布置和参考设备；`general_arrangement.dxf/svg` |
| 13.4 | `capacity_plan.py` | 舱容图及模型容积/重心；`capacity_plan.dxf/svg`、`capacity_plan.csv` |
| 13.5 | `design_drawings.py` | 调度、主参数核对、图纸元数据与索引；`principal_particulars.json/csv`、`drawings.json`、`index.html` |

`drawing_common.py` 提供网格切片、画布、字体轮廓、DXF/SVG输出和共享模型投影；`template_common.py` 提供模板读取、配色、引线和图框。参考图面数据位于 [templates/mother_35000](templates/mother_35000/README.md)。

## 运行

在项目根目录执行：

```powershell
python main.py --case 34000t --tasks drawings
```

在 `case.json` 中选择图种：

```json
"drawings": {
  "template": "mother_35000",
  "types": ["lines_plan", "typical_section", "general_arrangement", "capacity_plan"],
  "structure_details": true
}
```

`types` 省略时执行全部四类。运行日志显示13.1—13.4中被选中的子工序，最后生成13.5索引。各模块由统一入口调用，不必分别启动脚本。

默认输出到算例的 `results/13`；`drawings.revision` 可选子目录。主程序按第13工序整体缓存，改变图种、模板或绘图代码后更新本次选中的图纸；01、02可继续复用。

详细输入及适用范围见 [用户指南](../../docs/userguide.md) 和 [算例格式](../../docs/case_format.md)。
