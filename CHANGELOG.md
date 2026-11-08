# Changelog

All notable changes to TokenTray are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.3] - 2026-11-08

### Fixed
- The tray icon's loading tooltip still said "Copilot CLI tokens"; it now
  says "TokenTray: token usage", and the steady-state hover tooltip leads
  with the app name.
- The chart hover tooltip vanished in under a second, for two stacked
  reasons: QToolTip shown without an anchor rect hides on the first cursor
  movement, and even anchored, Qt's dwell-triggered ToolTip event hides a
  manual tip when the widget under the cursor has no tooltip text of its
  own. The hover now also sets the text on the chart view, so the dwell
  re-shows instead of hiding. The tip persists while the cursor sits on
  the bar, hides on leaving it, and is replaced when hovering another bar.

## [1.0.2] - 2026-11-08

### Added
- Hovering a bar in the 7-day chart shows that day's breakdown as a
  tooltip: compact and exact totals, cached/uncached/output split, turns,
  and sessions.

## [1.0.1] - 2026-11-04

### Changed
- Token counts in the popup now carry thousands separators ("13,974M"
  instead of "13974M") in the Advanced tables, Today stat tiles, History
  tiles, and the per-tool/per-model split lines. The tray badge keeps the
  compact form; a comma costs too much at 16 pixels.
- The Buy Me a Coffee dialog is now an About TokenTray dialog following the
  WordMD model: app name and version, product-page and GitHub links, then
  the support section. The honor-system unlock and 21-day nag cadence are
  unchanged. The tip destination is now the stable
  `reboundman.com/tokentray-tip` redirect (currently pointing at Buy Me a
  Coffee; repointable to a Stripe Payment Link without an app release).
  Tray menu: "Buy me a coffee" under Settings became a top-level
  "About TokenTray".
- Fleet-review fixes (Hawk/Sage/Lens/Beacon/Rookie, reviews/
  about-screen-and-product-page-*.md): gate-reason dialogs are titled
  "Unlock Advanced history" instead of "About TokenTray"; the suppress
  checkbox only appears on the startup nag (the only path that honors it);
  Enter now defaults to "Maybe later" instead of the unlock; the
  advanced-toggle gate copy no longer describes the ended trial as
  upcoming; banner text no longer points at the deleted Settings menu item;
  the locked-Advanced pane no longer claims VS Code capture; screen-reader
  description on the tip button, visible keyboard-focus indicators, and a
  WCAG-compliant checkbox border; README repo URLs and signing note
  corrected.

## [1.0.0] - 2026-08-16

The first signed release. TokenTray has been feature-stable across the 0.6-0.7
line (Copilot CLI, Agency, and Claude Code sources; history; the Advanced tab),
and shipping with a real publisher signature is the milestone that marks 1.0.

### Added
- Code signing via Azure Artifact Signing (publisher `ReboundMan.com LLC`):
  `build.ps1 -Sign` signs `TokenTray.exe` before packaging, the installer
  and uninstaller via Inno Setup's `SignTool=`, verifies both signatures
  including signer identity, and emits SHA256 files. Signing is local-only;
  CI now builds an `-UNSIGNED`-suffixed installer for validation and no
  longer publishes releases.

## [0.7.1] - 2026-08-06

