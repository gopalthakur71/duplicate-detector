"""Move user-selected files to the Recycle Bin / Trash.

Nothing here runs on its own: the GUI calls :func:`move_to_trash` only after
the user has selected files and confirmed.

Safety rules:

* Files are only ever *recycled*, never deleted permanently. On Windows,
  files on drives without a Recycle Bin (USB sticks, network shares) are
  skipped and left in place, and Windows is asked to warn before it would
  destroy a file instead of recycling it (for example, one too big for the
  Recycle Bin). If the user declines, the file stays.
* A file that changed or vanished since the scan is skipped.
* A file is skipped if every *other* copy in its group that the user chose to
  keep has since changed or vanished, so the last intact copy is never removed
  by accident.
"""

from __future__ import annotations

import csv
import os
import sys
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from .scanner import FileRecord, ScanResult

NOTE_TRASHED = "moved to Recycle Bin by user"
#: Allowed difference between scan-time and current modification time (seconds).
MTIME_TOLERANCE = 0.001
REASON_NO_RECYCLE_BIN = "this drive has no Recycle Bin, so the file was left in place"

TrashFunc = Callable[[str], None]
CheckFunc = Callable[[str], bool]


@dataclass(slots=True)
class DeleteOutcome:
    deleted: list[FileRecord] = field(default_factory=list)
    #: Changed or vanished since the scan.
    changed: list[FileRecord] = field(default_factory=list)
    #: Skipped because no other intact copy of the file would remain.
    no_survivor: list[FileRecord] = field(default_factory=list)
    failed: list[tuple[FileRecord, str]] = field(default_factory=list)
    log_error: str | None = None

    @property
    def deleted_bytes(self) -> int:
        return sum(r.size for r in self.deleted)


# ----------------------------------------------------------- platform trash
def has_recycle_bin(path: str) -> bool:
    """Whether files on this path's drive can be recycled instead of destroyed."""
    if sys.platform != "win32":
        return True  # send2trash uses the per-volume XDG / macOS Trash
    import ctypes

    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return False
    root = drive if drive.startswith("\\\\") else drive + "\\"
    if root.startswith("\\\\") and not root.endswith("\\"):
        root += "\\"
    drive_fixed = 3  # removable (2), network (4), CD (5) and RAM disks (6) have no Recycle Bin
    return bool(ctypes.windll.kernel32.GetDriveTypeW(root) == drive_fixed)


