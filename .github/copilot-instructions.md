# Repo-specific Copilot instructions for TokenTray

These override the defaults in `~/.copilot/copilot-instructions.md`.

## Stack
- Windows system-tray app. Python 3.11+, PyQt6 (+ PyQt6-Charts), pywin32, PyInstaller.
- The `tokentray` package (incl. `tokentray.parsers`) is **GUI-free** — headless consumers `pip install tokentray` without PyQt6. Do not add GUI imports to the core package.
- Fully local: no network calls, no accounts, no hosted backend.

## Conventions
- Test runner: `pytest` (see `tests/`)
- Lint command: none configured
- Build command: `build.ps1` (PyInstaller bundle via `TokenTray.spec`) + Inno Setup installer

## Hard rules
- Never break the GUI-free core: keep PyQt6 imports out of `tokentray/` and its parsers.
- Never commit secrets — `.env`, `*.pem`, `*.key` are gitignored.
- Bump the version in **all** sources together: `_version.py`, `tokentray/_version.py`, root `VERSION`, `pyproject.toml`, and the Inno Setup script.
- Update `spec/punchlist.md` when completing items (move them to Done).
- Update `spec/WORK_LOG.md` with meaningful work as it happens.
- Update `CHANGELOG.md` when shipping a release (once it exists).
- Update `spec/card.json` if the tagline/status/URL changes (then run `Sync-AppCards`).

## Session start — ProjectPatterns standards are binding
<!-- RM_standards_binding -->
Treat `C:\Users\jeffjame\OneDrive\Code\ProjectPatterns\STANDARDS.md` as **binding, not advisory**:
- On starting work here, run `Test-ProjectStandards` to see this repo's current score; do not regress any passing (✓) standard.
- When you add or change files, re-check the affected standards (punchlist, WORK_LOG, CHANGELOG, card.json, VERSION, etc.) before you finish.
- Do not invent a new convention when a ProjectPatterns standard already covers it.
- Web/hosting standards (Railway, Firebase, feedback widget, version footer) are **n/a** for this local desktop app.

## Persona-related
This repo uses the global fleet. See `~/.copilot/AGENTS.md`. Refer to personas by name (Hawk, Bolt, Sage, etc.).
