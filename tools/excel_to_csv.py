"""
Batch converter: Excel (.xlsx/.xls) → CSV

Recursively finds all Excel files under the input folder and converts them
to CSV using pandas. Values are read as strings to preserve comma-decimal
numbers and "Err" entries exactly as the main app expects.

Usage:
    python tools/excel_to_csv.py <input_folder> [--output DIR] [--overwrite] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


def resolve_output_path(src: Path, input_root: Path, output_root: Path | None) -> Path:
    if output_root is None:
        return src.with_suffix(".csv")
    rel = src.relative_to(input_root)
    return (output_root / rel).with_suffix(".csv")


def convert(src: Path, dst: Path, dry_run: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return
    df = pd.read_excel(src, sheet_name=0, dtype=str)
    df.to_csv(dst, index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert Excel files to CSV in bulk.")
    parser.add_argument("input_folder", type=Path, help="Root folder to search for Excel files")
    parser.add_argument("--output", type=Path, default=None, metavar="DIR",
                        help="Mirror tree under DIR instead of writing next to source")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing CSV files")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    args = parser.parse_args()

    input_root: Path = args.input_folder.resolve()
    if not input_root.is_dir():
        print(f"Error: {input_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    sources = sorted(input_root.rglob("*.xls*"))
    total = len(sources)
    converted = 0

    for src in sources:
        dst = resolve_output_path(src, input_root, args.output)
        if dst.exists() and not args.overwrite:
            print(f"[SKIP] {dst}")
            continue
        action = "[DRY-RUN]" if args.dry_run else "[OK]"
        convert(src, dst, dry_run=args.dry_run)
        print(f"{action} {dst}")
        converted += 1

    label = "Would convert" if args.dry_run else "Converted"
    print(f"\n{label} {converted} / {total} files")


if __name__ == "__main__":
    main()
