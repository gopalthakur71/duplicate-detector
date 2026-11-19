"""Core scanning logic. Has no GUI dependencies, so it can be tested and scripted.

The scanner is strictly read-only: it opens files for reading and never
writes, moves, renames, or deletes anything.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

try:
    import imagehash
    from PIL import Image

    IMAGE_HASH_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional extras
    IMAGE_HASH_AVAILABLE = False

CHUNK_SIZE = 1024 * 1024
#: Files that share a size are first compared by hashing only this many leading
#: bytes. Only files that still match are read completely.
PARTIAL_SIZE = 256 * 1024
#: Maximum pHash Hamming distance reported as "possibly similar".
MAX_PHASH_DISTANCE = 5
PHASH_BITS = 64

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".webm"})

NOTE_EMPTY = "empty file (not compared)"
NOTE_UNIQUE_SIZE = "unique size, cannot be a duplicate (not hashed)"
NOTE_UNIQUE_START = "beginning differs from all same-size files (not fully hashed)"
NOTE_READ_ERROR = "read error"


class ScanCancelled(Exception):
    """Raised inside a scan when its cancel event is set."""


@dataclass(slots=True)
class FileRecord:
    source: str
    path: str
    size: int
    mtime: float
    sha256: str | None = None
    phash: str | None = None
    note: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def is_image(self) -> bool:
        return os.path.splitext(self.path)[1].lower() in IMAGE_EXTENSIONS


@dataclass(frozen=True, slots=True)
class SimilarPair:
    left: FileRecord
    right: FileRecord
    distance: int


@dataclass(frozen=True, slots=True)
class Progress:
    """A progress update. ``total`` is ``None`` when progress cannot be measured."""

    message: str
    done: float = 0
    total: float | None = None


def spans_folders(files: Iterable[FileRecord]) -> bool:
    """True if the files were found in more than one of the chosen scan folders."""
    return len({f.source for f in files}) > 1


@dataclass(slots=True)
class ScanResult:
    records: list[FileRecord] = field(default_factory=list)
    exact_groups: list[list[FileRecord]] = field(default_factory=list)
    similar_pairs: list[SimilarPair] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: The folders that were scanned (after removing duplicates and nested folders).
    roots: list[str] = field(default_factory=list)

    def across_folders(self) -> ScanResult:
        """A copy keeping only groups and pairs that span more than one scan folder."""
        return ScanResult(
            records=self.records,
            exact_groups=[g for g in self.exact_groups if spans_folders(g)],
            similar_pairs=[p for p in self.similar_pairs if spans_folders((p.left, p.right))],
            errors=self.errors,
            roots=self.roots,
        )

    @property
    def wasted_bytes(self) -> int:
        """Bytes used by extra copies (every copy beyond the first in each group)."""
        return sum(g[0].size * (len(g) - 1) for g in self.exact_groups)


ProgressCallback = Callable[[Progress], None]


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ScanCancelled


def sha256_file(
    path: str | os.PathLike[str],
    limit: int | None = None,
    on_chunk: Callable[[int], None] | None = None,
    cancel: threading.Event | None = None,
) -> str:
    """Return the SHA-256 hex digest of a file, or of its first ``limit`` bytes."""
    digest = hashlib.sha256()
    remaining = limit
    with open(path, "rb") as f:
        while True:
            _check_cancel(cancel)
            size = CHUNK_SIZE if remaining is None else min(CHUNK_SIZE, remaining)
            if size <= 0:
                break
            chunk = f.read(size)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
            if on_chunk is not None:
                on_chunk(len(chunk))
    return digest.hexdigest()


def image_phash(path: str) -> str | None:
    """Return a perceptual hash for an image, or ``None`` if unavailable or unreadable."""
    if not IMAGE_HASH_AVAILABLE or os.path.splitext(path)[1].lower() not in IMAGE_EXTENSIONS:
        return None
    try:
        with Image.open(path) as im:
            # For JPEGs, decode at reduced resolution. pHash shrinks the image to
            # 32x32 anyway, so this is many times faster with no meaningful loss.
            im.draft("RGB", (128, 128))
            return str(imagehash.phash(im.convert("RGB")))
    except Exception:  # Pillow raises many exception types for bad files
        return None


def human_size(size: float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def walk_files(
    scan_root: str | os.PathLike[str],
    errors: list[str],
    cancel: threading.Event | None = None,
) -> Iterator[tuple[str, int, float]]:
    """Yield ``(path, size, mtime)`` for every regular file under ``scan_root``.

    Symlinks and junctions are skipped. Folders that cannot be opened are
    appended to ``errors`` instead of being silently ignored.
    """
    stack = [os.fspath(scan_root)]
    while stack:
        _check_cancel(cancel)
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink() or _is_junction(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            # On Windows this stat comes from the directory listing.
                            st = entry.stat(follow_symlinks=False)
                            yield entry.path, st.st_size, st.st_mtime
                    except OSError as exc:
                        errors.append(f"{entry.path}: {exc}")
        except OSError as exc:
            errors.append(f"{directory} (folder could not be opened): {exc}")


def _is_junction(entry: os.DirEntry[str]) -> bool:
    is_junction = getattr(entry, "is_junction", None)  # Python 3.12+
    return bool(is_junction()) if is_junction is not None else False


def find_similar_images(
    images: list[FileRecord], max_distance: int = MAX_PHASH_DISTANCE
) -> list[SimilarPair]:
    """Return pairs of images whose pHash distance is at most ``max_distance``.

    Uses the pigeonhole principle instead of comparing every pair: the 64-bit
    hash is split into ``max_distance + 1`` parts, and two hashes within
    ``max_distance`` bits must match exactly on at least one part, so only
    images sharing a part are compared.

    Files with identical contents (same SHA-256) are already exact duplicates
    and are not reported again.
    """
    candidates = [r for r in images if r.phash]
    hashes = [int(r.phash, 16) for r in candidates if r.phash]
    parts = max_distance + 1
    bounds = [(i * PHASH_BITS // parts, (i + 1) * PHASH_BITS // parts) for i in range(parts)]
    buckets: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, h in enumerate(hashes):
        for part, (lo, hi) in enumerate(bounds):
            buckets[(part, (h >> lo) & ((1 << (hi - lo)) - 1))].append(idx)

    found: dict[tuple[int, int], int] = {}
    for members in buckets.values():
        for a, i in enumerate(members):
            for j in members[a + 1 :]:
                if (i, j) in found:
                    continue
                distance = (hashes[i] ^ hashes[j]).bit_count()
                if distance > max_distance:
                    continue
                left, right = candidates[i], candidates[j]
                if left.sha256 is not None and left.sha256 == right.sha256:
                    continue
                found[(i, j)] = distance

    pairs = [SimilarPair(candidates[i], candidates[j], d) for (i, j), d in found.items()]
    pairs.sort(key=lambda p: (p.distance, p.left.path, p.right.path))
    return pairs


class _Throttle:
    """Lets an update through at most once every ``interval`` seconds."""

    def __init__(self, interval: float = 0.2) -> None:
        self.interval = interval
        self.last = float("-inf")

    def ready(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.interval:
            self.last = now
            return True
        return False


def _norm(path: str) -> str:
    # realpath resolves junctions, symlinks and subst/mapped drives, so two paths to the
    # same physical folder compare equal (otherwise every file would "duplicate" itself).
    return os.path.normcase(os.path.realpath(path))


def is_inside(path: str, folder: str) -> bool:
    """True if ``path`` is ``folder`` itself or anywhere below it."""
    p, f = _norm(path), _norm(folder)
    try:
        return os.path.commonpath([p, f]) == f
    except ValueError:  # different drives
        return False


def normalize_roots(
    roots: Iterable[str | os.PathLike[str]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Return ``(roots_to_scan, skipped)``.

    Duplicate entries, other paths to the same physical folder (junctions,
    symlinks, subst drives) and folders nested inside another chosen folder are
    skipped: their files are scanned once, as part of the other folder.
    ``skipped`` holds ``(skipped_folder, kept_folder)`` pairs; a repeat of the
    exact same path is dropped without being listed.
    """
    unique: list[str] = []
    skipped: list[tuple[str, str]] = []
    for root in roots:
        path = os.path.abspath(os.fspath(root))
        same = next((u for u in unique if _norm(path) == _norm(u)), None)
        if same is None:
            unique.append(path)
        elif os.path.normcase(path) != os.path.normcase(same):
            skipped.append((path, same))  # an alias of a folder already chosen
    keep: list[str] = []
    for path in unique:
        outer = next((o for o in unique if o != path and is_inside(path, o)), None)
        if outer is None:
            keep.append(path)
        else:
            skipped.append((path, outer))
    return keep, skipped


