# TokenTray — Agent Instructions

This project follows the ReboundMan persona fleet. The global fleet lives in `~/.copilot/AGENTS.md`; this file lists project-specific overrides and the named-persona table for offline reference.

## House standards are binding
<!-- RM_standards_binding -->
This repo operates under `C:\Users\jeffjame\OneDrive\Code\ProjectPatterns\STANDARDS.md` — treat it as **binding, not advisory**. On starting work, run `Test-ProjectStandards`; do not regress any passing standard, and re-check affected standards when you add or change files.

## Cast (same as global)

| Name | Role | Model |
|---|---|---|
| Hawk | security-auditor | `gpt-5.3-codex` |
| Bolt | performance-reviewer | `gpt-5.3-codex` |
| Sage | sceptical-architect | `claude-opus-4.7` |
| Forge | data-engineer | `claude-opus-4.7` |
| Atlas | ux-ui-researcher | `claude-opus-4.7` |
| Lens | ux-critic | `claude-sonnet-4.6` |
| Beacon | accessibility-reviewer | `claude-sonnet-4.6` |
| Rookie | new-engineer | `claude-haiku-4.5` |
| Chaos | qa-saboteur | `gpt-5.4-mini` |
| Scout | e2e-tester | `claude-sonnet-4.6` |

## Project-specific guidance

- **What it is:** a Windows **system-tray app** that shows live GitHub Copilot CLI token usage, parsed from local telemetry logs. Fully local — no network calls, no accounts.
- **Stack:** Python 3.11+, PyQt6 (+ PyQt6-Charts) for the GUI, pywin32, PyInstaller for the frozen bundle. The core `tokentray` package (incl. `tokentray.parsers`) is **GUI-free** so headless consumers can `pip install tokentray` without PyQt6 — keep it that way.
- **Auth family:** `none` — local-only. There is no Firebase, Railway, or hosted backend; ignore any web/hosting standards.
- **Tests:** `pytest` (see `tests/`).
- **Build:** `build.ps1` drives the PyInstaller bundle (`TokenTray.spec`); the Inno Setup script produces the installer.
- **Versioning:** single source of truth is `_version.py` (`__version__`), mirrored by the package copy `tokentray/_version.py`, the root `VERSION` file, `pyproject.toml` (dynamic), and the Inno Setup script. Bump all of them together.
- **Data:** History persists to SQLite at `%LOCALAPPDATA%\TokenTray\history.db`.

## Per-repo persona overrides

None by default. Drop overrides in `.copilot/personas/<name>.md` if a specific persona should behave differently in this repo.

## Reviews

Fleet output lands in `reviews/<ticket>-<persona>.md` (gitignored).
