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
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from functools import partial
from itertools import pairwise
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Literal

from . import __version__
from .deleter import (
    DeleteOutcome,
    apply_deletions,
    groups_losing_all_copies,
    has_recycle_bin,
    keep_one_per_group,
    move_to_trash,
)
from .report import default_report_name, write_csv
from .scanner import (
    IMAGE_HASH_AVAILABLE,
    FileRecord,
    Progress,
    ScanCancelled,
    ScanResult,
    SimilarPair,
    human_size,
    normalize_roots,
    scan,
    spans_folders,
)

APP_TITLE = "Local Duplicate Detector"
POLL_MS = 100
TICK_MS = 500
FILTER_DELAY_MS = 250
#: When filtering, matching groups are expanded automatically only up to this many.
AUTO_EXPAND_LIMIT = 200
THUMBNAIL_SIZE = 360
#: Identical copies of each checkbox image per tree (see DuplicateDetectorApp.__init__).
CHECK_IMAGE_COPIES = 64
PLACEHOLDER = "_"
#: How many paths the delete confirmation lists before summarizing.
CONFIRM_LIST_LIMIT = 200

#: Opening these runs code, so the app asks first instead of opening on double-click.
RISKY_EXTENSIONS = frozenset(
    {
        ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
        ".wsf", ".wsh", ".msi", ".msp", ".scr", ".pif", ".lnk", ".url", ".reg", ".hta",
        ".cpl", ".jar", ".py", ".pyw", ".sh", ".app", ".command",
    }
)  # fmt: skip

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 16)
FONT_STAT = ("Segoe UI Semibold", 15)

CheckState = Literal["off", "on", "partial"]
DeleteChoice = Literal["delete", "keep_one", "cancel"]


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


