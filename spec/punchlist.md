# TokenUsageTray — Punchlist

> Quick capture of features, ideas, and fixes. Newest items go at the top of each section.
> Use `punch "<idea>"` from the project root to add to **Ideas / Backlog** (default), or `punch -Section Bugs "..."` for other sections.

## 🔥 Now (In Progress)

<!-- items currently being worked on -->

## 📋 Next (Up Soon)

<!-- next items to pick up -->

## 💡 Ideas / Backlog

- [ ] 2026-07-23 — (S)(H) Show Claude Code usage alongside Copilot CLI; the vault's metrics/runs.csv is a ready-made source for vault-agent spend _(vault: processed/2026-07-23-tokentray-claude-code-update.md)_
<!-- new feature ideas land here by default -->

## 🐛 Bugs

<!-- known issues -->

## ✅ Done

<!-- completed items (most recent on top) -->

- [x] Today vs History totals disagreed when a long-running Agency session was
  active: the cumulative store accumulated every growing `session.shutdown`
  rollup snapshot as a separate row. Rollups now key by session and REPLACE in
  place; schema v3 migration collapses existing duplicates. (2026-06-07)

