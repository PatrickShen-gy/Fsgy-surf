# 12 批量复算与总报告

打开：[全工况HTML总报告](output/report.html)。可选择总览或39个工况，查看浮态、GZ、稳性指标、纵强度及许用载荷，并跳转各阶段原始明细。

```powershell
python run_pipeline.py --test
python run_pipeline.py --report-only
python run_pipeline.py --from-stage 3 --test
```

默认复用01—08、重算09—11并生成12；`--test`运行02—12的57项测试，完整输出保存在`output/logs`。`--report-only`只重新汇总，不更新上游。`--from-stage`与`--to-stage`控制重算区间；当前汇总始终要求完整01—11既有输出。

## 文件

| 文件 | 用途 |
|---|---|
| `report.html` / `report.md` | 离线交互报告 / 文本报告 |
| `case_comparison.csv` / `case_results.json` | 39工况跨阶段数值汇总 |
| `stage_inventory.csv` | 01—12程序入口与状态 |
| `known_issues.json` | 8类几何、假设、源表及读图问题 |
| `freshness_checks.csv` | 各阶段记录的上游文件/PDF指纹与当前值 |
| `reproducibility_manifest.json` | 程序指纹、Python版本、来源与运行记录 |
| `pipeline_status.json` | 链条、测试、新旧来源和完整复现状态 |
| `run_results.json` / `test_results.json` / `logs` | 子进程返回码、运行时间和日志 |

来源指纹检查与程序回归测试用途不同。前者检查读取数据是否已变化；后者检查程序约束和关键数值性质。两者通过也不表示已消除模型假设或完全复现PDF。

总报告明确保留`full_pdf_reproduction_complete=false`：上部储备浮力几何、纵向分布/CORR修正及许用载荷压载耦合等仍影响结果。已有误差和源表差异不通过拟合对照结果来隐藏。

测试：`python test_pipeline_report.py`。
