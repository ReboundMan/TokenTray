# TokenUsageTray — Punchlist

> Quick capture of features, ideas, and fixes. Newest items go at the top of each section.
> Use `punch "<idea>"` from the project root to add to **Ideas / Backlog** (default), or `punch -Section Bugs "..."` for other sections.

## 🔥 Now (In Progress)

<!-- items currently being worked on -->

## 📋 Next (Up Soon)

<!-- next items to pick up -->

## 💡 Ideas / Backlog

- [x] 2026-07-23 — (S)(H) Show Claude Code usage alongside Copilot CLI; the vault's metrics/runs.csv is a ready-made source for vault-agent spend _(vault: processed/2026-07-23-tokentray-claude-code-update.md)_. Spec drafted 2026-07-28 (features/show-claude-code-usage-alongside-copilot.md, status: built). shipped 2026-08-05 (PR #1 merged)
<!-- new feature ideas land here by default -->

## 🐛 Bugs

<!-- known issues -->

## ✅ Done

<!-- completed items (most recent on top) -->

- [x] Code-sign the installer via Azure Artifact Signing, reusing the WordMD
  publisher setup (WordMD spec 10024): shared `WordMD-PublicTrustCert` profile
  (Basic tier allows one Public Trust profile per account; re-confirmed
  account-scope Signer deliberately per that spec's standing caveat),
  `build.ps1 -Sign`, `#ifdef SIGN` in TokenTray.iss, CI demoted to unsigned
  build validation, checklist in spec/DEPLOY.md. First signed release pending.
  (2026-08-16)

- [x] Claude Code usage double-counted: one transcript line per content block,
  each repeating the same `message.usage` (same `requestId`), counted as
  independent events (2.1x measured). Parser now dedupes by `requestId`,
  last line wins; schema v5 purges inflated rows and re-ingests from
  transcripts. (2026-08-06)

- [x] Advanced tab under-counted and lost per-tool / per-model detail after
  resuming prior sessions (showed ~30M / one model vs Today's 401M across
  several LLMs). Session-state rollups now key by `(session, host, model)` and
  are authoritative for their session (ingest purges stale non-rollup rows), so
  resumed sessions update in place and multi-model fleet runs keep every model.
  Schema v4 adds `is_rollup`. (2026-06-22)

- [x] Today vs History totals disagreed when a long-running Agency session was
  active: the cumulative store accumulated every growing `session.shutdown`
  rollup snapshot as a separate row. Rollups now key by session and REPLACE in
  place; schema v3 migration collapses existing duplicates. (2026-06-07)

