from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from duplicate_detector import scanner
from duplicate_detector.scanner import FileRecord, find_similar_images, scan


def _rec(name: str, phash: int, sha: str | None = None) -> FileRecord:
    return FileRecord(source="", path=name, size=1, mtime=0, sha256=sha, phash=f"{phash:016x}")


def test_matches_brute_force() -> None:
    rng = random.Random(0)
    base = [rng.getrandbits(64) for _ in range(300)]
    near = [h ^ (1 << rng.randrange(64)) ^ (1 << rng.randrange(64)) for h in base[:100]]
    hashes = base + near
    images = [_rec(str(i), h) for i, h in enumerate(hashes)]

    fast = {(p.left.path, p.right.path) for p in find_similar_images(images)}
    brute = {
        (str(i), str(j))
        for i, j in itertools.combinations(range(len(hashes)), 2)
        if (hashes[i] ^ hashes[j]).bit_count() <= scanner.MAX_PHASH_DISTANCE
    }

    assert fast == brute
    assert len(fast) >= 100


def test_identical_looking_images_with_different_bytes_are_reported() -> None:
    pairs = find_similar_images([_rec("a.jpg", 42, "sha-a"), _rec("b.jpg", 42, "sha-b")])
    assert [(p.left.path, p.right.path, p.distance) for p in pairs] == [("a.jpg", "b.jpg", 0)]


def test_exact_duplicates_are_not_repeated_as_similar() -> None:
    assert find_similar_images([_rec("a.jpg", 42, "same"), _rec("b.jpg", 42, "same")]) == []


def test_distance_above_threshold_is_ignored() -> None:
    far = (1 << (scanner.MAX_PHASH_DISTANCE + 1)) - 1  # differs in MAX + 1 bits
    assert find_similar_images([_rec("a", 0), _rec("b", far)]) == []


def test_scan_finds_resaved_and_resized_photos(tmp_path: Path) -> None:
    pytest.importorskip("imagehash")
    from PIL import Image, ImageDraw, ImageFilter

    im = Image.new("RGB", (1600, 1200), "skyblue")
    draw = ImageDraw.Draw(im)
    draw.ellipse((200, 200, 900, 900), fill="orange")
    draw.rectangle((1000, 500, 1500, 1100), fill="darkgreen")
    im = im.filter(ImageFilter.GaussianBlur(4))
    im.save(tmp_path / "original.jpg", quality=95)
    im.save(tmp_path / "resaved.jpg", quality=60)
    im.resize((800, 600)).save(tmp_path / "small.png")
    (tmp_path / "notes.txt").write_text("not an image")

    result = scan(tmp_path, compare_images=True)

    names = {frozenset((p.left.name, p.right.name)) for p in result.similar_pairs}
    assert names == {
        frozenset(("original.jpg", "resaved.jpg")),
        frozenset(("original.jpg", "small.png")),
        frozenset(("resaved.jpg", "small.png")),
    }


def test_corrupt_image_is_ignored(tmp_path: Path) -> None:
    pytest.importorskip("imagehash")
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not really a jpeg")
    assert scanner.image_phash(str(bad)) is None
    assert scanner.image_phash(str(tmp_path / "doc.txt")) is None
