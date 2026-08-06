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

ONE EVENT PER API REQUEST, not per line (the v0.7.0 double-count fix):
Claude Code writes one JSONL line per CONTENT BLOCK, and every line of
the same API response repeats the full ``message.usage`` object with the
same ``requestId`` and ``message.id``. Summing per line multiplied real
usage by the block count (measured 2.1x across 32,815 lines / 15,518
requests on the machine that caught it). Lines are therefore deduped by
``requestId`` (``message.id`` fallback), last line wins: where usage
varies across a request's lines the last line always carries the final
totals (verified against every varying request on the same machine, all
1,718 of them). Both ids were present on every one of those lines; a
line with neither id degrades to per-line counting rather than being
dropped. As a guard against transcript layouts that copy history into a
new file (e.g. a future fork feature), a request key is also never
yielded twice across files within one iteration pass; today no request
id appears in more than one file (verified across 305 files).

Incremental parsing: session transcripts are append-only, so the cache
stores a byte offset per file alongside ``(size, mtime_ns)`` and a
resume parses only the appended bytes. A file whose size SHRANK (rewrite
or truncation, not append) falls back to a full re-parse. This is one
step past the Copilot ``LogCache``'s skip-unchanged shape, which the
spec names as the pattern to mirror; append-only files make the stronger
offset resume safe. A request's lines can straddle the resume boundary
(first block flushed on one tick, the rest on the next), so the cache
also keeps each event's request key: an appended line whose key is
already present REPLACES the earlier event in place instead of
appending a duplicate.

Failure posture (spec Behavior 5): read-only, content-blind, resilient.
A malformed line or unreadable file is skipped and counted; the skip
count rides each yielded batch's cache entry and is exposed via
``skipped_line_count`` for the UI tooltip. Never a crash.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokentray.parsers._common import UsageEvent
from tokentray.parsers.model_normalize import normalize_model

PROJECTS_DIR = Path(os.path.expanduser("~/.claude/projects"))

HOST_APP = "Claude Code"

# rel-path str -> (size_bytes, mtime_ns, byte_offset, events, skipped_lines,
# request_keys). The caller owns the dict, same contract shape as
# copilot_logs.LogCache (which is a 3-tuple; this one carries three extra
# slots: the resume offset, the malformed-line count, and the per-event
# request-key list that dedup-on-resume needs -- ``request_keys`` is
# parallel to ``events``, entry i holding event i's requestId/message.id,
# or None). The two caches are never interchangeable.
ClaudeCache = dict

# Both transcript layouts Claude Code writes (round-2 Rookie High: the
# original single glob missed subagent transcripts entirely -- 250 of 283
# files on the machine that caught it):
#   <project-slug>/<session-id>.jsonl                 (main sessions)
#   <project-slug>/<session-id>/subagents/agent-*.jsonl  (subagent runs)
_GLOBS = ("*/*.jsonl", "*/*/subagents/*.jsonl")


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


