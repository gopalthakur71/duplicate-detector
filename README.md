# Local Duplicate Detector

[![CI](https://github.com/gopalthakur71/duplicate-detector/actions/workflows/ci.yml/badge.svg)](https://github.com/gopalthakur71/duplicate-detector/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A **read-only** desktop tool that finds duplicate files anywhere under a folder or drive. It shows you what is duplicated and how much space the extra copies use. It **never deletes, moves, renames, or modifies** anything.

## Features

- **Exact duplicates, whatever their names:** files are compared by SHA-256 of their contents.
- **Fast on large drives:** only files that could be duplicates are read.
  1. Files with a unique size are skipped.
  2. Files that share a size are compared by their first 256 KB.
  3. Only files that still match are hashed in full.

  On a typical drive this reads a small fraction of the data.
- **Similar photos (optional):** perceptual hashing (pHash) flags photos that look the same, such as re-saved JPEGs, resized copies, or format conversions. Photos are compared with a pigeonhole index, so even 100,000 of them take seconds to compare.
- **Clear results:** duplicate groups are sorted by wasted space, and every stage shows live progress. You can cancel at any time.
- **CSV report:** includes every file's path, size, modified time, hash, group ID, and a note explaining how it was handled.
- **GUI and CLI:** a Tkinter desktop window, plus a `duplicate-detector scan` command for scripting.

## Install

### Windows executable (easiest)
Download `DuplicateDetector.exe` from the [latest release](https://github.com/gopalthakur71/duplicate-detector/releases/latest) and run it. You don't need to install Python.

### From source
Requires Python 3.11 or newer. On Windows, enable **"Add Python to PATH"** during installation.

```bat
git clone https://github.com/gopalthakur71/duplicate-detector.git
cd duplicate-detector
py -m pip install -r requirements.txt
py app.py
```

`requirements.txt` only adds the optional photo comparison. Exact duplicate detection needs nothing beyond the standard library.

### As a package
```bash
pip install "duplicate-detector[images] @ git+https://github.com/gopalthakur71/duplicate-detector.git"
duplicate-detector-gui                      # desktop window
duplicate-detector scan E:\ --csv report.csv --images
```

## Usage

1. Enter or browse to a folder. Use `E:\` to scan the whole drive.
2. Optionally tick **Also compare photos for visual similarity**. Exact duplicate scanning is faster without it.
3. Click **Scan for duplicates**. Use **Cancel scan** to stop at any time.
4. Review the **Exact duplicates** and **Similar photos** tabs. Expand a group to see its files, click a column heading to sort, or type in **Filter** to narrow by path.
5. Double-click a file to open it, or right-click for **Show in folder** and **Copy path**. Press Enter for details, including side-by-side previews of similar photos.
6. Click **Export report (CSV)** to save the full results.

Shortcuts: **Enter** to scan, **Esc** to cancel, **Ctrl+F** to filter, **Ctrl+E** to export. The last folder and options are remembered in `%APPDATA%\DuplicateDetector\settings.json`, the only file the app writes besides the reports you export.

### Command line

```text
duplicate-detector scan PATH [--csv FILE] [--images] [--quiet]
```

Progress is printed to stderr, and a summary and the report path to stdout. Exit codes: `0` for success, `2` for invalid arguments, `130` for a cancelled scan (Ctrl+C).

### CSV columns

| Column | Meaning |
|---|---|
| `source` | The scanned root folder (or `ERROR` for a read-error row) |
| `path`, `filename` | Full path and file name |
| `size_bytes`, `modified_time` | From the file system |
| `sha256` | Full content hash. Blank when the file was never fully read because it could not be a duplicate |
| `exact_duplicate_group` | Same number means identical contents |
| `possible_visual_matches` | e.g. `possible_pair_3_distance_0` (0 means the photos look identical) |
| `notes` | Why a file was not hashed, or that it had a read error |

## Limitations

- Photo similarity is a review aid, not proof. It can miss heavily cropped or edited copies and can flag photos that merely look alike.
- Videos are compared by exact content only.
- Large groups of same-size files, such as many identical-size videos, still have to be read in full.
- Empty (0-byte) files are listed in the CSV but not grouped as duplicates.
- Symlinks and junctions are skipped. Files and folders that can't be opened are listed as `ERROR` rows.
- Cloud placeholders (OneDrive / Google Drive "online-only" files) are downloaded when read, or fail with an error. Make them available offline before scanning.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```bash
uv sync --all-extras
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

```text
src/duplicate_detector/
  scanner.py   core scan logic (no GUI imports)
  report.py    CSV export
  cli.py       command-line interface
  gui.py       Tkinter interface
tests/         pytest suite
app.py         run-from-source launcher, also the PyInstaller entry point
```

## Safety

This tool is deliberately report-only. It has no delete controls. Review the CSV before removing anything by hand, and keep a backup.

## License

[MIT](LICENSE)
