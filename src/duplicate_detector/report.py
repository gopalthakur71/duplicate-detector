"""CSV report export."""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import TextIO

from .scanner import FileRecord, ScanResult

FIELDS = [
    "source",
    "path",
    "filename",
    "size_bytes",
    "modified_time",
    "sha256",
    "exact_duplicate_group",
    "possible_visual_matches",
    "notes",
]


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: str) -> str:
    """Stop spreadsheet apps from running file names as formulas (CSV injection).

    Scanned file names are untrusted: a file named ``=HYPERLINK(...)`` would
    otherwise become a live formula when the report is opened in Excel. A
    leading apostrophe makes the cell plain text.
    """
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def default_report_name(now: datetime | None = None) -> str:
    return f"duplicate_report_{(now or datetime.now()):%Y%m%d_%H%M%S}.csv"


NOTE_WITHIN_ONE_FOLDER = "duplicate within one folder only (left out by the cross-folder option)"


def write_csv(
    result: ScanResult,
    out: str | os.PathLike[str] | TextIO,
    *,
    within_one_folder: Iterable[Sequence[FileRecord]] = (),
) -> None:
    """Write one row per scanned file, followed by one ``ERROR`` row per read error.

    ``out`` may be a path or an open text file (opened with ``newline=""``).
    Paths are written as UTF-8 with a BOM so Excel opens them correctly.
    ``within_one_folder`` are duplicate groups left out of ``result`` by the
    cross-folder option; their files get a note so they don't look unique.
    """
    hidden = {r.path for g in within_one_folder for r in g}
    if isinstance(out, (str, os.PathLike)):
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            _write(result, f, hidden)
    else:
        _write(result, out, hidden)


def _write(result: ScanResult, f: TextIO, hidden: set[str]) -> None:
    exact_map: dict[str, int] = {}
    for group_id, group in enumerate(result.exact_groups, 1):
        for rec in group:
            exact_map[rec.path] = group_id

    similar_map: defaultdict[str, list[str]] = defaultdict(list)
    for pair_id, pair in enumerate(result.similar_pairs, 1):
        label = f"possible_pair_{pair_id}_distance_{pair.distance}"
        similar_map[pair.left.path].append(label)
        similar_map[pair.right.path].append(label)

    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    for r in result.records:
        writer.writerow(
            {
                "source": safe_cell(r.source),
                "path": safe_cell(r.path),
                "filename": safe_cell(r.name),
                "size_bytes": r.size,
                "modified_time": datetime.fromtimestamp(r.mtime).isoformat(sep=" "),
                "sha256": r.sha256 or "",
                "exact_duplicate_group": exact_map.get(r.path, ""),
                "possible_visual_matches": "; ".join(similar_map.get(r.path, [])),
                "notes": safe_cell(r.note or (NOTE_WITHIN_ONE_FOLDER if r.path in hidden else "")),
            }
        )
    for err in result.errors:
        writer.writerow({"source": "ERROR", "path": safe_cell(err)})
