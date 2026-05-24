# Phase spec: Unified token-usage parser + Advanced tab

**Status**: Approved (2026-05-24)
**Owner**: jeffjame
**Related**: AgencyUsageReport (consumer)

## Goal

1. Single source of truth for token-usage parsing lives in TokenTray.
2. AgencyUsageReport consumes the same parser instead of maintaining its own copy.
3. Both projects ingest three log sources without double-counting:
   - `~/.copilot/logs/process-*.log` (Clawpilot + direct Copilot CLI)
   - `~/.agency/logs/session_*/events.jsonl` (Agency wrapper)
   - `~/AppData/Roaming/Code/logs/**/GitHub Copilot Chat.log` (VS Code Copilot Chat)
4. New per-event fields (`host_app`, `model`) are surfaced in TokenTray on a BMC-gated "Advanced" tab.
5. Popup-open latency stays under target (=250 ms warm / =750 ms cold) even after the new sources land.

## Background

Today TokenTray parses only `~/.copilot/logs/` and reports a single token total. AgencyUsageReport has a near-duplicate parser and the same blind spot.

Investigation (see session checkpoint `001-investigating-agency-cli-token.md` for full evidence) found:

- **Clawpilot and direct Copilot CLI** both land in `~/.copilot/logs/`. They are distinguishable via `client.client_type` (`cli-server` vs `cli-interactive`).
- **Agency** keeps a parallel log universe under `~/.agency/logs/session_*/`. Disjoint filenames from `~/.copilot/logs/` (no double-count risk). 30-day volume: Agency 1.89 GB / 62 logs vs Copilot 80 MB / 11 logs - Agency is the user's dominant workflow but currently invisible to both tools.
- **VS Code Copilot Chat** does NOT spawn a Copilot CLI subprocess; it calls GitHub's cloud API directly. Local trail is only the extension-host log `GitHub Copilot Chat.log` which has request-level info but **no token counts**.
- Every host exposes the `model` field, but in slightly different formats - normalization required.

## Source-of-truth file layout (TokenTray repo)

```
TokenUsageTray/
  usage_core.py            # existing - keep; extend UsageEvent; re-export parsers
  parsers/
    __init__.py            # exports iter_all_events(), normalize_model(), detect_host()
    copilot_logs.py        # lifted from usage_core.iter_usage_events
    agency_events.py       # NEW - ~/.agency/logs/session_*/events.jsonl
    vscode_ccreq.py        # NEW - VS Code Copilot Chat.log
    model_normalize.py     # NEW - canonical model names across hosts
    host_detect.py         # NEW - Clawpilot/CLI/Agency/VSCode discriminator
  pyproject.toml           # NEW - declares tokentray package, parsers subpackage exposed
  docs/specs/              # NEW directory convention; this file is the first entry
```

`usage_core.py` re-exports the public API so existing tray-app imports (`from usage_core import iter_usage_events, UsageEvent`) keep working without change.

## UsageEvent schema additions (additive, all optional)

```python
@dataclass
class UsageEvent:
    timestamp: datetime
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # NEW (all optional - default None for legacy logs and old DB rows)
    host_app: str | None = None       # 'Clawpilot' | 'Copilot CLI' | 'Agency' | 'VS Code' | None
    model: str | None = None          # normalized: 'claude-opus-4.7', 'gpt-5.4-mini', ...
    raw_model: str | None = None      # exact string from the source
    source_path: str | None = None    # which log file this came from (debug aid)
    is_estimated: bool = False        # True for VS Code tokens derived from duration
```

`total` property unchanged. `cache_write` stays excluded from `total` (matches existing formula).

## Host detection rule

```python
def detect_host(log_path: Path, client_type: str | None) -> str:
    s = str(log_path).lower()
    if r"\.agency\logs\\" in s:
        return "Agency"
    if r"\code\logs\\" in s:
        return "VS Code"
    if client_type == "cli-server":
        return "Clawpilot"
    if client_type == "cli-interactive":
        return "Copilot CLI"
    return "Unknown"
```

Path takes precedence over `client_type` because the Agency-wrapped CLI subprocess reports `cli-server` too (it IS a CLI server, just spawned by Agency).

## Model normalization

```python
def normalize_model(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.split(" -> ")[-1].strip().lower()       # VS Code alias arrow
    raw = raw.replace("-1m-internal", "").replace("-internal", "")
    raw = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", raw)     # OpenAI date stamp
    return raw or None
```

Test cases:

| Raw | Normalized |
|---|---|
| `claude-opus-4.7` | `claude-opus-4.7` |
| `claude-opus-4.7-1m-internal` | `claude-opus-4.7` |
| `claude-opus-4.6 -> claude-opus-4-6` | `claude-opus-4-6` |
| `gpt-4o-mini-2024-07-18` | `gpt-4o-mini` |
| `gpt-5.4-mini` | `gpt-5.4-mini` |

