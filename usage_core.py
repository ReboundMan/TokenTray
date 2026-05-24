"""Token-usage extraction from Copilot CLI telemetry logs.

Parses ``~/.copilot/logs/*.log`` for ``assistant_usage`` telemetry events and
returns per-event records with ISO timestamps so callers can bucket by day,
hour, or session as needed.

This module mirrors the parsing logic in
``AgencyUsageReport/build_report.py::extract_tokens_from_logs`` but adds
per-event timestamps (read from the log line prefix) so the systray app can
render intra-day cumulative charts. If you change the event schema, update
both consumers.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

LOG_DIR = Path(os.path.expanduser("~/.copilot/logs"))
SESSION_STORE = Path(os.path.expanduser("~/.copilot/session-store.db"))
TELEMETRY_MARKER = "[Telemetry] cli.telemetry:"

# Line prefix like:  2026-05-18T15:39:28.130Z [INFO] [Telemetry] ...
_LINE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")


@dataclass
class UsageEvent:
    timestamp: datetime
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        # Mirror the IT usage report: cached + uncached + output.
        # input_tokens already includes cache_write (per CLI docs),
        # so total = cache_read + input + output.
        return self.cache_read_tokens + self.input_tokens + self.output_tokens


@dataclass
class DayBucket:
    day: date
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    events: int = 0
    sessions: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return self.cache_read_tokens + self.input_tokens + self.output_tokens

    @property
    def label(self) -> str:
        return f"{self.day.strftime('%a')} {self.day.month}/{self.day.day}"

    @property
    def short_label(self) -> str:
        return f"{self.day.month}/{self.day.day}"


@dataclass
class HourBucket:
    hour: int  # 0..23 local
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    events: int = 0
    sessions: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return self.cache_read_tokens + self.input_tokens + self.output_tokens

    @property
    def label(self) -> str:
        h = self.hour
        if h == 0:
            return "12a"
        if h < 12:
            return f"{h}a"
        if h == 12:
            return "12p"
        return f"{h - 12}p"


def _parse_ts(line: str) -> datetime | None:
    m = _LINE_TS_RE.match(line)
    if not m:
        return None
    s = m.group(1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_log_file(log_path: Path) -> list[UsageEvent]:
    """Parse a single ``*.log`` file into a list of :class:`UsageEvent`.

    Pulled out of :func:`iter_usage_events` so callers can amortize the
    cost across refresh ticks via the per-file cache. Parsing semantics
    (brace-balanced multi-line JSON, ``assistant_usage`` filter,
    ``session_id``-required, three-tier timestamp fallback) are unchanged.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[UsageEvent] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        if TELEMETRY_MARKER not in lines[i]:
            i += 1
            continue
        ts = _parse_ts(lines[i])
        # JSON object starts on the next non-empty line that begins with "{"
        j = i + 1
        while j < n and not lines[j].lstrip().startswith("{"):
            j += 1
        if j >= n:
            break
        # Brace-balanced read across multiple lines.
        depth = 0
        start = j
        in_str = False
        esc = False
        done = False
        while j < n:
            for ch in lines[j]:
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        done = True
                        break
            if done:
                break
            j += 1
        if not done:
            break
        block = "\n".join(lines[start : j + 1])
        try:
            ev = json.loads(block)
        except json.JSONDecodeError:
            i = j + 1
            continue
        if ev.get("kind") != "assistant_usage":
            i = j + 1
            continue
        sid = ev.get("session_id")
        metrics = ev.get("metrics") or {}
        if not sid:
            i = j + 1
            continue
        # Fall back to event-internal timestamp if line prefix lacked one.
        if ts is None:
            inner = ev.get("timestamp") or ev.get("created_at")
            if isinstance(inner, str):
                try:
                    ts = datetime.fromisoformat(inner.replace("Z", "+00:00"))
                except ValueError:
                    ts = None
        if ts is None:
            # Last resort: file mtime
            try:
                ts = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                ts = datetime.now(tz=timezone.utc)
        out.append(
            UsageEvent(
                timestamp=ts,
                session_id=sid,
                input_tokens=int(metrics.get("input_tokens") or 0),
                output_tokens=int(metrics.get("output_tokens") or 0),
                cache_read_tokens=int(metrics.get("cache_read_tokens") or 0),
                cache_write_tokens=int(metrics.get("cache_write_tokens") or 0),
            )
        )
        i = j + 1
    return out


# Cache key per file: (size_bytes, mtime_ns) -- both move when content
# changes. Value is the parsed event list. Caller (e.g. TrayApp) owns
# the dict so the cache lifetime matches process lifetime.
LogCache = dict  # alias for type hint: dict[str, tuple[int, int, list[UsageEvent]]]

