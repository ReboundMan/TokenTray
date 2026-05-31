# Measuring Clawpilot usage (Clawpilot 5.1.2)

**Status:** investigation + design. Documents the measurement model only;
the parser/ingest changes are not yet implemented.
**Date:** 2026-05-29
**Author:** investigation triggered by "Clawpilot is running but not in the
client list".

## TL;DR

Clawpilot usage *is* recoverable, exactly. Clawpilot's backend
`assistant_usage` telemetry already lands in `~/.copilot/logs/process-*.log`
(the same files this tool already parses) but is keyed by an opaque *backend
session id* and carries client metadata identical to the standalone Copilot
CLI. The plaintext `~/.copilot/m-diagnostics.jsonl` maps each Clawpilot
session to its backend id. **Join the two and attribute matching `session_id`s
to `"Clawpilot"`.**

## Background: why Clawpilot is invisible today

`tokentray/parsers/copilot_logs.py` attributes *everything* under
`~/.copilot/logs/` to `"Copilot CLI"`. A May-2026 investigation retired the
old `client_type == "cli-server" -> Clawpilot` heuristic (the field
discriminates CLI versions, not host apps) and concluded Clawpilot "writes no
token telemetry locally". That conclusion is now outdated for Clawpilot 5.1.2.

## Evidence (verified 2026-05-29)

Environment: Clawpilot **5.1.2** installed at
`%LOCALAPPDATA%\Programs\Clawpilot`; active today.

1. **Clawpilot stores data under a `~/.copilot/m-*` namespace**, separate from
   the CLI's files:
   - `m-sessions/*.json` — conversation bodies, **AES-encrypted** (magic header
     `MSE`, key in `m-encryption-key.enc`). No readable token counts.
   - `m-sessions/sessions-index.json` — also encrypted.
   - `m-diagnostics.jsonl` (+ rotated `m-diagnostics.jsonl.old`) — **plaintext**
     operational log. Categories: `CopilotBackend`, `SessionManager`,
     `AutomationManager`, `M365*`, `Telemetry`, etc. Contains **no** token
     metrics (`grep input_tokens|output_tokens|totalTokens|usage` = 0 hits).
   - `m-memory.json`, `m-skills/`, `m-automations/`, `m-audit-logs/` — other
     Clawpilot state.

2. **`session-store.db` is not a usage source.** It gained a `host_type`
   column, but it is `NULL` for all 650 rows, has no token columns, and
   Clawpilot frontend session ids are not present in it.

3. **The decisive link — `m-diagnostics.jsonl` `SessionManager` lines:**

   ```
   Backend session created for: <frontend_id> (backend ID: <backend_id>)
   Backend session resumed for: <frontend_id> (backend ID: <backend_id>) — full conversation history restored
   ```

   The `<backend_id>` UUIDs **equal the `session_id`** of `assistant_usage`
   events in `~/.copilot/logs/process-*.log`. All 5 backend ids discovered in
   diagnostics had matching `assistant_usage` telemetry in the process logs.

4. **Telemetry content cannot distinguish Clawpilot from CLI.** Both emit
   `client_type == "cli-server"`, `cli_version 1.0.40`, and the *same*
   `client_name` hash. The diagnostics backend-id mapping is the only local
   discriminator.

5. **Magnitude.** On 2026-05-29 a single long-lived Clawpilot backend process
   log multiplexed 5 backend sessions (via resumes), totalling ~**23.6M
   tokens / 104 turns** — all currently mislabelled `"Copilot CLI"`. (The
   standalone CLI emitted no `assistant_usage` into that dir in the same
   window.)

## The model

### Inputs
- `~/.copilot/logs/process-*.log` — existing telemetry source.
- `~/.copilot/m-diagnostics.jsonl` and `~/.copilot/m-diagnostics.jsonl.old` —
  Clawpilot session→backend-id discovery source.

