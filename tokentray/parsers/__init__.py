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
from tokentray.parsers.model_normalize import normalize_model


def iter_all_events(
    *,
    cache: LogCache | None = None,
    agency_cache: AgencyCache | None = None,
) -> Iterable[UsageEvent]:
    """Yield every :class:`UsageEvent` from every known source.

    Phase 2 chains:

    * ``~/.copilot/logs/*.log`` via
      :func:`tokentray.parsers.copilot_logs.iter_usage_events`
      (Clawpilot + native Copilot CLI sessions)
    * ``~/.agency/logs/session_*/events.jsonl`` via
      :func:`tokentray.parsers.agency_events.iter_agency_events`
      (Agency-wrapped sessions)

    The two callers can pass independent per-source caches so a single
    refresh re-parses only the files that actually changed since last
    call. Passing ``None`` for either disables that source's cache.

    Consumers should keep treating this as the only entry point they
    need; the per-source iterators (``iter_usage_events``,
    ``iter_agency_events``) remain importable but should be considered
    semi-private. Pinning to ``iter_all_events`` lets us add or
    rearrange sources without breaking the consumer contract.
    """
    yield from iter_usage_events(cache=cache)
    yield from iter_agency_events(cache=agency_cache)


__all__ = [
    "UsageEvent",
    "LogCache",
    "AgencyCache",
    "LOG_DIR",
    "AGENCY_LOG_ROOT",
    "iter_all_events",
    "iter_usage_events",
    "iter_agency_events",
    "normalize_model",
]

