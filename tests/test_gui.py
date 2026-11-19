from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from pathlib import Path
from tkinter import messagebox

import pytest

from duplicate_detector import gui
from duplicate_detector.scanner import FileRecord, Progress, ScanResult, SimilarPair, scan

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


def _children(tree: tk.ttk.Treeview, iid: str) -> list[str]:
    return [c for c in tree.get_children(iid) if not c.endswith(gui.PLACEHOLDER)]


def test_results_show_groups_with_lazy_file_rows(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))

    groups = app.exact_tree.get_children()
    assert len(groups) == 2
    assert all(_children(app.exact_tree, g) == [] for g in groups)  # not built yet

    app._set_open(True)

    assert all(len(_children(app.exact_tree, g)) == 2 for g in groups)
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


def test_queued_progress_does_not_hide_cancelling(app: gui.DuplicateDetectorApp) -> None:
    app.scanning = True
    app.cancel_scan()
    app._show_progress(Progress("Hashed 1 GB…", 1, 2))
    assert app.stage_var.get() == "Cancelling…"


def test_filter_narrows_and_expands_results(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))

    app.filter_var.set("backup")
    app._refresh_results()

    groups = app.exact_tree.get_children()
    assert len(groups) == 1
    assert app.exact_tree.item(groups[0], "open")
    assert len(_children(app.exact_tree, groups[0])) == 2
    assert "(1 of 2)" in app.notebook.tab(0, "text")


def test_sort_keeps_expanded_groups_and_selection(
    app: gui.DuplicateDetectorApp, data: Path
) -> None:
    app._show_results(scan(data))
    first = app.exact_tree.get_children()[0]
    app._populate(app.exact_tree, first)
    app.exact_tree.item(first, open=True)
    child = _children(app.exact_tree, first)[1]
    app.exact_tree.selection_set(child)

    app._sort_by("exact", "size")

    assert app.exact_tree.item(first, "open")
    assert app.exact_tree.selection() == (child,)


def test_sort_by_size_toggles(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))

    app._sort_by("exact", "size")
    sizes = [app.exact_tree.set(g, "size") for g in app.exact_tree.get_children()]
    assert sizes[0] == "4.9 KB"  # biggest first
    assert "▼" in app.exact_tree.heading("size", "text")

    app._sort_by("exact", "size")
    sizes = [app.exact_tree.set(g, "size") for g in app.exact_tree.get_children()]
    assert sizes[-1] == "4.9 KB"


def test_similar_tab_sorts_by_distance(app: gui.DuplicateDetectorApp) -> None:
    def rec(name: str) -> FileRecord:
        return FileRecord(source="", path=name, size=1, mtime=0)

    pairs = [SimilarPair(rec("a"), rec("b"), 4), SimilarPair(rec("c"), rec("d"), 0)]
    app._show_results(ScanResult(records=[rec("a")], similar_pairs=pairs))

    app._sort_by("similar", "distance")
    distances = [app.similar_tree.set(i, "distance") for i in app.similar_tree.get_children()]
    assert distances == ["4", "0"]


def test_similar_stat_uses_setting_the_scan_ran_with(
    app: gui.DuplicateDetectorApp, data: Path
) -> None:
    app._scan_compare_images = False
    app.do_images.set(True)  # ticked while the scan was running
    app._show_results(scan(data))
    assert app.stat_vars["similar"].get() == "off"


def test_selected_file_and_copy_path(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))
    app._set_open(True)
    group = app.exact_tree.get_children()[0]
    app.exact_tree.selection_set(_children(app.exact_tree, group)[0])

    rec = app._selected_file()
    assert rec is not None
    app._copy_selected()
    assert app.root.clipboard_get() == rec.path


def test_programs_are_not_opened_without_confirmation(
    app: gui.DuplicateDetectorApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    revealed: list[str] = []
    monkeypatch.setattr(gui, "open_path", opened.append)
    monkeypatch.setattr(gui, "reveal_path", revealed.append)
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)

    app.open_file(r"E:\Downloads\setup.exe")
    app.open_file(r"E:\Photos\beach.jpg")

    assert opened == [r"E:\Photos\beach.jpg"]
    assert revealed == [r"E:\Downloads\setup.exe"]


def test_file_action_reports_missing_files(
    app: gui.DuplicateDetectorApp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[str] = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: errors.append(msg))

    app.file_action(gui.reveal_path, str(tmp_path / "gone.jpg"))

    assert len(errors) == 1
    assert "no longer exists" in errors[0]


def test_settings_are_remembered(app: gui.DuplicateDetectorApp, tmp_path: Path) -> None:
    app.scan_root.set(str(tmp_path))
    app._save_settings()

    assert gui.load_settings(tmp_path / "settings.json")["last_folder"] == str(tmp_path)


def test_load_settings_tolerates_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "settings.json"
    bad.write_text("{not json", encoding="utf-8")
    assert gui.load_settings(bad) == {}
    assert gui.load_settings(tmp_path / "missing.json") == {}


def test_thumbnail_loads_off_thread(tmp_path: Path) -> None:
    pytest.importorskip("imagehash")
    from PIL import Image

    Image.new("RGB", (2000, 1000), "red").save(tmp_path / "big.png")
    thumb = gui.load_thumbnail(str(tmp_path / "big.png"), 100)
    assert thumb is not None
    assert max(thumb.size) == 100
    assert gui.load_thumbnail(str(tmp_path / "missing.png"), 100) is None


def test_format_elapsed() -> None:
    assert gui.format_elapsed(3725) == "1:02:05"


def test_is_risky() -> None:
    assert gui.is_risky("C:/x/Setup.EXE")
    assert gui.is_risky("run.bat")
    assert not gui.is_risky("photo.jpg")
