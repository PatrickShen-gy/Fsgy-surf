# Fsgy-surf

用母型 DXF 和目标主尺度生成设计船型，计算邦戎、静水力、舱容及装载性能，并输出型线图、典型横剖面图、总布置图和舱容图。

## 浏览器界面

首次使用时，在项目根目录安装 Python 依赖，再启动本地工作台。以后只需运行第二条命令。

```powershell
python -m pip install -r requirements.txt
python src/js/serve.py
```

服务启动后，系统默认浏览器会自动打开 [本地工作台](http://127.0.0.1:8765/)。需要只启动服务时，运行 `python src/js/serve.py --no-browser`。按 `Ctrl+C` 停止服务。

工作台默认载入 `34000t` 示例。试算前，请在「算例与母型」填写本机有权使用的型线图、总布置图和典型横剖面图路径。也可以将这三份 DXF 按 `lines.dxf`、`arrangement.dxf`、`section.dxf` 放入本地 `cases/example_ship/drawings/`，直接使用算例中预设的路径。[算例配置](cases/34000t/case.json) · [装载工况](cases/34000t/loading.json)

也可选择其他模板、从空白目标尺度开始或导入已有的 `case.json`。填写主尺度和设计参数、勾选计算任务后，点击「开始计算」。计算完成后，可从同页打开报告、三维船体或图纸，也可单独导出当前表单的 `case.json`。

提交后，工作台地址会带上本次运行编号。刷新同一页面会重新显示当前工序、日志或已完成的结果，同一标签页也会恢复表单。相同输入正在计算时，重复提交会接回同一次运行。输入文件、参数、计算代码和已保存的产物都匹配时，工作台会复用已完成的结果。

母型 DXF 和独立装载 JSON 的相对路径按「算例与母型」中的路径基准解析，也可输入本机绝对路径。装载 JSON 还可通过「载入装载 JSON」直接读入表单。浏览器运行的配置、日志、缓存和结果保存在 `cases/web-runs/<运行编号>/`，每次使用独立目录。

## 命令行

可按 [用户操作指南](docs/userguide.md) 准备算例并运行命令行程序。

```powershell
python -m pip install -r requirements.txt
python main.py --list-cases
python main.py --case example
python main.py --case 34000t --tasks drawings
```

打开 `cases/example/results/12/report.html` 查看综合报告，打开 `cases/34000t/results/13/index.html` 查看图纸和下载文件。程序复用有效的 `cache/`，使用 `--force` 可强制重算。配置内的相对路径均以该 JSON 所在目录为基准。

## 01—05 HTML 报告

当本次任务包含 01—05 时，`main.py` 会在计算结束后自动汇总输入、三维分舱、邦戎、静水力和舱容结果：

```powershell
python main.py example/case.json
```

如果终端位于 `example` 目录：`python ../main.py case.json`。报告输出到 `example/results/report_01_05.html`，无需额外运行报告脚本。计算命中缓存时也会自动更新报告。报告读取已有结果，曲线和数据表嵌入 HTML；三维模型引用同一结果目录中的 `02/ship.html`。仅运行部分阶段且未包含完整 01—05 时，不生成这份报告。

## 算例与任务

- `example` 使用母型默认尺度，默认执行 01—05、13 和 12。直接运行 `python main.py` 会选择这个算例。
- `34000t` 包含新船的十项主参数和装载输入，默认执行 01—08、10、13 和 12。
- `example_ship` 是原有基础算例。目录入口 `python main.py cases/example_ship` 和 JSON 路径入口 `python main.py example/case.json` 均可使用。

`design.target_dimensions` 支持独立指定总长 LOA 和垂线间长 LBP，也可设置型宽 B、型深 D、设计吃水 T 和结构吃水 Ts。`design.target_parameters` 可记录 Ts 排水量、空船重量、载重量和方形系数。空船重量参与装载计算，输入和复算值分别写入 04、13 的主参数表。

`tasks` 支持 `input`、`geometry`、`bonjean`、`hydrostatics`、`compartments`、`weights`、`equilibrium`、`stability`、`criteria`、`longitudinal`、`hold_loads`、`report` 和 `drawings`。程序自动补齐计算依赖。

在配置中加入 `"loading": "loading.example.json"`，并将 `weights`、`equilibrium`、`stability` 或 `longitudinal` 加入 `tasks`，即可继续计算装载性能。示例重量和重心仅演示输入格式，需要按设计船修改。主尺度变化不会自动缩放用户给定的重量、重心和载荷区间；这些输入统一采用目标船坐标。

也可临时覆盖任务：

```powershell
python main.py cases/my_ship/case.json --tasks equilibrium longitudinal report
```

## 程序目录

`src/01_*` 至 `src/13_*` 对应 13 项任务，各提供 `run(context)` 接口。`src/fsgy_surf` 提供配置、数据读写、流程调度和结果展示公共代码。网格、舱室和装载结果在同次运行中共享，并按算例持久化。

[第 13 工序说明](src/13_设计图纸输出/README.md)列出四类图纸、DXF R2010 与 SVG 预览、型值和舱容数据表，以及图种与版式设置。

详细输入与模型范围见 [docs/case_format.md](docs/case_format.md)。
