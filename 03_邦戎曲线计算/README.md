# 03 邦戎曲线计算

运行：

```powershell
python bonjean_calculation.py
python bonjean_calculation.py --preview
python test_bonjean.py
```

依赖安装：`python -m pip install -r requirements.txt`。默认通过项目根目录`.cad_deps`补充PDF读取库。

输入是02的`calculation_mesh.npz`中的`outer_vertices`和`hydro_faces`。所有截面均由三角网格求交，未直接使用型值表或站位环作为计算结果。钢板、隔板的材料域不用于排水横剖面积。

PDF第32—38页为验证基准：26站、1—13m每0.5m一档，共650组。原表数值、原行文字和页码保存在`output/reference`；不会自动修改非单调值。

输出到`output`：

- `bonjean_results.csv`、`bonjean_data.npz`：全部31个原始站位、25档标准吃水加9.83/10.27m，共837行。
- `verification.csv`：650组面积及对基线面积矩对照；相对误差为百分数，参考值为0时为空。
- `station_summary.csv`：逐站误差与满足阈值的组数，便于定位需回查的剖面。
- `verification_report.json`：误差统计、阈值、参考表异常与独立数值验证。
- `bonjean_verification.html`：离线站位曲线、切片和逐行对照，可直接浏览器打开。
- `bonjean_comparison.png`：典型站位与误差热图。
- `section_contours.json`：截面闭合轮廓，Y/Z单位米。
- `diagnostic_18mm.csv`：18mm二维外扩后的诊断对照，不是已确认的原手册算法。

面积单位m²，MOM BL是对基线的一次面积矩，单位m³，不是力矩或质量矩。截面质心高度等于MOM BL/A。

独立数值验证采用全船切片面积/面积矩积分，与有向四面体体积/体积矩对照；解析箱体、三角剖面和孔洞算例在测试文件中。

默认误差阈值为面积0.05m²、面积矩0.05m³，再各加参考值的1%。这是程序诊断阈值，可用参数调整，不代表船级社标准。
PDF第39页静水力条件给出18mm外板、20mm龙骨，但第31页邦戎说明没有明确相同修正口径，因此主结果保留02原始包络，另列18mm诊断，不把它当成已证实的修正。
本阶段不自动修改02模型。计算完成与全部参考表校核通过是两件事，实际状态以`verification_report.json`为准。
