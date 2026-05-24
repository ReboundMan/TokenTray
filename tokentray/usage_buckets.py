"""Aggregation helpers for :class:`UsageEvent` streams.

These are *consumer-side* utilities (not parser-side) but they ship in
``tokentray`` rather than alongside the tray UI because AgencyUsageReport
also wants to bucket-by-day for its WoW token card. Keeping them here
lets both projects share the bucketing logic, which - like the parser -
was previously duplicated.

GUI-free by design: no PyQt imports anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from tokentray.parsers._common import UsageEvent


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


__all__ = [
    "DayBucket",
    "HourBucket",
    "bucket_by_day",
    "bucket_by_hour",
    "fmt_tokens",
]
