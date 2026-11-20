# Notes for Claude

## Git workflow (repo-wide rule)

- Never commit directly to `main`. For every change, including small bug fixes, create a branch from an up-to-date `main`, commit there, push, and open a pull request.
- Branch names: `fix/<short-description>` for bugs, `feature/<short-description>` for features, `docs/` or `chore/` for everything else.
- Fill in `.github/PULL_REQUEST_TEMPLATE.md` and add a line under **Unreleased** in `CHANGELOG.md`.
- Before committing, run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy`.

See `CONTRIBUTING.md` for the rest.