### Fixed
- Claude Code usage counted at roughly double its true value. Claude Code
  writes one transcript line per content block and repeats the same
  `message.usage` object (same `requestId`) on every line of an API
  response; the v0.7.0 parser counted each line as an independent event
  (measured 2.1x inflation: 32,815 lines vs 15,518 actual requests).
  Events now dedupe by `requestId` with last-line-wins (the last line
  carries the request's final totals), including when a request's lines
  straddle an offset-resume boundary or, defensively, repeat across
  files. Schema bumped to v5: the migration purges the inflated Claude
  Code rows and clears their refresh watermarks so history re-ingests
  correct totals from the transcripts on the next refresh.

## [0.7.0] - 2026-08-05

### Added
- Claude Code usage pane: TokenTray now reads `~/.claude` transcripts and shows
  Claude Code token usage alongside Copilot, with a per-tool Today split, a
  per-model tooltip breakdown, and an offset-resume transcript cache so large
  transcript files are not re-parsed on every refresh.

## [0.6.2] - 2026-06-22

### Fixed
- Advanced tab under-counting and missing per-tool / per-model detail after
  resuming prior Copilot CLI sessions. The Copilot session-state
  `session.shutdown` rollup has the same cumulative, fixed-timestamp shape as
  the Agency rollup fixed in 0.6.1 but was never given rollup keying, and the
  tray skipped any session-state event whose session id was already persisted.
  Resuming a session (a common workflow) therefore dropped all of its later
  growth, freezing History while the live Today tab kept climbing, and the
  Advanced "By model" / "By tool used" tables showed only a fraction of usage.
  Session-state rollups now key by `(session, host, model)` so multi-model
  fleet runs keep one row per model, and a rollup is treated as the
  authoritative total for its session: ingest purges that session's stale
  non-rollup rows so a resumed session updates in place instead of stacking
  duplicates. Schema bumped to v4 (adds an `is_rollup` column).

## [0.6.1] - 2026-06-12

### Fixed
- Today vs History token mismatch when a long-running Agency session was active.
  An Agency `session.shutdown` rollup keeps a fixed timestamp while its
  cumulative totals grow, and the store dedup id included token counts, so each
  larger snapshot was stored as a new row instead of replacing the prior one.
  Agency rollups now key by session and ingest with `INSERT OR REPLACE`. Schema
  bumped to v3 with a migration that collapses existing duplicate rollup rows.

## [0.6.0] - 2026-05-31

### Added
- Single-instance guard so only one TokenTray can run at a time.

### Changed
- Support GitHub Copilot CLI 1.0.54+ telemetry log format.

## [0.5.3] - 2026-05-26

### Fixed
- Clawpilot host/model misattribution.
- Recovery of Agency sessions that lack an `events.jsonl` file.

## [0.5.2] - 2026-05-25

### Fixed
- Buy Me a Coffee button visibility.
- Advanced-tab table contrast.
- Tab selection persistence across popup opens.

## [0.5.1] - 2026-05-25

### Fixed
- Buy Me a Coffee dialog contrast.
- Advanced-tab unlock flow.

## [0.5.0] - 2026-05-24

### Added
- Phase 3 history: schema migration and a Buy Me a Coffee gated Advanced tab.

## [0.4.1] - 2026-05-24

### Fixed
- Buy Me a Coffee link now points at the real slug
  (`buymeacoffee.com/reboundman`) in the coffee dialog, README, and
  `.github/FUNDING.yml`.

## [0.4.0] - 2026-05-24

### Added
- Honor-system supporter unlock for re-enabling Advanced history after the
  60-day trial, backed by a local SQLite flag (no backend, no license keys, no
  telemetry).
- 21-day startup nag for users past the trial who have not unlocked, with a
  permanent "Don't show again" option.
- Settings entry to restore supporter status.

## [0.3.0] - 2026-05-24

### Added
- Local-only History tab with Day / Week / Month / All-time rollups persisted to
  SQLite at `%LOCALAPPDATA%\TokenTray\history.db`, surviving Copilot CLI log
  rotation.
- 60-day free trial of local recording, auto-enabled on first launch.
- Settings toggle `Advanced history (record locally)` to gate capture after the
  trial; existing data stays viewable either way.

### Privacy
- No backfill across recording-off windows: a `recording_active_since_utc`
  watermark drops events older than the most recent enable.

## [0.2.1] - 2026-05-23

### Added
- Light-dismiss: the popup closes on focus loss.

## [0.2.0] - 2026-05-21

### Added
- Settings menu.
- Version label in the popup.

[Unreleased]: https://github.com/ReboundMan/TokenTray/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/ReboundMan/TokenTray/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/ReboundMan/TokenTray/compare/v0.5.3...v0.6.0
[0.5.3]: https://github.com/ReboundMan/TokenTray/compare/v0.5.2...v0.5.3
[0.5.2]: https://github.com/ReboundMan/TokenTray/compare/v0.5.1...v0.5.2
[0.5.1]: https://github.com/ReboundMan/TokenTray/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/ReboundMan/TokenTray/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/ReboundMan/TokenTray/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/ReboundMan/TokenTray/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ReboundMan/TokenTray/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/ReboundMan/TokenTray/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/ReboundMan/TokenTray/commits/v0.2.0
