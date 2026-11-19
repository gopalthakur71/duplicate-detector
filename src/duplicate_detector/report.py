"""CSV report export."""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime
from typing import TextIO

from .scanner import ScanResult

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


def default_report_name(now: datetime | None = None) -> str:
    return f"duplicate_report_{(now or datetime.now()):%Y%m%d_%H%M%S}.csv"


def write_csv(result: ScanResult, out: str | os.PathLike[str] | TextIO) -> None:
    """Write one row per scanned file, followed by one ``ERROR`` row per read error.

    ``out`` may be a path or an open text file (opened with ``newline=""``).
    Paths are written as UTF-8 with a BOM so Excel opens them correctly.
    """
    if isinstance(out, (str, os.PathLike)):
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            _write(result, f)
    else:
        _write(result, out)


def _write(result: ScanResult, f: TextIO) -> None:
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
                "source": r.source,
                "path": r.path,
                "filename": r.name,
                "size_bytes": r.size,
                "modified_time": datetime.fromtimestamp(r.mtime).isoformat(sep=" "),
                "sha256": r.sha256 or "",
                "exact_duplicate_group": exact_map.get(r.path, ""),
                "possible_visual_matches": "; ".join(similar_map.get(r.path, [])),
                "notes": r.note,
            }
        )
    for err in result.errors:
        writer.writerow({"source": "ERROR", "path": err})
