"""Command-line interface.

duplicate-detector                 # open the GUI
duplicate-detector gui             # open the GUI
duplicate-detector scan E:\\ --csv report.csv [--images]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from . import __version__
from .report import default_report_name, write_csv
from .scanner import IMAGE_HASH_AVAILABLE, Progress, ScanCancelled, human_size, scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duplicate-detector",
        description="Find duplicate files. Read-only: never deletes, moves, or modifies files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the graphical interface (default)")

    scan_p = sub.add_parser("scan", help="scan a folder from the command line")
    scan_p.add_argument("path", help="folder or drive to scan, e.g. E:\\")
    scan_p.add_argument(
        "--csv",
        metavar="FILE",
        help="write the CSV report here (default: duplicate_report_<timestamp>.csv)",
    )
    scan_p.add_argument(
        "--images",
        action="store_true",
        help="also look for visually similar photos (needs the 'images' extra)",
    )
    scan_p.add_argument("-q", "--quiet", action="store_true", help="hide progress output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import main as gui_main

        gui_main()
        return 0
    return _run_scan(args)


def _run_scan(args: argparse.Namespace) -> int:
    if not os.path.isdir(args.path):
        print(f"error: not a folder or cannot be accessed: {args.path}", file=sys.stderr)
        return 2
    if args.images and not IMAGE_HASH_AVAILABLE:
        print(
            "error: photo comparison needs Pillow and ImageHash: "
            "pip install 'duplicate-detector[images]'",
            file=sys.stderr,
        )
        return 2

    def on_progress(p: Progress) -> None:
        if not args.quiet:
            print(f"\r{p.message[:100]:<100}", end="", file=sys.stderr, flush=True)

    try:
        result = scan(args.path, compare_images=args.images, on_progress=on_progress)
    except (KeyboardInterrupt, ScanCancelled):
        print("\nScan cancelled. No files were changed.", file=sys.stderr)
        return 130
    if not args.quiet:
        print(file=sys.stderr)

    out = args.csv or default_report_name()
    write_csv(result, out)
    print(
        f"Scanned {len(result.records):,} files. "
        f"{len(result.exact_groups):,} exact duplicate groups "
        f"({human_size(result.wasted_bytes)} in extra copies), "
        f"{len(result.similar_pairs):,} possible similar-photo pairs, "
        f"{len(result.errors):,} read errors."
    )
    print(f"Report: {os.path.abspath(out)}")
    return 0
