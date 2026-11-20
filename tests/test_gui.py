from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator
from pathlib import Path
from tkinter import messagebox

import pytest

from duplicate_detector import deleter, gui
from duplicate_detector.scanner import FileRecord, Progress, ScanResult, SimilarPair, scan

from .conftest import MakeFile


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
    app.folders = []
    app.add_folder(str(tmp_path))
    app._save_settings()

    assert gui.load_settings(tmp_path / "settings.json")["folders"] == [str(tmp_path)]


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


# ----------------------------------------------------------- deletion
def test_nothing_is_selected_after_a_scan(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))
    assert app.selected == set()
    assert "disabled" in app.delete_btn.state()
    assert "disabled" not in app.select_all_btn.state()


def test_result_trees_do_not_share_checkbox_images(
    app: gui.DuplicateDetectorApp, make_file: MakeFile, tmp_path: Path
) -> None:
    # Tk slows down badly when two widgets show many rows with the same image:
    # releasing the image in one tree costs time per row in the other. With
    # tens of thousands of results this froze the window, so each tree gets
    # its own images.
    make_file("d/a.jpg", b"same")
    make_file("d/b.jpg", b"same")
    result = scan(tmp_path / "d")
    a, b = result.exact_groups[0]
    result.similar_pairs = [SimilarPair(a, b, 1)]
    app._show_results(result)

    exact_img = str(app.exact_tree.item(app.exact_tree.get_children()[0], "image")[0])
    similar_img = str(app.similar_tree.item(app.similar_tree.get_children()[0], "image")[0])
    exact_names = {str(i) for pool in app._check_images["exact"].values() for i in pool}
    similar_names = {str(i) for pool in app._check_images["similar"].values() for i in pool}
    assert exact_img in exact_names
    assert similar_img in similar_names
    assert not exact_names & similar_names


def test_checkbox_rows_spread_over_image_copies(app: gui.DuplicateDetectorApp) -> None:
    # Rows sharing one Tk image make changing any of them slower the more rows
    # there are, so consecutive rows get different (identical-looking) copies.
    used = {str(app._check_image(app.exact_tree, "off")) for _ in range(gui.CHECK_IMAGE_COPIES)}
    assert len(used) == gui.CHECK_IMAGE_COPIES


def test_toggle_file_and_group(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))
    group = app.exact_tree.get_children()[0]
    app._populate(app.exact_tree, group)
    first, second = _children(app.exact_tree, group)

    app.toggle(first)
    assert app.selected == {app._file_by_iid[first].path}
    partial_imgs = {str(img) for img in app._check_images["exact"]["partial"]}
    assert str(app.exact_tree.item(group, "image")[0]) in partial_imgs
    assert "1 files selected" in app.selection_var.get()
    assert "disabled" not in app.delete_btn.state()

    app.toggle(group)  # partly selected group -> select every file
    assert app.selected == {app._file_by_iid[first].path, app._file_by_iid[second].path}

    app.toggle(group)  # fully selected group -> unselect every file
    assert app.selected == set()


def test_select_all_and_clear(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))
    app.select_all()
    assert len(app.selected) == 4
    app.clear_selection()
    assert app.selected == set()