## Per-source parser behavior

| Source | Path | What we parse | Token data |
|---|---|---|---|
| Copilot CLI | `~/.copilot/logs/process-*.log` | `[Telemetry] cli.telemetry:` blocks where `kind == assistant_usage` | Full |
| Agency | `~/.agency/logs/session_*/events.jsonl` | One UsageEvent per `assistant.message` event; fallback to `session.shutdown.modelMetrics` rollup only when no per-turn data exists | Full |
| VS Code | `~/AppData/Roaming/Code/logs/*/window*/exthost/GitHub.copilot-chat/GitHub Copilot Chat.log` | `ccreq:<id>.<*> \| <status> \| <model> \| <ms>ms \| [<source>]` regex | None measured; tokens estimated (see below) |

## De-dup / double-count guards

- Agency process logs are disjoint from `~/.copilot/logs/`. No path overlap (verified).
- TokenTray's `history_store` uses a SHA-1 primary key over `(timestamp, session_id, metrics)`. Any event that somehow appeared twice would dedupe for free.
- Agency `events.jsonl` is per-turn (`assistant.message`), NOT the `session.shutdown` rollup. We pick ONE source per session - prefer per-turn if present, fall back to shutdown rollup only when no per-turn data exists.

## VS Code: no-double-count verification gate

VS Code Copilot Chat does not write token-level data locally. We may *estimate* tokens from request duration, but only after proving the same chat traffic is not already captured by another parser:

1. **Scripted overlap check** (must run as part of Phase 2): enumerate every `~/.copilot/logs/process-*.log` and every `~/.agency/logs/session_*/process-*.log` whose mtime window overlaps a known VS Code chat-session window. If any such log contains non-zero `assistant_usage` events with VS-Code-pattern client metadata, VS Code IS double-routed and estimation MUST stay off.
2. **Spot-check today's data**: today's VS Code session 06:31-07:01 produced 55 ccreq lines. The only `~/.copilot/logs/process-*.log` with overlapping mtime today is PID 7072 (a Clawpilot session that started yesterday) - unrelated. Initial signal: no double-count.
3. **Document the verification result** in `tests/fixtures/verification_log.txt` and a test asserting estimation is gated on the documented result.

If verification passes, the estimation algorithm:

```python
def estimate_vscode_tokens(model: str, duration_ms: int, calibration: dict) -> int:
    rate = calibration.get(normalize_model(model))  # tokens/sec for that model
    if rate is None:
        return 0  # no calibration -> show zero rather than guess
    return int(rate * duration_ms / 1000)
```

Calibration table is recomputed periodically from the user's own recent Agency/CLI data (where both `duration` AND token counts are known), so accuracy improves over time. Estimated rows in the UI are marked `(est.)` so measured vs modeled is always distinguishable.

If verification fails, VS Code falls back to request-counts-only rows with `input_tokens = output_tokens = 0`.

## TokenTray UI changes

`popup_window.py`:
- New tab `_build_advanced_tab()` registered after "History".
- Tab is always visible. Content depends on supporter status:
  - If `tier_status.advanced_enabled and tier_status.supporter_purchased`: render the breakdowns.
  - Otherwise: locked-state placeholder with a "Buy me a coffee to unlock" button that calls `coffee_dialog.show_coffee_dialog(..., reason="advanced_tab")`.
- Unlocked content has two tables:
  1. **By tool used** (host_app): one row per `Clawpilot / Copilot CLI / Agency / VS Code / Unknown` with Today / Week / Month totals + turn counts.
  2. **By model**: one row per normalized model with Today / Week / Month tokens + premium-request counts. VS Code rows marked `(est.)` when applicable.
- Tab content is built **lazily** on first `currentChanged` signal, not in `__init__`.

`history_store.py`:
- Schema migration: add nullable `host_app` and `model` columns to the events table. `PRAGMA user_version` bump.
- New aggregation methods: `totals_by_host(period)`, `totals_by_model(period)`.
- Pre-migration rows surface as `"Unknown"` in the UI.

`coffee_dialog.py`:
- Add `"advanced_tab"` as a recognized `reason` with copy: "Buy me a coffee to unlock per-tool and per-model breakdowns."

## AgencyUsageReport changes (Phase 4 only)

`build_report.py`:
- Self-bootstrap shim (see Cross-repo wiring below).
- Replace inline `extract_tokens_from_logs()` with `from tokentray.parsers import iter_all_events`.
- Picks up Agency events automatically - the report finally captures the user's primary workflow.
- Tracking DB adds nullable `host_app` and `model` columns (additive migration).
- WoW token card grows a small per-host breakdown line: e.g. "Agency 1.2M | Clawpilot 340K | CLI 90K | VS Code ~45K (55 req)".
- `$275/hr` value-of-time stays as-is - it represents BVL calculation input, not a cost number.

## Cross-repo wiring: self-bootstrapping pip install

