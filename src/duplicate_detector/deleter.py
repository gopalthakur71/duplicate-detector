"""Move user-selected files to the Recycle Bin / Trash.

Nothing here runs on its own: the GUI calls :func:`move_to_trash` only after
the user has selected files and confirmed. Files are never deleted
permanently. If the Recycle Bin cannot take a file (for example on some
network drives), that file is reported as failed and left in place.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

from .scanner import FileRecord, ScanResult

NOTE_TRASHED = "moved to Recycle Bin by user"
#: Allowed difference between scan-time and current modification time (seconds).
MTIME_TOLERANCE = 0.001

TrashFunc = Callable[[str], None]


@dataclass(slots=True)
class DeleteOutcome:
    deleted: list[FileRecord] = field(default_factory=list)
    changed: list[FileRecord] = field(default_factory=list)
    failed: list[tuple[FileRecord, str]] = field(default_factory=list)

    @property
    def deleted_bytes(self) -> int:
        return sum(r.size for r in self.deleted)


def file_unchanged(rec: FileRecord) -> bool:
    """True if the file still exists with the size and modified time seen during the scan."""
    try:
        st = os.stat(rec.path)
    except OSError:
        return False
    return st.st_size == rec.size and abs(st.st_mtime - rec.mtime) <= MTIME_TOLERANCE


def groups_losing_all_copies(
    groups: Iterable[list[FileRecord]], selected: Collection[str]
) -> list[list[FileRecord]]:
    """Duplicate groups in which every copy is selected for deletion."""
    return [g for g in groups if all(r.path in selected for r in g)]


def keep_one_per_group(groups: Iterable[list[FileRecord]], selected: set[str]) -> list[FileRecord]:
    """Unselect the oldest copy in every fully selected group; return the kept files."""
    kept = []
    for group in groups_losing_all_copies(groups, selected):
        oldest = min(group, key=lambda r: (r.mtime, r.path))
        selected.discard(oldest.path)
        kept.append(oldest)
    return kept


def move_to_trash(
    records: Iterable[FileRecord],
    *,
    log_path: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    trash: TrashFunc = send2trash,
) -> DeleteOutcome:
    """Move each file to the Recycle Bin, skipping any that changed since the scan."""
    items = list(records)
    outcome = DeleteOutcome()
    for done, rec in enumerate(items, 1):
        if not file_unchanged(rec):
            outcome.changed.append(rec)
        else:
            try:
                trash(rec.path)
            except Exception as exc:  # send2trash raises OSError and its own TrashPermissionError
                outcome.failed.append((rec, str(exc) or type(exc).__name__))
            else:
                outcome.deleted.append(rec)
        if on_progress is not None:
            on_progress(done, len(items))
    if log_path is not None and outcome.deleted:
        _append_log(log_path, outcome.deleted)
    return outcome


def _append_log(log_path: Path, deleted: list[FileRecord]) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not log_path.exists()
        with open(log_path, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(["time", "action", "path", "size_bytes", "sha256"])
            now = datetime.now().isoformat(sep=" ", timespec="seconds")
            for rec in deleted:
                writer.writerow([now, "recycle_bin", rec.path, rec.size, rec.sha256 or ""])
    except OSError:
        pass  # the deletion itself succeeded; a missing log line must not hide that


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
