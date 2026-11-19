# Security Policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through
[GitHub Security Advisories](https://github.com/gopalthakur71/duplicate-detector/security/advisories/new).

Include steps to reproduce and the affected version. You can expect an initial response within 7 days.

## Scope notes

- The app is read-only and makes no network connections.
- CSV reports contain full file paths from your computer. Treat them as private, and remove paths before attaching a report to an issue.
- Opening a crafted image file uses Pillow. Keep dependencies updated (Dependabot is enabled for this repository).
