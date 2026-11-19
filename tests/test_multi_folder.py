"""Scanning several folders together (each including all its subfolders)."""

from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Iterator
from pathlib import Path

import pytest

from duplicate_detector import cli, gui
from duplicate_detector.scanner import is_inside, normalize_roots, scan, spans_folders

from .conftest import MakeFile


@pytest.fixture
def two_folders(make_file: MakeFile, tmp_path: Path) -> tuple[Path, Path]:
    # Photos and Backup share one file; Photos also has an internal duplicate;
    # files are nested several levels deep.
    make_file("Photos/2024/Goa/beach.jpg", b"beach photo bytes")
    make_file("Photos/2024/Goa/deep/er/still/beach copy.jpg", b"beach photo bytes")
    make_file("Backup/old phone/DCIM/beach.jpg", b"beach photo bytes")
    make_file("Photos/notes.txt", b"only in photos, twice")
    make_file("Photos/sub/notes again.txt", b"only in photos, twice")
    make_file("Backup/unique.txt", b"backup only")
    return tmp_path / "Photos", tmp_path / "Backup"


# ------------------------------------------------------------ scanner
def test_finds_duplicates_across_and_within_folders(two_folders: tuple[Path, Path]) -> None:
    photos, backup = two_folders

    result = scan([photos, backup])

    assert result.roots == [str(photos), str(backup)]
    sizes = sorted(len(g) for g in result.exact_groups)
    assert sizes == [2, 3]  # notes x2 (inside Photos), beach x3 (both folders)
    beach = next(g for g in result.exact_groups if len(g) == 3)
    assert {r.source for r in beach} == {str(photos), str(backup)}
    assert spans_folders(beach)


def test_across_folders_keeps_only_shared_duplicates(two_folders: tuple[Path, Path]) -> None:
    result = scan(list(two_folders)).across_folders()
    assert [len(g) for g in result.exact_groups] == [3]


def test_nested_folder_is_scanned_once(two_folders: tuple[Path, Path]) -> None:
    photos, _ = two_folders
    nested = photos / "2024"

    roots, skipped = normalize_roots([photos, nested, photos])
    result = scan([photos, nested])

    assert roots == [str(photos)]
    assert skipped == [(str(nested), str(photos))]
    paths = [r.path for r in result.records]
    assert len(paths) == len(set(paths))  # no file listed twice


def test_is_inside(tmp_path: Path) -> None:
    assert is_inside(str(tmp_path / "a" / "b"), str(tmp_path / "a"))
    assert is_inside(str(tmp_path / "a"), str(tmp_path / "a"))
    assert not is_inside(str(tmp_path / "ab"), str(tmp_path / "a"))
    if os.name == "nt":
        assert not is_inside("D:\\Backup", "E:\\")


def test_single_folder_still_works(two_folders: tuple[Path, Path]) -> None:
    photos, _ = two_folders
    result = scan(photos)
    assert result.roots == [str(photos)]
    assert sorted(len(g) for g in result.exact_groups) == [2, 2]


# ---------------------------------------------------------------- CLI
def test_cli_scans_several_folders(
    two_folders: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "r.csv"
    code = cli.main(["scan", *map(str, two_folders), "--across-folders", "--csv", str(out), "-q"])
    assert code == 0
    assert "in 2 folder(s)" in capsys.readouterr().out


def test_cli_across_folders_needs_two(
    two_folders: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["scan", str(two_folders[0]), "--across-folders"]) == 2
    assert "at least two folders" in capsys.readouterr().err


# ---------------------------------------------------------------- GUI
@pytest.fixture
def multi_app(tk_root: tk.Tk, tmp_path: Path) -> Iterator[gui.DuplicateDetectorApp]:
    application = gui.DuplicateDetectorApp(tk_root, settings_file=tmp_path / "settings.json")
    yield application
    for job in tk_root.tk.splitlist(tk_root.tk.call("after", "info")):
        tk_root.after_cancel(job)
    for child in tk_root.winfo_children():
        child.destroy()


def test_gui_add_and_remove_folders(
    multi_app: gui.DuplicateDetectorApp, two_folders: tuple[Path, Path]
) -> None:
    photos, backup = two_folders
    multi_app.folders = []
    multi_app.add_folder(str(photos))
    multi_app.add_folder(str(backup))
    multi_app.add_folder(str(photos))  # duplicates are ignored

    assert multi_app.folders == [str(photos), str(backup)]
    assert "disabled" not in multi_app.across_check.state()

    multi_app.remove_folder(str(backup))
    assert multi_app.folders == [str(photos)]
    assert "disabled" in multi_app.across_check.state()


def test_gui_across_filter_and_found_in(
    multi_app: gui.DuplicateDetectorApp, two_folders: tuple[Path, Path]
) -> None:
    multi_app._show_results(scan(list(two_folders)))
    tree = multi_app.exact_tree
    assert len(tree.get_children()) == 2
    assert "Backup, Photos" in [tree.set(g, "found") for g in tree.get_children()]

    multi_app.across_only.set(True)
    multi_app._on_across_toggled()

    assert len(tree.get_children()) == 1
    assert multi_app.stat_vars["groups"].get() == "1"


def test_gui_select_copies_in_folder_never_selects_every_copy(
    multi_app: gui.DuplicateDetectorApp, two_folders: tuple[Path, Path]
) -> None:
    photos, backup = two_folders
    multi_app._show_results(scan([photos, backup]))
    assert "disabled" not in multi_app.select_in_btn.state()

    multi_app.select_copies_in(str(backup))

    assert {Path(p).name for p in multi_app.selected} == {"beach.jpg"}
    assert all(is_inside(p, str(backup)) for p in multi_app.selected)

    multi_app.clear_selection()
    multi_app.select_copies_in(str(photos))
    # Both Photos copies of the beach photo; the notes pair exists only in Photos, so
    # it is skipped (selecting it would remove every copy).
    assert {Path(p).name for p in multi_app.selected} == {"beach.jpg", "beach copy.jpg"}
