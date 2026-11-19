from __future__ import annotations

import csv
import os
from pathlib import Path

from duplicate_detector import deleter
from duplicate_detector.deleter import (
    apply_deletions,
    file_unchanged,
    groups_losing_all_copies,
    keep_one_per_group,
    move_to_trash,
)
from duplicate_detector.scanner import FileRecord, ScanResult, SimilarPair, scan

from .conftest import MakeFile


def _rec(path: str, mtime: float = 0, size: int = 1) -> FileRecord:
    return FileRecord(source="", path=path, size=size, mtime=mtime)


def test_moves_selected_files_and_logs(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/a.txt", b"same")
    make_file("data/b.txt", b"same")
    result = scan(tmp_path / "data")
    victim = next(r for r in result.records if r.name == "b.txt")
    trashed: list[str] = []
    log = tmp_path / "log" / "deletion_log.csv"

    outcome = move_to_trash([victim], log_path=log, trash=trashed.append)

    assert trashed == [victim.path]
    assert outcome.deleted == [victim]
    assert outcome.deleted_bytes == 4
    with open(log, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["path"] == victim.path
    assert rows[0]["action"] == "recycle_bin"


def test_really_moves_to_recycle_bin(tmp_path: Path, make_file: MakeFile) -> None:
    path = make_file("data/copy.txt", b"duplicate")
    rec = scan(tmp_path / "data").records[0]

    outcome = move_to_trash([rec])

    assert outcome.deleted == [rec], outcome.failed
    assert not path.exists()


def test_files_changed_since_scan_are_skipped(tmp_path: Path, make_file: MakeFile) -> None:
    path = make_file("data/a.txt", b"original")
    rec = scan(tmp_path / "data").records[0]
    path.write_bytes(b"edited after the scan, different size")
    trashed: list[str] = []

    outcome = move_to_trash([rec], trash=trashed.append)

    assert trashed == []
    assert outcome.changed == [rec]
    assert path.exists()


def test_missing_and_failing_files_are_reported(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/a.txt", b"x")
    make_file("data/b.txt", b"y")
    recs = sorted(scan(tmp_path / "data").records, key=lambda r: r.name)
    os.remove(recs[0].path)

    def refuse(path: str) -> None:
        raise OSError("no Recycle Bin on this drive")

    outcome = move_to_trash(recs, trash=refuse)

    assert outcome.changed == [recs[0]]
    assert outcome.failed == [(recs[1], "no Recycle Bin on this drive")]
    assert outcome.deleted == []


def test_progress_is_reported(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/a.txt", b"x")
    recs = scan(tmp_path / "data").records
    seen: list[tuple[int, int]] = []
    move_to_trash(recs, trash=lambda p: None, on_progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 1)]


def test_groups_losing_all_copies_and_keep_one() -> None:
    old, new = _rec("old", mtime=1), _rec("new", mtime=5)
    other = [_rec("x"), _rec("y")]
    selected = {"old", "new", "x"}

    assert groups_losing_all_copies([[old, new], other], selected) == [[old, new]]

    kept = keep_one_per_group([[old, new], other], selected)

    assert kept == [old]
    assert selected == {"new", "x"}


def test_apply_deletions_updates_groups_and_pairs() -> None:
    a, b, c, d = _rec("a"), _rec("b"), _rec("c"), _rec("d")
    result = ScanResult(
        records=[a, b, c, d],
        exact_groups=[[a, b, c], [d, _rec("e")]],
        similar_pairs=[SimilarPair(a, d, 0), SimilarPair(b, c, 1)],
    )

    apply_deletions(result, {"a", "e"})

    assert result.exact_groups == [[b, c]]  # d is alone now, so not a duplicate
    assert result.similar_pairs == [SimilarPair(b, c, 1)]
    assert a.note == deleter.NOTE_TRASHED
    assert len(result.records) == 4  # kept for the CSV report


def test_file_unchanged(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/a.txt", b"x")
    rec = scan(tmp_path / "data").records[0]
    assert file_unchanged(rec)
    assert not file_unchanged(_rec(str(tmp_path / "missing")))


def test_drive_without_recycle_bin_is_skipped(tmp_path: Path, make_file: MakeFile) -> None:
    path = make_file("data/a.txt", b"x")
    rec = scan(tmp_path / "data").records[0]
    trashed: list[str] = []

    outcome = move_to_trash([rec], trash=trashed.append, can_recycle=lambda p: False)

    assert trashed == []
    assert outcome.failed == [(rec, deleter.REASON_NO_RECYCLE_BIN)]
    assert path.exists()


def test_last_intact_copy_is_never_removed(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/keep.txt", b"same")
    make_file("data/delete.txt", b"same")
    result = scan(tmp_path / "data")
    keep = next(r for r in result.records if r.name == "keep.txt")
    victim = next(r for r in result.records if r.name == "delete.txt")
    os.remove(keep.path)  # the copy the user meant to keep vanished after the scan
    trashed: list[str] = []

    outcome = move_to_trash([victim], groups=result.exact_groups, trash=trashed.append)

    assert trashed == []
    assert outcome.no_survivor == [victim]


def test_fully_selected_group_is_allowed_when_user_confirmed(
    tmp_path: Path, make_file: MakeFile
) -> None:
    make_file("data/a.txt", b"same")
    make_file("data/b.txt", b"same")
    result = scan(tmp_path / "data")
    trashed: list[str] = []

    outcome = move_to_trash(result.records, groups=result.exact_groups, trash=trashed.append)

    assert len(trashed) == 2
    assert outcome.no_survivor == []


def test_log_error_is_reported(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/a.txt", b"x")
    rec = scan(tmp_path / "data").records[0]
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file, so the log folder cannot be created")

    outcome = move_to_trash([rec], trash=lambda p: None, log_path=blocker / "log.csv")

    assert outcome.deleted == [rec]
    assert outcome.log_error is not None


def test_log_is_written_per_file(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("data/a.txt", b"x")
    make_file("data/b.txt", b"yy")
    recs = scan(tmp_path / "data").records
    log = tmp_path / "log.csv"
    lines_seen: list[int] = []

    def trash(path: str) -> None:
        lines_seen.append(
            len(log.read_text(encoding="utf-8-sig").splitlines()) if log.exists() else 0
        )

    move_to_trash(recs, trash=trash, log_path=log)

    assert lines_seen == [0, 2]  # header + first row already on disk before the second file
    assert len(log.read_text(encoding="utf-8-sig").splitlines()) == 3


def test_has_recycle_bin(tmp_path: Path) -> None:
    assert deleter.has_recycle_bin(str(tmp_path))
    if os.name == "nt":
        assert not deleter.has_recycle_bin("\\\\no-such-server\\share\\file.txt")  # UNC path


def test_pairs_count_as_groups_for_keep_one() -> None:
    a, b = _rec("a", mtime=1), _rec("b", mtime=2)
    selected = {"a", "b"}
    assert keep_one_per_group([(a, b)], selected) == [a]
    assert selected == {"b"}
