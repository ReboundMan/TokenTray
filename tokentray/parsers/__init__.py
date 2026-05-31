"""Source-specific token-usage parsers and the unified iterator.

Phase 2 adds Agency event-stream parsing on top of Phase 1's Copilot CLI
log parser. The two sources have disjoint log roots
(``~/.copilot/logs/`` vs ``~/.agency/logs/``) and disjoint session_ids,
so :func:`iter_all_events` can chain them without de-duplication. A
future VS Code parser will need the no-double-count gate documented in
``docs/specs/2026-05-shared-parser-and-advanced-tab.md`` before being
chained in.

Per the spec at ``docs/specs/2026-05-shared-parser-and-advanced-tab.md``::

    from tokentray.parsers import iter_all_events
    for ev in iter_all_events():
        ...
"""
from __future__ import annotations

from typing import Iterable

from tokentray.parsers._common import UsageEvent
from tokentray.parsers.copilot_logs import (
    LOG_DIR,
    LogCache,
    iter_usage_events,
)
from tokentray.parsers.agency_events import (
    LOG_ROOT as AGENCY_LOG_ROOT,
    AgencyCache,
    iter_agency_events,
)
from tokentray.parsers.copilot_session_state import (
    LOG_DIR as SESSION_STATE_DIR,
    SessionStateCache,
    is_session_state_event,
    iter_copilot_session_state_events,
)
from tokentray.parsers.model_normalize import normalize_model


def iter_all_events(
    *,
    cache: LogCache | None = None,
    agency_cache: AgencyCache | None = None,
    session_state_cache: SessionStateCache | None = None,
    extra_skip_session_ids: set[str] | None = None,
) -> Iterable[UsageEvent]:
    """Yield every :class:`UsageEvent` from every known source.

    Chains, in precedence order:

    * ``~/.copilot/logs/*.log`` via
      :func:`tokentray.parsers.copilot_logs.iter_usage_events`
      (Copilot CLI <= 1.0.53 process-log telemetry)
    * ``~/.agency/logs/session_*/events.jsonl`` via
      :func:`tokentray.parsers.agency_events.iter_agency_events`,
      with a process-*.log fallback
    * ``~/.copilot/session-state/<id>/events.jsonl`` via
      :func:`tokentray.parsers.copilot_session_state.iter_copilot_session_state_events`
      (Copilot CLI 1.0.54+, which no longer writes process-log
      telemetry)

    The session-state store shares its ``session_id`` with the
    process-log telemetry, so the SAME session can be described by more
    than one source. To avoid double-counting, the process-log and
    Agency sources are yielded first and the (non-estimated) session_ids
    they emit are collected into a live skip set that is handed to the
    session-state parser - so it only contributes sessions no earlier
    source already covered. ``extra_skip_session_ids`` (e.g. the set of
    session_ids already persisted in the history DB) is added to that
    skip set so a session already counted from a now-rotated process log
    is not re-added by the session-state rollup.

    Each source takes an independent per-source cache so a single
    refresh re-parses only the files that actually changed.

    Consumers should keep treating this as the only entry point they
    need; the per-source iterators remain importable but semi-private.
    """
    # Only non-estimated events represent persisted, authoritative usage;
    # estimated (active-session) rows are dropped by the history store, so
    # they must NOT suppress a later exact session-state rollup.
    seen: set[str] = set()
    for ev in iter_usage_events(cache=cache):
        if not ev.is_estimated and ev.session_id:
            seen.add(ev.session_id)
        yield ev
    for ev in iter_agency_events(cache=agency_cache):
        if not ev.is_estimated and ev.session_id:
            seen.add(ev.session_id)
        yield ev

    skip = seen if not extra_skip_session_ids else (seen | set(extra_skip_session_ids))
    yield from iter_copilot_session_state_events(
        cache=session_state_cache, skip_session_ids=skip
    )


__all__ = [
    "UsageEvent",
    "LogCache",
    "AgencyCache",
    "SessionStateCache",
    "LOG_DIR",
    "AGENCY_LOG_ROOT",
    "SESSION_STATE_DIR",
    "iter_all_events",
    "iter_usage_events",
    "iter_agency_events",
    "iter_copilot_session_state_events",
    "is_session_state_event",
    "normalize_model",
]

