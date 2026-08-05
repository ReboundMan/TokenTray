"""Parser for the Copilot CLI per-session event store.

Copilot CLI **1.0.54+** (the build shipped alongside the Claude Opus 4.8
rollout, verified Nov 2026) stopped writing the
``[Telemetry] cli.telemetry: { ... "kind": "assistant_usage" ... }``
blocks into ``~/.copilot/logs/process-*.log`` that
:mod:`tokentray.parsers.copilot_logs` reads. Token usage now lives only
in a per-session event stream at::

    ~/.copilot/session-state/<session_id>/events.jsonl

so a tracker that only reads the old process logs shows **zero** usage
for any session created by the new CLI. This parser reads the new store.

Schema (one JSON object per line)::

    {"type":"session.start","data":{"sessionId":"<guid>",
        "producer":"agency"|"copilot-agent", "copilotVersion":"1.0.54", ...}}
    {"type":"session.model_change","data":{"newModel":"claude-opus-4.8", ...}}
    {"type":"assistant.message","data":{"model":"claude-opus-4.8",
        "outputTokens":124, ...}}                       # per-turn, output only
    {"type":"session.shutdown","data":{
        "currentModel":"claude-opus-4.8",
        "modelMetrics":{"<model>":{"usage":{
            "inputTokens":..., "outputTokens":...,
            "cacheReadTokens":..., "cacheWriteTokens":..., ...}}}}}

The ``session.shutdown`` ``modelMetrics`` rollup is authoritative and
carries the full input / output / cache breakdown **per model**, so a
multi-model session (e.g. one that switched from Opus to a GPT model
mid-stream) yields one :class:`UsageEvent` per model. Per-model rows whose
four token counts are all zero are skipped, both to avoid noise and to
sidestep an ``event_id`` hash collision in
:func:`history_store._event_id` (which hashes only timestamp + session_id
+ token counts) between two all-zero model buckets at the same shutdown
timestamp.

Token attribution strategy (per session dir), mirroring
:mod:`tokentray.parsers.agency_events`:

* COMPLETED sessions (``session.shutdown`` with a non-empty
  ``modelMetrics``): emit the exact per-model rollup events with
  ``is_estimated=False`` and ``is_rollup=True``. The cumulative totals of
  a resumed session grow across successive parses while keying stays
  ``(session_id, host_app, model)``, so the history store REPLACES the
  prior snapshot in place instead of stacking duplicate rows.
* ACTIVE / INTERRUPTED sessions (no usable rollup yet): emit one
  ``is_estimated=True`` event per ``assistant.message`` carrying only
  ``outputTokens`` so consumers can show "something is happening" without
  overcounting. :meth:`history_store.HistoryStore.ingest` drops
  ``is_estimated`` rows, so when the session later closes and the rollup
  lands it is persisted exactly once.

De-duplication: the session_id here is the SAME id the old process-log
telemetry used (verified Nov 2026 - every ``session_id`` in a current
process log matches a ``session-state/<id>`` dir), so a session can be
described by BOTH this store and a ``process-*.log``. :func:`iter_all_events`
therefore lets the process-log / Agency parsers win and passes their
session_ids to this parser via ``skip_session_ids`` so the same session is
never counted twice across sources.
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


LOG_DIR = Path(os.path.expanduser("~/.copilot/session-state"))

# Marker used by :func:`is_session_state_event` to recognize events that
# originated from this store via their ``source_path`` without coupling
# callers to the absolute install path.
SOURCE_MARKER = "session-state"

# Per-session cache: dict[session_dir_name, (size, mtime_ns, events)].
# Same shape/contract as the other parsers' caches so the tray can hand
# each source its own dict from one refresh loop.
SessionStateCache = dict

_PROFILE = os.environ.get("TOKENTRAY_PROFILE") == "1"


def is_session_state_event(ev: UsageEvent) -> bool:
    """Return True when *ev* was produced by this parser.

    Used by persistence layers that need to apply session-state-specific
    de-duplication (skip a rollup whose session is already counted from a
    process log) without also short-circuiting the per-turn incremental
    ingest of the other sources.
    """
    sp = getattr(ev, "source_path", None)
    return bool(sp) and (os.sep + SOURCE_MARKER + os.sep) in (os.sep + sp.replace("/", os.sep) + os.sep)


def _host_for_producer(producer: str | None) -> str:
    if producer == "agency":
        return "Agency"
    return "Copilot CLI"


def _parse_iso(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_session_events(events_path: Path) -> list[UsageEvent]:
    """Parse one ``session-state/<id>/events.jsonl`` into UsageEvents.

    Always returns the full set of events for the file (no skip applied):
    skip filtering happens in :func:`iter_copilot_session_state_events` so
    the per-file cache can store the unfiltered parse regardless of which
    skip set a given caller passes.

    Tolerant of partial / truncated files (an active session's file is
    being appended to as we read): malformed JSONL lines are skipped.
    Sessions with no discoverable ``session.start`` ``sessionId`` are
    skipped entirely - without a session_id the history store's
    idempotency contract cannot be honored.
    """
    try:
        text = events_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    src = str(events_path)
    session_id: str | None = None
    producer: str | None = None
    last_model: str | None = None
    assistant_messages: list[tuple[datetime, str | None, int]] = []
    shutdown_ts: datetime | None = None
    shutdown_metrics: dict | None = None
    shutdown_model: str | None = None
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
        if not isinstance(data, dict):
            data = {}
        ts = _parse_iso(ev.get("timestamp"))
        if ts is not None and fallback_ts is None:
            fallback_ts = ts

        if etype == "session.start":
            sid = data.get("sessionId")
            if isinstance(sid, str) and sid:
                session_id = sid
            prod = data.get("producer")
            if isinstance(prod, str) and prod:
                producer = prod
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
                assistant_messages.append(
                    (ts, model if isinstance(model, str) else None, out_tokens)
                )
            continue
        if etype == "session.shutdown":
            mm = data.get("modelMetrics")
            if isinstance(mm, dict) and mm:
                shutdown_metrics = mm
                shutdown_ts = ts
                cm = data.get("currentModel")
                if isinstance(cm, str) and cm:
                    shutdown_model = cm
            continue

    if not session_id:
        return []

    host = _host_for_producer(producer)
    out: list[UsageEvent] = []

    if shutdown_metrics is not None:
        # Canonical rollup path: one exact event per model used.
        ts = shutdown_ts or fallback_ts or datetime.now(tz=timezone.utc)
        for model_name, mdata in shutdown_metrics.items():
            usage = (mdata or {}).get("usage") if isinstance(mdata, dict) else None
            if not isinstance(usage, dict):
                continue

            def _u(key: str) -> int:
                try:
                    return int(usage.get(key) or 0)
                except (TypeError, ValueError):
                    return 0

            inp = _u("inputTokens")
            outp = _u("outputTokens")
            cr = _u("cacheReadTokens")
            cw = _u("cacheWriteTokens")
            if inp == 0 and outp == 0 and cr == 0 and cw == 0:
                continue
            raw_model = model_name if isinstance(model_name, str) else str(model_name)
            out.append(
                UsageEvent(
                    timestamp=ts,
                    session_id=session_id,
                    input_tokens=inp,
                    output_tokens=outp,
                    cache_read_tokens=cr,
                    cache_write_tokens=cw,
                    host_app=host,
                    model=normalize_model(raw_model),
                    raw_model=raw_model,
                    source_path=src,
                    is_estimated=False,
                    is_rollup=True,
                )
            )
        if out:
            return out
        # Rollup present but every model bucket was empty: fall through to
        # the per-turn estimate path so an in-flight session still shows.

    # Active / interrupted session: per-turn output-only estimates.
    last = shutdown_model or last_model
    for ts, model, out_tokens in assistant_messages:
        m = model or last
        out.append(
            UsageEvent(
                timestamp=ts,
                session_id=session_id,
                input_tokens=0,
                output_tokens=int(out_tokens),
                cache_read_tokens=0,
                cache_write_tokens=0,
                host_app=host,
                model=normalize_model(m),
                raw_model=m,
                source_path=src,
                is_estimated=True,
            )
        )
    return out


def iter_copilot_session_state_events(
    root: Path | None = None,
    *,
    cache: SessionStateCache | None = None,
    skip_session_ids: set[str] | None = None,
) -> Iterable[UsageEvent]:
    """Yield UsageEvents from every ``session-state/<id>/events.jsonl``.

    Walks ``root`` (defaults to ``~/.copilot/session-state``). Each
    immediate child directory holds one session's ``events.jsonl``.

    *skip_session_ids* lets callers suppress sessions already accounted
    for by another source (the process-log / Agency parsers) so the same
    session is never double-counted. All events in one file share a
    session_id, so a skipped session is dropped whole.

    The optional *cache* mirrors the other parsers' contract: a
    caller-owned dict keyed by ``session_dir.name`` ->
    ``(size, mtime_ns, events)``. Unchanged files are not re-parsed;
    sessions that disappear are evicted at end-of-walk. The cache always
    stores the unfiltered parse so a changing *skip_session_ids* between
    calls stays correct.

    Silently yields nothing when *root* does not exist.
    """
    root = root or LOG_DIR
    if not root.exists():
        return
    skip = skip_session_ids or set()

    seen: set[str] = set()
    for sess_dir in sorted(root.glob("*")):
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

        events: list[UsageEvent] | None = None
        if cache is not None:
            entry = cache.get(key)
            if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                if _PROFILE:
                    import sys as _sys
                    _sys.stderr.write(f"[tokentray.profile] session-state cache-hit {key}\n")
                events = entry[2]
        if events is None:
            if _PROFILE:
                import sys as _sys
                t0 = _time.perf_counter()
                events = _parse_session_events(events_path)
                _sys.stderr.write(
                    f"[tokentray.profile] parsed session-state/{key} "
                    f"({st.st_size/1e6:.1f} MB) -> {len(events)} events "
                    f"in {(_time.perf_counter()-t0)*1000:.0f} ms\n"
                )
            else:
                events = _parse_session_events(events_path)
            if cache is not None:
                cache[key] = (st.st_size, st.st_mtime_ns, events)

        if skip and events and events[0].session_id in skip:
            continue
        yield from events

    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]


__all__ = [
    "LOG_DIR",
    "SOURCE_MARKER",
    "SessionStateCache",
    "is_session_state_event",
    "iter_copilot_session_state_events",
]
