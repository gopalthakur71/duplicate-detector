from __future__ import annotations

from pathlib import Path

import pytest

from duplicate_detector import __version__, cli

from .conftest import MakeFile


def test_scan_writes_report(
    tmp_path: Path, make_file: MakeFile, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file("data/a.txt", b"same")
    make_file("data/b.txt", b"same")
    out = tmp_path / "out.csv"

    code = cli.main(["scan", str(tmp_path / "data"), "--csv", str(out), "--quiet"])

    assert code == 0
    assert out.exists()
    stdout = capsys.readouterr().out
    assert "1 exact duplicate groups" in stdout
    assert str(out) in stdout


def test_scan_shows_progress_on_stderr(
    tmp_path: Path, make_file: MakeFile, capsys: pytest.CaptureFixture[str]
) -> None:
    make_file("data/a.txt", b"x")
    cli.main(["scan", str(tmp_path / "data"), "--csv", str(tmp_path / "o.csv")])
    assert "Listing files" in capsys.readouterr().err


def test_scan_rejects_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan", str(tmp_path / "nope")]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_images_flag_without_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "IMAGE_HASH_AVAILABLE", False)
    assert cli.main(["scan", str(tmp_path), "--images"]) == 2
    assert "Pillow" in capsys.readouterr().err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_gui_module_imports() -> None:
    # The GUI itself needs a display; importing it catches syntax/import errors.
    from duplicate_detector import gui

    assert callable(gui.main)