def test_delete_moves_selected_and_updates_results(
    app: gui.DuplicateDetectorApp, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app._show_results(scan(data))
    victim = next(r for r in app.result.records if r.name == "a copy.jpg")
    app.set_selected([victim.path], True)
    asked: list[int] = []

    def confirm(records: list[FileRecord], at_risk: list[list[FileRecord]]) -> str:
        asked.append(len(records))
        assert at_risk == []
        return "delete"

    monkeypatch.setattr(app, "_confirm_delete", confirm)

    app.delete_selected()
    _wait_for_delete(app)

    assert asked == [1]
    assert not Path(victim.path).exists()
    assert app.selected == set()
    assert len(app.result.exact_groups) == 1  # that group has one copy left
    assert app.stat_vars["groups"].get() == "1"
    assert "Moved 1 files" in app.stage_var.get()
    assert app.log_file.exists()


def test_cancel_in_confirmation_deletes_nothing(
    app: gui.DuplicateDetectorApp, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app._show_results(scan(data))
    app.select_all()
    monkeypatch.setattr(app, "_confirm_delete", lambda records, at_risk: "cancel")

    app.delete_selected()

    assert not app.deleting
    assert all(Path(p).exists() for p in app.selected)
    assert len(app.selected) == 4


def test_keep_one_copy_when_every_copy_is_selected(
    app: gui.DuplicateDetectorApp, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app._show_results(scan(data))
    app.select_all()
    warned: list[int] = []

    def confirm(records: list[FileRecord], at_risk: list[list[FileRecord]]) -> str:
        warned.append(len(at_risk))
        return "keep_one"

    monkeypatch.setattr(app, "_confirm_delete", confirm)

    app.delete_selected()
    _wait_for_delete(app)

    assert warned == [2]
    remaining = [p for p in (data / "photos/a.jpg", data / "backup/a copy.jpg") if p.exists()]
    assert len(remaining) == 1
    remaining = [p for p in (data / "docs/report.pdf", data / "old/report.pdf") if p.exists()]
    assert len(remaining) == 1
    assert app.result.exact_groups == []


def _wait_for_delete(app: gui.DuplicateDetectorApp, timeout: float = 10) -> None:
    import time

    deadline = time.monotonic() + timeout
    while app.deleting and time.monotonic() < deadline:
        try:
            kind, *payload = app.events.get(timeout=0.1)
        except Exception:
            continue
        if kind == "deleted":
            app._finish_delete(payload[0])
    assert not app.deleting


def test_rows_keep_identity_after_deleting(
    app: gui.DuplicateDetectorApp, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app._show_results(scan(data))
    pdf_group = next(
        g for g in app.exact_tree.get_children() if "report.pdf" in app.exact_tree.item(g, "text")
    )
    app._populate(app.exact_tree, pdf_group)
    app.exact_tree.item(pdf_group, open=True)
    highlighted = _children(app.exact_tree, pdf_group)[1]
    highlighted_path = app._file_by_iid[highlighted].path
    app.exact_tree.selection_set(highlighted)
    # Delete one photo copy: the photo group disappears and indices shift.
    victim = next(r for r in app.result.records if r.name == "a copy.jpg")
    app.set_selected([victim.path], True)
    monkeypatch.setattr(app, "_confirm_delete", lambda records, at_risk: "delete")

    app.delete_selected()
    _wait_for_delete(app)

    assert app.exact_tree.item(pdf_group, "open")
    (now_selected,) = app.exact_tree.selection()
    assert app._file_by_iid[now_selected].path == highlighted_path


def test_both_photos_of_a_pair_trigger_the_warning(
    app: gui.DuplicateDetectorApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rec(name: str) -> FileRecord:
        return FileRecord(source="", path=name, size=1, mtime=0)

    a, b = rec("a.jpg"), rec("b.jpg")
    app._show_results(ScanResult(records=[a, b], similar_pairs=[SimilarPair(a, b, 0)]))
    app.notebook.select(1)
    app.select_all()
    seen: list[int] = []

    def confirm(records: list[FileRecord], at_risk: list[list[FileRecord]]) -> str:
        seen.append(len(at_risk))
        return "cancel"

    monkeypatch.setattr(app, "_confirm_delete", confirm)

    app.delete_selected()

    assert seen == [1]


def test_space_toggles_focused_row_without_expanding(
    app: gui.DuplicateDetectorApp, data: Path
) -> None:
    app._show_results(scan(data))
    group = app.exact_tree.get_children()[0]
    app.exact_tree.focus(group)

    result = app._on_space(app.exact_tree, None)  # type: ignore[arg-type]

    assert result == "break"
    assert len(app.selected) == 2
    assert not app.exact_tree.item(group, "open")


def test_select_all_respects_the_filter(app: gui.DuplicateDetectorApp, data: Path) -> None:
    app._show_results(scan(data))
    app.filter_var.set("backup")
    app._refresh_results()

    app.select_all()

    assert {Path(p).name for p in app.selected} == {"a copy.jpg"}


def test_failed_file_in_dropped_group_is_unticked(
    app: gui.DuplicateDetectorApp, data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app._show_results(scan(data))
    photo_group = next(g for g in app.result.exact_groups if g[0].name.endswith(".jpg"))
    app.set_selected([r.path for r in photo_group], True)
    deleted, failed = photo_group
    outcome = deleter.DeleteOutcome(deleted=[deleted], failed=[(failed, "locked")])
    warnings: list[str] = []
    monkeypatch.setattr(messagebox, "showwarning", lambda title, msg: warnings.append(msg))

    app._finish_delete(outcome)

    # The group is gone (one copy left), so its failed file must not stay secretly ticked.
    assert app.selected == set()
    assert "locked" in warnings[0]
