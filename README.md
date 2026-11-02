# Fsgy-surf

用母型 DXF 和用户指定的目标主尺度生成设计船型，计算邦戎、静水力、舱容及装载性能。正常运行不读取 PDF，不输出参考值对照或误差报告。

首次使用请按 [用户操作指南](docs/userguide.md) 准备算例、填写配置并运行程序。

```powershell
python -m pip install -r requirements.txt
python main.py cases/example_ship/case.json
```

打开 `cases/example_ship/results/12/report.html` 查看结果。第一次运行读取图纸并计算；再次运行复用 `cache/`。强制重算使用 `--force`。所有相对路径均相对于 `case.json`，可以从任意工作目录运行入口脚本。

## 01—05 HTML 报告

当本次任务包含 01—05 时，`main.py` 会在计算结束后自动汇总输入、三维分舱、邦戎、静水力和舱容结果：

```powershell
python main.py example/case.json
```

如果终端位于 `example` 目录：`python ../main.py case.json`。报告输出到 `example/results/report_01_05.html`，无需额外运行报告脚本。计算命中缓存时也会自动更新报告。报告读取已有结果，曲线和数据表嵌入 HTML；三维模型引用同一结果目录中的 `02/ship.html`。仅运行部分阶段且未包含完整 01—05 时，不生成这份报告。

## 算例与任务

示例包含现有母型的三份 DXF，默认执行 01—05 和 12。修改 `design.target_dimensions` 即可调整设计船型；三向仿射变换分别使用 LBP、型宽、型深的比值。LOA 随船长同比变化，设计吃水单独指定。母型图纸和源文件不会被改写。

`tasks` 可选：`input`、`geometry`、`bonjean`、`hydrostatics`、`compartments`、`weights`、`equilibrium`、`stability`、`criteria`、`longitudinal`、`hold_loads`、`report`。程序自动补齐计算依赖，无需分别运行阶段脚本。

在配置中加入 `"loading": "loading.example.json"`，并将 `weights`、`equilibrium`、`stability` 或 `longitudinal` 加入 `tasks`，即可继续计算装载性能。示例重量和重心仅演示输入格式，需要按设计船修改。主尺度变化不会自动缩放用户给定的重量、重心和载荷区间；这些输入统一采用目标船坐标。

也可临时覆盖任务：

```powershell
python main.py cases/my_ship/case.json --tasks equilibrium longitudinal report
```

## 程序目录

`src/01_*` 至 `src/12_*` 对应 12 项任务，各提供 `run(context)` 接口。`src/fsgy_surf` 提供配置、数据读写、流程调度和结果展示公共代码。网格、舱室和装载结果在同次运行中共享，并按算例持久化；输入变更只更新受影响的阶段。

详细输入与模型范围见 [docs/case_format.md](docs/case_format.md)。
