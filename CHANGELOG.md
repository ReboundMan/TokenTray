# Changelog

All notable changes to TokenTray are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