def _windows_recycle(path: str) -> None:
    """Recycle one file with SHFileOperationW, asking before any permanent delete."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = (
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        )

    fo_delete = 3
    fof_silent, fof_noconfirmation, fof_allowundo = 0x4, 0x10, 0x40
    # Overrides NOCONFIRMATION for files that would be destroyed rather than recycled.
    fof_wantnukewarning = 0x4000
    op = SHFILEOPSTRUCTW()
    op.wFunc = fo_delete
    op.pFrom = os.path.abspath(path) + "\0"  # the API wants a double-NUL-terminated list
    op.fFlags = fof_allowundo | fof_noconfirmation | fof_wantnukewarning | fof_silent
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0:
        raise OSError(f"Windows could not recycle the file (error {code:#x})")
    if op.fAnyOperationsAborted:
        raise OSError("not deleted: Windows would have deleted it permanently and you declined")
    if os.path.lexists(path):
        raise OSError("the file is still there after recycling")


def default_trash(path: str) -> None:
    if sys.platform == "win32":
        _windows_recycle(path)
    else:
        from send2trash import send2trash

        send2trash(path)


# ------------------------------------------------------------------- checks
def file_unchanged(rec: FileRecord) -> bool:
    """True if the file still exists with the size and modified time seen during the scan."""
    try:
        st = os.stat(rec.path)
    except OSError:
        return False
    return st.st_size == rec.size and abs(st.st_mtime - rec.mtime) <= MTIME_TOLERANCE


def groups_losing_all_copies(
    groups: Iterable[Sequence[FileRecord]], selected: Collection[str]
) -> list[Sequence[FileRecord]]:
    """Groups (or similar pairs) in which every file is selected for deletion."""
    return [g for g in groups if all(r.path in selected for r in g)]


def keep_one_per_group(
    groups: Iterable[Sequence[FileRecord]], selected: set[str]
) -> list[FileRecord]:
    """Unselect the oldest file in every fully selected group; return the kept files."""
    kept = []
    for group in groups_losing_all_copies(groups, selected):
        oldest = min(group, key=lambda r: (r.mtime, r.path))
        selected.discard(oldest.path)
        kept.append(oldest)
    return kept


def without_survivor(
    groups: Iterable[Sequence[FileRecord]],
    selected: Collection[str],
    unchanged: Callable[[FileRecord], bool] = file_unchanged,
) -> set[str]:
    """Selected paths whose group would keep no intact copy.

    Only partly selected groups are checked: there the user meant to keep some
    copies, so if all of those have changed or vanished since the scan, deleting
    the selected ones would remove the last intact copy.
    """
    unsafe: set[str] = set()
    for group in groups:
        picked = [r for r in group if r.path in selected]
        kept = [r for r in group if r.path not in selected]
        if picked and kept and not any(unchanged(r) for r in kept):
            unsafe.update(r.path for r in picked)
    return unsafe


# ------------------------------------------------------------------ delete
def move_to_trash(
    records: Iterable[FileRecord],
    *,
    groups: Iterable[Sequence[FileRecord]] = (),
    log_path: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    trash: TrashFunc = default_trash,
    can_recycle: CheckFunc = has_recycle_bin,
) -> DeleteOutcome:
    """Recycle each file, skipping any that would be unsafe (see the module docstring).

    ``groups`` are the duplicate groups the records belong to, used to make
    sure a kept copy still exists. Each recycled file is logged immediately.
    """
    items = list(records)
    outcome = DeleteOutcome()
    unsafe = without_survivor(groups, {r.path for r in items})
    log = _Log(log_path)
    try:
        for done, rec in enumerate(items, 1):
            if rec.path in unsafe:
                outcome.no_survivor.append(rec)
            elif not file_unchanged(rec):
                outcome.changed.append(rec)
            elif not can_recycle(rec.path):
                outcome.failed.append((rec, REASON_NO_RECYCLE_BIN))
            else:
                try:
                    trash(rec.path)
                except Exception as exc:  # OSError, or send2trash's TrashPermissionError
                    outcome.failed.append((rec, str(exc) or type(exc).__name__))
                else:
                    outcome.deleted.append(rec)
                    log.write(rec)
            if on_progress is not None:
                on_progress(done, len(items))
    finally:
        log.close()
    outcome.log_error = log.error
    return outcome


class _Log:
    """Appends one CSV row per recycled file, flushed immediately."""

    def __init__(self, path: Path | None) -> None:
        self.error: str | None = None
        self._file: TextIO | None = None
        self._writer: Any = None  # csv writer objects have no public type
        self._path = path

    def write(self, rec: FileRecord) -> None:
        if self._path is None or self.error is not None:
            return
        try:
            if self._writer is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                is_new = not self._path.exists()
                self._file = open(self._path, "a", newline="", encoding="utf-8-sig")  # noqa: SIM115
                self._writer = csv.writer(self._file)
                if is_new:
                    self._writer.writerow(["time", "action", "path", "size_bytes", "sha256"])
            now = datetime.now().isoformat(sep=" ", timespec="seconds")
            self._writer.writerow([now, "recycle_bin", rec.path, rec.size, rec.sha256 or ""])
            assert self._file is not None
            self._file.flush()
        except OSError as exc:
            self.error = f"{self._path}: {exc}"

    def close(self) -> None:
        if self._file is not None:
            self._file.close()


# ----------------------------------------------------------------- results
def apply_deletions(result: ScanResult, deleted_paths: Collection[str]) -> None:
    """Update scan results after files were moved to the Recycle Bin.

    Deleted files stay in ``records`` (so the CSV report shows what happened)
    but leave their groups. Groups with fewer than two files left are no
    longer duplicates and are dropped, as are similar pairs missing a file.
    """
    if not deleted_paths:
        return
    for rec in result.records:
        if rec.path in deleted_paths:
            rec.note = NOTE_TRASHED
    groups = []
    for group in result.exact_groups:
        remaining = [r for r in group if r.path not in deleted_paths]
        if len(remaining) > 1:
            groups.append(remaining)
    result.exact_groups = groups
    result.similar_pairs = [
        p
        for p in result.similar_pairs
        if p.left.path not in deleted_paths and p.right.path not in deleted_paths
    ]
