"""Claude Code session-transcript parser (spec 10017).

Reads the storage Claude Code itself writes on every machine it runs on:
``~/.claude/projects/<project-slug>/<session-id>.jsonl``, one JSONL file
per session, one line per message. Assistant-message lines carry a
``message.usage`` object with exact token counts plus ``message.model``
and a top-level ISO ``timestamp`` (shape verified against live
transcripts, 2026-08-04). No JJ-specific infrastructure is assumed
anywhere: this is the generic, tool-owned source, per the spec's own
round-1 rework ("we can't assume any of the JJ patterns are in place").

PRIVACY, the hard rule (spec Model): transcripts contain full
conversation content. This parser extracts ONLY ``message.usage``,
``message.model``, and the line's ``timestamp``/``sessionId`` fields and
never reads, stores, or yields message content. Keep it that way: any
future field addition here must be a metadata field, never content.

Incremental parsing: session transcripts are append-only, so the cache
stores a byte offset per file alongside ``(size, mtime_ns)`` and a
resume parses only the appended bytes. A file whose size SHRANK (rewrite
or truncation, not append) falls back to a full re-parse. This is one
step past the Copilot ``LogCache``'s skip-unchanged shape, which the
spec names as the pattern to mirror; append-only files make the stronger
offset resume safe.

Failure posture (spec Behavior 5): read-only, content-blind, resilient.
A malformed line or unreadable file is skipped and counted; the skip
count rides each yielded batch's cache entry and is exposed via
``skipped_line_count`` for the UI tooltip. Never a crash.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokentray.parsers._common import UsageEvent
from tokentray.parsers.model_normalize import normalize_model

PROJECTS_DIR = Path(os.path.expanduser("~/.claude/projects"))

HOST_APP = "Claude Code"

# rel-path str -> (size_bytes, mtime_ns, byte_offset, events, skipped_lines).
# The caller owns the dict, same contract as copilot_logs.LogCache.
ClaudeCache = dict


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _parse_lines(raw: bytes, session_id: str, source_path: str) -> tuple[list[UsageEvent], int]:
    """Parse JSONL bytes into events. Returns (events, skipped_line_count).

    Only assistant lines with a usage dict yield events; every other
    well-formed line (user messages, tool results, sidechain chatter) is
    NOT counted as skipped -- skipped means malformed or surprising."""
    events: list[UsageEvent] = []
    skipped = 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            skipped += 1
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            skipped += 1
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            # An assistant line without usage (e.g. a synthetic entry) is
            # surprising but harmless; count it so drift is visible.
            skipped += 1
            continue
        ts = _parse_ts(obj.get("timestamp"))
        if ts is None:
            skipped += 1
            continue
        raw_model = msg.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            raw_model = str(raw_model)

        def _tok(key: str) -> int:
            v = usage.get(key)
            return int(v) if isinstance(v, (int, float)) else 0

        events.append(
            UsageEvent(
                timestamp=ts,
                session_id=str(obj.get("sessionId") or session_id),
                input_tokens=_tok("input_tokens"),
                output_tokens=_tok("output_tokens"),
                cache_read_tokens=_tok("cache_read_input_tokens"),
                cache_write_tokens=_tok("cache_creation_input_tokens"),
                host_app=HOST_APP,
                model=normalize_model(raw_model) if raw_model else None,
                raw_model=raw_model,
                source_path=source_path,
            )
        )
    return events, skipped


def iter_claude_code_events(
    projects_dir: Path | None = None,
    *,
    cache: ClaudeCache | None = None,
) -> Iterable[UsageEvent]:
    """Yield UsageEvent records from every session transcript.

    Absent directory yields nothing (the pane says "no Claude Code data
    found"; spec Behavior 1). With *cache*, unchanged files are served
    from cache and grown files resume from the stored byte offset.
    """
    root = projects_dir or PROJECTS_DIR
    if not root.exists():
        return

    seen: set[str] = set()
    for jsonl_path in sorted(root.glob("*/*.jsonl")):
        try:
            st = jsonl_path.stat()
        except OSError:
            continue
        key = str(jsonl_path.relative_to(root))
        seen.add(key)
        session_id = jsonl_path.stem
        src = str(jsonl_path)

        entry = cache.get(key) if cache is not None else None
        if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
            yield from entry[3]
            continue

        try:
            if entry is not None and st.st_size > entry[0]:
                # Append-only resume: parse only the new bytes.
                with open(jsonl_path, "rb") as fh:
                    fh.seek(entry[2])
                    raw = fh.read()
                new_events, new_skipped = _parse_lines(raw, session_id, src)
                events = list(entry[3]) + new_events
                skipped = entry[4] + new_skipped
            else:
                # Cold parse, or the file shrank (rewrite): start over.
                raw = jsonl_path.read_bytes()
                events, skipped = _parse_lines(raw, session_id, src)
        except OSError:
            continue

        if cache is not None:
            cache[key] = (st.st_size, st.st_mtime_ns, st.st_size, events, skipped)
        yield from events

    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]


def skipped_line_count(cache: ClaudeCache | None) -> int:
    """Total malformed/surprising lines skipped, for the UI tooltip."""
    if not cache:
        return 0
    return sum(entry[4] for entry in cache.values())
