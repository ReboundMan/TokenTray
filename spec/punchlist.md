# TokenUsageTray — Punchlist

> Quick capture of features, ideas, and fixes. Newest items go at the top of each section.
> Use `punch "<idea>"` from the project root to add to **Ideas / Backlog** (default), or `punch -Section Bugs "..."` for other sections.

## 🔥 Now (In Progress)

<!-- items currently being worked on -->

## 📋 Next (Up Soon)

<!-- next items to pick up -->

## 💡 Ideas / Backlog

<!-- new feature ideas land here by default -->

## 🐛 Bugs

<!-- known issues -->

## ✅ Done

<!-- completed items (most recent on top) -->

- Advanced tab under-counted and lost per-tool / per-model detail after
  resuming prior sessions (showed ~30M / one model vs Today's 401M across
  several LLMs). Session-state rollups now key by `(session, host, model)` and
  are authoritative for their session (ingest purges stale non-rollup rows), so
  resumed sessions update in place and multi-model fleet runs keep every model.
  Schema v4 adds `is_rollup`. (2026-06-22)

- Today vs History totals disagreed when a long-running Agency session was
  active: the cumulative store accumulated every growing `session.shutdown`
  rollup snapshot as a separate row. Rollups now key by session and REPLACE in
  place; schema v3 migration collapses existing duplicates. (2026-06-07)