def is_risky(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in RISKY_EXTENSIONS


def open_path(path: str) -> None:
    """Open a file with its default application."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"File no longer exists: {path}")
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_path(path: str) -> None:
    """Show a file selected in the system file manager."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"File no longer exists: {path}")
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


def load_thumbnail(path: str, size: int) -> Any:
    """Decode a small RGB preview (a PIL image), or None. Safe to call off the Tk thread."""
    if not IMAGE_HASH_AVAILABLE:
        return None
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.draft("RGB", (size, size))
            rgb = im.convert("RGB")
        rgb.thumbnail((size, size))
        return rgb
    except Exception:
        return None


def make_checkbox(master: tk.Misc, size: int, state: CheckState) -> tk.PhotoImage:
    """Draw a checkbox image (no image files needed)."""
    img = tk.PhotoImage(master=master, width=size, height=size)
    border = C.neutral_pressed if state == "off" else C.primary
    fill = "#ffffff" if state == "off" else C.primary
    img.put(border, to=(0, 0, size, size))
    img.put(fill, to=(1, 1, size - 1, size - 1))
    thick = max(2, size // 7)
    if state == "partial":
        mid = size // 2 - thick // 2
        img.put("#ffffff", to=(3, mid, size - 3, mid + thick))
    elif state == "on":
        # A tick: down-right from the left, then up-right to the top corner.
        points = [(0.22, 0.52), (0.42, 0.72), (0.80, 0.30)]
        for (x0, y0), (x1, y1) in pairwise(points):
            steps = size * 2
            for i in range(steps + 1):
                x = int((x0 + (x1 - x0) * i / steps) * size)
                y = int((y0 + (y1 - y0) * i / steps) * size)
                img.put("#ffffff", to=(x, y, min(x + thick, size - 1), min(y + thick, size - 1)))
    return img


def _group_key(group: Sequence[FileRecord]) -> str:
    return group[0].sha256 or group[0].path


def _pair_key(pair: SimilarPair) -> tuple[str, str]:
    return (pair.left.path, pair.right.path)


GroupKey = Callable[[tuple[int, list[FileRecord]]], Any]
PairKey = Callable[[tuple[int, SimilarPair]], Any]

EXACT_SORT_KEYS: dict[str, GroupKey] = {
    "#0": lambda item: item[0],
    "copies": lambda item: len(item[1]),
    "size": lambda item: item[1][0].size,
    "wasted": lambda item: item[1][0].size * (len(item[1]) - 1),
    "modified": lambda item: max(r.mtime for r in item[1]),
    "found": lambda item: (len({r.source for r in item[1]}), sorted({r.source for r in item[1]})),
}
SIMILAR_SORT_KEYS: dict[str, PairKey] = {
    "#0": lambda item: item[0],
    "distance": lambda item: item[1].distance,
    "size": lambda item: max(item[1].left.size, item[1].right.size),
    "modified": lambda item: max(item[1].left.mtime, item[1].right.mtime),
    "found": lambda item: sorted({item[1].left.source, item[1].right.source}),
}


# ------------------------------------------------------------------- app
class DuplicateDetectorApp:
    def __init__(self, root: tk.Tk, settings_file: Path | None = None) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} {__version__}")
        # Pixel sizes are designed for 96 DPI; scale them on high-DPI screens.
        self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96)
        screen_w = root.winfo_screenwidth() / self.scale
        screen_h = root.winfo_screenheight() / self.scale
        width, height = min(1240, int(screen_w * 0.85)), min(820, int(screen_h * 0.85))
        self.root.geometry(f"{self.px(width)}x{self.px(height)}")
        self.root.minsize(self.px(860), self.px(560))
        if sys.platform == "win32" and screen_h < 900:
            self.root.state("zoomed")  # small screens: use all the space for results

        self.settings_file = settings_file or default_settings_file()
        self.log_file = self.settings_file.parent / "deletion_log.csv"
        settings = load_settings(self.settings_file)

        self.events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.result = ScanResult()
        self.scanning = False
        self.deleting = False
        self._scan_compare_images = False
        self._scan_started: float | None = None
        self._tick_job: str | None = None
        self._filter_job: str | None = None

        # Rows of the result trees. File rows are created when their group opens.
        self._file_by_iid: dict[str, FileRecord] = {}
        self._group_by_iid: dict[str, tuple[str, int]] = {}
        self._group_files: dict[str, Sequence[FileRecord]] = {}
        self._file_iids_by_path: defaultdict[str, list[str]] = defaultdict(list)
        self._group_iids_by_path: defaultdict[str, list[str]] = defaultdict(list)
        self._exact_shown = 0
        self._similar_shown = 0
        self._sort: dict[str, tuple[str, bool]] = {"exact": ("#0", False), "similar": ("#0", False)}

        #: Paths the user ticked for deletion.
        self.selected: set[str] = set()
        self._record_by_path: dict[str, FileRecord] = {}
        # Stable numbers for groups, pairs and files, so row ids (and the user's
        # expanded groups and highlighted row) survive deletions that reshuffle lists.
        self._group_num: dict[str, int] = {}
        self._pair_num: dict[tuple[str, str], int] = {}
        self._file_num: dict[str, int] = {}

        default_root = "E:\\" if os.name == "nt" else os.path.expanduser("~")
        saved = settings.get("folders")
        if isinstance(saved, list) and all(isinstance(x, str) for x in saved):
            folders = saved  # may be empty: the user removed every folder
        else:
            folders = [settings.get("last_folder") or default_root]
        #: Folders to scan together (each with all its subfolders).
        self.folders: list[str] = []
        for folder in folders:
            self._add_unique(folder)
        self._skipped_roots: list[tuple[str, str]] = []
        #: Text box for typing a folder to add.
        self.scan_root = tk.StringVar(value="")
        #: Only show duplicates found in more than one of the chosen folders.
        self.across_only = tk.BooleanVar(value=False)
        self._root_labels: dict[str, str] = {}
        self.do_images = tk.BooleanVar(
            value=bool(settings.get("compare_images")) and IMAGE_HASH_AVAILABLE
        )
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._schedule_filter())

        # Tk keeps a list of every row that shows an image, and removing a row from
        # that list (deleting the row or changing its checkbox) walks it. With one
        # shared image per state, large scans (100,000+ rows) froze the window for
        # minutes. Each tree therefore gets its own pool of identical copies, handed
        # out in turn, so every list stays short.
        box = self.px(14)
        self._check_images: dict[str, dict[CheckState, list[tk.PhotoImage]]] = {}
        for kind in ("exact", "similar"):
            self._check_images[kind] = {}
            for state in ("off", "on", "partial"):
                first = make_checkbox(self.root, box, state)
                pool = [first] + [first.copy() for _ in range(CHECK_IMAGE_COPIES - 1)]
                self._check_images[kind][state] = pool
        self._check_turn = 0

        self._setup_styles()
        self._build_ui()
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(POLL_MS, self._process_events)

    def _check_image(self, tree: ttk.Treeview, state: CheckState) -> tk.PhotoImage:
        """A checkbox image for a row of ``tree`` (see the pool set up in ``__init__``)."""
        pool = self._check_images["exact" if tree is self.exact_tree else "similar"][state]
        self._check_turn += 1
        return pool[self._check_turn % len(pool)]

    def px(self, value: float) -> int:
        return round(value * self.scale)

    @property
    def busy(self) -> bool:
        return self.scanning or self.deleting

    # ---------------------------------------------------------- styles
    def _setup_styles(self) -> None:
        s = ttk.Style(self.root)
        s.theme_use("clam")
        self.root.configure(background=C.bg)
        s.configure(".", background=C.bg, foreground=C.text, font=FONT)
        s.configure("TFrame", background=C.bg)
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
                thickness=self.px(10),
            )

        s.configure(
            "Treeview",
            background=C.card,
            fieldbackground=C.card,
            foreground=C.text,
            rowheight=self.px(26),
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
            "TNotebook.Tab", background=[("selected", C.card)], foreground=[("selected", C.primary)]
        )

    def _card(self, parent: tk.Misc, title: str = "") -> tuple[tk.Frame, tk.Frame]:
        """Return (outer, inner) frames of a white bordered card."""
        outer = tk.Frame(
            parent, bg=C.card, highlightthickness=1, highlightbackground=C.border, bd=0
        )
        if title:
            tk.Label(outer, text=title, font=FONT_BOLD, bg=C.card, fg=C.text).pack(
                anchor="w", padx=14, pady=(10, 0)
            )
        inner = tk.Frame(outer, bg=C.card)
        inner.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        return outer, inner

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
        header.columnconfigure(1, weight=1)
        tk.Label(header, text=APP_TITLE, font=FONT_TITLE, bg=C.header_bg, fg=C.header_fg).grid(
            row=0, column=0, sticky="w", padx=(20, 16), pady=8
        )
        tk.Label(
            header,
            text="Nothing is deleted unless you select files and click Delete; "
            "they then go to the Recycle Bin.",
            font=FONT_SMALL,
            bg=C.header_bg,
            fg=C.header_sub,
        ).grid(row=0, column=1, sticky="w")
        tk.Label(
            header, text=f"v{__version__}", font=FONT_SMALL, bg=C.header_bg, fg=C.header_sub
        ).grid(row=0, column=2, sticky="e", padx=20)

    def _build_scan_card(self, body: ttk.Frame) -> None:
        card, f = self._card(body, "1   Choose folders to scan (all subfolders are included)")
        card.pack(fill="x")
        f.columnconfigure(0, weight=1)

        self.location_entry = ttk.Entry(f, textvariable=self.scan_root, font=FONT)
        self.location_entry.grid(row=0, column=0, sticky="ew", ipady=2)
        ttk.Button(f, text="+ Add", style="Secondary.TButton", command=self._add_typed).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(f, text="Browse…", style="Secondary.TButton", command=self._browse).grid(
            row=0, column=2, padx=(8, 0)
        )
        self.chips = tk.Frame(f, bg=C.card)
        self.chips.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._render_chips()

        options = tk.Frame(f, bg=C.card)
        options.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        image_text = "Also compare photos for visual similarity (slower)"
        if not IMAGE_HASH_AVAILABLE:
            image_text += "  (install Pillow + ImageHash to enable)"
        self.images_check = ttk.Checkbutton(
            options, text=image_text, variable=self.do_images, style="Card.TCheckbutton"
        )
        self.images_check.pack(side="left")
        if not IMAGE_HASH_AVAILABLE:
            self.images_check.state(["disabled"])
        self.across_check = ttk.Checkbutton(
            options,
            text="Only show duplicates found in more than one of these folders",
            variable=self.across_only,
            style="Card.TCheckbutton",
            command=self._on_across_toggled,
        )
        self.across_check.pack(side="left", padx=(24, 0))
        self._update_across_check()

        # Buttons on the left, live status beside them (saves vertical space).
        actions = tk.Frame(f, bg=C.card)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
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
            value="Choose a folder or drive, then press Scan (or Enter)."
        )
        self.stage_label = tk.Label(
            status, textvariable=self.stage_var, font=FONT_BOLD, bg=C.card, fg=C.text, anchor="w"
        )
        self.stage_label.pack(fill="x")
        tk.Label(
            status, textvariable=self.detail_var, font=FONT_SMALL, bg=C.card, fg=C.muted, anchor="w"
        ).pack(fill="x")
        self.progress = ttk.Progressbar(f, mode="determinate", style="Scan.Horizontal.TProgressbar")
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))

    def _build_stats(self, body: ttk.Frame) -> None:
        row = ttk.Frame(body)
        row.pack(fill="x", pady=6)
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
            value.pack(anchor="w", padx=14, pady=(4, 0))
            label = tk.Label(card, text=caption, font=FONT_SMALL, bg=C.card, fg=C.muted)
            label.pack(anchor="w", padx=14, pady=(0, 4))
            self.stat_vars[key] = var
            self.stat_labels[key] = value
            if key == "errors":
                for w in (card, value, label):
                    w.bind("<Button-1>", lambda _e: self.show_errors())
                self.errors_card = card

    def _build_results(self, body: ttk.Frame) -> None:
        card, f = self._card(body)
        card.pack(fill="both", expand=True)

        bar = tk.Frame(f, bg=C.card)
        bar.pack(fill="x", pady=(4, 6))
        tk.Label(bar, text="2   Results", font=FONT_BOLD, bg=C.card, fg=C.text).pack(side="left")
        # Selection: the user picks files; nothing is ever selected automatically.
        self.select_all_btn = ttk.Button(
            bar, text="Select all", style="Small.Secondary.TButton", command=self.select_all
        )
        self.select_all_btn.pack(side="left", padx=(16, 0))
        self.clear_btn = ttk.Button(
            bar,
            text="Clear selection",
            style="Small.Secondary.TButton",
            command=self.clear_selection,
        )
        self.clear_btn.pack(side="left", padx=(6, 0))
        self.select_in_btn = ttk.Menubutton(
            bar, text="Select copies in ▾", style="Small.Secondary.TButton"
        )
        self.select_in_menu = tk.Menu(self.select_in_btn, tearoff=False)
        self.select_in_btn["menu"] = self.select_in_menu
        self.select_in_btn.pack(side="left", padx=(6, 0))
        self.select_in_btn.state(["disabled"])
        self.selection_var = tk.StringVar(value="No files selected")
        self.selection_label = tk.Label(
            bar, textvariable=self.selection_var, font=FONT, bg=C.card, fg=C.muted
        )
        self.selection_label.pack(side="left", padx=(12, 0))
        for b in (self.select_all_btn, self.clear_btn):
            b.state(["disabled"])
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
        tk.Label(bar, text="Filter (Ctrl+F)", font=FONT_SMALL, bg=C.card, fg=C.muted).pack(
            side="right", padx=(0, 6)
        )

        tk.Label(
            f,
            text="Click a box (or press Space) to select  ·  Double-click a file to open it  ·  "
            "Right-click for Show in folder / Copy path  ·  Click a column heading to sort",
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
                ("found", "Found in", 170),
                ("modified", "Modified", 140),
            ],
        )
        self.similar_tree = self._make_tree(
            "similar",
            [
                ("#0", "Pair / file", 600),
                ("distance", "Distance", 90),
                ("size", "File size", 100),
                ("found", "Found in", 170),
                ("modified", "Modified", 140),
            ],
        )
        self._update_tab_titles()

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Select / unselect", command=self._toggle_focused)
        self.menu.add_separator()
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
            tree.column(col, width=self.px(width), anchor="w", stretch=col == "#0")
        tree.tag_configure("group", background=C.group_row, font=FONT_BOLD)
        tree.tag_configure("checked", foreground=C.danger)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.bind("<<TreeviewOpen>>", lambda _e: self._populate(tree, tree.focus()))
        tree.bind("<Button-1>", self._on_click)
        tree.bind("<space>", partial(self._on_space, tree))
        tree.bind("<Double-1>", self._on_double_click)
        tree.bind("<Return>", lambda _e: self.show_details())
        tree.bind("<Delete>", lambda _e: self.delete_selected())
        tree.bind("<Button-3>", self._on_right_click)
        if sys.platform == "darwin":
            tree.bind("<Button-2>", self._on_right_click)
        return tree

    def _build_footer(self, body: ttk.Frame) -> None:
        bottom = ttk.Frame(body)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self.delete_btn = ttk.Button(
            bottom, text="Delete selected…", style="Danger.TButton", command=self.delete_selected
        )
        self.delete_btn.pack(side="left")
        self.delete_btn.state(["disabled"])
        self.export_btn = ttk.Button(
            bottom, text="Export report (CSV)", style="Success.TButton", command=self.export_csv
        )
        self.export_btn.pack(side="left", padx=(8, 0))
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
            text="✔  Deleted files go to the Recycle Bin and can be restored.",
            font=FONT,
            bg=C.bg,
            fg=C.success_pressed,
        ).pack(side="right")

    def _bind_keys(self) -> None:
        self.location_entry.bind("<Return>", lambda _e: self._on_entry_return())
        self.root.bind("<Escape>", lambda _e: self.cancel_scan())
        self.root.bind("<Control-f>", lambda _e: self.filter_entry.focus_set())
        self.root.bind("<Control-e>", lambda _e: self.export_csv())

    def _browse(self) -> None:
        last = self.folders[-1] if self.folders else ""
        path = filedialog.askdirectory(initialdir=last if os.path.isdir(last) else None)
        if path:
            self.add_folder(path)

    def _on_entry_return(self) -> None:
        # Enter adds a typed folder; on an empty box it starts the scan.
        if self.scan_root.get().strip():
            self._add_typed()
        else:
            self.start_scan()

    def _add_typed(self) -> None:
        text = self.scan_root.get().strip().strip('"')
        if not text:
            return
        if not os.path.isdir(text):
            messagebox.showerror("Folder not found", f"This folder does not exist:\n{text}")
            return
        self.add_folder(text)
        self.scan_root.set("")

    def _add_unique(self, path: str) -> None:
        path = os.path.normpath(os.path.abspath(path))
        if all(os.path.normcase(path) != os.path.normcase(f) for f in self.folders):
            self.folders.append(path)

    def add_folder(self, path: str) -> None:
        self._add_unique(path)
        self._render_chips()
        self._update_across_check()

    def remove_folder(self, path: str) -> None:
        if self.busy:
            return
        self.folders = [f for f in self.folders if f != path]
        self._render_chips()
        self._update_across_check()

    def _render_chips(self) -> None:
        for child in self.chips.winfo_children():
            child.destroy()
        if not self.folders:
            tk.Label(
                self.chips,
                text="No folders yet: type a path above and press Enter, or click Browse…",
                font=FONT_SMALL,
                bg=C.card,
                fg=C.muted,
            ).pack(side="left")
            return
        roots, _ = normalize_roots(self.folders)
        kept = {os.path.normcase(r) for r in roots}
        for path in self.folders:
            nested = os.path.normcase(os.path.abspath(path)) not in kept
            chip = tk.Frame(
                self.chips,
                bg=C.group_row,
                highlightthickness=1,
                highlightbackground=C.neutral_hover,
            )
            chip.pack(side="left", padx=(0, 6))
            text = path if len(path) <= 48 else "…" + path[-47:]
            if nested:
                text += "  (already covered by another folder)"
            tk.Label(
                chip, text=text, font=FONT_SMALL, bg=C.group_row, fg=C.muted if nested else C.text
            ).pack(side="left", padx=(8, 2), pady=2)
            close = tk.Label(
                chip, text="✕", font=FONT_SMALL, bg=C.group_row, fg=C.muted, cursor="hand2"
            )
            close.pack(side="left", padx=(2, 6))
            close.bind("<Button-1>", partial(self._on_chip_close, path))

    def _on_chip_close(self, path: str, _event: tk.Event[Any]) -> None:
        self.remove_folder(path)

    def _update_across_check(self) -> None:
        if len(normalize_roots(self.folders)[0]) > 1 or len(self.result.roots) > 1:
            self.across_check.state(["!disabled"])
        else:
            self.across_only.set(False)
            self.across_check.state(["disabled"])

    def _on_across_toggled(self) -> None:
        hidden = self._untick_hidden()
        self._refresh_results()
        self._update_stats()
        if hidden:
            self.detail_var.set(
                f"Unticked {hidden:,} selected files that this option hides, so nothing you "
                "can't see gets deleted."
            )

    def _visible_paths(self) -> set[str]:
        view = self._view()
        return {r.path for g in view.exact_groups for r in g} | {
            r.path for p in view.similar_pairs for r in (p.left, p.right)
        }

    def _untick_hidden(self) -> int:
        """Unselect files that are no longer listed; returns how many were unselected."""
        hidden = self.selected - self._visible_paths()
        if hidden:
            self.set_selected(list(hidden), False)
        return len(hidden)

    def _across(self) -> bool:
        return self.across_only.get() and len(self.result.roots) > 1

    def _root_label(self, root: str) -> str:
        return self._root_labels.get(root, root)

    def _make_root_labels(self, roots: Sequence[str]) -> dict[str, str]:
        """Short names for scan folders (folder name, or full path when names clash)."""
        names = {r: os.path.basename(r.rstrip("\\/")) or r for r in roots}
        counts: defaultdict[str, int] = defaultdict(int)
        for name in names.values():
            counts[name.lower()] += 1
        return {r: (n if counts[n.lower()] == 1 else r) for r, n in names.items()}

    def _found_in(self, files: Iterable[FileRecord]) -> str:
        return ", ".join(sorted({self._root_label(r.source) for r in files}, key=str.lower))

    # ------------------------------------------------------------ scan
    def start_scan(self) -> None:
        if self.busy:
            return
        if self.scan_root.get().strip():
            self._add_typed()  # a path typed but not yet added
            if self.scan_root.get().strip():
                return  # it was invalid; the error was shown
        if not self.folders:
            messagebox.showwarning("Folder required", "Add at least one folder or drive to scan.")
            return
        missing = [f for f in self.folders if not os.path.isdir(f)]
        if missing:
            messagebox.showerror(
                "Folder not found",
                "These folders do not exist or cannot be accessed:\n\n" + "\n".join(missing),
            )
            return
        folders, self._skipped_roots = normalize_roots(self.folders)
        self._save_settings()
        self.scanning = True
        self._scan_compare_images = self.do_images.get()
        self.cancel_event.clear()
        self.result = ScanResult()
        self._record_by_path = {}
        self.selected.clear()
        self._refresh_results()
        for var in self.stat_vars.values():
            var.set("–")
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
            target=self._scan_worker, args=(folders, self._scan_compare_images), daemon=True
        ).start()

    def cancel_scan(self) -> None:
        if not self.scanning:
            return
        self.cancel_event.set()
        self.cancel_btn.state(["disabled"])
        self._set_stage("Cancelling…", C.warn)

    def _scan_worker(self, folders: list[str], compare_images: bool) -> None:
        try:
            result = scan(
                folders,
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
        if self._tick_job is not None:
            self.root.after_cancel(self._tick_job)
            self._tick_job = None
        if self._scan_started is None:
            return
        self.elapsed_var.set(f"⏱  {format_elapsed(time.monotonic() - self._scan_started)}")
        if self.scanning:
            self._tick_job = self.root.after(TICK_MS, self._tick)

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
                elif kind == "thumbnail":
                    self._show_thumbnail(*payload)
                elif kind == "delete_progress":
                    done, total = payload
                    self.progress.config(maximum=max(total, 1), value=done)
                    self.detail_var.set(f"Moved {done:,} of {total:,} files…")
                elif kind == "deleted":
                    self._finish_delete(payload[0])
                elif kind == "delete_failed":
                    self._end_delete()
                    self._set_stage("Delete failed", C.danger)
                    messagebox.showerror("Delete failed", payload[0])
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._process_events)

    def _show_progress(self, p: Progress) -> None:
        if self.cancel_event.is_set():
            return  # keep showing "Cancelling…" while queued updates drain
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
        self._tick()  # final elapsed time; cancels the pending tick
        self._scan_started = None

    def _show_results(self, result: ScanResult) -> None:
        self.result = result
        self._record_by_path = {r.path: r for r in result.records}
        self._file_num = {r.path: n for n, r in enumerate(result.records)}
        self._group_num = {_group_key(g): n for n, g in enumerate(result.exact_groups, 1)}
        self._pair_num = {_pair_key(p): n for n, p in enumerate(result.similar_pairs, 1)}
        self._root_labels = self._make_root_labels(result.roots)
        self.selected.clear()
        self._update_across_check()
        self._build_select_in_menu()
        self._scan_ended("Done.Horizontal.TProgressbar")
        self._refresh_results()
        self._update_stats()

        if result.exact_groups or result.similar_pairs:
            self._set_stage("✔  Scan complete: duplicates found", C.success)
            detail = (
                f"{len(result.exact_groups):,} groups use {human_size(result.wasted_bytes)} in "
                "extra copies. Tick the files you want to delete."
            )
        else:
            self._set_stage("✔  Scan complete: no duplicates found", C.success)
            detail = f"All {len(result.records):,} files are unique."
        if len(result.roots) > 1:
            across = len(result.across_folders().exact_groups)
            detail += f"  {across:,} groups have copies in more than one folder."
        for inner, outer in self._skipped_roots:
            detail += f"  {inner} is inside (or the same as) {outer}, so it was scanned once."
        if result.errors:
            detail += f"  {len(result.errors):,} items could not be read (see View errors)."
        self.detail_var.set(detail)
        self.export_btn.state(["!disabled"] if result.records else ["disabled"])
        self.details_btn.state(["!disabled"])
        self.errors_btn.state(["!disabled"] if result.errors else ["disabled"])
        if not result.exact_groups and result.similar_pairs:
            self.notebook.select(1)
        self.root.bell()

    def _view(self) -> ScanResult:
        """The results as shown: limited to cross-folder duplicates when that option is on."""
        return self.result.across_folders() if self._across() else self.result

    def _update_stats(self) -> None:
        result = self._view()
        stats = {
            "files": f"{len(result.records):,}",
            "groups": f"{len(result.exact_groups):,}",
            "wasted": human_size(result.wasted_bytes),
            "similar": f"{len(result.similar_pairs):,}" if self._scan_compare_images else "off",
            "errors": f"{len(result.errors):,}",
        }
        for key, value in stats.items():
            self.stat_vars[key].set(value)
        self.stat_labels["errors"].config(fg=C.danger if result.errors else C.muted)
        self.errors_card.config(cursor="hand2" if result.errors else "")

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
        """Rebuild the group rows. File rows are only created when a group is opened."""
        self._filter_job = None
        needle = self.filter_var.get().strip().lower()
        trees = (("exact", self.exact_tree), ("similar", self.similar_tree))
        state = {
            kind: (
                {iid for iid in tree.get_children() if tree.item(iid, "open")},
                tree.selection(),
            )
            for kind, tree in trees
        }
        for _, tree in trees:
            tree.delete(*tree.get_children())
        for d in (self._file_by_iid, self._group_by_iid, self._group_files):
            d.clear()
        self._file_iids_by_path.clear()
        self._group_iids_by_path.clear()

        for kind, tree in trees:
            was_open, selected = state[kind]
            shown = self._fill_exact(needle) if kind == "exact" else self._fill_similar(needle)
            auto_open = bool(needle) and len(shown) <= AUTO_EXPAND_LIMIT
            for iid in shown:
                if iid in was_open or auto_open:
                    self._populate(tree, iid)
                    tree.item(iid, open=True)
            if selected and tree.exists(selected[0]):
                tree.selection_set(selected[0])
                tree.see(selected[0])
        self._update_headings()
        self._update_tab_titles(needle)
        self._update_selection_summary()

    def _add_group(
        self, tree: ttk.Treeview, kind: str, idx: int, iid: str, text: str, values: tuple[Any, ...]
    ) -> None:
        files = (
            self.result.exact_groups[idx]
            if kind == "exact"
            else (self.result.similar_pairs[idx].left, self.result.similar_pairs[idx].right)
        )
        tree.insert(
            "",
            "end",
            iid=iid,
            text=text,
            values=values,
            image=self._check_image(tree, self._group_state(files)),
            tags=("group",),
        )
        tree.insert(iid, "end", iid=f"{iid}:{PLACEHOLDER}", text="…")
        self._group_by_iid[iid] = (kind, idx)
        self._group_files[iid] = files
        for rec in files:
            self._group_iids_by_path[rec.path].append(iid)

    def _fill_exact(self, needle: str) -> list[str]:
        column, reverse = self._sort["exact"]
        groups = sorted(
            enumerate(self.result.exact_groups), key=EXACT_SORT_KEYS[column], reverse=reverse
        )
        shown: list[str] = []
        across = self._across()
        for idx, group in groups:
            if needle and not any(needle in r.path.lower() for r in group):
                continue
            if across and not spans_folders(group):
                continue
            size = group[0].size
            num = self._group_num.get(_group_key(group), idx + 1)
            iid = f"g{num}"
            self._add_group(
                self.exact_tree,
                "exact",
                idx,
                iid,
                f" Group {num}   {group[0].name}",
                (
                    len(group),
                    human_size(size),
                    human_size(size * (len(group) - 1)),
                    self._found_in(group),
                    "",
                ),
            )
            shown.append(iid)
        self._exact_shown = len(shown)
        return shown

    def _fill_similar(self, needle: str) -> list[str]:
        column, reverse = self._sort["similar"]
        pairs = sorted(
            enumerate(self.result.similar_pairs), key=SIMILAR_SORT_KEYS[column], reverse=reverse
        )
        shown: list[str] = []
        across = self._across()
        for idx, pair in pairs:
            if needle and not any(needle in r.path.lower() for r in (pair.left, pair.right)):
                continue
            if across and not spans_folders((pair.left, pair.right)):
                continue
            num = self._pair_num.get(_pair_key(pair), idx + 1)
            iid = f"p{num}"
            label = "looks identical" if pair.distance == 0 else f"distance {pair.distance}"
            self._add_group(
                self.similar_tree,
                "similar",
                idx,
                iid,
                f" Pair {num}   {pair.left.name}  ↔  {pair.right.name}   ({label})",
                (pair.distance, "", self._found_in((pair.left, pair.right)), ""),
            )
            shown.append(iid)
        self._similar_shown = len(shown)
        return shown

    def _populate(self, tree: ttk.Treeview, iid: str) -> None:
        """Create the file rows of a group the first time it is opened."""
        placeholder = f"{iid}:{PLACEHOLDER}"
        if not iid or not tree.exists(placeholder):
            return
        tree.delete(placeholder)
        kind, idx = self._group_by_iid[iid]
        for j, rec in enumerate(self._group_files[iid]):
            fid = f"{iid}:f{self._file_num.get(rec.path, j)}"
            values: tuple[str, ...]
            found = self._root_label(rec.source)
            if kind == "exact":
                values = ("", human_size(rec.size), "", found, format_time(rec.mtime))
            else:
                values = ("", human_size(rec.size), found, format_time(rec.mtime))
            checked = rec.path in self.selected
            tree.insert(
                iid,
                "end",
                iid=fid,
                text=f" {rec.path}",
                values=values,
                image=self._check_image(tree, "on" if checked else "off"),
                tags=("checked",) if checked else (),
            )
            self._file_by_iid[fid] = rec
            self._group_by_iid[fid] = (kind, idx)
            self._file_iids_by_path[rec.path].append(fid)

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
            if (needle or self._across()) and shown != total:
                return f"{name}  ({shown:,} of {total:,})"
            return f"{name}  ({total:,})"

        self.notebook.tab(
            0, text=title("Exact duplicates", self._exact_shown, len(self.result.exact_groups))
        )
        self.notebook.tab(
            1, text=title("Similar photos", self._similar_shown, len(self.result.similar_pairs))
        )

    def _set_open(self, is_open: bool) -> None:
        tree = self._current_tree()
        for iid in tree.get_children():
            if is_open:
                self._populate(tree, iid)
            tree.item(iid, open=is_open)

    # ------------------------------------------------------- checkboxes
    def _group_state(self, files: Sequence[FileRecord]) -> CheckState:
        count = sum(1 for r in files if r.path in self.selected)
        if count == 0:
            return "off"
        return "on" if count == len(files) else "partial"

    def set_selected(self, paths: Iterable[str], selected: bool) -> None:
        """Tick or untick files, then redraw only the rows that show them."""
        changed = []
        for path in paths:
            if selected and path not in self.selected:
                self.selected.add(path)
                changed.append(path)
            elif not selected and path in self.selected:
                self.selected.discard(path)
                changed.append(path)
        self._redraw_checks(changed)
        self._update_selection_summary()

    def _redraw_checks(self, paths: Iterable[str]) -> None:
        groups: set[str] = set()
        for path in paths:
            checked = path in self.selected
            for fid in self._file_iids_by_path.get(path, ()):
                tree = self.exact_tree if fid.startswith("g") else self.similar_tree
                tree.item(
                    fid,
                    image=self._check_image(tree, "on" if checked else "off"),
                    tags=("checked",) if checked else (),
                )
            groups.update(self._group_iids_by_path.get(path, ()))
        for gid in groups:
            tree = self.exact_tree if gid.startswith("g") else self.similar_tree
            state = self._group_state(self._group_files[gid])
            tree.item(gid, image=self._check_image(tree, state))

    def toggle(self, iid: str) -> None:
        """Toggle a file row, or every file in a group row."""
        if self.busy:
            return
        rec = self._file_by_iid.get(iid)
        if rec is not None:
            self.set_selected([rec.path], rec.path not in self.selected)
            return
        files = self._group_files.get(iid)
        if files:
            all_on = self._group_state(files) == "on"
            self.set_selected([r.path for r in files], not all_on)

    def _toggle_focused(self) -> None:
        iid = self._selected_iid()
        if iid:
            self.toggle(iid)

    def _on_space(self, tree: ttk.Treeview, _event: tk.Event[Any]) -> str:
        iid = tree.focus()
        if iid:
            tree.selection_set(iid)
            self.toggle(iid)
        return "break"  # otherwise ttk also expands/collapses the row

    def select_all(self) -> None:
        """Select every file shown in the current tab. With a filter, only matching files."""
        tree = self._current_tree()
        needle = self.filter_var.get().strip().lower()
        paths = [
            r.path
            for gid in tree.get_children()
            for r in self._group_files.get(gid, ())
            if not needle or needle in r.path.lower()
        ]
        self.set_selected(paths, True)

    def select_copies_in(self, root: str) -> None:
        """Select files in ``root`` that also have a copy in another chosen folder.

        Only exact duplicates count. A group is skipped unless at least one copy
        outside ``root`` is still unselected, so (even after earlier selections)
        this never leaves a group with every copy selected. With filter text,
        only matching files are selected, like Select all.
        """
        needle = self.filter_var.get().strip().lower()
        paths: list[str] = []
        for group in self._view().exact_groups:
            inside = [r for r in group if r.source == root]
            outside = [r for r in group if r.source != root]
            if not inside or not outside:
                continue
            if all(r.path in self.selected for r in outside):
                continue  # selecting these would leave no copy
            paths.extend(r.path for r in inside if not needle or needle in r.path.lower())
        self.set_selected(paths, True)
        self.notebook.select(0)
        if not paths:
            self.detail_var.set(
                f"Nothing in {self._root_label(root)} also has a copy in another chosen folder."
            )

    def _build_select_in_menu(self) -> None:
        self.select_in_menu.delete(0, "end")
        for root in self.result.roots:
            self.select_in_menu.add_command(
                label=f"{self._root_label(root)}   ({root})",
                command=partial(self.select_copies_in, root),
            )

    def clear_selection(self) -> None:
        self.set_selected(list(self.selected), False)

    def _update_selection_summary(self) -> None:
        count = len(self.selected)
        has_results = bool(self.result.exact_groups or self.result.similar_pairs)
        if count:
            size = sum(self._record_by_path[p].size for p in self.selected)
            self.selection_var.set(f"{count:,} files selected  ·  {human_size(size)}")
            self.selection_label.config(fg=C.danger)
        else:
            self.selection_var.set("No files selected")
            self.selection_label.config(fg=C.muted)
        self.select_all_btn.state(["!disabled"] if has_results and not self.busy else ["disabled"])
        multi = has_results and len(self.result.roots) > 1 and not self.busy
        self.select_in_btn.state(["!disabled"] if multi else ["disabled"])
        self.clear_btn.state(["!disabled"] if count and not self.busy else ["disabled"])
        self.delete_btn.state(["!disabled"] if count and not self.busy else ["disabled"])
        self.delete_btn.config(
            text=f"Delete selected ({count:,})…" if count else "Delete selected…"
        )

    # ---------------------------------------------------------- delete
    def delete_selected(self) -> None:
        if self.busy or not self.selected:
            return
        groups = self._all_groups()
        at_risk = groups_losing_all_copies(groups, self.selected)
        records = self._selected_records()
        choice = self._confirm_delete(records, at_risk)
        if choice == "cancel":
            return
        if choice == "keep_one":
            kept = keep_one_per_group(groups, self.selected)
            self._redraw_checks(r.path for r in kept)
            self._update_selection_summary()
            records = self._selected_records()
            if not records:
                return
        self.deleting = True
        self._update_selection_summary()
        self.scan_btn.state(["disabled"])
        self.progress.stop()
        self.progress.config(
            mode="determinate",
            style="Scan.Horizontal.TProgressbar",
            maximum=len(records),
            value=0,
        )
        self._set_stage("Moving files to the Recycle Bin…", C.danger)
        threading.Thread(
            target=self._delete_worker, args=(records, [list(g) for g in groups]), daemon=True
        ).start()

    def _all_groups(self) -> list[Sequence[FileRecord]]:
        """Exact duplicate groups plus similar-photo pairs."""
        return [
            *self.result.exact_groups,
            *((p.left, p.right) for p in self.result.similar_pairs),
        ]

    def _selected_records(self) -> list[FileRecord]:
        return sorted(
            (self._record_by_path[p] for p in self.selected if p in self._record_by_path),
            key=lambda r: r.path,
        )

    def _delete_worker(self, records: list[FileRecord], groups: list[list[FileRecord]]) -> None:
        try:
            outcome = move_to_trash(
                records,
                groups=groups,
                log_path=self.log_file,
                on_progress=lambda d, t: self.events.put(("delete_progress", d, t)),
            )
            self.events.put(("deleted", outcome))
        except Exception:
            self.events.put(("delete_failed", traceback.format_exc()))

    def _end_delete(self) -> None:
        self.deleting = False
        self.scan_btn.state(["!disabled"])
        self.progress.config(style="Done.Horizontal.TProgressbar", maximum=1, value=1)
        self._update_selection_summary()

    def _finish_delete(self, outcome: DeleteOutcome) -> None:
        deleted = {r.path for r in outcome.deleted}
        apply_deletions(self.result, deleted)
        # Keep ticks only on files that are still listed (e.g. ones that failed and still
        # have a group); a hidden ticked file could otherwise be deleted later by surprise.
        self.selected &= self._visible_paths() - {r.path for r in outcome.changed}
        self._end_delete()
        self._refresh_results()
        self._update_stats()

        self._set_stage(
            f"✔  Moved {len(outcome.deleted):,} files ({human_size(outcome.deleted_bytes)}) "
            "to the Recycle Bin",
            C.success if outcome.deleted else C.warn,
        )
        if outcome.log_error:
            self.detail_var.set(f"Log could NOT be written: {outcome.log_error}")
        else:
            self.detail_var.set(f"You can restore them from the Recycle Bin. Log: {self.log_file}")
        problems = []
        if outcome.no_survivor:
            problems.append(
                f"{len(outcome.no_survivor):,} files were kept because the copy you chose to keep "
                "has changed or disappeared since the scan, so they may be the last copy:\n"
                + "\n".join(f"  {r.path}" for r in outcome.no_survivor[:15])
            )
        if outcome.changed:
            problems.append(
                f"{len(outcome.changed):,} files were skipped because they changed or "
                "disappeared since the scan (rescan to update):\n"
                + "\n".join(f"  {r.path}" for r in outcome.changed[:15])
            )
        if outcome.failed:
            problems.append(
                f"{len(outcome.failed):,} files could not be moved to the Recycle Bin and "
                "were left in place:\n"
                + "\n".join(f"  {r.path}: {err}" for r, err in outcome.failed[:15])
            )
        if problems:
            messagebox.showwarning("Some files were not deleted", "\n\n".join(problems))

    def _confirm_delete(
        self, records: list[FileRecord], at_risk: Sequence[Sequence[FileRecord]]
    ) -> DeleteChoice:
        """Ask the user to confirm. Returns "delete", "keep_one" or "cancel"."""
        total = sum(r.size for r in records)
        no_bin = [r for r in records if not has_recycle_bin(r.path)]
        win = self._toplevel("Confirm delete", 760, 520 if at_risk or no_bin else 440)
        choice: list[DeleteChoice] = ["cancel"]

        tk.Label(
            win,
            text=f"Move {len(records):,} files ({human_size(total)}) to the Recycle Bin?",
            font=("Segoe UI Semibold", 13),
            bg=C.card,
            fg=C.text,
        ).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(
            win,
            text="You can restore them from the Recycle Bin later. Files that changed since "
            "the scan are skipped.",
            font=FONT,
            bg=C.card,
            fg=C.muted,
        ).pack(anchor="w", padx=16)

        if at_risk:
            names = ", ".join(g[0].name for g in at_risk[:5]) + ("…" if len(at_risk) > 5 else "")
            tk.Label(
                win,
                text=f"⚠  Every file is selected in {len(at_risk):,} duplicate group(s) or "
                f"similar-photo pair(s) ({names}). Deleting them leaves no copy outside the "
                "Recycle Bin. (Similar photos are different files, not copies.)",
                font=FONT_BOLD,
                bg="#fef2f2",
                fg=C.danger,
                wraplength=self.px(700),
                justify="left",
                padx=10,
                pady=8,
            ).pack(fill="x", padx=16, pady=(10, 0))

        if no_bin:
            tk.Label(
                win,
                text=f"{len(no_bin):,} selected file(s) are on a drive without a Recycle Bin "
                "(USB stick or network drive). They will be skipped and left in place.",
                font=FONT,
                bg="#fffbeb",
                fg=C.warn,
                wraplength=self.px(700),
                justify="left",
                padx=10,
                pady=6,
            ).pack(fill="x", padx=16, pady=(10, 0))

        box = tk.Text(win, height=10, wrap="none", font=("Consolas", 9), relief="flat", bg=C.bg)
        box.pack(fill="both", expand=True, padx=16, pady=10)
        lines = [r.path for r in records[:CONFIRM_LIST_LIMIT]]
        if len(records) > CONFIRM_LIST_LIMIT:
            lines.append(f"… and {len(records) - CONFIRM_LIST_LIMIT:,} more")
        box.insert("1.0", "\n".join(lines))
        box.config(state="disabled")

        buttons = tk.Frame(win, bg=C.card)
        buttons.pack(fill="x", padx=16, pady=(0, 14))

        def finish(value: DeleteChoice) -> None:
            choice[0] = value
            win.destroy()

        cancel = ttk.Button(
            buttons, text="Cancel", style="Secondary.TButton", command=partial(finish, "cancel")
        )
        cancel.pack(side="right")
        ttk.Button(
            buttons,
            text="Delete all selected" if at_risk else "Move to Recycle Bin",
            style="Danger.TButton",
            command=partial(finish, "delete"),
        ).pack(side="right", padx=(0, 8))
        if at_risk:
            ttk.Button(
                buttons,
                text="Keep one copy of each",
                style="Primary.TButton",
                command=partial(finish, "keep_one"),
            ).pack(side="right", padx=(0, 8))
            tk.Label(
                buttons,
                text="Keeps the oldest copy",
                font=FONT_SMALL,
                bg=C.card,
                fg=C.muted,
            ).pack(side="left")
        cancel.focus_set()
        win.bind("<Escape>", lambda _e: finish("cancel"))
        win.protocol("WM_DELETE_WINDOW", partial(finish, "cancel"))
        try:
            win.wait_visibility()  # grabbing an unmapped window fails on X11
            win.grab_set()
        except tk.TclError:
            pass
        self.root.wait_window(win)
        return choice[0]

    # ------------------------------------------------------- selection
    def _current_tree(self) -> ttk.Treeview:
        return self.exact_tree if self.notebook.index("current") == 0 else self.similar_tree

    def _selected_iid(self) -> str | None:
        selected = self._current_tree().selection()
        return selected[0] if selected else None

    def _selected_file(self) -> FileRecord | None:
        iid = self._selected_iid()
        return self._file_by_iid.get(iid) if iid else None

    def _on_click(self, event: tk.Event[Any]) -> str | None:
        tree: ttk.Treeview = event.widget
        iid = tree.identify_row(event.y)
        if iid and "image" in str(tree.identify_element(event.x, event.y)):
            tree.selection_set(iid)
            tree.focus(iid)
            self.toggle(iid)
            return "break"  # don't also expand/collapse the group
        return None

    def _on_double_click(self, event: tk.Event[Any]) -> str | None:
        tree: ttk.Treeview = event.widget
        if "image" in str(tree.identify_element(event.x, event.y)):
            # Tk delivers the second click of a fast double click here, not to <Button-1>,
            # so treat it as the second toggle.
            return self._on_click(event)
        rec = self._file_by_iid.get(tree.identify_row(event.y))
        if rec is None:
            return None  # default behavior expands/collapses the group
        self.open_file(rec.path)
        return "break"

    def _on_right_click(self, event: tk.Event[Any]) -> None:
        tree: ttk.Treeview = event.widget
        iid = tree.identify_row(event.y)
        if not iid:
            return
        tree.selection_set(iid)
        tree.focus(iid)
        state = "normal" if iid in self._file_by_iid else "disabled"
        for index in (2, 3, 4):  # Open file, Show in folder, Copy path
            self.menu.entryconfigure(index, state=state)
        self.menu.entryconfigure(0, state="disabled" if self.busy else "normal")
        self.menu.tk_popup(event.x_root, event.y_root)

    def file_action(self, action: Callable[[str], None], path: str) -> None:
        """Run open/reveal on a path, reporting failures instead of failing silently."""
        try:
            action(path)
        except OSError as exc:
            messagebox.showerror("Could not open", f"{path}\n\n{exc}")

    def open_file(self, path: str) -> None:
        if is_risky(path) and not messagebox.askyesno(
            "Open program or script?",
            f"{os.path.basename(path)} is a program, script, or shortcut. Opening it will run "
            "it.\n\nOpen it anyway? (Choose No to show it in its folder instead.)",
            icon="warning",
            default="no",
        ):
            self.file_action(reveal_path, path)
            return
        self.file_action(open_path, path)

    def _open_selected(self) -> None:
        rec = self._selected_file()
        if rec is not None:
            self.open_file(rec.path)

    def _reveal_selected(self) -> None:
        rec = self._selected_file()
        if rec is not None:
            self.file_action(reveal_path, rec.path)

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
        win = self._toplevel(f"Exact duplicate group {idx + 1}", 860, 460)
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
                command=partial(self.file_action, reveal_path, rec.path),
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
        win = self._toplevel(f"Similar photos: pair {idx + 1}", 900, 600)
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
        size = self.px(THUMBNAIL_SIZE)
        for col, rec in enumerate((pair.left, pair.right)):
            side.columnconfigure(col, weight=1, uniform="pair")
            cell = tk.Frame(side, bg=C.card)
            cell.grid(row=0, column=col, sticky="nsew", padx=6)
            preview = tk.Label(cell, text="Loading preview…", bg=C.bg, fg=C.muted, height=12)
            preview.pack(fill="x", pady=(0, 8))
            # Decode off the Tk thread; big PNG/TIFF files can take seconds.
            threading.Thread(
                target=self._thumbnail_worker, args=(rec.path, preview, size), daemon=True
            ).start()
            tk.Label(
                cell,
                text=f"{rec.path}\n{human_size(rec.size)}  ·  modified {format_time(rec.mtime)}",
                wraplength=self.px(400),
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
                command=partial(self.open_file, rec.path),
            ).pack(side="left")
            ttk.Button(
                buttons,
                text="Show in folder",
                style="Small.Secondary.TButton",
                command=partial(self.file_action, reveal_path, rec.path),
            ).pack(side="left", padx=(6, 0))

    def _thumbnail_worker(self, path: str, label: tk.Label, size: int) -> None:
        # Runs on a worker thread: only decode here; the label is touched on the Tk thread.
        self.events.put(("thumbnail", label, load_thumbnail(path, size)))

    def _show_thumbnail(self, label: tk.Label, image: Any) -> None:
        if not label.winfo_exists():
            return  # window was closed before the preview finished
        if image is None:
            label.config(text="(preview unavailable)")
            return
        from PIL import ImageTk

        photo = ImageTk.PhotoImage(image, master=self.root)
        label.config(image=photo, text="", height=0)
        label.image = photo  # type: ignore[attr-defined]  # keep a reference alive

    def show_errors(self) -> None:
        if not self.result.errors:
            return
        win = self._toplevel(f"Unreadable items ({len(self.result.errors):,})", 860, 420)
        tk.Label(
            win,
            text="These files or folders could not be read (often system folders, locked "
            "files, or online-only cloud files). They are also listed in the CSV report.",
            font=FONT,
            bg=C.card,
            wraplength=self.px(820),
            justify="left",
        ).pack(anchor="w", padx=14, pady=(12, 6))
        box = tk.Text(win, wrap="none", font=("Consolas", 9), relief="flat", bg=C.bg)
        box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        box.insert("1.0", "\n".join(self.result.errors))
        box.config(state="disabled")

    def _toplevel(self, title: str, width: int, height: int) -> tk.Toplevel:
        win = tk.Toplevel(self.root, bg=C.card)
        win.title(title)
        win.geometry(f"{self.px(width)}x{self.px(height)}")
        win.transient(self.root)
        win.bind("<Escape>", lambda _e: win.destroy())
        return win

    # ----------------------------------------------------------- misc
    def export_csv(self) -> None:
        if self.busy or not self.result.records:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_report_name(),
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            # Export what is shown; with the cross-folder option, files of within-folder
            # groups are noted rather than looking unique.
            within = [g for g in self.result.exact_groups if not spans_folders(g)]
            write_csv(self._view(), path, within_one_folder=within if self._across() else [])
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        if messagebox.askyesno("Report saved", f"CSV report saved to:\n{path}\n\nOpen it now?"):
            self.file_action(open_path, path)

    def _save_settings(self) -> None:
        save_settings(
            self.settings_file,
            {"folders": self.folders, "compare_images": self.do_images.get()},
        )

    def on_close(self) -> None:
        if self.deleting:
            messagebox.showinfo(
                "Please wait", "Files are being moved to the Recycle Bin. Wait for it to finish."
            )
            return
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
