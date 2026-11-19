# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Delete duplicates you choose.** Every file row has a checkbox. Tick files one at a time, tick a group to select all its files, or use **Select all** and **Clear selection**. A live counter shows how many files and bytes are selected.
- **Delete selected…** moves the selected files to the Recycle Bin after a confirmation that lists every file. Nothing is selected or deleted automatically.
  - If every copy in a group is selected, the confirmation warns you and offers **Keep one copy of each**, which keeps the oldest copy.
  - Files that changed or disappeared since the scan are skipped.
  - Files the Recycle Bin can't take are left in place and reported.
  - Each deletion is appended to `%APPDATA%\DuplicateDetector\deletion_log.csv`.
  - Results, stats and the CSV report update after deleting. Deleted files are noted in the report.
- Keyboard: Space toggles the selected row, and Delete opens the delete confirmation.
- New dependency: `Send2Trash`, for moving files to the Recycle Bin.
- Redesigned window: colored buttons with hover states (blue Scan, red Cancel, green Export), a header bar, and summary cards for files, duplicate groups, extra space, similar pairs and unreadable items.
- Results are expandable groups with one row per file. Click a column heading to sort, or type to filter by path.
- Double-click a file to open it. Right-click for **Show in folder**, **Open file** and **Copy path**.
- The similar-photo details window shows both thumbnails side by side.
- **View errors** window, elapsed-time display, and scan percentage.
- Keyboard shortcuts: Enter (scan), Esc (cancel), Ctrl+F (filter), Ctrl+E (export).
- The last folder and photo option are remembered (`%APPDATA%\DuplicateDetector\settings.json`).
- Asks before quitting while a scan is running. Sharp text and correctly scaled sizes on high-DPI screens.
- Programs, scripts and shortcuts (`.exe`, `.bat`, `.ps1`, `.lnk`…) are never run by a double-click. The app asks first and shows the file in its folder instead.
- File rows are built only when a group is opened, so sorting and filtering stay fast on large scans and keep expanded groups and the selection. Photo previews load in the background.

### Fixed
- The progress bar showed empty when a scan finished.
- The **Browse…** button could be pushed off-screen by the fixed-width path box.

### Changed
- CI: pinned `astral-sh/setup-uv` to an exact release (`v10.3.3`), because it publishes no `v10` major tag.

## [0.2.0] - 2026-11-19

### Added
- **Cancel scan** button, and per-stage progress (listing, checking, hashing in bytes, photos).
- "Extra space" column, with duplicate groups sorted by wasted space.
- `notes` column in the CSV explaining how each file was handled.
- Command-line interface: `duplicate-detector scan PATH --csv FILE [--images]`.
- Installable package (`pyproject.toml`), test suite, CI on Windows, Linux and macOS, and Windows `.exe` releases.

### Changed
- Much faster scans. Files with a unique size are no longer read, and same-size files are compared by their first 256 KB before being hashed in full.
- Much faster photo comparison: a pigeonhole index replaces the all-pairs comparison, and JPEGs are decoded at reduced size for fingerprinting.
- The drive is walked once instead of twice, using cached directory-listing metadata.
- Python 3.11 or newer is now required (3.10 reached end of life in October 2026).

### Fixed
- CSV reports could contain live spreadsheet formulas if a scanned file name started with `=`, `+`, `-` or `@` (CSV injection). Such cells now start with an apostrophe.
- Photos that look identical but differ in bytes (pHash distance 0) were not reported as similar.
- Folders that could not be opened were silently skipped. They are now reported as errors.
- An unexpected error during a scan left the **Scan** button disabled.
- The photo-comparison step showed no progress and looked frozen on large photo libraries.

## [0.1.0]

### Added
- Initial release: a read-only Tkinter app that finds exact duplicates by SHA-256, finds similar photos with optional pHash matching, and exports a CSV report.

[Unreleased]: https://github.com/gopalthakur71/duplicate-detector/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/gopalthakur71/duplicate-detector/releases/tag/v0.2.0
