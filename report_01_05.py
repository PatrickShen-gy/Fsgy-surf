"""Generate an HTML report from existing stage 01-05 results, without recalculation."""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent/'src'))
import fsgy_surf
from basic_report import generate_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case', type=Path, help='Path to case.json or its containing directory')
    parser.add_argument('--output', type=Path, help='Output HTML; defaults to results/report_01_05.html')
    args = parser.parse_args()
    try:
        path = generate_report(args.case, args.output)
    except (ValueError, KeyError, FileNotFoundError) as error:
        parser.exit(1, f'Report generation failed: {error}\n')
    print(f'Report: {path}')


if __name__ == '__main__':
    main()
