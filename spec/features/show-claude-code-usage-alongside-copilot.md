---
feature: show-claude-code-usage-alongside-copilot
status: draft
agent: emily
drafted: 2026-07-28
source: spec/punchlist.md (Ideas, 2026-07-23), from vault processed/2026-07-23-tokentray-claude-code-update.md; priority confirmed H by JJ 2026-07-28
size: S
priority: H
---

# Show Claude Code usage alongside Copilot CLI

Drafted by Emily. The capture's insight is that the hard part is already done: the vault's ledger is a ready-made data source, so this is a reader and a pane, not a metering project.

## Problem

TokenUsageTray shows Copilot CLI usage and nothing else, while the household's heaviest model spend has moved to Claude Code: the vault fleet alone books multiple runs daily into `C:\Code\Vault\metrics\runs.csv` (Medici's ledger: date, job, trigger, provider, model, quantity, unit, cost_usd). The tray answers yesterday's question.

## Charter

Add a Claude Code section to the tray fed from the vault ledger: per-agent counts and spend alongside the existing Copilot view, read-only, refreshed on the tray's existing cadence.

## Behavior

1. Read `metrics/runs.csv` (path configurable, defaulting to the vault; BOM-tolerant, header-checked, missing file shows an honest "no ledger found" rather than an empty pane).
2. Aggregate the recent window (default: current month) by `job`: run count, quantity with unit where uniform, summed `cost_usd` where present. **Honesty rule from the ledger's own semantics:** subscription-covered runs carry blank cost, so the pane never invents dollars; it shows "N runs, $X metered" with metered clearly scoped, not a fake total.
3. Render alongside Copilot in the tray's existing visual language; the agent names (emily, hopper, dewey, marcus, and company) are the row labels, which makes the tray a payroll view in miniature.
4. No writes, ever: the ledger belongs to Medici; the tray is a reader.

## Relationships

- **Connects to** `C:\Code\Vault\metrics\runs.csv` and `scripts/record_cost.py` (Medici): the source of truth and its schema doc.
- **Connects to** the vault Ideas item "One view of overnight agent runs and costs across vault and app fleets": that item wants the cross-fleet ledger view; this tray pane is a first consumer and a proof of shape, not a replacement. Flagged so the two do not diverge into rival dashboards.
- **Contradicts** nothing found in this repo.

## Acceptance

- The tray shows Claude Code usage per agent for the current window next to Copilot, with metered cost scoped honestly and blank-cost runs counted but never priced; a missing or malformed ledger degrades to a clear message, never a crash.

## Open questions

- Window: current month, rolling 30 days, or match whatever the Copilot pane uses? Emily proposes matching the Copilot pane for visual coherence.
- Should the pane deep-link to the vault's weekly activity report for the full payroll? Cheap if the tray supports links; cosmetic otherwise.
