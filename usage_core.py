"""Legacy import surface for the tray app.

The actual parser implementation now lives in
``tokentray.parsers.copilot_logs`` and the bucket helpers live in
``tokentray.usage_buckets`` so they can be shared with
``AgencyUsageReport`` (or any other consumer) via
``pip install git+https://github.com/ReboundMan/TokenTray.git``.

This module is kept as a thin re-export shim so that existing
top-level imports continue to work without changes::

    from usage_core import UsageEvent, iter_usage_events
    from usage_core import DayBucket, HourBucket
    from usage_core import bucket_by_day, bucket_by_hour, fmt_tokens

The tray-only helpers that are NOT part of the shared parser API -
the read-only ``~/.copilot/session-store.db`` lookup used by the
"active session" UI affordance, and the ``__main__`` smoke test -
remain implemented here so they do not bloat the installable package.

When adding new functionality:

* parser / event extraction work -> add it under ``tokentray/parsers/``
  and re-export here only if existing call sites need it.
* tray-UI-only helpers -> add them here (or in a sibling top-level
  module). Do NOT put GUI-coupled code inside ``tokentray/`` or you
  will break headless ``pip install tokentray`` consumers.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from tokentray.parsers._common import UsageEvent
from tokentray.parsers.copilot_logs import (
    LOG_DIR,
    LogCache,
    TELEMETRY_MARKER,
    _parse_log_file,
    _parse_ts,
    iter_usage_events,
)
from tokentray.usage_buckets import (
    DayBucket,
    HourBucket,
    bucket_by_day,
    bucket_by_hour,
    fmt_tokens,
)

SESSION_STORE = Path(os.path.expanduser("~/.copilot/session-store.db"))


def fetch_active_session(db: Path | None = None) -> tuple[str, str] | None:
    """Return ``(session_id, cwd)`` of the most-recently-updated session."""
    db = db or SESSION_STORE
    if not db.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = c.execute(
            "SELECT id, COALESCE(cwd,'') FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        c.close()
    except sqlite3.Error:
        return None
    return (row[0], row[1]) if row else None


__all__ = [
    "UsageEvent",
    "DayBucket",
    "HourBucket",
    "LOG_DIR",
    "LogCache",
    "SESSION_STORE",
    "TELEMETRY_MARKER",
    "_parse_log_file",
    "_parse_ts",
    "iter_usage_events",
    "bucket_by_day",
    "bucket_by_hour",
    "fmt_tokens",
    "fetch_active_session",
]


if __name__ == "__main__":
    evs = list(iter_usage_events())
    print(f"Parsed {len(evs)} assistant_usage events")
    if evs:
        print(f"  earliest: {min(e.timestamp for e in evs).isoformat()}")
        print(f"  latest:   {max(e.timestamp for e in evs).isoformat()}")
    buckets = bucket_by_day(evs, days=7)
    for b in buckets:
        print(
            f"  {b.day}  total={fmt_tokens(b.total):>6}  "
            f"in={fmt_tokens(b.input_tokens):>6}  out={fmt_tokens(b.output_tokens):>6}  "
            f"cache_r={fmt_tokens(b.cache_read_tokens):>6}  events={b.events}  sessions={len(b.sessions)}"
        )
