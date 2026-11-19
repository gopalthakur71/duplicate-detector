"""Command-line interface.

duplicate-detector                 # open the GUI
duplicate-detector gui             # open the GUI
duplicate-detector scan E:\\ --csv report.csv [--images]
duplicate-detector scan E:\\Photos D:\\Backup --across-folders
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from . import __version__
from .report import default_report_name, write_csv
from .scanner import (
    IMAGE_HASH_AVAILABLE,
    FileRecord,
    Progress,
    ScanCancelled,
    human_size,
    normalize_roots,
    scan,
    spans_folders,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duplicate-detector",
        description="Find duplicate files. The scan command is report-only and never deletes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("gui", help="open the graphical interface (default)")

    scan_p = sub.add_parser("scan", help="scan one or more folders from the command line")
    scan_p.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="folders or drives to scan (all subfolders included), e.g. E:\\Photos D:\\Backup",
    )
    scan_p.add_argument(
        "--across-folders",
        action="store_true",
        help="only report duplicates found in more than one of the given folders",
    )
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
    for path in args.paths:
        if not os.path.isdir(path):
            print(f"error: not a folder or cannot be accessed: {path}", file=sys.stderr)
            return 2
    roots, skipped = normalize_roots(args.paths)
    for inner, outer in skipped:
        print(
            f"note: {inner} is inside (or the same as) {outer}; scanning it once", file=sys.stderr
        )
    if args.across_folders and len(roots) < 2:
        print(
            "error: --across-folders needs at least two separate folders "
            "(not the same folder twice, or one inside the other)",
            file=sys.stderr,
        )
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
        result = scan(roots, compare_images=args.images, on_progress=on_progress)
    except (KeyboardInterrupt, ScanCancelled):
        print("\nScan cancelled. No files were changed.", file=sys.stderr)
        return 130
    if not args.quiet:
        print(file=sys.stderr)

    within_one_folder: list[list[FileRecord]] = []
    if args.across_folders:
        within_one_folder = [g for g in result.exact_groups if not spans_folders(g)]
        result = result.across_folders()
    out = args.csv or default_report_name()
    write_csv(result, out, within_one_folder=within_one_folder)
    print(
        f"Scanned {len(result.records):,} files in {len(roots)} folder(s). "
        f"{len(result.exact_groups):,} exact duplicate groups "
        f"({human_size(result.wasted_bytes)} in extra copies), "
        f"{len(result.similar_pairs):,} possible similar-photo pairs, "
        f"{len(result.errors):,} read errors."
    )
    print(f"Report: {os.path.abspath(out)}")
    return 0
