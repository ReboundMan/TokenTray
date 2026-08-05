"""Per-tool split line + tooltip for the popup (spec 10017).

Pure functions, no Qt imports, so the test suite can drive them in the
PyQt-free test environment (round-2 Rookie: the original inline method on
TrayApp was untestable without a QApplication).
"""
from __future__ import annotations

from datetime import datetime

from usage_core import fmt_tokens
from tokentray.parsers import CLAUDE_PROJECTS_DIR
from tokentray.parsers.claude_code import skipped_line_count


def tool_split_line(events, *, claude_dir_exists: bool | None = None, now=None) -> str:
    """Today's totals split by tool, matching bucket_by_day's
    local-calendar "today" semantics. Claude Code gets an explicit
    no-data state when its storage directory is absent, never a silent
    zero. Pass ``claude_dir_exists``/``now`` explicitly in tests."""
    if claude_dir_exists is None:
        claude_dir_exists = CLAUDE_PROJECTS_DIR.exists()
    today_local = (now or datetime.now()).astimezone().date()
    totals: dict[str, int] = {}
    for ev in events:
        if ev.timestamp.astimezone().date() != today_local:
            continue
        # "Unknown" matches how the history store buckets a missing
        # host_app, so the split line and Advanced tab agree.
        host = ev.host_app or "Unknown"
        totals[host] = totals.get(host, 0) + ev.total
    parts = [
        f"{host} {fmt_tokens(n)}"
        for host, n in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
    if "Claude Code" not in totals:
        if claude_dir_exists:
            parts.append("Claude Code 0")
        else:
            parts.append("Claude Code: no data found")
    # parts is never empty: the branch above guarantees at least the
    # Claude Code entry even when totals is empty.
    return "Today by tool:  " + "  ·  ".join(parts)


def tool_split_tooltip(claude_cache, events, *, now=None) -> str:
    """Tooltip for the split line: source dir, transcript count, skip
    count, and a per-model breakdown of today (the spec's per-model ask,
    surfaced here so it is visible without the supporter-locked Advanced
    tab; round-2 Rookie acceptance gap)."""
    skipped = skipped_line_count(claude_cache)
    lines = [f"Claude Code source: {CLAUDE_PROJECTS_DIR}"]
    if claude_cache:
        lines.append(f"{len(claude_cache)} transcript file(s) tracked")
    if skipped:
        lines.append(f"{skipped} malformed transcript line(s) skipped")
    today_local = (now or datetime.now()).astimezone().date()
    by_model: dict[str, int] = {}
    for ev in events:
        if ev.timestamp.astimezone().date() != today_local:
            continue
        model = ev.model or "unknown model"
        by_model[model] = by_model.get(model, 0) + ev.total
    if by_model:
        lines.append("Today by model:")
        for model, n in sorted(by_model.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {model}  {fmt_tokens(n)}")
    return "\n".join(lines)