def scan(
    scan_roots: str | os.PathLike[str] | Sequence[str | os.PathLike[str]],
    *,
    compare_images: bool = False,
    cancel: threading.Event | None = None,
    on_progress: ProgressCallback | None = None,
) -> ScanResult:
    """Scan one or more folders recursively (all subfolders) for duplicate files.

    Duplicates are found across all the folders together. Each file's
    ``source`` is the chosen folder it was found in. Raises
    :class:`ScanCancelled` if ``cancel`` is set during the scan.
    """

    def report(progress: Progress) -> None:
        if on_progress is not None:
            on_progress(progress)

    if isinstance(scan_roots, (str, os.PathLike)):
        scan_roots = [scan_roots]
    roots, _ = normalize_roots(scan_roots)
    result = ScanResult(roots=roots)
    records, errors = result.records, result.errors
    throttle = _Throttle()

    # Stage 1: list files. Sizes come for free from the directory listing.
    report(Progress("Listing files…"))
    for root in roots:
        for path, size, mtime in walk_files(root, errors, cancel):
            records.append(FileRecord(source=root, path=path, size=size, mtime=mtime))
            if throttle.ready():
                report(Progress(f"Listing files… {len(records):,} found"))

    # Only files that share an exact size can be duplicates.
    by_size: defaultdict[int, list[FileRecord]] = defaultdict(list)
    for rec in records:
        if rec.size == 0:
            rec.note = NOTE_EMPTY
        else:
            by_size[rec.size].append(rec)
    candidates: list[FileRecord] = []
    for group in by_size.values():
        if len(group) > 1:
            candidates.extend(group)
        else:
            group[0].note = NOTE_UNIQUE_SIZE

    # Stage 2: hash the first part of each candidate.
    total = len(candidates)
    report(Progress(f"Checking {total:,} files that share a size with another file…", 0, total))
    by_partial: defaultdict[tuple[int, str], list[FileRecord]] = defaultdict(list)
    for done, rec in enumerate(candidates, 1):
        try:
            partial = sha256_file(rec.path, limit=PARTIAL_SIZE, cancel=cancel)
        except OSError as exc:
            errors.append(f"{rec.path}: {exc}")
            rec.note = NOTE_READ_ERROR
            continue
        if rec.size <= PARTIAL_SIZE:
            rec.sha256 = partial  # the whole file was read, so this is the full hash
        by_partial[(rec.size, partial)].append(rec)
        if throttle.ready() or done == total:
            report(Progress(f"Checked {done:,} of {total:,} files…", done, total))

    need_full: list[FileRecord] = []
    for (size, _), group in by_partial.items():
        if len(group) > 1 and size > PARTIAL_SIZE:
            need_full.extend(group)
        elif len(group) == 1 and group[0].sha256 is None:
            group[0].note = NOTE_UNIQUE_START

    # Stage 3: full hash only for files that still look identical.
    total_bytes = sum(r.size for r in need_full)
    report(
        Progress(
            f"Fully hashing {len(need_full):,} likely duplicates ({human_size(total_bytes)})…",
            0,
            max(total_bytes, 1),
        )
    )
    done_bytes = 0

    def on_chunk(n: int) -> None:
        nonlocal done_bytes
        done_bytes += n
        if throttle.ready():
            report(
                Progress(
                    f"Hashed {human_size(done_bytes)} of {human_size(total_bytes)}…",
                    done_bytes,
                    max(total_bytes, 1),
                )
            )

    for rec in need_full:
        try:
            rec.sha256 = sha256_file(rec.path, on_chunk=on_chunk, cancel=cancel)
        except OSError as exc:
            errors.append(f"{rec.path}: {exc}")
            rec.note = NOTE_READ_ERROR

    by_hash: defaultdict[str, list[FileRecord]] = defaultdict(list)
    for rec in records:
        if rec.sha256 is not None:
            by_hash[rec.sha256].append(rec)
    result.exact_groups = [g for g in by_hash.values() if len(g) > 1]
    # Biggest space savings first.
    result.exact_groups.sort(key=lambda g: g[0].size * (len(g) - 1), reverse=True)

    if compare_images and IMAGE_HASH_AVAILABLE:
        images = [r for r in records if r.size > 0 and r.is_image]
        total = len(images)
        report(Progress(f"Fingerprinting {total:,} photos…", 0, max(total, 1)))
        for done, rec in enumerate(images, 1):
            _check_cancel(cancel)
            rec.phash = image_phash(rec.path)
            if throttle.ready() or done == total:
                report(Progress(f"Fingerprinted {done:,} of {total:,} photos…", done, total))
        _check_cancel(cancel)
        report(Progress("Comparing photo fingerprints…"))
        result.similar_pairs = find_similar_images(images)

    return result