### Algorithm
1. **Discover Clawpilot backend ids.** Scan the diagnostics files line by line
   for the exact `SessionManager` patterns and capture only the backend UUID:

   ```
   Backend session (?:created|resumed) for:\s*[0-9a-f-]{36}\s*\(backend ID:\s*([0-9a-f-]{36})\)
   ```

   Validate UUID shape. Collect into a set `clawpilot_backend_ids`. Ignore all
   other diagnostic content (privacy: do not retain raw lines or frontend ids).

2. **Classify telemetry.** When emitting a `UsageEvent` from a process log,
   set `host_app = "Clawpilot"` if `session_id in clawpilot_backend_ids`, else
   `"Copilot CLI"`.

3. **Bucket as usual.** Existing day/hour/model aggregation works unchanged —
   attribution is per `session_id`, bucketing is per event `timestamp`. A
   long-lived Clawpilot session legitimately contributes to multiple days.

### Why it is correct
- Backend ids are backend-minted UUIDs; the chance of colliding with a
  genuine standalone-CLI `session_id` is negligible, and membership is
  *exclusive* — the "Backend session created/resumed for" line is only written
  for sessions Clawpilot itself spawned.
- Token counts are the real logged values, not an estimate. Accuracy
  qualifier: **exact for any Clawpilot session whose backend id is present in
  diagnostics (or in a persisted attribution mapping — see below).** Sessions
  whose diagnostics rotated away before discovery degrade gracefully to
  `"Copilot CLI"`.

## Durability concerns (must address when implementing)

These came out of design review and are the difference between "works in a
live demo" and "stays correct":

1. **Historical rows do not self-correct.** `history_store` event identity does
   not include `host_app`, and ingest is `INSERT OR IGNORE`, so re-parsing will
   not relabel already-stored rows (including the ~23.6M tokens above).
   Implementation needs a reconciliation pass, e.g.
   `UPDATE events SET host_app='Clawpilot' WHERE session_id IN (<ids>) AND host_app='Copilot CLI'`.

2. **Parser per-file cache can go stale.** The cache is keyed on process-log
   `(size, mtime_ns)`. If only `m-diagnostics.jsonl` changes, cached events
   keep their old label. Prefer caching *unclassified* events and applying
   `session_id`→host classification *after* cache retrieval (classification is
   cheap), or fold a diagnostics fingerprint into the cache key.

3. **Ingest watermark can skip correction.** `ingest_logs()` early-returns when
   source `(size, mtime)` is unchanged. Treat diagnostics as its own
   attribution input with its own watermark, or run reconciliation on a
   separate path from event ingestion.

4. **Diagnostics rotation loses old mappings.** `m-diagnostics.jsonl.old` only
   holds one prior generation. Persist each discovered `backend_id -> Clawpilot`
   mapping locally (e.g. a small table in `history.db`) so attribution survives
   rotation. Use diagnostics as a *discovery* source, not the long-term source
   of truth.

5. **Telemetry/diagnostics write race.** A telemetry event may be parsed before
   its diagnostics line is flushed. Make attribution *eventually consistent*:
   every refresh that discovers new backend ids reclassifies existing rows for
   those `session_id`s (ties into #1/#4).

## Privacy

`m-diagnostics.jsonl` contains unrelated operational context (M365 tool calls,
etc.). The parser must read it line by line, extract only backend UUIDs via the
strict regex, and store neither raw diagnostic lines nor frontend session ids.
No network egress; consistent with the tool's local-only posture.

## Rejected / non-viable alternatives

- **Decrypt `m-sessions/*.json`** using `m-encryption-key.enc`: brittle,
  invasive, version-coupled, and unnecessary — the token data is already in the
  process logs.
- **`client_type` / `client_name` heuristic:** CLI and Clawpilot backend are
  byte-identical here. This is exactly the heuristic that was (correctly)
  retired.
- **`m-diagnostics.jsonl` as the token source:** it has no token metrics.
- **`session-store.db host_type`:** unpopulated (all `NULL`).

## Out of scope here
Implementation (parser change, history reconciliation, Advanced-tab row split,
tests). This document defines the model and the constraints any implementation
must satisfy.
