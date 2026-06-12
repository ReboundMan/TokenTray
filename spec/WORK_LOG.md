# TokenTray — Work Log

Reverse-chronological log of meaningful work. Quick capture as it happens.

## 2026-06-07
- Fixed Today vs History token mismatch (Today 77M / History 99M). Root cause:
  the History tab reads the cumulative SQLite store while the Today tab live-
  parses the logs, and the store's dedup id (`_event_id`) includes token counts.
  A long-running Agency `session.shutdown` rollup keeps a fixed timestamp while
  its cumulative totals grow, so each larger snapshot was stored as a new row
  instead of replacing the prior one. Added `UsageEvent.is_rollup`; Agency
  rollups now key by session (`_rollup_event_id`) and ingest with `INSERT OR
  REPLACE`. Bumped schema to v3 with a migration that collapses existing
  duplicate Agency rollup rows. Added tests in `tests/test_history_store.py`.

## 2026-05-31
- Adopted ProjectPatterns standards. Consolidated scattered specs into `spec/`
  (moved `Anon_usage_plan.md` and `docs/specs/*` in; removed empty `docs/`).
- Added `spec/SPEC.md`, `spec/card.json`, `spec/punchlist.md`, `spec/README.md`
  tailored to the desktop tray app (Standards #1/#2/#3/#6).
- Added root `VERSION` (`0.6.0`) mirroring `_version.py` (#10); added this work
  log (#5); added `AGENTS.md` (#8) and `.github/copilot-instructions.md` (#19)
  with the `RM_standards_binding` marker (#25).
- Fixed stale version mismatch: `tokentray/_version.py` `0.5.3` → `0.6.0` to
  agree with the root `_version.py` / `pyproject.toml` / `v0.6.0` tag.
