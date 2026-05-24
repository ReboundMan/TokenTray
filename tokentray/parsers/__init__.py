"""Source-specific token-usage parsers and the unified iterator.

Phase 1 ships a single source (Copilot CLI / Clawpilot logs under
``~/.copilot/logs/``). Phase 2 adds Agency event streams and VS Code
``ccreq`` traces. ``iter_all_events`` is the stable public surface and
will silently grow to enumerate every available source as new parsers
land - existing callers do not have to change.

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


def iter_all_events(
    *,
    cache: LogCache | None = None,
) -> Iterable[UsageEvent]:
    """Yield every :class:`UsageEvent` from every known source.

    Phase 1 delegates to the Copilot CLI parser. Phase 2 will add
    Agency (``~/.agency/logs/session_*/events.jsonl``) and a
    no-double-count gate before optionally emitting VS Code estimated
    events.

    Consumers should treat this as the only entry point they need; the
    per-source iterators (``copilot_logs.iter_usage_events`` and friends)
    remain importable but should be considered semi-private. Pinning to
    ``iter_all_events`` lets us add or rearrange sources without
    breaking the consumer contract.
    """
    yield from iter_usage_events(cache=cache)


__all__ = [
    "UsageEvent",
    "LogCache",
    "LOG_DIR",
    "iter_all_events",
    "iter_usage_events",
]
