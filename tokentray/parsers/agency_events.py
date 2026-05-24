"""Parser for Agency-wrapped Copilot sessions.

Agency is a wrapper around the Copilot CLI that adds its own per-session
event stream. Each Agency session lives under::

    ~/.agency/logs/session_<timestamp>_<pid>/
        events.jsonl                            <-- this parser reads here
        chat.json
        process-<...>.log                       <-- debug stream, no telemetry
        agency_*.log                            <-- Agency-side logs, no telemetry

Agency does NOT write to ``~/.copilot/logs/`` (we verified this against
real session ids), so there is no double-count risk between the
``copilot_logs`` and ``agency_events`` parsers - the two log roots are
disjoint and the unified ``iter_all_events`` can chain them safely.

Token attribution strategy:

* COMPLETED sessions (``session.shutdown`` event present with
  ``data.tokenDetails``): emit ONE :class:`UsageEvent` summarizing
  the session. Token counts are exact (from Agency's own rollup);
  the canonical model is the last value seen on
  ``session.model_change`` or ``assistant.message``.

* ACTIVE / INTERRUPTED sessions (no ``session.shutdown`` yet, or
  shutdown without ``tokenDetails``): emit one event per
  ``assistant.message`` carrying only ``outputTokens`` (Agency does
  not break out input/cache per turn) with ``is_estimated=True`` so
  consumers can label the row "active session, totals pending".
  When the session eventually closes and we re-parse, the rollup
  path takes over and the per-turn estimates are dropped.

Per-turn input/cache_read/cache_write counts are NOT recoverable from
the Agency event stream as of v1.0.28; they are only ever surfaced in
the session.shutdown rollup. Surfacing per-turn estimates with
``input_tokens=0`` deliberately underreports an in-flight session so
the user can see "something is happening" without overcounting against
the rollup that will land on session close.
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokentray.parsers._common import UsageEvent
from tokentray.parsers.model_normalize import normalize_model


LOG_ROOT = Path(os.path.expanduser("~/.agency/logs"))

# Per-events.jsonl cache: dict[session_dir_name, (size, mtime_ns, events)].
# Same shape and contract as :data:`tokentray.parsers.copilot_logs.LogCache`
# so the tray can hand both parsers a per-source dict from a single
# refresh loop.
AgencyCache = dict

_PROFILE = os.environ.get("TOKENTRAY_PROFILE") == "1"


def _parse_iso(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_events_jsonl(events_path: Path) -> list[UsageEvent]:
    """Parse one Agency ``events.jsonl`` into :class:`UsageEvent` rows.

    Tolerant of partial / truncated files: malformed JSONL lines are
    skipped, never aborting the surrounding file. Sessions that lack a
    discoverable ``session.start`` event with a ``sessionId`` are
    skipped entirely - without a session_id we cannot honor
    ``history_store``'s idempotency contract.
    """
    try:
        text = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    src = str(events_path)
    session_id: str | None = None
    last_model: str | None = None
    assistant_messages: list[tuple[datetime, str | None, int]] = []
    shutdown_ts: datetime | None = None
    shutdown_token_details: dict | None = None
    fallback_ts: datetime | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        data = ev.get("data") or {}
        ts = _parse_iso(ev.get("timestamp"))
        if ts is not None and fallback_ts is None:
            fallback_ts = ts

        if etype == "session.start":
            sid = data.get("sessionId")
            if isinstance(sid, str) and sid:
                session_id = sid
            if ts is not None:
                fallback_ts = ts
            continue
        if etype == "session.model_change":
            new_model = data.get("newModel")
            if isinstance(new_model, str) and new_model:
                last_model = new_model
            continue
        if etype == "assistant.message":
            model = data.get("model")
            if isinstance(model, str) and model:
                last_model = model
            out_tokens = data.get("outputTokens")
            if ts is not None and isinstance(out_tokens, int):
                assistant_messages.append((ts, model if isinstance(model, str) else None, out_tokens))
            continue
        if etype == "session.shutdown":
            td = data.get("tokenDetails")
            if isinstance(td, dict):
                shutdown_token_details = td
                shutdown_ts = ts
            continue

    if not session_id:
        return []

    out: list[UsageEvent] = []
    if shutdown_token_details is not None:
        # Canonical rollup path: use Agency's own session totals.
        def _tc(key: str) -> int:
            v = shutdown_token_details.get(key) or {}
            n = v.get("tokenCount") if isinstance(v, dict) else None
            try:
                return int(n or 0)
            except (TypeError, ValueError):
                return 0
        ts = shutdown_ts or fallback_ts or datetime.now(tz=timezone.utc)
        out.append(
            UsageEvent(
                timestamp=ts,
                session_id=session_id,
                input_tokens=_tc("input"),
                output_tokens=_tc("output"),
                cache_read_tokens=_tc("cache_read"),
                cache_write_tokens=_tc("cache_write"),
                host_app="Agency",
                model=normalize_model(last_model),
                raw_model=last_model,
                source_path=src,
                is_estimated=False,
            )
        )
    else:
        # Active session: emit per-turn output-only estimates.
        for ts, model, out_tokens in assistant_messages:
            out.append(
                UsageEvent(
                    timestamp=ts,
                    session_id=session_id,
                    input_tokens=0,
                    output_tokens=int(out_tokens),
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    host_app="Agency",
                    model=normalize_model(model),
                    raw_model=model,
                    source_path=src,
                    is_estimated=True,
                )
            )
    return out


def iter_agency_events(
    log_root: Path | None = None,
    *,
    cache: AgencyCache | None = None,
) -> Iterable[UsageEvent]:
    """Yield :class:`UsageEvent` records from every Agency session under
    *log_root* (defaults to ``~/.agency/logs/``).

    Walks ``session_*/events.jsonl`` files and dispatches to
    :func:`_parse_events_jsonl`. The optional *cache* mirrors the
    Copilot-logs parser's contract: a caller-owned dict keyed by
    session-dir name -> (size, mtime_ns, parsed events). Unchanged
    files are not re-parsed; deleted sessions are evicted.

    The cache key is the session-dir name (not the full events.jsonl
    path) so log rotation that removes an entire session-dir does the
    right thing - the eviction sweep at end-of-walk removes the cache
    entry on the next call.

    Silently yields nothing when the Agency log root does not exist,
    so consumers like AgencyUsageReport that run on machines without
    Agency installed do not need to special-case the integration.
    """
    root = log_root or LOG_ROOT
    if not root.exists():
        return

    seen: set[str] = set()
    for sess_dir in sorted(root.glob("session_*")):
        if not sess_dir.is_dir():
            continue
        events_path = sess_dir / "events.jsonl"
        if not events_path.exists():
            continue
        try:
            st = events_path.stat()
        except OSError:
            continue
        key = sess_dir.name
        seen.add(key)
        if cache is not None:
            entry = cache.get(key)
            if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                if _PROFILE:
                    import sys as _sys
                    _sys.stderr.write(f"[tokentray.profile] agency cache-hit {key}\n")
                yield from entry[2]
                continue
        if _PROFILE:
            import sys as _sys
            t0 = _time.perf_counter()
            events = _parse_events_jsonl(events_path)
            _sys.stderr.write(
                f"[tokentray.profile] parsed agency/{key} "
                f"({st.st_size/1e6:.1f} MB) -> {len(events)} events "
                f"in {(_time.perf_counter()-t0)*1000:.0f} ms\n"
            )
        else:
            events = _parse_events_jsonl(events_path)
        if cache is not None:
            cache[key] = (st.st_size, st.st_mtime_ns, events)
        yield from events

    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]


__all__ = [
    "LOG_ROOT",
    "AgencyCache",
    "iter_agency_events",
]
