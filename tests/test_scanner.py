from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from duplicate_detector import scanner
from duplicate_detector.scanner import (
    NOTE_EMPTY,
    NOTE_UNIQUE_SIZE,
    NOTE_UNIQUE_START,
    Progress,
    ScanCancelled,
    human_size,
    scan,
    sha256_file,
    walk_files,
)

from .conftest import MakeFile


def _groups(result: scanner.ScanResult, root: Path) -> list[list[str]]:
    return sorted(sorted(os.path.relpath(r.path, root) for r in g) for g in result.exact_groups)


def _notes(result: scanner.ScanResult, root: Path) -> dict[str, str]:
    return {os.path.relpath(r.path, root): r.note for r in result.records}


def test_finds_renamed_copies_in_different_folders(
    tmp_path: Path, make_file: MakeFile, big_bytes: bytes
) -> None:
    make_file("a/one.bin", big_bytes)
    make_file("b/deep/renamed.bin", big_bytes)
    make_file("a/small.txt", b"hello")
    make_file("c/small_copy.txt", b"hello")

    result = scan(tmp_path)

    assert _groups(result, tmp_path) == [
        [os.path.join("a", "one.bin"), os.path.join("b", "deep", "renamed.bin")],
        [os.path.join("a", "small.txt"), os.path.join("c", "small_copy.txt")],
    ]
    # Largest wasted space first.
    assert result.exact_groups[0][0].size == len(big_bytes)
    assert result.wasted_bytes == len(big_bytes) + 5
    assert result.errors == []


def test_same_size_but_different_end_is_not_a_duplicate(
    tmp_path: Path, make_file: MakeFile, big_bytes: bytes
) -> None:
    make_file("a.bin", big_bytes)
    make_file("b.bin", big_bytes[:-1] + bytes([big_bytes[-1] ^ 1]))

    result = scan(tmp_path)

    assert result.exact_groups == []
    # Both were fully hashed because their first part matched.
    assert all(r.sha256 for r in result.records)


def test_unique_files_are_never_hashed(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("unique.txt", b"only once")
    make_file("x.bin", b"A" * (scanner.PARTIAL_SIZE + 10))
    make_file("y.bin", b"B" * (scanner.PARTIAL_SIZE + 10))
    make_file("empty1", b"")
    make_file("sub/empty2", b"")

    result = scan(tmp_path)
    notes = _notes(result, tmp_path)

    assert result.exact_groups == []
    assert notes["unique.txt"] == NOTE_UNIQUE_SIZE
    assert notes["x.bin"] == notes["y.bin"] == NOTE_UNIQUE_START
    assert notes["empty1"] == NOTE_EMPTY
    assert all(r.sha256 is None for r in result.records)


def test_full_hash_matches_hashlib(tmp_path: Path, make_file: MakeFile, big_bytes: bytes) -> None:
    path = make_file("f.bin", big_bytes)
    assert sha256_file(path) == hashlib.sha256(big_bytes).hexdigest()
    assert sha256_file(path, limit=10) == hashlib.sha256(big_bytes[:10]).hexdigest()


def test_reports_progress_and_finishes(
    tmp_path: Path, make_file: MakeFile, big_bytes: bytes
) -> None:
    make_file("a.bin", big_bytes)
    make_file("b.bin", big_bytes)
    events: list[Progress] = []

    scan(tmp_path, on_progress=events.append)

    assert events[0].message.startswith("Listing files")
    assert any(e.message.startswith("Fully hashing") for e in events)


def test_cancel_raises(tmp_path: Path, make_file: MakeFile) -> None:
    make_file("a.txt", b"x")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ScanCancelled):
        scan(tmp_path, cancel=cancel)


def test_unreadable_folder_is_reported(
    tmp_path: Path, make_file: MakeFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_file("ok/a.txt", b"x")
    blocked = make_file("blocked/b.txt", b"x").parent
    real_scandir = os.scandir

    def fake_scandir(path: str) -> object:
        if os.path.samefile(path, blocked):
            raise PermissionError("access denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    errors: list[str] = []

    paths = [p for p, _, _ in walk_files(tmp_path, errors)]

    assert [os.path.basename(p) for p in paths] == ["a.txt"]
    assert len(errors) == 1
    assert "folder could not be opened" in errors[0]


def test_file_read_error_is_reported(
    tmp_path: Path, make_file: MakeFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_file("a.txt", b"same")
    make_file("b.txt", b"same")
    real = scanner.sha256_file

    def flaky(path: str, *args: object, **kwargs: object) -> str:
        if os.path.basename(path) == "b.txt":
            raise PermissionError("locked")
        return real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(scanner, "sha256_file", flaky)

    result = scan(tmp_path)

    assert result.exact_groups == []
    assert len(result.errors) == 1
    assert "locked" in result.errors[0]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_symlinks_are_skipped(tmp_path: Path, make_file: MakeFile) -> None:
    target = make_file("real.txt", b"data")
    try:
        (tmp_path / "link.txt").symlink_to(target)
    except OSError:
        pytest.skip("creating symlinks needs extra privileges on this system")

    result = scan(tmp_path)

    assert [r.name for r in result.records] == ["real.txt"]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0.0 B"),
        (1023, "1023.0 B"),
        (1024, "1.0 KB"),
        (5 * 1024**3, "5.0 GB"),
        (3 * 1024**4, "3.0 TB"),
    ],
)
def test_human_size(size: int, expected: str) -> None:
    assert human_size(size) == expected
