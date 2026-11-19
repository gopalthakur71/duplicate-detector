from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

MakeFile = Callable[[str, bytes], Path]


@pytest.fixture
def make_file(tmp_path: Path) -> MakeFile:
    """Create a file under tmp_path (with parent folders) and return its path."""

    def _make(relative: str, data: bytes) -> Path:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    return _make


@pytest.fixture
def big_bytes() -> bytes:
    """Data larger than the partial-hash window, so full hashing is exercised."""
    return os.urandom(3 * 1024 * 1024)
