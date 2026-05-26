"""Parser for Agency-wrapped Copilot sessions.

Agency is a wrapper around the Copilot CLI that adds its own per-session
event stream. Each Agency session lives under::

    ~/.agency/logs/session_<timestamp>_<pid>/
        events.jsonl                            <-- primary source
        chat.json
        process-<...>.log                       <-- secondary source (captured
                                                    copilot subprocess
                                                    telemetry; format identical
                                                    to ~/.copilot/logs/*.log)
        agency_*.log                            <-- Agency-side logs, no telemetry

Agency does NOT write to ``~/.copilot/logs/`` (verified against real
session ids in May 2026) - it redirects the spawned copilot
subprocess's log into the session dir instead. So the two log roots
are disjoint and the unified ``iter_all_events`` can chain the
``copilot_logs`` and ``agency_events`` parsers without
double-counting.

Token attribution strategy (per session dir):

* PRIMARY - ``events.jsonl`` exists:

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

* FALLBACK - no ``events.jsonl`` but ``process-*.log`` present:

  Newer Agency builds (observed May 2026) often skip the
  ``events.jsonl`` writer entirely. The captured copilot subprocess
  log under the session dir still contains the same
  ``[Telemetry] cli.telemetry: { ... "kind": "assistant_usage" ... }``
  blocks that the standalone CLI writes, so we reuse
  ``copilot_logs._parse_log_file`` with ``host_override="Agency"``
  and emit per-turn events with full input / output / cache_read /
  cache_write breakdown. This is in fact a *richer* signal than the
  events.jsonl rollup (per-turn vs per-session) but lacks the
  session-level totals reconciliation.

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
from tokentray.parsers.copilot_logs import _parse_log_file
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

    Walks ``session_*/`` directories. For each session dir:

    * If ``events.jsonl`` exists, parses it via
      :func:`_parse_events_jsonl` (primary path).
    * Otherwise falls back to parsing every ``process-*.log`` in the
      session dir via :func:`copilot_logs._parse_log_file` with
      ``host_override="Agency"`` (newer Agency builds often skip the
      events.jsonl writer; the captured copilot subprocess log
      preserves the same telemetry blocks the standalone CLI emits).

    The two paths are mutually exclusive per session dir to keep
    cross-source totals consistent: ``events.jsonl`` carries the
    authoritative session-level rollup, and parsing both would
    double-count once a session closes.

    The optional *cache* mirrors the Copilot-logs parser's contract:
    a caller-owned dict keyed by an opaque cache key ->
    (size, mtime_ns, parsed events). Primary entries are keyed by
    ``sess_dir.name`` (a ``str``); fallback entries are keyed by the
    tuple ``("proc", sess_dir.name, process_log_filename)`` so
    multiple process logs in a single session dir cache independently
    and never collide with a future primary entry for the same
    session. Unchanged files are not re-parsed; sessions that
    disappear are evicted at end-of-walk.

    Silently yields nothing when the Agency log root does not exist,
    so consumers like AgencyUsageReport that run on machines without
    Agency installed do not need to special-case the integration.
    """
    root = log_root or LOG_ROOT
    if not root.exists():
        return

    seen: set = set()
    for sess_dir in sorted(root.glob("session_*")):
        if not sess_dir.is_dir():
            continue
        events_path = sess_dir / "events.jsonl"
        if events_path.exists():
            yield from _iter_primary(sess_dir, events_path, cache=cache, seen=seen)
        else:
            yield from _iter_fallback(sess_dir, cache=cache, seen=seen)

    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]


def _iter_primary(
    sess_dir: Path,
    events_path: Path,
    *,
    cache: AgencyCache | None,
    seen: set,
) -> Iterable[UsageEvent]:
    try:
        st = events_path.stat()
    except OSError:
        return
    key = sess_dir.name
    seen.add(key)
    if cache is not None:
        entry = cache.get(key)
        if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
            if _PROFILE:
                import sys as _sys
                _sys.stderr.write(f"[tokentray.profile] agency cache-hit {key}\n")
            yield from entry[2]
            return
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


def _iter_fallback(
    sess_dir: Path,
    *,
    cache: AgencyCache | None,
    seen: set,
) -> Iterable[UsageEvent]:
    """Parse every ``process-*.log`` in *sess_dir* via the Copilot CLI
    block parser, stamping ``host_app="Agency"``.

    Yields no events (and does not touch the cache) when the session
    dir contains no ``process-*.log`` files - leaves the cache eviction
    sweep at the end of the walk responsible for cleaning up any
    stale entries from an earlier call when the file did exist.
    """
    for proc_path in sorted(sess_dir.glob("process-*.log")):
        try:
            st = proc_path.stat()
        except OSError:
            continue
        key = ("proc", sess_dir.name, proc_path.name)
        seen.add(key)
        if cache is not None:
            entry = cache.get(key)
            if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                if _PROFILE:
                    import sys as _sys
                    _sys.stderr.write(
                        f"[tokentray.profile] agency cache-hit {sess_dir.name}/{proc_path.name}\n"
                    )
                yield from entry[2]
                continue
        if _PROFILE:
            import sys as _sys
            t0 = _time.perf_counter()
            events = _parse_log_file(proc_path, host_override="Agency")
            _sys.stderr.write(
                f"[tokentray.profile] parsed agency-proc/{sess_dir.name}/{proc_path.name} "
                f"({st.st_size/1e6:.1f} MB) -> {len(events)} events "
                f"in {(_time.perf_counter()-t0)*1000:.0f} ms\n"
            )
        else:
            events = _parse_log_file(proc_path, host_override="Agency")
        if cache is not None:
            cache[key] = (st.st_size, st.st_mtime_ns, events)
        yield from events


__all__ = [
    "LOG_ROOT",
    "AgencyCache",
    "iter_agency_events",
]
