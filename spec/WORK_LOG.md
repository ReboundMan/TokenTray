# TokenTray — Work Log

Reverse-chronological log of meaningful work. Quick capture as it happens.

## 2026-06-22
- Fixed Advanced tab under-counting and lost per-tool / per-model detail after
  resuming prior sessions (reported: Advanced showed 30M / one model while
  Today showed 401M across several LLMs). Root cause was two-fold. (1) The
  Copilot session-state `session.shutdown` rollup has the same cumulative,
  fixed-timestamp shape as the Agency rollup fixed in 0.6.1 but was never
  flagged `is_rollup`, so a growing resumed session stored each snapshot as a
  new content-addressed row. (2) `tray_app.refresh` and `ingest_logs` skipped
  any session-state event whose session id was already in the DB, so resuming
  a session (its id already persisted) dropped all later growth and left a
  stale snapshot under the original date.
- Fix: session-state rollups now carry `is_rollup=True` and key by
  `(session, host, model)` (one row per model, so multi-model fleet runs keep
  every model). A rollup is authoritative for its whole session, so `ingest`
  purges that session's stale non-rollup rows before inserting, superseding
  legacy / per-turn rows (including retired `Clawpilot` attributions) instead
  of double-counting. Removed the broad "skip if already persisted" dedup;
  cross-source double-counting within a parse is still handled inside
  `iter_all_events`. Schema bumped to v4 (adds `is_rollup`; no up-front row
  deletion, so a currently-active resumed session never vanishes from History).
- Verified against a copy of the live DB: Advanced "By model" repopulated with
  all four models, DB Today matched the live Today parse (within the expected
  active-session estimate delta), zero duplicate rollup keys, and 28 days of
  history preserved. Added tests in `tests/test_history_store.py` and
  `tests/test_copilot_session_state.py`. Bumped version 0.6.1 -> 0.6.2.

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
