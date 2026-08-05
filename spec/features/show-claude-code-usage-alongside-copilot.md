---
spec: 10017
feature: show-claude-code-usage-alongside-copilot
status: ready
revised: 2026-07-31 (JJ review round 1); 2026-07-31 (Emily round 1 rework: vault-ledger design replaced with the tools' own generic storage, verified on disk)
agent: emily
drafted: 2026-07-28
source: spec/punchlist.md (Ideas, 2026-07-23), from vault processed/2026-07-23-tokentray-claude-code-update.md; priority confirmed H by JJ 2026-07-28
size: S
priority: H
---
# Show Claude Code usage alongside Copilot CLI

Reworked by Emily after JJ's round-1 review killed the first design's core assumption. The original draft read the vault fleet's private cost ledger; TokenUsageTray is a general-use app that anyone downloads, so it may assume nothing about JJ's personal setup. This revision reads what the Claude tools themselves write to disk on every machine they run on.

## The ask

<<\<UNTRUSTED PUNCHLIST CONTENT
2026-07-23 — (S)(H) Show Claude Code usage alongside Copilot CLI; the vault's metrics/runs.csv is a ready-made source for vault-agent spend _(vault: processed/2026-07-23-tokentray-claude-code-update.md)_
UNTRUSTED PUNCHLIST CONTENT>>>

Provenance: `spec/punchlist.md` Ideas / Backlog, line dated 2026-07-23. Vault note `processed/2026-07-23-tokentray-claude-code-update.md` transcribed from `inbox/raw/2026-07-23T082712-capture.md` (JJ's voice is the origin). Filed by commit `a922748` ("emily: Claude Code usage pane spec from the vault ledger", 2026-07-28). Section retrofitted 2026-08-02.

## Problem

The tray shows Copilot CLI usage and nothing for Claude, while for many users (JJ included) Claude Code is the heavier tool. The data exists locally on every machine that runs Claude Code; the tray just never looks at it.

## Model (the tools' own storage, verified on this machine 2026-07-31)

* **Claude Code session transcripts** live at `%USERPROFILE%\.claude\projects\<project-slug>\<session-id>.jsonl`, one JSONL file per session, one line per message. Assistant-message lines carry a `usage` object, verified verbatim from a live transcript: `"usage":{"input_tokens":5651,"cache_creation_input_tokens":2755,"cache_read_input_tokens":25133,"output_tokens":519,...}` alongside `"model":"claude-opus-4-8"` and a timestamp. This is the generic, tool-owned source: no JJ pattern, no ledger, present wherever Claude Code has run.

* The `~/.claude` home also holds `history.jsonl`, `sessions/`, and `tasks/` (directory listing verified); whether any of these carries aggregated usage is **unverified**, and the transcripts alone are sufficient for v1.

* **Claude Desktop / Cowork**: `%APPDATA%\Claude` exists on this machine; its internal storage format for session or token data is **unverified** (not asserted). Desktop support is scoped as a follow-on spike, not a v1 claim.

* **Privacy-relevant fact**: transcripts contain full conversation content. The tray must read only the `usage`, `model`, and timestamp fields and must never parse, store, or display message content; this is a hard rule, not an optimization.

## Charter

A Claude Code pane beside the Copilot pane, fed exclusively from the tool's own local storage, working on any machine with a default Claude Code install and degrading to an honest "no Claude Code data found" when the directory is absent. Zero assumptions about who the user is.

## Behavior

1. **Discovery**: default root `%USERPROFILE%\.claude\projects`, overridable in the tray's existing settings. Absent directory: the pane says so plainly and stays quiet.
2. **Parsing**: stream each session `.jsonl`, extracting only usage/model/timestamp per assistant message. Files grow large and accumulate; parse incrementally with a per-file offset cache keyed by (path, size, mtime), the same shape the tray's Copilot rollup cache already uses, so a refresh reads only new bytes.
3. **Aggregation**: tokens by day and by model (input, output, cache-read and cache-creation kept distinct, since cache reads dominate raw counts and would mislead if lumped in), plus per-project rollups from the directory slug. Session counts come free from file counts.
4. **Display**: alongside the Copilot pane in its visual language, window matched to whatever the Copilot pane uses. Token counts are the truth; **no dollar figures**, because subscription-plan usage has no meaningful per-token price and inventing one would be exactly the dishonesty the first draft's ledger-notes rule existed to avoid. A pricing table for API users can be a later opt-in.
5. **Isolation**: read-only, content-blind, and resilient: a malformed line or unreadable file skips with a count of skipped entries surfaced in a tooltip, never a crash.

## Relationships

* **Connects to** the tray's existing Copilot ingestion and cache (the incremental-parse pattern to mirror; its session-rollup fix in the punchlist Done section is prior art for the offset cache).

* **Diminishes, deliberately**: the first draft's dependence on `C:\Code\Vault\metrics\runs.csv`. JJ's own machine benefits incidentally (his transcripts are as generic as anyone's), and the vault ledger remains the fleet's payroll view, unrelated to this app.

* **Contradicts** nothing found in this repo.

## Acceptance

* On a machine with Claude Code and no JJ infrastructure whatsoever, the pane shows per-day, per-model token usage from the transcripts alone; on a machine without Claude Code, it says so and shows nothing invented.

* Message content is never read beyond field extraction, never stored, never displayed; code review can verify the parser touches only the three field groups.

* Large transcript directories refresh incrementally; a full cold scan is bounded and reported the first time only.

* No dollar amounts appear anywhere in v1.

## Open questions

* **JJ, 2026-07-31:** This is wrong - it should not read anything associated with my personal implementation, it should read a generic token counter from the place that Claude, Claude Code, and CLaud Cowork store them.  This is an app that is general use and many people download and use it, so we can't assume any of the JJ Patterns are in place - we need to read usage from how the tools themselves track usage, sessions, tokens, etc

  * *Emily, 2026-07-31: reworked as directed; the design above reads only* *`%USERPROFILE%\.claude`* *transcript storage, verified generic, with the vault ledger fully removed. One piece I could not verify from here: where Claude Desktop/Cowork stores its usage (its* *`%APPDATA%\Claude`* *folder exists but I did not assert its format). Scoped as a follow-on spike below rather than guessed at.*

* Desktop/Cowork spike: identify whether `%APPDATA%\Claude` (or another location) exposes parseable session or token data; until then the pane is Claude Code only, labeled as such.\
  JJ: fine for now

* Window: matched to the Copilot pane (carried over from round 1, still Emily's proposal).\
  JJ: build it as spec'd and we'll do a full review once it is working
