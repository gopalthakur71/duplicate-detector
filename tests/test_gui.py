from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from pathlib import Path

import pytest

from duplicate_detector import gui
from duplicate_detector.scanner import Progress, scan

from .conftest import MakeFile


@pytest.fixture(scope="session")
def tk_root() -> Iterator[tk.Tk]:
    # One shared root: creating many Tk interpreters in a row is unreliable on Windows.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(tk_root: tk.Tk, tmp_path: Path) -> Iterator[gui.DuplicateDetectorApp]:
    application = gui.DuplicateDetectorApp(tk_root, settings_file=tmp_path / "settings.json")
    yield application
    for job in tk_root.tk.splitlist(tk_root.tk.call("after", "info")):
        tk_root.after_cancel(job)
    for child in tk_root.winfo_children():
        child.destroy()


@pytest.fixture
def data(make_file: MakeFile, tmp_path: Path) -> Path:
    make_file("data/photos/a.jpg", b"same picture bytes")
    make_file("data/backup/a copy.jpg", b"same picture bytes")
    make_file("data/docs/report.pdf", b"x" * 5000)
    make_file("data/old/report.pdf", b"x" * 5000)
    make_file("data/unique.txt", b"only once")
    return tmp_path / "data"


def test_results_show_groups_with_file_rows(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))

    groups = app.exact_tree.get_children()
    assert len(groups) == 2
    assert all(len(app.exact_tree.get_children(g)) == 2 for g in groups)
    assert app.stat_vars["files"].get() == "5"
    assert app.stat_vars["groups"].get() == "2"
    assert "Exact duplicates  (2)" in app.notebook.tab(0, "text")
    assert "Scan complete" in app.stage_var.get()
    assert "disabled" not in app.export_btn.state()


def test_progress_bar_is_full_when_finished(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app.scanning = True
    app._show_progress(Progress("Listing files…"))  # indeterminate mode
    app._show_results(scan(data))

    assert float(app.progress.cget("value")) == float(app.progress.cget("maximum"))
    assert not app.scanning
    assert "disabled" in app.cancel_btn.state()


def test_filter_narrows_results(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))

    app.filter_var.set("backup")
    app._refresh_results()

    groups = app.exact_tree.get_children()
    assert len(groups) == 1
    assert app.exact_tree.item(groups[0], "open")
    assert "(1 of 2)" in app.notebook.tab(0, "text")


def test_sort_by_size_toggles(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))

    app._sort_by("exact", "size")
    sizes = [app.exact_tree.set(g, "size") for g in app.exact_tree.get_children()]
    assert sizes[0] == "4.9 KB"  # biggest first
    assert "▼" in app.exact_tree.heading("size", "text")

    app._sort_by("exact", "size")
    sizes = [app.exact_tree.set(g, "size") for g in app.exact_tree.get_children()]
    assert sizes[-1] == "4.9 KB"


def test_selected_file_and_copy_path(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))
    group = app.exact_tree.get_children()[0]
    child = app.exact_tree.get_children(group)[0]
    app.exact_tree.selection_set(child)

    rec = app._selected_file()
    assert rec is not None
    app._copy_selected()
    assert app.root.clipboard_get() == rec.path


def test_settings_are_remembered(app: gui.DuplicateDetectorApp, tmp_path: Path) -> None:
    app.scan_root.set(str(tmp_path))
    app._save_settings()

    assert gui.load_settings(tmp_path / "settings.json")["last_folder"] == str(tmp_path)


def test_load_settings_tolerates_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "settings.json"
    bad.write_text("{not json", encoding="utf-8")
    assert gui.load_settings(bad) == {}
    assert gui.load_settings(tmp_path / "missing.json") == {}


def test_format_elapsed() -> None:
    assert gui.format_elapsed(3725) == "1:02:05"
