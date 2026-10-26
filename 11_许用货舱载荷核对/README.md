# 11 单舱与相邻双舱载荷包络

运行：`python hold_mass_check.py`。查看：[HTML核对](output/hold_mass_verification.html)与[原图读图审计](output/reference/digitization_audit.html)。输入为06载荷与07平衡浮态。

从手册334—340页图片转录5条单舱和4条相邻双舱的标注端点，组成分段线性外包络。原图无损提取、来源指纹和转录参数保存在`output/reference`；这里没有从船体结构独立推导许用载荷。

39工况×9个舱组×海上/港内2模式，共702项。单舱采用舱段中点局部吃水，双舱采用合并纵向范围中点，这一位置约定已记录。货舱内所有CH项都计入，包括货舱装水；相关WBT压载单独列出，当前未将其直接加到货舱内容物重量上。灰色曲线族、双层底压载耦合仍待重建。

## 当前分类

| 状态 | 数量 | 含义 |
|---|---:|---|
| `within_digitized_envelope` | 595 | 位于转录外包络内 |
| `boundary_digitization_band` | 3 | 越界幅度在5t读图不确定度带内，保留待核对 |
| `outside_digitized_envelope` | 12 | 超出当前采用的货舱内容物外包络 |
| `outside_chart_domain` | 92 | 局部吃水超出原图0—10.27m范围，不外推 |

5t只标识读图/打印误差，不是可超载额度。12项图外也不能直接宣布为实船违规或超载：双层底压载依赖尚未独立实现，程序已标记相关压载待复核。海上和港内同时列出，不凭工况名称擅自确定运行模式。

`output/hold_mass_results.json`保存全工况结果，`hold_mass_checks.csv`提供702项明细，`verification_report.json`提供分类统计和限制。

测试：`python test_hold_mass.py`，检查转录端点/平台、边界及图域分类、工况覆盖和原图来源。