def _parse_lines(
    raw: bytes, session_id: str, source_path: str
) -> tuple[list[tuple[str | None, UsageEvent]], int]:
    """Parse JSONL bytes into (request_key, event) pairs, plus a skip count.

    ``request_key`` is the line's ``requestId`` (``message.id`` fallback,
    None if neither is a string); the caller dedupes on it, last line
    wins, so a multi-block response contributes ONE event. Dedup lives in
    the caller because a request's lines can straddle an offset-resume
    boundary and the merge needs the previously cached keys.

    Only assistant lines with a usage dict yield events; every other
    well-formed line (user messages, tool results, sidechain chatter) is
    NOT counted as skipped -- skipped means malformed or surprising.
    Synthetic assistant entries (model "<synthetic>", system-generated,
    verified to carry an all-zeros usage dict) are neither events nor
    skipped: they are known non-usage, filtered so they can't inflate the
    Events stat or plant a "<synthetic>" row in per-model tables
    (round-2 Rookie)."""
    pairs: list[tuple[str | None, UsageEvent]] = []
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
            # An assistant line without usage is surprising; count it so
            # drift in the transcript format is visible in the tooltip.
            skipped += 1
            continue
        ts = _parse_ts(obj.get("timestamp"))
        if ts is None:
            skipped += 1
            continue
        # Metadata fields are typed-checked, never str()-coerced: a dict
        # smuggled into `model` or `sessionId` would otherwise stringify
        # CONTENT into fields that persist to the history DB and the UI,
        # outliving the transcript itself (round-2 Hawk M-2).
        raw_model = msg.get("model")
        if not isinstance(raw_model, str):
            raw_model = None
        if raw_model == "<synthetic>":
            continue
        sid = obj.get("sessionId")
        if not isinstance(sid, str) or not sid:
            sid = session_id
        req_key = obj.get("requestId")
        if not isinstance(req_key, str) or not req_key:
            req_key = msg.get("id")
            if not isinstance(req_key, str) or not req_key:
                req_key = None

        def _tok(key: str) -> int:
            # NaN/Infinity are valid to Python's json loader and would
            # crash int() out of the whole refresh tick (round-2 Hawk M-1).
            v = usage.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return 0
            if not math.isfinite(v):
                return 0
            return int(v)

        pairs.append(
            (
                req_key,
                UsageEvent(
                    timestamp=ts,
                    session_id=sid,
                    input_tokens=_tok("input_tokens"),
                    output_tokens=_tok("output_tokens"),
                    cache_read_tokens=_tok("cache_read_input_tokens"),
                    cache_write_tokens=_tok("cache_creation_input_tokens"),
                    host_app=HOST_APP,
                    model=normalize_model(raw_model) if raw_model else None,
                    raw_model=raw_model,
                    source_path=source_path,
                ),
            )
        )
    return pairs, skipped


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
    # Cross-file guard: a request key is yielded at most once per pass.
    # No transcript layout observed to date repeats a request across
    # files (verified over 305 files), so this only bites if e.g. a
    # future fork feature copies history lines into a new session file.
    yielded_keys: set[str] = set()

    def _yield_deduped(events, request_keys):
        for ev, rk in zip(events, request_keys):
            if rk is not None:
                if rk in yielded_keys:
                    continue
                yielded_keys.add(rk)
            yield ev

    paths: list[Path] = []
    for pattern in _GLOBS:
        paths.extend(root.glob(pattern))
    for jsonl_path in sorted(set(paths)):
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
            yield from _yield_deduped(entry[3], entry[5])
            continue

        # Live-writer discipline (round-2 Hawk H-1): a transcript can be
        # mid-append while we read it. Two rules make that safe:
        #   1. Never read past the size we statted (an unbounded read()
        #      could swallow bytes written after the stat, then the next
        #      resume would re-read them: duplicate events).
        #   2. Only consume up to the last complete newline; a torn final
        #      line stays unconsumed and is parsed whole on the next tick,
        #      once the writer finishes it (JSONL lines are newline-
        #      terminated, so a fragment now is a full line later).
        try:
            if entry is not None and st.st_size > entry[0]:
                # Append-only resume: parse only the new bytes.
                start = entry[2]
                with open(jsonl_path, "rb") as fh:
                    fh.seek(start)
                    raw = fh.read(st.st_size - start)
                base_events: list[UsageEvent] = list(entry[3])
                base_skipped = entry[4]
                base_keys: list[str | None] = list(entry[5])
            else:
                # Cold parse, or the file shrank (rewrite): start over.
                start = 0
                with open(jsonl_path, "rb") as fh:
                    raw = fh.read(st.st_size)
                base_events = []
                base_skipped = 0
                base_keys = []
        except OSError:
            continue

        last_nl = raw.rfind(b"\n")
        complete = raw[: last_nl + 1] if last_nl >= 0 else b""
        new_pairs, new_skipped = _parse_lines(complete, session_id, src)

        # Merge with last-line-wins dedup per request key: a later line of
        # an already-seen request (another content block of the same API
        # response, possibly appended a tick later) REPLACES the earlier
        # event in place -- its usage is the request's final totals.
        events = base_events
        request_keys = base_keys
        index = {rk: i for i, rk in enumerate(request_keys) if rk is not None}
        for rk, ev in new_pairs:
            if rk is not None and rk in index:
                events[index[rk]] = ev
            else:
                if rk is not None:
                    index[rk] = len(events)
                events.append(ev)
                request_keys.append(rk)
        skipped = base_skipped + new_skipped
        offset = start + (last_nl + 1 if last_nl >= 0 else 0)

        if cache is not None:
            cache[key] = (
                st.st_size, st.st_mtime_ns, offset, events, skipped,
                request_keys,
            )
        yield from _yield_deduped(events, request_keys)

    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]


def skipped_line_count(cache: ClaudeCache | None) -> int:
    """Total malformed/surprising lines skipped, for the UI tooltip."""
    if not cache:
        return 0
    return sum(entry[4] for entry in cache.values())
