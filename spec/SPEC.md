# TokenTray — Specification

| Field | Value |
|---|---|
| Slug | `tokentray` |
| Package | `tokentray` (PyInstaller bundle) |
| Category | `productivity` / developer tooling |
| Status | `live` (v0.6.0) |
| Platform | Windows (system tray) |
| Auth family | `none` — local-only, no accounts (`auth_family: none`) |
| Hosting | n/a — desktop app, no Railway/Firebase |
| Distribution | PyInstaller bundle + Inno Setup installer (`installer/`, `build.ps1`) |
| GitHub | `ReboundMan/TokenTray` |
| Owner | Jeff |

## Overview
A lightweight Windows **system-tray app** that shows your live **GitHub Copilot CLI**
token usage, parsed from local telemetry logs. No network calls and no extra accounts —
just a number in the tray and a click-away breakdown.

## Users + jobs-to-be-done
- **User:** a developer who runs the GitHub Copilot CLI on Windows.
- **Job:** see, at a glance, how many tokens I've used today (and over time) without opening logs or a browser.
- **Frequency:** ambient/all-day; auto-refreshes every 2 minutes.

## Scope
**In scope:**
- Tray icon showing today's total tokens (`6.9M`, `124k`, `0`, …) with a hover tooltip (turn/session counts, last refresh).
- Popup **Today** tab: today's totals broken into Uncached input / Output / Cached input / Sessions / Turns, plus a stacked **7-day** bar chart.
- Popup **History** tab *(Advanced)*: Day / Week / Month / All-time rollups persisted across CLI log rotation.
- Auto-refresh (2 min) + manual Refresh; optional auto-start at login.

**Out of scope:**
- Any network calls, telemetry upload, or online accounts (the app is fully local).
- Non-Windows platforms.

## Key flows
1. App reads the local Copilot CLI telemetry logs, aggregates per-event token usage, and renders the tray icon + popup.
2. History (Advanced) records per-event usage to a local SQLite DB so rollups survive the CLI rotating its raw logs.

## Data model (summary)
Local only — **no Firestore**. History persists to SQLite at
`%LOCALAPPDATA%\TokenTray\history.db` (per-event token usage; Day/Week/Month/All-time rollups).

## Versioning
Single source of truth: `_version.py` (`__version__`), consumed by the popup, `pyproject.toml`
(dynamic version), and the Inno Setup script. Currently `0.6.0`.

## External integrations
- None. Reads local GitHub Copilot CLI telemetry log files only.

## Open questions
- [ ] Add a root `VERSION` file (Standard #10) that mirrors `_version.py`, or wire `_version.py` as the source?

## Decisions log
| Date | Decision | Why |
|---|---|---|
| 2026-05-31 | Adopt ProjectPatterns `spec/` standard; consolidate scattered specs | Portfolio consistency (was: root + `docs/specs/`) |
| (prior) | Fully local, no network / no accounts | Privacy + zero-friction; nothing to sign into |
| (prior) | SQLite (`history.db`) for the Advanced History tier | Rollups must survive Copilot CLI log rotation |
| (prior) | Single-source the version in `_version.py` | Frozen PyInstaller bundle has no package metadata at runtime |
