"""Run a user-defined Fsgy-surf case: python main.py cases/example_ship/case.json."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from fsgy_surf.workflow.pipeline import run_case
from basic_report import generate_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path, help='Path to case.json')
    parser.add_argument('--force', action='store_true', help='Recompute selected tasks and dependencies')
    parser.add_argument('--tasks', nargs='+', help='Override the task list in case.json')
    args = parser.parse_args()
    try:
        context = run_case(args.case, force=args.force, tasks=args.tasks)
        if all(number in context.data for number in range(1, 6)):
            report = generate_report(context.config_path)
            print(f'01-05 report: {report}', flush=True)
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as error:
        parser.exit(1, f'Calculation failed: {error}\n')


if __name__ == '__main__':
    main()