TokenTray ships a `pyproject.toml` so it is installable from its public GitHub URL. `build_report.py` self-bootstraps on first run:

```python
def _ensure_tokentray():
    try:
        from tokentray.parsers import iter_all_events
        return iter_all_events
    except ImportError:
        import subprocess, sys
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "--quiet",
            "git+https://github.com/ReboundMan/TokenTray.git",
        ])
        from tokentray.parsers import iter_all_events
        return iter_all_events

iter_all_events = _ensure_tokentray()
```

| Distribution channel | How it keeps working |
|---|---|
| Hand-the-prompt to a human | Their agent runs build_report.py -> tokentray missing -> pip installs from GitHub -> continues. ~5s one-time delay. |
| Hand-the-prompt to an agent | Same. The agent's venv just acquires tokentray. |
| You running locally | Identical to today after first run. |
| Air-gapped user | pip install fails; one-time manual install required. Documented in the prompt. |

`Agency_Usage_prompt.md` gets a note in the Setup section pointing at the manual fallback.

## Phase 0 - Popup performance baseline + fix

User reports the popup currently takes multiple seconds to open. Adding three more log sources without addressing this would make it worse. Phase 0 sits before everything else.

**Profile** the popup-show path with `time.perf_counter()` checkpoints in `PopupWindow.update_stats()`:
- (a) log-file scan, (b) history DB query, (c) chart render, (d) layout pass.

Log the timings via TokenTray's debug log on one warm + one cold run.

**Likely culprits** (to be confirmed by profiling):
- `usage_core.iter_usage_events()` rescans every file in `~/.copilot/logs/` on every popup open. With log volume about to grow to ~2 GB from Agency, this is going to dominate.
- Chart series rebuilt from scratch each open.
- All tabs build content in `__init__` rather than on first show.

**Fix recipes** (apply only what profiling justifies):
1. **Read from history DB, not logs**: the background tray timer already ingests new events idempotently via `history_store.ingest()`. The popup should query the DB for "today's totals" rather than re-parse logs.
2. **Lazy tab content**: build each tab's widgets only on first `currentChanged` to that tab. Big win for the new Advanced tab.
3. **Memoize today's totals** with a 2-second TTL so multiple repaints within a single popup session don't re-query.
4. **Defer chart render** to `QTimer.singleShot(0, ...)` so text paints immediately and the chart fills in a frame later.

**Acceptance**:
- Debug-logged open time under 250 ms warm / 750 ms cold on the current dataset.
- `tests/test_popup_perf.py` asserts `update_stats()` returns under a sane upper bound on a fixture-sized DB (sanity check, not a strict perf gate).

## Tests to add (TokenTray)

- `tests/test_parsers/test_copilot_logs.py` - existing usage_core tests moved here; assert `host_app == "Clawpilot"` or `"Copilot CLI"` based on `client_type`.
- `tests/test_parsers/test_agency_events.py` - synthetic `events.jsonl` fixture; assert per-turn UsageEvents + `host_app="Agency"` + model normalization + fallback to shutdown rollup when per-turn data absent.
- `tests/test_parsers/test_vscode_ccreq.py` - synthetic ccreq log; assert request-count rows; assert estimation when calibration provided; assert zero tokens when not.
- `tests/test_parsers/test_model_normalize.py` - table of (raw, expected) pairs from the table above.
- `tests/test_parsers/test_host_detect.py` - every path + client_type combination.
- `tests/test_history_store_advanced.py` - schema migration adds new columns; round-trip with host_app/model; old rows surface as "Unknown"; new aggregation methods.
- `tests/test_popup_perf.py` - performance sanity bound (Phase 0).

## Phase ordering

0. **Performance baseline + fix** - profile, then apply the smallest set of changes that hits the open-time target. No new sources yet. Spec committed first.
1. **Shared parser refactor** - move CLI parsing into `parsers/copilot_logs.py`, extend UsageEvent, add `pyproject.toml`. No behavior change for the existing tray.
2. **Add Agency + VS Code sources** - with verification gate before VS Code estimation goes live. `iter_all_events()` fans out across all sources.
3. **Advanced tab UI** - DB schema migration, new lazy-built tab, BMC gate via existing `coffee_dialog`.
4. **AgencyUsageReport swap-in** - replace inline parser with self-bootstrapping import. WoW token card grows per-host breakdown. `$275/hr` untouched.

Each phase is independently shippable.

## Open follow-ups (not in this spec)

- MCP-tool attribution (which tools the AI invoked). Different parsing path (the `tool.execution_complete` events in Agency, or `tool_complete_call_id` telemetry kind in Copilot CLI). Larger scope; intentionally deferred.
- Publishing `tokentray` to PyPI so the install URL can become `pip install tokentray`. Not required for v1.
- Replacing `$275/hr` value-of-time with actual `metrics.cost` if/when the BVL math gets revisited. Out of scope.
