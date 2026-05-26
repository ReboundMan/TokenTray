"""Cross-source primitives used by every parser.

The :class:`UsageEvent` dataclass is the *normalized* row format that
every source must produce. Adding a new source means writing a parser
that yields ``UsageEvent`` instances - downstream code (history store
ingest, bucket aggregation, AgencyUsageReport WoW report) never has to
know which log file or telemetry stream a row came from.

When extending this dataclass, keep new fields *optional* with sensible
defaults so:

* old pickled / cached events keep deserializing
* ``history_store._event_id()`` (which only hashes the four token
  metrics, ``timestamp``, and ``session_id``) keeps producing the same
  IDs - the SQLite ``events`` table is keyed on those IDs and adding a
  new field that participates in the hash would silently break
  idempotency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class UsageEvent:
    """One ``assistant_usage`` event normalized across sources.

    Required:
        timestamp:      UTC datetime of the event
        session_id:     opaque per-host session id

    Token metrics (all default 0; for sources that estimate rather than
    measure - currently the VS Code parser planned for Phase 2 - set
    ``is_estimated=True`` so callers can label or filter):
        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens

    Provenance (all optional; populated by Phase 1+ parsers, ignored by
    pre-Phase 1 callers):
        host_app:       'Copilot CLI' | 'Agency' | 'VS Code' |
                        'Clawpilot' | None (= unknown / legacy)
                        Note: 'Clawpilot' is a legacy value carried for
                        rows ingested before May 2026 when the
                        cli-server -> Clawpilot heuristic was retired.
                        New rows never get this value because Clawpilot
                        is an Electron desktop app that writes no token
                        telemetry locally.
        model:          canonical normalized model name
                        ('claude-opus-4.6', 'gpt-5.5', ...) or None
        raw_model:      verbatim model id as logged (debugging aid)
        source_path:    absolute path to the log file this event came
                        from. Helpful for ``history_store.ingest_logs``'s
                        (size, mtime_ns) watermark and for surfacing
                        provenance in the Advanced tab.
        is_estimated:   True only when token counts were inferred (e.g.
                        VS Code, where the ``ccreq`` traces have request
                        counts but not token counts).
    """

    timestamp: datetime
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    host_app: str | None = None
    model: str | None = None
    raw_model: str | None = None
    source_path: str | None = None
    is_estimated: bool = False

    @property
    def total(self) -> int:
        # IT-report convention: cached + uncached + output.
        # ``input_tokens`` from CLI telemetry already includes
        # ``cache_write`` (per CLI contract), so we add cache_read but
        # NOT cache_write to avoid double-counting.
        return self.cache_read_tokens + self.input_tokens + self.output_tokens