_PROFILE = os.environ.get("TOKENTRAY_PROFILE") == "1"


def iter_usage_events(
    log_dir: Path | None = None,
    *,
    cache: LogCache | None = None,
) -> Iterable[UsageEvent]:
    """Yield UsageEvent records from every ``*.log`` under ``log_dir``.

    When *cache* is provided (a dict the caller owns) files whose
    ``(size, mtime_ns)`` is unchanged since the last call are not
    re-parsed -- their previously-parsed events are yielded from the
    cache instead. Files no longer present on disk are evicted. This
    keeps repeated refresh ticks cheap once the cache is warm: a typical
    refresh re-parses only the single actively-being-written log.

    Set ``TOKENTRAY_PROFILE=1`` in the environment to log per-file parse
    timings to stderr; useful for diagnosing scan regressions without
    requiring callers to instrument the function.
    """
    log_dir = log_dir or LOG_DIR
    if not log_dir.exists():
        return

    seen: set[str] = set()
    for log_path in sorted(log_dir.glob("*.log")):
        try:
            st = log_path.stat()
        except OSError:
            continue
        key = log_path.name
        seen.add(key)
        if cache is not None:
            entry = cache.get(key)
            if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                if _PROFILE:
                    import sys as _sys
                    _sys.stderr.write(f"[tokentray.profile] cache-hit {key}\n")
                yield from entry[2]
                continue
        if _PROFILE:
            import sys as _sys
            import time as _time
            t0 = _time.perf_counter()
            events = _parse_log_file(log_path)
            _sys.stderr.write(
                f"[tokentray.profile] parsed {key} "
                f"({st.st_size/1e6:.1f} MB) -> {len(events)} events "
                f"in {(_time.perf_counter()-t0)*1000:.0f} ms\n"
            )
        else:
            events = _parse_log_file(log_path)
        if cache is not None:
            cache[key] = (st.st_size, st.st_mtime_ns, events)
        yield from events

    # Evict cache entries for files that disappeared (log rotation /
    # manual cleanup). Keeps memory bounded over long-running tray
    # processes.
    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]


def bucket_by_day(
    events: Iterable[UsageEvent],
    *,
    tz: timezone | None = None,
    days: int = 7,
) -> list[DayBucket]:
    """Group events into the last ``days`` calendar buckets ending today (local).

    Returns a list of length ``days`` in chronological order (oldest first).
    Days with no activity get an empty bucket so charts have stable x-axis ticks.
    """
    if tz is None:
        # Local timezone for "today" semantics.
        tz = datetime.now().astimezone().tzinfo  # type: ignore[assignment]
    today = datetime.now(tz=tz).date()
    start = today - timedelta(days=days - 1)
    buckets: dict[date, DayBucket] = {
        start + timedelta(days=i): DayBucket(day=start + timedelta(days=i))
        for i in range(days)
    }
    for ev in events:
        local_day = ev.timestamp.astimezone(tz).date()
        if local_day < start or local_day > today:
            continue
        b = buckets[local_day]
        b.input_tokens += ev.input_tokens
        b.output_tokens += ev.output_tokens
        b.cache_read_tokens += ev.cache_read_tokens
        b.cache_write_tokens += ev.cache_write_tokens
        b.events += 1
        b.sessions.add(ev.session_id)
    return [buckets[start + timedelta(days=i)] for i in range(days)]


def bucket_by_hour(
    events: Iterable[UsageEvent],
    *,
    tz: timezone | None = None,
) -> list[HourBucket]:
    """Group today's events into 24 hourly buckets (0..23 in local time)."""
    if tz is None:
        tz = datetime.now().astimezone().tzinfo  # type: ignore[assignment]
    today = datetime.now(tz=tz).date()
    buckets = [HourBucket(hour=h) for h in range(24)]
    for ev in events:
        local = ev.timestamp.astimezone(tz)
        if local.date() != today:
            continue
        b = buckets[local.hour]
        b.input_tokens += ev.input_tokens
        b.output_tokens += ev.output_tokens
        b.cache_read_tokens += ev.cache_read_tokens
        b.cache_write_tokens += ev.cache_write_tokens
        b.events += 1
        b.sessions.add(ev.session_id)
    return buckets


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


def fmt_tokens(n: int) -> str:
    """Compact token-count formatting suitable for a tray badge (max 4 chars)."""
    if n < 1_000:
        return str(n)
    if n < 10_000:
        return f"{n/1000:.1f}k"
    if n < 1_000_000:
        return f"{n//1000}k"
    if n < 10_000_000:
        return f"{n/1_000_000:.1f}M"
    return f"{n//1_000_000}M"


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
