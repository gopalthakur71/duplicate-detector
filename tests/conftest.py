from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

MakeFile = Callable[[str, bytes], Path]


@pytest.fixture(scope="session")
def tk_root() -> Iterator[tk.Tk]:
    """One shared Tk root: creating many Tk interpreters in a row is unreliable on Windows."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture(autouse=True)
def _delete_tk_images(request: pytest.FixtureRequest) -> Iterator[None]:
    """Delete the Tk images a test created once it ends.

    Each app window makes a few hundred checkbox images, and Tk callbacks keep a
    finished test's app alive, so without this they pile up across the suite
    until Tk crashes (access violation on Windows).
    """
    if "tk_root" not in request.fixturenames:
        yield
        return
    root: tk.Tk = request.getfixturevalue("tk_root")
    before = set(root.tk.splitlist(root.tk.call("image", "names")))
    yield
    created = set(root.tk.splitlist(root.tk.call("image", "names"))) - before
    if created:
        root.tk.call("image", "delete", *created)


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
