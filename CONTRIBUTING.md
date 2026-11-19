# Contributing

Thanks for helping improve Local Duplicate Detector.

## Ground rule: the user decides

- Scanning (`scanner.py`) must stay read-only: it only opens files for reading.
- Files may only be removed by `deleter.py`, only when the user selected them and confirmed, and only by moving them to the Recycle Bin (never a permanent delete).
- Never add automatic selection or deletion without an explicit user action.

## Setup

This project uses [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/gopalthakur71/duplicate-detector.git
cd duplicate-detector
uv sync --all-extras          # creates .venv with runtime, image, and dev dependencies
uv run pre-commit install     # run lint/format/type checks on every commit
```

Run the app from source with `uv run duplicate-detector-gui`, or `uv run python app.py`.

## Checks

```bash
uv run pytest                 # tests
uv run pytest --cov           # with coverage (must stay ≥ 90%, GUI excluded)
uv run ruff check .           # lint
uv run ruff format .          # format
uv run mypy                   # strict type checking
```

CI runs these on Windows, Linux, and macOS for Python 3.11 to 3.15.

## Code layout

- `scanner.py`: all scan logic. It must not import tkinter. It reports progress through a callback.
- `report.py`: CSV export.
- `cli.py` and `gui.py`: thin front-ends over `scanner.scan()`.

New behavior goes into `scanner.py`, with tests in `tests/`. Use the `tmp_path` and `make_file` fixtures to build test folder trees.

## Pull requests

1. Branch from `main`.
2. Keep each PR focused, and add tests for behavior changes.
3. Add a line under **Unreleased** in `CHANGELOG.md`.
4. Use clear commit messages in the imperative mood ("Add cancel button").

## Releasing (maintainers)

1. Update `__version__` in `src/duplicate_detector/__init__.py`, and move the **Unreleased** changelog entries under the new version.
2. Commit, then run `git tag vX.Y.Z && git push origin main vX.Y.Z`.
3. The **Release** workflow builds the wheel, sdist, and `DuplicateDetector.exe`, then publishes a GitHub Release.
