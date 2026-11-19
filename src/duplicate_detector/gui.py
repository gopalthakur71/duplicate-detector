"""Tkinter desktop interface."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .report import default_report_name, write_csv
from .scanner import IMAGE_HASH_AVAILABLE, Progress, ScanCancelled, ScanResult, human_size, scan

APP_TITLE = "Local Duplicate Detector"
POLL_MS = 150

HEADINGS = {
    "group": ("Group", 70),
    "count": ("Copies", 70),
    "size": ("File size", 100),
    "wasted": ("Extra space", 100),
    "distance": ("Distance", 80),
    "files": ("Files / locations", 700),
}


class DuplicateDetectorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} {__version__}")
        self.root.geometry("1120x720")
        self.root.minsize(900, 600)
        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.result = ScanResult()
        self._build_ui()
        self.root.after(POLL_MS, self._process_events)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=APP_TITLE, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Scans one folder tree, including all subfolders. Exact matches use SHA-256. "
                "Optional image matching finds visually similar photos."
            ),
            wraplength=1050,
        ).pack(anchor="w", pady=(3, 12))

        source = ttk.LabelFrame(outer, text="1. Select a folder or drive to scan", padding=10)
        source.pack(fill="x")

        self.scan_root = tk.StringVar(value="E:\\" if os.name == "nt" else os.path.expanduser("~"))
        ttk.Label(source, text="Scan location").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(source, textvariable=self.scan_root, width=95).grid(
            row=0, column=1, sticky="ew", padx=8, pady=3
        )
        ttk.Button(source, text="Browse…", command=self._browse).grid(row=0, column=2, pady=3)
        source.columnconfigure(1, weight=1)

        options = ttk.Frame(source)
        options.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.do_images = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            options,
            text="Also compare photos for visual similarity (requires Pillow + ImageHash)",
            variable=self.do_images,
        ).pack(side="left")
        self.scan_btn = ttk.Button(options, text="Scan for duplicates", command=self.start_scan)
        self.scan_btn.pack(side="left", padx=(18, 0))
        self.cancel_btn = ttk.Button(
            options, text="Cancel scan", command=self.cancel_scan, state="disabled"
        )
        self.cancel_btn.pack(side="left", padx=(8, 0))

        status_frame = ttk.Frame(outer)
        status_frame.pack(fill="x", pady=10)
        self.status = tk.StringVar(
            value="Choose a folder or drive, then scan. Nothing will be changed or deleted."
        )
        ttk.Label(status_frame, textvariable=self.status).pack(anchor="w")
        self.progress = ttk.Progressbar(status_frame, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 0))

        results = ttk.LabelFrame(outer, text="2. Results", padding=8)
        results.pack(fill="both", expand=True)
        self.notebook = ttk.Notebook(results)
        self.notebook.pack(fill="both", expand=True)
        self.exact_tree = self._make_tree(
            "Exact duplicates (same file contents)", ("group", "count", "size", "wasted", "files")
        )
        self.similar_tree = self._make_tree(
            "Similar photos (review manually)", ("group", "distance", "files")
        )

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Export report (CSV)", command=self.export_csv).pack(side="left")
        ttk.Button(bottom, text="Open selected group details", command=self.show_details).pack(
            side="left", padx=8
        )
        ttk.Label(
            bottom,
            text="Safety: this version never deletes, moves, or modifies files.",
            foreground="#166534",
        ).pack(side="right")

        if not IMAGE_HASH_AVAILABLE:
            self.status.set(
                "Image similarity is unavailable (Pillow/ImageHash not installed). "
                "Exact duplicate detection works."
            )

    def _browse(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.scan_root.set(path)

    def _make_tree(self, title: str, columns: tuple[str, ...]) -> ttk.Treeview:
        frame = ttk.Frame(self.notebook, padding=5)
        self.notebook.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            label, width = HEADINGS[col]
            tree.heading(col, text=label)
            tree.column(col, width=width, anchor="w")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    # ---------------------------------------------------------------- scan
    def start_scan(self) -> None:
        location = self.scan_root.get().strip()
        if not location:
            messagebox.showwarning("Location required", "Select a folder or drive to scan.")
            return
        if not os.path.isdir(location):
            messagebox.showerror(
                "Invalid location",
                "The selected folder or drive does not exist or cannot be accessed.",
            )
            return
        self.cancel_event.clear()
        self.scan_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.exact_tree.delete(*self.exact_tree.get_children())
        self.similar_tree.delete(*self.similar_tree.get_children())
        self.progress.config(value=0)
        self.status.set("Starting scan…")
        threading.Thread(
            target=self._scan_worker, args=(location, self.do_images.get()), daemon=True
        ).start()

    def cancel_scan(self) -> None:
        self.cancel_event.set()
        self.cancel_btn.config(state="disabled")
        self.status.set("Cancelling…")

    def _scan_worker(self, location: str, compare_images: bool) -> None:
        try:
            result = scan(
                location,
                compare_images=compare_images,
                cancel=self.cancel_event,
                on_progress=lambda p: self.events.put(("progress", p)),
            )
            self.events.put(("finished", result))
        except ScanCancelled:
            self.events.put(("cancelled",))
        except Exception:
            self.events.put(("failed", traceback.format_exc()))

    def _scan_ended(self, message: str) -> None:
        self.progress.stop()
        self.progress.config(mode="determinate")
        self.scan_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.status.set(message)

    def _process_events(self) -> None:
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == "progress":
                    self._show_progress(payload[0])
                elif kind == "finished":
                    self._show_results(payload[0])
                elif kind == "cancelled":
                    self.progress.config(value=0)
                    self._scan_ended("Scan cancelled. No files were changed.")
                elif kind == "failed":
                    self.progress.config(value=0)
                    self._scan_ended("Scan failed unexpectedly. No files were changed.")
                    messagebox.showerror("Scan failed", payload[0])
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._process_events)

    def _show_progress(self, p: Progress) -> None:
        self.status.set(p.message)
        if p.total is None:
            if str(self.progress.cget("mode")) != "indeterminate":
                self.progress.config(mode="indeterminate")
                self.progress.start(15)
        else:
            if str(self.progress.cget("mode")) != "determinate":
                self.progress.stop()
                self.progress.config(mode="determinate")
            self.progress.config(maximum=max(p.total, 1), value=p.done)

    def _show_results(self, result: ScanResult) -> None:
        self.result = result
        self.progress.stop()
        self.progress.config(mode="determinate", maximum=1, value=1)
        for i, group in enumerate(result.exact_groups, 1):
            size = group[0].size
            self.exact_tree.insert(
                "",
                "end",
                iid=str(i - 1),
                values=(
                    i,
                    len(group),
                    human_size(size),
                    human_size(size * (len(group) - 1)),
                    "  |  ".join(r.path for r in group),
                ),
            )
        for i, pair in enumerate(result.similar_pairs, 1):
            self.similar_tree.insert(
                "",
                "end",
                iid=str(i - 1),
                values=(i, pair.distance, f"{pair.left.path}  |  {pair.right.path}"),
            )
        msg = (
            f"Finished. Scanned {len(result.records):,} files. "
            f"Found {len(result.exact_groups):,} exact duplicate groups "
            f"({human_size(result.wasted_bytes)} in extra copies) and "
            f"{len(result.similar_pairs):,} possible similar-photo pairs."
        )
        if not IMAGE_HASH_AVAILABLE and self.do_images.get():
            msg += " Install Pillow and ImageHash to enable visual photo matching."
        if result.errors:
            msg += f" {len(result.errors):,} files or folders could not be read; see CSV report."
        self._scan_ended(msg)
        messagebox.showinfo("Scan complete", msg)

    # ------------------------------------------------------------- details
    def show_details(self) -> None:
        tab = self.notebook.index("current")  # type: ignore[no-untyped-call]
        tree = self.exact_tree if tab == 0 else self.similar_tree
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Select a group", "Select a result row first.")
            return
        idx = int(selected[0])
        if tab == 0:
            group = self.result.exact_groups[idx]
            text = "\n\n".join(
                f"Path: {r.path}\nSize: {human_size(r.size)}\nSHA-256: {r.sha256}" for r in group
            )
            title = "Exact duplicate group"
        else:
            pair = self.result.similar_pairs[idx]
            not_hashed = "(not hashed: no same-size file)"
            text = (
                f"Possible visual match (pHash distance: {pair.distance}; "
                "lower means more similar, 0 = looks identical).\n"
                "This is only a suggestion. Review the images manually.\n\n"
                f"{pair.left.path}\nSHA-256: {pair.left.sha256 or not_hashed}\n\n"
                f"{pair.right.path}\nSHA-256: {pair.right.sha256 or not_hashed}"
            )
            title = "Possible similar-photo pair"
        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry("850x420")
        box = tk.Text(win, wrap="word")
        box.pack(fill="both", expand=True, padx=10, pady=10)
        box.insert("1.0", text)
        box.config(state="disabled")

    def export_csv(self) -> None:
        if not self.result.records:
            messagebox.showinfo("No results", "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_report_name(),
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            write_csv(self.result, path)
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Report saved", f"CSV report saved to:\n{path}")


def main() -> None:
    root = tk.Tk()
    DuplicateDetectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
