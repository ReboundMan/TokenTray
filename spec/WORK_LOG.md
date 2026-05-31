# TokenTray — Work Log

Reverse-chronological log of meaningful work. Quick capture as it happens.

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
