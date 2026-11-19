from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

from duplicate_detector.report import FIELDS, default_report_name, write_csv
from duplicate_detector.scanner import FileRecord, ScanResult, SimilarPair, scan

from .conftest import MakeFile


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_csv_has_a_row_per_file_and_group_ids(tmp_path: Path, make_file: MakeFile) -> None:
    root = tmp_path / "data"
    make_file("data/a.txt", b"same")
    make_file("data/b.txt", b"same")
    make_file("data/c.txt", b"unique!")
    result = scan(root)
    result.errors.append("X:\\locked.bin: access denied")
    out = tmp_path / "report.csv"

    write_csv(result, out)
    rows = _read(out)

    assert list(rows[0].keys()) == FIELDS
    by_name = {r["filename"]: r for r in rows if r["source"] != "ERROR"}
    assert by_name["a.txt"]["exact_duplicate_group"] == "1"
    assert by_name["b.txt"]["exact_duplicate_group"] == "1"
    assert by_name["c.txt"]["exact_duplicate_group"] == ""
    assert by_name["c.txt"]["sha256"] == ""
    assert by_name["c.txt"]["notes"]
    assert rows[-1]["source"] == "ERROR"
    assert rows[-1]["path"] == "X:\\locked.bin: access denied"


def test_csv_lists_similar_pairs_and_handles_unicode() -> None:
    left = FileRecord(source="r", path="r/фото.jpg", size=1, mtime=0)
    right = FileRecord(source="r", path="r/写真.jpg", size=2, mtime=0)
    result = ScanResult(records=[left, right], similar_pairs=[SimilarPair(left, right, 3)])
    buf = io.StringIO(newline="")

    write_csv(result, buf)
    rows = list(csv.DictReader(io.StringIO(buf.getvalue())))

    assert [r["filename"] for r in rows] == ["фото.jpg", "写真.jpg"]
    assert rows[0]["possible_visual_matches"] == "possible_pair_1_distance_3"


def test_default_report_name() -> None:
    assert (
        default_report_name(datetime(2026, 1, 2, 3, 4, 5)) == "duplicate_report_20260102_030405.csv"
    )
