#!/usr/bin/env python3
"""AI Agent for Automated Food Image Collection & Processing.

Reads food item names from an Excel file, finds a suitable image online,
validates its quality, resizes it to 1800x1200 (<10 MB), renames it with the
exact food item name and uploads it to a Google Drive folder.

Usage:
    python main.py --excel sample_input.xlsx
    python main.py --excel menu.xlsx --column "Food Item" --dry-run
"""

import argparse
import logging
import sys

from agent.config import Config
from agent.pipeline import run


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automated food image collection agent")
    p.add_argument("--excel", default="sample_input.xlsx",
                   help="Path to the input Excel file (default: sample_input.xlsx)")
    p.add_argument("--column", default="Food Item",
                   help="Name of the column containing food items")
    p.add_argument("--output-dir", default="output",
                   help="Local folder for processed images (default: output/)")
    p.add_argument("--report", default="processing_report.xlsx",
                   help="Path of the processing report (default: processing_report.xlsx)")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only the first N items (0 = all)")
    p.add_argument("--max-candidates", type=int, default=6,
                   help="Candidate images to try per food item (default: 6)")
    p.add_argument("--dry-run", action="store_true",
                   help="Skip Google Drive upload even if it is configured")
    p.add_argument("--strict", action="store_true",
                   help="Fail an item if no image passes quality validation "
                        "(default: best-effort fallback)")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = Config(
        excel_path=args.excel,
        column=args.column,
        output_dir=args.output_dir,
        report_path=args.report,
        max_candidates=args.max_candidates,
        dry_run=args.dry_run,
        best_effort=not args.strict,
    )

    if args.limit > 0:
        from agent.excel_reader import read_food_items
        items = read_food_items(cfg.excel_path, cfg.column)[: args.limit]
        reporter = run(cfg, items=items)
    else:
        reporter = run(cfg)

    print("\n" + "=" * 60)
    print(reporter.summary())
    print(f"Report: {args.report} (+ .csv)")
    print(f"Images: {args.output_dir}/")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
