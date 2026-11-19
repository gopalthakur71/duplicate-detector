"""Tkinter desktop interface."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from . import __version__
from .report import default_report_name, write_csv
from .scanner import (
    IMAGE_HASH_AVAILABLE,
    FileRecord,
    Progress,
    ScanCancelled,
    ScanResult,
    human_size,
    scan,
)

APP_TITLE = "Local Duplicate Detector"
POLL_MS = 100
FILTER_DELAY_MS = 250
THUMBNAIL_SIZE = 360

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 16)
FONT_STAT = ("Segoe UI Semibold", 15)


class C:
    """Color palette."""

    primary, primary_hover, primary_pressed = "#2563eb", "#1d4ed8", "#1e40af"
    danger, danger_hover, danger_pressed = "#dc2626", "#b91c1c", "#991b1b"
    success, success_hover, success_pressed = "#16a34a", "#15803d", "#166534"
    neutral, neutral_hover, neutral_pressed = "#e2e8f0", "#cbd5e1", "#94a3b8"
    disabled_bg, disabled_fg = "#e5e7eb", "#9ca3af"
    header_bg, header_fg, header_sub = "#0f172a", "#f8fafc", "#94a3b8"
    bg, card, border = "#f1f5f9", "#ffffff", "#e2e8f0"
    text, muted = "#0f172a", "#64748b"
    warn = "#b45309"
    group_row = "#eff6ff"


# --------------------------------------------------------------- helpers
def default_settings_file() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "DuplicateDetector" / "settings.json"


def load_settings(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass  # settings are a convenience; never fail because of them


def open_path(path: str) -> None:
    """Open a file with its default application (read-only for this app)."""
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_path(path: str) -> None:
    """Show a file selected in the system file manager."""
    if sys.platform == "win32":
        # Windows paths cannot contain '"', so this quoting is safe.
        subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path)])


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def format_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02}:{s % 60:02}"


# ------------------------------------------------------------------- app
class DuplicateDetectorApp:
    def __init__(self, root: tk.Tk, settings_file: Path | None = None) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} {__version__}")
        width = min(1240, int(root.winfo_screenwidth() * 0.85))
        height = min(820, int(root.winfo_screenheight() * 0.85))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(860, 560)
        if sys.platform == "win32" and root.winfo_screenheight() < 900:
            self.root.state("zoomed")  # small screens: use all the space for results

        self.settings_file = settings_file or default_settings_file()
        settings = load_settings(self.settings_file)

        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.result = ScanResult()
        self.scanning = False
        self._scan_started: float | None = None
        self._filter_job: str | None = None
        self._file_by_iid: dict[str, FileRecord] = {}
        self._group_by_iid: dict[str, tuple[str, int]] = {}
        self._sort: dict[str, tuple[str, bool]] = {
            "exact": ("#0", False),
            "similar": ("#0", False),
        }

        default_root = "E:\\" if os.name == "nt" else os.path.expanduser("~")
        self.scan_root = tk.StringVar(value=settings.get("last_folder") or default_root)
        self.do_images = tk.BooleanVar(
            value=bool(settings.get("compare_images")) and IMAGE_HASH_AVAILABLE
        )
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._schedule_filter())

        self._setup_styles()
        self._build_ui()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._process_events)

    # ---------------------------------------------------------- styles
    def _setup_styles(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        self.root.configure(background=C.bg)
        s.configure(".", background=C.bg, foreground=C.text, font=FONT)
        s.configure("TFrame", background=C.bg)
        s.configure("Card.TFrame", background=C.card)
        s.configure("Card.TLabel", background=C.card, foreground=C.text)
        s.configure("Card.TCheckbutton", background=C.card, foreground=C.text)
        s.map("Card.TCheckbutton", background=[("active", C.card)])
        s.configure("TEntry", padding=6, fieldbackground="#ffffff", bordercolor=C.neutral_hover)
        s.map("TEntry", bordercolor=[("focus", C.primary)], lightcolor=[("focus", C.primary)])

        def button(name: str, bg: str, hover: str, pressed: str, fg: str = "#ffffff") -> None:
            s.configure(
                name,
                background=bg,
                foreground=fg,
                bordercolor=bg,
                lightcolor=bg,
                darkcolor=bg,
                focuscolor=fg,
                padding=(16, 8),
                font=FONT_BOLD,
                relief="flat",
            )
            states = [("disabled", C.disabled_bg), ("pressed", pressed), ("active", hover)]
            s.map(
                name,
                background=states,
                bordercolor=states,
                lightcolor=states,
                darkcolor=states,
                foreground=[("disabled", C.disabled_fg)],
            )

        button("Primary.TButton", C.primary, C.primary_hover, C.primary_pressed)
        button("Danger.TButton", C.danger, C.danger_hover, C.danger_pressed)
        button("Success.TButton", C.success, C.success_hover, C.success_pressed)
        button("Secondary.TButton", C.neutral, C.neutral_hover, C.neutral_pressed, fg=C.text)
        s.configure("Small.Secondary.TButton", padding=(10, 4), font=FONT_SMALL)

        for name, color in (("Scan", C.primary), ("Done", C.success), ("Stopped", C.warn)):
            s.configure(
                f"{name}.Horizontal.TProgressbar",
                troughcolor=C.border,
                background=color,
                bordercolor=C.border,
                lightcolor=color,
                darkcolor=color,
                thickness=10,
            )

        s.configure(
            "Treeview",
            background=C.card,
            fieldbackground=C.card,
            foreground=C.text,
            rowheight=26,
            bordercolor=C.border,
        )
        s.map("Treeview", background=[("selected", C.primary)], foreground=[("selected", "#fff")])
        s.configure(
            "Treeview.Heading", background="#f8fafc", font=FONT_BOLD, relief="flat", padding=6
        )
        s.map("Treeview.Heading", background=[("active", C.neutral)])
        s.configure("TNotebook", background=C.card, borderwidth=0)
        s.configure("TNotebook.Tab", padding=(14, 6), background=C.neutral, font=FONT_BOLD)
        s.map(
            "TNotebook.Tab",
            background=[("selected", C.card)],
            foreground=[("selected", C.primary)],
        )

    def _card(self, parent: tk.Misc, title: str) -> tk.Frame:
        outer = tk.Frame(
            parent, bg=C.card, highlightthickness=1, highlightbackground=C.border, bd=0
        )
        if title:
            tk.Label(outer, text=title, font=FONT_BOLD, bg=C.card, fg=C.text).pack(
                anchor="w", padx=14, pady=(10, 0)
            )
        inner = tk.Frame(outer, bg=C.card)
        inner.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        outer.inner = inner  # type: ignore[attr-defined]
        return outer

    # -------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        self._build_header()
        body = ttk.Frame(self.root, padding=(16, 12, 16, 12))
        body.pack(fill="both", expand=True)
        self._build_scan_card(body)
        self._build_stats(body)
        # Footer first, packed to the bottom, so it can never be squeezed off-screen.
        self._build_footer(body)
        self._build_results(body)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=C.header_bg)
        header.pack(fill="x")
        header.columnconfigure(0, weight=1)
        tk.Label(header, text=APP_TITLE, font=FONT_TITLE, bg=C.header_bg, fg=C.header_fg).grid(
            row=0, column=0, sticky="w", padx=20, pady=(10, 0)
        )
        tk.Label(
            header,
            text="Finds duplicate files and similar photos. Read-only: never deletes, moves, "
            "or modifies anything.",
            font=FONT,
            bg=C.header_bg,
            fg=C.header_sub,
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 10))
        tk.Label(
            header, text=f"v{__version__}", font=FONT_SMALL, bg=C.header_bg, fg=C.header_sub
        ).grid(row=0, column=1, rowspan=2, sticky="e", padx=20)

    def _build_scan_card(self, body: ttk.Frame) -> None:
        card = self._card(body, "1   Choose what to scan")
        card.pack(fill="x")
        f: tk.Frame = card.inner  # type: ignore[attr-defined]
        f.columnconfigure(0, weight=1)

        self.location_entry = ttk.Entry(f, textvariable=self.scan_root, font=FONT)
        self.location_entry.grid(row=0, column=0, sticky="ew", ipady=2)
        ttk.Button(f, text="Browse…", style="Secondary.TButton", command=self._browse).grid(
            row=0, column=1, padx=(8, 0)
        )

        image_text = "Also compare photos for visual similarity (slower)"
        if not IMAGE_HASH_AVAILABLE:
            image_text += "  (install Pillow + ImageHash to enable)"
        self.images_check = ttk.Checkbutton(
            f, text=image_text, variable=self.do_images, style="Card.TCheckbutton"
        )
        self.images_check.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        if not IMAGE_HASH_AVAILABLE:
            self.images_check.state(["disabled"])

        # Buttons on the left, live status beside them (saves vertical space).
        actions = tk.Frame(f, bg=C.card)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.scan_btn = ttk.Button(
            actions, text="▶  Scan for duplicates", style="Primary.TButton", command=self.start_scan
        )
        self.scan_btn.pack(side="left")
        self.cancel_btn = ttk.Button(
            actions, text="■  Cancel", style="Danger.TButton", command=self.cancel_scan
        )
        self.cancel_btn.pack(side="left", padx=(8, 0))
        self.cancel_btn.state(["disabled"])
        self.elapsed_var = tk.StringVar(value="")
        tk.Label(actions, textvariable=self.elapsed_var, font=FONT, bg=C.card, fg=C.muted).pack(
            side="right"
        )

        status = tk.Frame(actions, bg=C.card)
        status.pack(side="left", fill="x", expand=True, padx=(16, 8))
        self.stage_var = tk.StringVar(value="Ready")
        self.detail_var = tk.StringVar(
            value="Choose a folder or drive, then press Scan (or Enter). Nothing will be changed."
        )
        self.stage_label = tk.Label(
            status, textvariable=self.stage_var, font=FONT_BOLD, bg=C.card, fg=C.text, anchor="w"
        )
        self.stage_label.pack(fill="x")
        tk.Label(
            status, textvariable=self.detail_var, font=FONT_SMALL, bg=C.card, fg=C.muted, anchor="w"
        ).pack(fill="x")
        self.progress = ttk.Progressbar(f, mode="determinate", style="Scan.Horizontal.TProgressbar")
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_stats(self, body: ttk.Frame) -> None:
        row = ttk.Frame(body)
        row.pack(fill="x", pady=8)
        specs = [
            ("files", "Files scanned", C.text),
            ("groups", "Duplicate groups", C.primary),
            ("wasted", "Space in extra copies", C.danger),
            ("similar", "Similar photo pairs", C.warn),
            ("errors", "Unreadable", C.muted),
        ]
        self.stat_vars: dict[str, tk.StringVar] = {}
        self.stat_labels: dict[str, tk.Label] = {}
        for col, (key, caption, color) in enumerate(specs):
            row.columnconfigure(col, weight=1, uniform="stats")
            card = tk.Frame(row, bg=C.card, highlightthickness=1, highlightbackground=C.border)
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0))
            var = tk.StringVar(value="–")
            value = tk.Label(card, textvariable=var, font=FONT_STAT, bg=C.card, fg=color)
            value.pack(anchor="w", padx=14, pady=(6, 0))
            label = tk.Label(card, text=caption, font=FONT_SMALL, bg=C.card, fg=C.muted)
            label.pack(anchor="w", padx=14, pady=(0, 6))
            self.stat_vars[key] = var
            self.stat_labels[key] = value
            if key == "errors":
                for w in (card, value, label):
                    w.bind("<Button-1>", lambda _e: self.show_errors())
                self.errors_card = card

    def _build_results(self, body: ttk.Frame) -> None:
        card = self._card(body, "")
        card.pack(fill="both", expand=True)
        f: tk.Frame = card.inner  # type: ignore[attr-defined]

        bar = tk.Frame(f, bg=C.card)
        bar.pack(fill="x", pady=(4, 8))
        tk.Label(bar, text="2   Results", font=FONT_BOLD, bg=C.card, fg=C.text).pack(side="left")
        ttk.Button(
            bar,
            text="Collapse all",
            style="Small.Secondary.TButton",
            command=lambda: self._set_open(False),
        ).pack(side="right")
        ttk.Button(
            bar,
            text="Expand all",
            style="Small.Secondary.TButton",
            command=lambda: self._set_open(True),
        ).pack(side="right", padx=(0, 6))
        self.filter_entry = ttk.Entry(bar, textvariable=self.filter_var, width=34, font=FONT)
        self.filter_entry.pack(side="right", padx=(0, 12))
        tk.Label(bar, text="🔍 Filter (Ctrl+F)", font=FONT_SMALL, bg=C.card, fg=C.muted).pack(
            side="right", padx=(0, 6)
        )

        tk.Label(
            f,
            text="Double-click a file to open it  ·  Right-click for Show in folder / Copy path"
            "  ·  Enter for details  ·  Click a column heading to sort",
            font=FONT_SMALL,
            bg=C.card,
            fg=C.muted,
        ).pack(side="bottom", anchor="w", pady=(6, 0))
        self.notebook = ttk.Notebook(f)
        self.notebook.pack(fill="both", expand=True)
        self.exact_tree = self._make_tree(
            "exact",
            [
                ("#0", "Group / file", 560),
                ("copies", "Copies", 70),
                ("size", "File size", 100),
                ("wasted", "Extra space", 110),
                ("modified", "Modified", 140),
            ],
        )
        self.similar_tree = self._make_tree(
            "similar",
            [
                ("#0", "Pair / file", 600),
                ("distance", "Distance", 90),
                ("size", "File size", 100),
                ("modified", "Modified", 140),
            ],
        )
        self._update_tab_titles()

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Open file", command=self._open_selected)
        self.menu.add_command(label="Show in folder", command=self._reveal_selected)
        self.menu.add_command(label="Copy path", command=self._copy_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Details…", command=self.show_details)

    def _make_tree(self, kind: str, columns: list[tuple[str, str, int]]) -> ttk.Treeview:
        frame = tk.Frame(self.notebook, bg=C.card)
        self.notebook.add(frame, text=kind)
        data_cols = [c for c, _, _ in columns if c != "#0"]
        tree = ttk.Treeview(frame, columns=data_cols, show="tree headings", selectmode="browse")
        for col, label, width in columns:
            tree.heading(col, text=label, anchor="w", command=partial(self._sort_by, kind, col))
            tree.column(col, width=width, anchor="w", stretch=col == "#0")
        tree.tag_configure("group", background=C.group_row, font=FONT_BOLD)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.bind("<Double-1>", self._on_double_click)
        tree.bind("<Return>", lambda _e: self.show_details())
        tree.bind("<Button-3>", self._on_right_click)
        if sys.platform == "darwin":
            tree.bind("<Button-2>", self._on_right_click)
        return tree

    def _build_footer(self, body: ttk.Frame) -> None:
        bottom = ttk.Frame(body)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self.export_btn = ttk.Button(
            bottom,
            text="⤓  Export report (CSV)",
            style="Success.TButton",
            command=self.export_csv,
        )
        self.export_btn.pack(side="left")
        self.details_btn = ttk.Button(
            bottom, text="Details", style="Secondary.TButton", command=self.show_details
        )
        self.details_btn.pack(side="left", padx=(8, 0))
        self.errors_btn = ttk.Button(
            bottom, text="View errors", style="Secondary.TButton", command=self.show_errors
        )
        self.errors_btn.pack(side="left", padx=(8, 0))
        for b in (self.export_btn, self.details_btn, self.errors_btn):
            b.state(["disabled"])
        tk.Label(
            bottom,
            text="🛡  Safe: this app never deletes, moves, or modifies files.",
            font=FONT,
            bg=C.bg,
            fg=C.success_pressed,
        ).pack(side="right")

    def _bind_keys(self) -> None:
        self.location_entry.bind("<Return>", lambda _e: self.start_scan())
        self.root.bind("<Escape>", lambda _e: self.cancel_scan() if self.scanning else None)
        self.root.bind("<Control-f>", lambda _e: self.filter_entry.focus_set())
        self.root.bind("<Control-e>", lambda _e: self.export_csv())

    def _browse(self) -> None:
        initial = self.scan_root.get() if os.path.isdir(self.scan_root.get()) else None
        path = filedialog.askdirectory(initialdir=initial)
        if path:
            self.scan_root.set(os.path.normpath(path))

    # ------------------------------------------------------------ scan
    def start_scan(self) -> None:
        if self.scanning:
            return
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
        self._save_settings()
        self.scanning = True
        self.cancel_event.clear()
        self.result = ScanResult()
        self._refresh_results()
        for key in self.stat_vars:
            self.stat_vars[key].set("–")
        self.scan_btn.state(["disabled"])
        self.cancel_btn.state(["!disabled"])
        for b in (self.export_btn, self.details_btn, self.errors_btn):
            b.state(["disabled"])
        self.progress.config(style="Scan.Horizontal.TProgressbar", value=0)
        self._set_stage("Scanning…", C.primary)
        self.detail_var.set("Starting…")
        self._scan_started = time.monotonic()
        self._tick()
        threading.Thread(
            target=self._scan_worker, args=(location, self.do_images.get()), daemon=True
        ).start()

    def cancel_scan(self) -> None:
        if not self.scanning:
            return
        self.cancel_event.set()
        self.cancel_btn.state(["disabled"])
        self._set_stage("Cancelling…", C.warn)

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

    def _tick(self) -> None:
        if self._scan_started is None:
            return
        self.elapsed_var.set(f"⏱  {format_elapsed(time.monotonic() - self._scan_started)}")
        if self.scanning:
            self.root.after(500, self._tick)

    def _set_stage(self, text: str, color: str) -> None:
        self.stage_var.set(text)
        self.stage_label.config(fg=color)

    def _process_events(self) -> None:
        try:
            while True:
                kind, *payload = self.events.get_nowait()
                if kind == "progress":
                    self._show_progress(payload[0])
                elif kind == "finished":
                    self._show_results(payload[0])
                elif kind == "cancelled":
                    self._scan_ended("Stopped.Horizontal.TProgressbar")
                    self._set_stage("Scan cancelled", C.warn)
                    self.detail_var.set("No files were changed.")
                elif kind == "failed":
                    self._scan_ended("Stopped.Horizontal.TProgressbar")
                    self._set_stage("Scan failed", C.danger)
                    self.detail_var.set("Something went wrong. No files were changed.")
                    messagebox.showerror("Scan failed", payload[0])
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._process_events)

    def _show_progress(self, p: Progress) -> None:
        self.detail_var.set(p.message)
        if p.total is None:
            if str(self.progress.cget("mode")) != "indeterminate":
                self.progress.config(mode="indeterminate")
                self.progress.start(12)
            self._set_stage("Scanning…", C.primary)
            return
        if str(self.progress.cget("mode")) != "determinate":
            self.progress.stop()
            self.progress.config(mode="determinate")
        total = max(p.total, 1)
        self.progress.config(maximum=total, value=p.done)
        self._set_stage(f"Scanning…  {p.done / total:.0%}", C.primary)

    def _scan_ended(self, bar_style: str) -> None:
        self.scanning = False
        # stop() resets the bar to 0, so it must come before setting the final value.
        self.progress.stop()
        self.progress.config(mode="determinate", style=bar_style, maximum=1, value=1)
        self.scan_btn.state(["!disabled"])
        self.cancel_btn.state(["disabled"])
        self._tick()
        self._scan_started = None

    def _show_results(self, result: ScanResult) -> None:
        self.result = result
        self._scan_ended("Done.Horizontal.TProgressbar")
        self._refresh_results()
        stats = {
            "files": f"{len(result.records):,}",
            "groups": f"{len(result.exact_groups):,}",
            "wasted": human_size(result.wasted_bytes),
            "similar": f"{len(result.similar_pairs):,}" if self.do_images.get() else "off",
            "errors": f"{len(result.errors):,}",
        }
        for key, value in stats.items():
            self.stat_vars[key].set(value)
        self.stat_labels["errors"].config(fg=C.danger if result.errors else C.muted)
        self.errors_card.config(cursor="hand2" if result.errors else "")

        if result.exact_groups or result.similar_pairs:
            self._set_stage("✔  Scan complete: duplicates found", C.success)
            self.detail_var.set(
                f"{len(result.exact_groups):,} groups use {human_size(result.wasted_bytes)} in "
                "extra copies. Expand a group to see its files."
            )
        else:
            self._set_stage("✔  Scan complete: no duplicates found", C.success)
            self.detail_var.set(f"All {len(result.records):,} files are unique.")
        if result.errors:
            self.detail_var.set(
                self.detail_var.get()
                + f"  {len(result.errors):,} items could not be read (see View errors)."
            )
        self.export_btn.state(["!disabled"] if result.records else ["disabled"])
        self.details_btn.state(["!disabled"])
        self.errors_btn.state(["!disabled"] if result.errors else ["disabled"])
        if not result.exact_groups and result.similar_pairs:
            self.notebook.select(1)
        self.root.bell()

    # --------------------------------------------------------- results
    def _schedule_filter(self) -> None:
        if self._filter_job is not None:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(FILTER_DELAY_MS, self._refresh_results)

    def _sort_by(self, kind: str, column: str) -> None:
        current, reverse = self._sort[kind]
        # Numbers are most useful biggest-first, so the first click sorts descending.
        self._sort[kind] = (column, not reverse if column == current else column != "#0")
        self._refresh_results()

    def _refresh_results(self) -> None:
        self._filter_job = None
        needle = self.filter_var.get().strip().lower()
        self._file_by_iid.clear()
        self._group_by_iid.clear()
        self._fill_exact(needle)
        self._fill_similar(needle)
        self._update_headings()
        self._update_tab_titles(needle)

    def _fill_exact(self, needle: str) -> None:
        tree = self.exact_tree
        tree.delete(*tree.get_children())
        keys: dict[str, Callable[[tuple[int, list[FileRecord]]], Any]] = {
            "#0": lambda item: item[0],
            "copies": lambda item: len(item[1]),
            "size": lambda item: item[1][0].size,
            "wasted": lambda item: item[1][0].size * (len(item[1]) - 1),
            "modified": lambda item: max(r.mtime for r in item[1]),
        }
        column, reverse = self._sort["exact"]
        groups = sorted(enumerate(self.result.exact_groups), key=keys[column], reverse=reverse)
        self._exact_shown = 0
        for idx, group in groups:
            if needle and not any(needle in r.path.lower() for r in group):
                continue
            self._exact_shown += 1
            size = group[0].size
            iid = f"g{idx}"
            tree.insert(
                "",
                "end",
                iid=iid,
                text=f"Group {idx + 1}   {group[0].name}",
                values=(len(group), human_size(size), human_size(size * (len(group) - 1)), ""),
                tags=("group",),
                open=bool(needle),
            )
            self._group_by_iid[iid] = ("exact", idx)
            for j, rec in enumerate(group):
                fid = f"{iid}:{j}"
                tree.insert(
                    iid,
                    "end",
                    iid=fid,
                    text=rec.path,
                    values=("", human_size(rec.size), "", format_time(rec.mtime)),
                )
                self._file_by_iid[fid] = rec
                self._group_by_iid[fid] = ("exact", idx)

    def _fill_similar(self, needle: str) -> None:
        tree = self.similar_tree
        tree.delete(*tree.get_children())
        column, reverse = self._sort["similar"]
        pairs = list(enumerate(self.result.similar_pairs))
        if column == "distance":
            pairs.sort(key=lambda item: item[1].distance, reverse=reverse)
        elif column == "size":
            pairs.sort(key=lambda item: max(item[1].left.size, item[1].right.size), reverse=reverse)
        elif column == "modified":
            pairs.sort(
                key=lambda item: max(item[1].left.mtime, item[1].right.mtime), reverse=reverse
            )
        elif reverse:
            pairs.reverse()
        self._similar_shown = 0
        for idx, pair in pairs:
            files = (pair.left, pair.right)
            if needle and not any(needle in r.path.lower() for r in files):
                continue
            self._similar_shown += 1
            iid = f"p{idx}"
            label = "looks identical" if pair.distance == 0 else f"distance {pair.distance}"
            tree.insert(
                "",
                "end",
                iid=iid,
                text=f"Pair {idx + 1}   {pair.left.name}  ↔  {pair.right.name}   ({label})",
                values=(pair.distance, "", ""),
                tags=("group",),
                open=bool(needle),
            )
            self._group_by_iid[iid] = ("similar", idx)
            for j, rec in enumerate(files):
                fid = f"{iid}:{j}"
                tree.insert(
                    iid,
                    "end",
                    iid=fid,
                    text=rec.path,
                    values=("", human_size(rec.size), format_time(rec.mtime)),
                )
                self._file_by_iid[fid] = rec
                self._group_by_iid[fid] = ("similar", idx)

    def _update_headings(self) -> None:
        for kind, tree in (("exact", self.exact_tree), ("similar", self.similar_tree)):
            column, reverse = self._sort[kind]
            for col in ("#0", *tree["columns"]):
                text = str(tree.heading(col, "text")).rstrip(" ▲▼")
                if col == column:
                    text += " ▼" if reverse else " ▲"
                tree.heading(col, text=text)

    def _update_tab_titles(self, needle: str = "") -> None:
        def title(name: str, shown: int, total: int) -> str:
            if needle and shown != total:
                return f"{name}  ({shown:,} of {total:,})"
            return f"{name}  ({total:,})"

        total_exact = len(self.result.exact_groups)
        total_similar = len(self.result.similar_pairs)
        self.notebook.tab(
            0,
            text=title("Exact duplicates", getattr(self, "_exact_shown", total_exact), total_exact),
        )
        self.notebook.tab(
            1,
            text=title(
                "Similar photos", getattr(self, "_similar_shown", total_similar), total_similar
            ),
        )

    def _set_open(self, is_open: bool) -> None:
        tree = self._current_tree()
        for iid in tree.get_children():
            tree.item(iid, open=is_open)

    # ------------------------------------------------------- selection
    def _current_tree(self) -> ttk.Treeview:
        tab = self.notebook.index("current")
        return self.exact_tree if tab == 0 else self.similar_tree

    def _selected_iid(self) -> str | None:
        selected = self._current_tree().selection()
        return selected[0] if selected else None

    def _selected_file(self) -> FileRecord | None:
        iid = self._selected_iid()
        return self._file_by_iid.get(iid) if iid else None

    def _on_double_click(self, event: tk.Event[Any]) -> str | None:
        tree: ttk.Treeview = event.widget
        iid = tree.identify_row(event.y)
        if iid in self._file_by_iid:
            self._open_selected()
            return "break"
        return None  # default behavior expands/collapses the group

    def _on_right_click(self, event: tk.Event[Any]) -> None:
        tree: ttk.Treeview = event.widget
        iid = tree.identify_row(event.y)
        if not iid:
            return
        tree.selection_set(iid)
        tree.focus(iid)
        state = "normal" if iid in self._file_by_iid else "disabled"
        for index in (0, 1, 2):
            self.menu.entryconfigure(index, state=state)
        self.menu.tk_popup(event.x_root, event.y_root)

    def _run_file_action(self, action: Callable[[str], None]) -> None:
        rec = self._selected_file()
        if rec is None:
            return
        try:
            action(rec.path)
        except OSError as exc:
            messagebox.showerror("Could not open", f"{rec.path}\n\n{exc}")

    def _open_selected(self) -> None:
        self._run_file_action(open_path)

    def _reveal_selected(self) -> None:
        self._run_file_action(reveal_path)

    def _copy_selected(self) -> None:
        rec = self._selected_file()
        if rec is not None:
            self.root.clipboard_clear()
            self.root.clipboard_append(rec.path)
            self.detail_var.set(f"Copied: {rec.path}")

    # --------------------------------------------------------- windows
    def show_details(self) -> None:
        iid = self._selected_iid()
        if iid is None or iid not in self._group_by_iid:
            messagebox.showinfo("Select a row", "Select a group or file in the results first.")
            return
        kind, idx = self._group_by_iid[iid]
        if kind == "similar":
            self._show_pair_window(idx)
            return
        group = self.result.exact_groups[idx]
        win = self._toplevel(f"Exact duplicate group {idx + 1}", "860x460")
        tk.Label(
            win,
            text=f"{len(group)} identical copies of {human_size(group[0].size)}  ·  "
            f"{human_size(group[0].size * (len(group) - 1))} in extra copies",
            font=FONT_BOLD,
            bg=C.card,
        ).pack(anchor="w", padx=14, pady=(12, 4))
        tk.Label(
            win, text=f"SHA-256: {group[0].sha256}", font=FONT_SMALL, bg=C.card, fg=C.muted
        ).pack(anchor="w", padx=14)
        listing = tk.Frame(win, bg=C.card)
        listing.pack(fill="both", expand=True, padx=14, pady=10)
        for rec in group:
            row = tk.Frame(listing, bg=C.card)
            row.pack(fill="x", pady=3)
            ttk.Button(
                row,
                text="Show in folder",
                style="Small.Secondary.TButton",
                command=partial(reveal_path, rec.path),
            ).pack(side="right")
            tk.Label(
                row,
                text=f"{rec.path}\nModified {format_time(rec.mtime)}",
                justify="left",
                anchor="w",
                font=FONT,
                bg=C.card,
            ).pack(side="left", fill="x", expand=True)

    def _show_pair_window(self, idx: int) -> None:
        pair = self.result.similar_pairs[idx]
        win = self._toplevel(f"Similar photos: pair {idx + 1}", "900x600")
        verdict = (
            "look identical" if pair.distance == 0 else f"are similar (distance {pair.distance})"
        )
        tk.Label(
            win,
            text=f"These photos {verdict}. This is a suggestion only; compare them yourself.",
            font=FONT_BOLD,
            bg=C.card,
        ).pack(anchor="w", padx=14, pady=(12, 8))
        side = tk.Frame(win, bg=C.card)
        side.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        win.images = []  # type: ignore[attr-defined]  # keep PhotoImage references alive
        for col, rec in enumerate((pair.left, pair.right)):
            side.columnconfigure(col, weight=1, uniform="pair")
            cell = tk.Frame(side, bg=C.card)
            cell.grid(row=0, column=col, sticky="nsew", padx=6)
            photo = self._thumbnail(rec.path)
            if photo is not None:
                win.images.append(photo)  # type: ignore[attr-defined]
                tk.Label(cell, image=photo, bg=C.bg).pack(pady=(0, 8))
            else:
                tk.Label(cell, text="(preview unavailable)", bg=C.bg, fg=C.muted, height=12).pack(
                    fill="x", pady=(0, 8)
                )
            tk.Label(
                cell,
                text=f"{rec.path}\n{human_size(rec.size)}  ·  modified {format_time(rec.mtime)}",
                wraplength=400,
                justify="left",
                font=FONT_SMALL,
                bg=C.card,
            ).pack(anchor="w")
            buttons = tk.Frame(cell, bg=C.card)
            buttons.pack(anchor="w", pady=(6, 0))
            ttk.Button(
                buttons,
                text="Open",
                style="Small.Secondary.TButton",
                command=partial(open_path, rec.path),
            ).pack(side="left")
            ttk.Button(
                buttons,
                text="Show in folder",
                style="Small.Secondary.TButton",
                command=partial(reveal_path, rec.path),
            ).pack(side="left", padx=(6, 0))

    def _thumbnail(self, path: str) -> Any:
        if not IMAGE_HASH_AVAILABLE:
            return None
        try:
            from PIL import Image, ImageTk

            with Image.open(path) as im:
                im.draft("RGB", (THUMBNAIL_SIZE, THUMBNAIL_SIZE))
                rgb = im.convert("RGB")
            rgb.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE))
            return ImageTk.PhotoImage(rgb, master=self.root)
        except Exception:
            return None

    def show_errors(self) -> None:
        if not self.result.errors:
            return
        win = self._toplevel(f"Unreadable items ({len(self.result.errors):,})", "860x420")
        tk.Label(
            win,
            text="These files or folders could not be read (often system folders, locked "
            "files, or online-only cloud files). They are also listed in the CSV report.",
            font=FONT,
            bg=C.card,
            wraplength=820,
            justify="left",
        ).pack(anchor="w", padx=14, pady=(12, 6))
        box = tk.Text(win, wrap="none", font=("Consolas", 9), relief="flat", bg=C.bg)
        box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        box.insert("1.0", "\n".join(self.result.errors))
        box.config(state="disabled")

    def _toplevel(self, title: str, geometry: str) -> tk.Toplevel:
        win = tk.Toplevel(self.root, bg=C.card)
        win.title(title)
        win.geometry(geometry)
        win.transient(self.root)
        win.bind("<Escape>", lambda _e: win.destroy())
        return win

    # ----------------------------------------------------------- misc
    def export_csv(self) -> None:
        if self.scanning or not self.result.records:
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
        if messagebox.askyesno("Report saved", f"CSV report saved to:\n{path}\n\nOpen it now?"):
            try:
                open_path(path)
            except OSError as exc:
                messagebox.showerror("Could not open", str(exc))

    def _save_settings(self) -> None:
        save_settings(
            self.settings_file,
            {"last_folder": self.scan_root.get().strip(), "compare_images": self.do_images.get()},
        )

    def on_close(self) -> None:
        if self.scanning:
            if not messagebox.askyesno("Scan running", "A scan is still running. Quit anyway?"):
                return
            self.cancel_event.set()
        self._save_settings()
        self.root.destroy()


def _enable_high_dpi() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main() -> None:
    _enable_high_dpi()
    root = tk.Tk()
    DuplicateDetectorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
