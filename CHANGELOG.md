# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
