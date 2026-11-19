"""Regenerate the README screenshots from made-up results (no real files are read or changed).

Run from the repo root on Windows:  uv run python docs/make_screenshots.py docs/images
"""

import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

from duplicate_detector import gui
from duplicate_detector.deleter import groups_losing_all_copies
from duplicate_detector.scanner import FileRecord, ScanResult

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
t0 = time.mktime((2024, 3, 14, 10, 30, 0, 0, 0, -1))


ROOTS = [r"E:\Photos", r"E:\Phone Backup", r"E:\Downloads"]


def rec(path, size, days, sha):
    source = next(r for r in ROOTS if path.startswith(r + "\\"))
    return FileRecord(source=source, path=path, size=size, mtime=t0 + days * 86400, sha256=sha)


groups = [
    [
        rec(r"E:\Photos\Goa Trip\Beach day.mp4", 1_288_490_188, 0, "a1"),
        rec(r"E:\Phone Backup\2024\VID_20240314_103000.mp4", 1_288_490_188, 40, "a1"),
    ],
    [
        rec(r"E:\Photos\2024\Goa\IMG_2041.jpg", 6_291_456, 0, "b2"),
        rec(r"E:\Phone Backup\DCIM\Camera\IMG_2041.jpg", 6_291_456, 40, "b2"),
        rec(r"E:\Photos\Desktop copy\IMG_2041 (1).jpg", 6_291_456, 95, "b2"),
    ],
    [
        rec(r"E:\Phone Backup\Music\01 - Intro.mp3", 8_912_896, 10, "d4"),
        rec(r"E:\Downloads\01 - Intro.mp3", 8_912_896, 200, "d4"),
    ],
    [
        rec(r"E:\Downloads\Invoice_March.pdf", 482_304, 5, "c3"),
        rec(r"E:\Downloads\Old\Invoice_March (2).pdf", 482_304, 6, "c3"),
    ],
]
unique = [
    rec(rf"E:\Photos\2024\Goa\IMG_{n}.jpg", 5_000_000 + n, 0, f"u{n}") for n in range(2042, 2400)
]
result = ScanResult(
    records=[r for g in groups for r in g] + unique,
    exact_groups=sorted(groups, key=lambda g: g[0].size * (len(g) - 1), reverse=True),
    roots=ROOTS,
)

gui._enable_high_dpi()
root = tk.Tk()
root.bell = lambda: None
app = gui.DuplicateDetectorApp(root, settings_file=out / "_settings.json")
root.geometry("1366x768")
root.state("normal")
app.folders = list(ROOTS)
app._render_chips()
app._scan_compare_images = False
app._show_results(result)
app.elapsed_var.set("⏱  0:03:12")
tree = app.exact_tree


def grab(widget, name):
    widget.update()
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    ImageGrab.grab((x, y, x + widget.winfo_width(), y + widget.winfo_height())).save(out / name)


def step1():
    for g in tree.get_children()[:2]:
        app._populate(tree, g)
        tree.item(g, open=True)
    grab(root, "main-window.png")
    # "Select copies in > Phone Backup": every Phone Backup file that also exists elsewhere.
    app.select_copies_in(ROOTS[1])
    root.after(400, step2)


def step2():
    grab(root, "select-and-delete.png")
    app.select_all()
    root.after(300, step3)


def step3():
    def shoot():
        w = [c for c in root.winfo_children() if isinstance(c, tk.Toplevel)][-1]
        grab(w, "confirm-delete.png")
        w.destroy()

    root.after(1200, shoot)
    app._confirm_delete(
        app._selected_records(), groups_losing_all_copies(app._all_groups(), app.selected)
    )
    root.destroy()


root.attributes("-topmost", True)
root.after(1500, step1)
root.mainloop()
(out / "_settings.json").unlink(missing_ok=True)
print("ok")
