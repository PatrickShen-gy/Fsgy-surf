"""运行 Fsgy-surf 算例。"""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from fsgy_surf import PROJECT
from fsgy_surf.workflow.pipeline import run_case
from basic_report import generate_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', nargs='?', type=Path, help='算例目录或 JSON 配置路径')
    parser.add_argument('--case', '-c', dest='case_name', help='cases 下的算例名，默认为 example')
    parser.add_argument('--list-cases', action='store_true', help='列出内置算例')
    parser.add_argument('--force', action='store_true', help='Recompute selected tasks and dependencies')
    parser.add_argument('--tasks', nargs='+', help='Override the task list in case.json')
    args = parser.parse_args()
    cases = {path.parent.name: path for path in sorted((PROJECT / 'cases').glob('*/case.json'))}
    if args.list_cases:
        for name, path in cases.items():
            print(f'{name}: {path}')
        return
    if args.case is not None and args.case_name is not None:
        parser.error('算例路径与 --case 算例名只能选择一种')
    case_path = args.case
    if case_path is None:
        name = args.case_name or 'example'
        if name not in cases:
            parser.error(f'找不到算例 {name}，可用算例为 {", ".join(cases)}')
        case_path = cases[name]
    try:
        context = run_case(case_path, force=args.force, tasks=args.tasks)
        if all(number in context.data for number in range(1, 6)):
            report = generate_report(context.config_path)
            print(f'01-05 report: {report}', flush=True)
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as error:
        parser.exit(1, f'Calculation failed: {error}\n')


if __name__ == '__main__':
    main()
