"""Local SQLite history store for TokenTray's Advanced-tier history feature.

Persists per-event token usage so Day / Week / Month / All-time rollups survive
the Copilot CLI rotating its raw ``~/.copilot/logs`` files.

Design notes
------------
* **Zero install**: uses ``sqlite3`` from the Python stdlib. No new package
  dependency, no service to run, no user-visible setup.
* **Trial-then-toggle tier model**: on first DB init we stamp
  ``first_seen_at_utc`` + ``trial_ends_at_utc`` (= first + 60 days).
  ``trial_ends_at_utc`` is *never* recomputed. ``last_seen_at_utc`` is
  monotonically advanced on every open as a guard against system-clock
  rollback (turning the clock back must not re-extend the trial or
  re-enable recording).
* **Idempotent ingest**: each event's primary key is a SHA-1 over a
  normalized microsecond-UTC ISO timestamp + session id + the four metric
  fields. Re-parsing the same log file is a no-op.
* **No backfill across "recording disabled" gaps**: ``meta`` stores
  ``recording_active_since_utc``. It is stamped on first DB init and
  advanced again every time the user transitions recording from disabled
  to enabled (post-trial advanced toggle, or clearing opt-out). ``ingest``
  silently drops events older than that watermark, so re-enabling
  recording cannot rope in events that occurred while recording was off.
* **Per-file watermarks**: we also persist ``(size, mtime_ns)`` per log
  file in ``meta`` so unchanged files are skipped on refresh ticks --
  keeps every-2-minute refreshes cheap as the log corpus grows.
* **Local-time boundaries**: storage is UTC ISO; "Today / This week /
  This month" are computed by deriving local-tz boundary datetimes first
  and converting *those* to UTC for the range query. Week start = ISO
  Monday.
* **Forward-compatible**: ``schema_version`` lives in ``meta`` so future
  migrations have an anchor. A corrupt DB is quarantined as
  ``history.db.corrupt-<ts>`` and a fresh one is created so a single bad
  file never bricks the tray.

This module deliberately has no Qt dependency so it stays testable and
importable from CLI tools.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

from usage_core import UsageEvent, iter_usage_events
from tokentray.parsers import iter_all_events
from tokentray.parsers.agency_events import LOG_ROOT as AGENCY_LOG_ROOT
from tokentray.parsers.copilot_session_state import LOG_DIR as SESSION_STATE_DIR

SCHEMA_VERSION = "3"
TRIAL_DAYS = 60
COFFEE_PROMPT_CADENCE_DAYS = 21
DB_FILENAME = "history.db"


class SupporterRequiredError(RuntimeError):
    """Raised when post-trial recording is requested without a supporter unlock.

    Callers (the tray UI) catch this and surface the "Buy me a coffee"
    dialog instead of silently no-op-ing the toggle.
    """


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def default_db_path() -> Path:
    """Return the platform-appropriate path for ``history.db``."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "TokenTray" / DB_FILENAME
    return Path(os.path.expanduser("~/.tokentray")) / DB_FILENAME


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Totals:
    """Aggregated metrics over a time range."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    events: int = 0
    sessions: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        # Matches usage_core's "displayed total" convention.
        return self.cache_read_tokens + self.input_tokens + self.output_tokens

    @property
    def session_count(self) -> int:
        return len(self.sessions)


@dataclass
class TierStatus:
    """Snapshot of the user's tier / recording state."""

    first_seen_at_utc: datetime
    trial_ends_at_utc: datetime
    now_utc: datetime
    advanced_enabled: bool
    recording_opt_out: bool
    coffee_purchased_at_utc: datetime | None = None

    @property
    def in_trial(self) -> bool:
        return self.now_utc < self.trial_ends_at_utc

    @property
    def trial_days_remaining(self) -> int:
        if not self.in_trial:
            return 0
        # Round up so "12 hours left" still reads as "1 day remaining".
        delta = self.trial_ends_at_utc - self.now_utc
        seconds = max(0, int(delta.total_seconds()))
        return (seconds + 86_399) // 86_400

    @property
    def supporter_purchased(self) -> bool:
        return self.coffee_purchased_at_utc is not None

    @property
    def recording_enabled(self) -> bool:
        if self.recording_opt_out:
            return False
        if self.in_trial:
            return True
        # Post-trial: only counts if both advanced is on AND supporter unlocked.
        return self.advanced_enabled and self.supporter_purchased

    @property
    def banner_text(self) -> str:
        if self.recording_opt_out:
            return (
                "Recording disabled. Historical data below is still viewable. "
                "Re-enable in Settings to capture new events."
            )
        if self.in_trial:
            return (
                f"Free trial: {self.trial_days_remaining} day"
                f"{'' if self.trial_days_remaining == 1 else 's'} remaining. "
                "After the trial, buy me a coffee in Settings to keep recording."
            )
        if self.advanced_enabled and self.supporter_purchased:
            return "Advanced history active — recording locally. Thanks for the coffee! ☕"
        if self.advanced_enabled and not self.supporter_purchased:
            return (
                "Advanced history is enabled but locked. "
                "Buy me a coffee in Settings to resume recording new events."
            )
        return (
            "Trial ended. Recording is paused. "
            "Buy me a coffee in Settings to resume capturing new events. "
            "Existing data below is still viewable."
        )


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def _normalize_ts(ts: datetime) -> str:
    """Canonical microsecond-precision UTC ISO string for hashing/storage."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    # %f is 6-digit microseconds; trailing "Z" makes UTC explicit.
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _event_id(ts_norm: str, sid: str, ev: UsageEvent) -> str:
    h = hashlib.sha1()
    h.update(ts_norm.encode("utf-8"))
    h.update(b"|")
    h.update(sid.encode("utf-8"))
    h.update(b"|")
    h.update(
        f"{ev.input_tokens}|{ev.output_tokens}|"
        f"{ev.cache_read_tokens}|{ev.cache_write_tokens}".encode("utf-8")
    )
    return h.hexdigest()


def _rollup_event_id(sid: str, host_app: str | None) -> str:
    """Stable id for a per-session cumulative rollup.

    Unlike :func:`_event_id`, this deliberately excludes token counts and
    the timestamp so a later, larger snapshot of the same session rollup
    (e.g. an in-progress Agency session whose ``session.shutdown`` total
    keeps growing) maps to the same row and REPLACES the prior one rather
    than accumulating a duplicate.
    """
    h = hashlib.sha1()
    h.update(b"rollup|")
    h.update((host_app or "").encode("utf-8"))
    h.update(b"|")
    h.update(sid.encode("utf-8"))
    return h.hexdigest()



# ---------------------------------------------------------------------------
# Local-time boundary helpers
# ---------------------------------------------------------------------------

def _local_tz() -> timezone:
    # The current local tz; matches usage_core.bucket_by_day semantics.
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None
    return tz  # type: ignore[return-value]


def _local_midnight_utc(d: date, tz=None) -> datetime:
    """Convert a local-calendar date's 00:00 to a UTC datetime.

    When *tz* is explicit (e.g. tests passing ``timezone.utc``) we attach it
    directly. When no tz is provided we deliberately build a *naive* local
    datetime and let ``astimezone()`` consult the OS's tz database for that
    specific date -- this yields correct results across DST transitions.
    (Capturing ``datetime.now().astimezone().tzinfo`` once would freeze
    today's UTC offset and mis-compute days that sit on the other side of a
    spring-forward / fall-back boundary.)
    """
    if tz is not None:
        return datetime.combine(d, time(0, 0), tzinfo=tz).astimezone(timezone.utc)
    return datetime.combine(d, time(0, 0)).astimezone(timezone.utc)


def _today_range_utc(tz=None) -> tuple[datetime, datetime]:
    tz = tz or _local_tz()
    today = datetime.now(tz=tz).date()
    start = _local_midnight_utc(today, tz)
    end = _local_midnight_utc(today + timedelta(days=1), tz)
    return start, end


def _this_week_range_utc(tz=None) -> tuple[datetime, datetime]:
    """ISO week: Monday 00:00 local -> next Monday 00:00 local, in UTC."""
    tz = tz or _local_tz()
    today = datetime.now(tz=tz).date()
    monday = today - timedelta(days=today.weekday())
    start = _local_midnight_utc(monday, tz)
    end = _local_midnight_utc(monday + timedelta(days=7), tz)
    return start, end


def _this_month_range_utc(tz=None) -> tuple[datetime, datetime]:
    tz = tz or _local_tz()
    today = datetime.now(tz=tz).date()
    first = today.replace(day=1)
    if first.month == 12:
        next_first = first.replace(year=first.year + 1, month=1)
    else:
        next_first = first.replace(month=first.month + 1)
    start = _local_midnight_utc(first, tz)
    end = _local_midnight_utc(next_first, tz)
    return start, end


def _period_range_utc(
    period: str, *, tz=None
) -> tuple[datetime | None, datetime | None]:
    """Resolve a named period to a (start_utc, end_utc) pair.

    ``"all_time"`` returns ``(None, None)`` so the caller's WHERE clause
    is skipped entirely; the other periods reuse the same local-tz
    boundary helpers as :meth:`HistoryStore.summarize_today` and
    friends so the breakdowns line up exactly with the summary cards.
    """
    if period == "today":
        return _today_range_utc(tz)
    if period == "week":
        return _this_week_range_utc(tz)
    if period == "month":
        return _this_month_range_utc(tz)
    if period == "all_time":
        return None, None
    raise ValueError(f"Unknown period: {period!r}")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id           TEXT PRIMARY KEY,
    ts_utc             TEXT NOT NULL,
    session_id         TEXT NOT NULL,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    host_app           TEXT,
    model              TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_utc);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Indexes that reference columns added by a migration are created
# AFTER :meth:`HistoryStore._migrate_schema` runs so a legacy v1 DB
# (which doesn't yet have host_app / model columns) doesn't fail the
# index DDL with "no such column".
_POST_MIGRATION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_events_host_app ON events(host_app);
CREATE INDEX IF NOT EXISTS idx_events_model ON events(model);
"""

# Label used when a row predates the Phase 3 migration (host_app /
# model are NULL) or when the parser could not attribute a value. Kept
# as a single constant so the UI surfaces one consistent label and the
# value-of-time math in AgencyUsageReport can recognize it.
UNKNOWN_LABEL = "Unknown"


class HistoryStore:
    """Thin wrapper around a per-user SQLite history database."""

    def __init__(self, conn: sqlite3.Connection, path: Path, *, now_utc: datetime | None = None) -> None:
        self._conn = conn
        self.path = path
        self._init_schema(now_utc=now_utc or datetime.now(tz=timezone.utc))

    # -- construction -------------------------------------------------------

    @classmethod
    def open(
        cls,
        path: Path | None = None,
        *,
        now_utc: datetime | None = None,
    ) -> "HistoryStore":
        """Open (and if necessary create / repair) the history database."""
        path = path or default_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(path), isolation_level=None)
            # Touch the DB so we fail fast on corruption rather than at first query.
            conn.execute("PRAGMA schema_version").fetchone()
        except sqlite3.DatabaseError:
            # Windows holds the file open via the failed connection; close it
            # first or the subsequent rename / unlink raises PermissionError.
            try:
                conn.close()  # type: ignore[has-type]
            except Exception:
                pass
            quarantine = path.with_name(
                f"{path.name}.corrupt-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            )
            try:
                path.replace(quarantine)
                print(f"[history_store] DB corrupt; quarantined to {quarantine}")
            except OSError:
                # Best effort -- if we can't rename, fall back to deleting.
                try:
                    path.unlink()
                except OSError:
                    raise
            conn = sqlite3.connect(str(path), isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return cls(conn, path, now_utc=now_utc)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # -- schema / meta ------------------------------------------------------

    def _init_schema(self, *, now_utc: datetime) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_SCHEMA_SQL)
        self._migrate_schema()
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_POST_MIGRATION_INDEX_SQL)
        if self._get_meta("schema_version") is None:
            self._set_meta("schema_version", SCHEMA_VERSION)
        if self._get_meta("first_seen_at_utc") is None:
            now_iso = _normalize_ts(now_utc)
            ends_iso = _normalize_ts(now_utc + timedelta(days=TRIAL_DAYS))
            self._set_meta("first_seen_at_utc", now_iso)
            self._set_meta("trial_ends_at_utc", ends_iso)
            self._set_meta("last_seen_at_utc", now_iso)
            # The trial begins recording from "now" forward. Pre-install
            # log events in the Copilot CLI log dir must not be backfilled.
            self._set_meta("recording_active_since_utc", now_iso)
        # Monotonically advance last_seen_at; clock rollback must not extend trial.
        last_seen = self._parse_meta_dt("last_seen_at_utc") or now_utc
        new_last = max(last_seen, now_utc)
        self._set_meta("last_seen_at_utc", _normalize_ts(new_last))

    def _migrate_schema(self) -> None:
        """Apply forward-only ALTER TABLE migrations for legacy databases.

        Phase 3 (schema_version 1 -> 2) adds the nullable ``host_app`` and
        ``model`` columns and their supporting indexes. ``CREATE TABLE
        IF NOT EXISTS`` covers brand-new DBs; this method covers DBs
        that already exist with the v1 schema. ``ALTER TABLE`` is
        wrapped in a try/except so a partial prior migration (column
        already added, version not bumped) is a no-op rather than a
        crash.

        New rows surface ``host_app`` / ``model`` from the parser; old
        rows keep NULL and are bucketed as
        :data:`UNKNOWN_LABEL` by :meth:`totals_by_host` /
        :meth:`totals_by_model`. No backfill is attempted - the rows
        predate Phase 2's host attribution fix anyway, so we cannot
        accurately attribute them after the fact.
        """
        current = self._get_meta("schema_version")
        # New-DB path: tables were just created with the latest schema; the
        # version stamp lands below.
        if current is None:
            return
        if current == SCHEMA_VERSION:
            return
        if current == "1":
            for ddl in (
                "ALTER TABLE events ADD COLUMN host_app TEXT",
                "ALTER TABLE events ADD COLUMN model TEXT",
            ):
                try:
                    self._conn.execute(ddl)
                except sqlite3.OperationalError:
                    # Column already exists from a partial prior run.
                    pass
            current = "2"
            self._set_meta("schema_version", "2")
        if current == "2":
            # Collapse the duplicate per-session rollup rows that earlier
            # builds accumulated (each growing Agency ``session.shutdown``
            # snapshot was stored as a distinct content-addressed row),
            # then re-key the survivor to the stable rollup id so future
            # ingests REPLACE it in place.
            self._collapse_agency_rollups()
            current = "3"
            self._set_meta("schema_version", "3")

    def _collapse_agency_rollups(self) -> None:
        """Deduplicate persisted Agency rollups, one row per session.

        Every persisted Agency row is a canonical ``session.shutdown``
        rollup (the per-turn estimates are never ingested), so any two
        rows sharing a ``session_id`` are snapshots of the same rollup.
        Keep the largest/newest snapshot per session and re-key it to the
        stable :func:`_rollup_event_id` so the next ingest overwrites it
        rather than re-creating a duplicate.
        """
        cur = self._conn.execute(
            "SELECT event_id, session_id, host_app, ts_utc, "
            "input_tokens + output_tokens + cache_read_tokens AS total "
            "FROM events WHERE host_app = 'Agency'"
        )
        by_sid: dict[str, list[tuple]] = {}
        for eid, sid, host_app, ts_utc, total in cur.fetchall():
            by_sid.setdefault(sid, []).append((eid, host_app, ts_utc, total))
        for sid, group in by_sid.items():
            # Survivor = latest snapshot, breaking ties on the larger
            # cumulative total (rollups grow monotonically within a run).
            survivor = max(group, key=lambda g: (g[2], g[3]))
            survivor_eid, host_app, _ts, _total = survivor
            for eid, _h, _t, _n in group:
                if eid != survivor_eid:
                    self._conn.execute(
                        "DELETE FROM events WHERE event_id = ?", (eid,)
                    )
            stable = _rollup_event_id(sid, host_app)
            if survivor_eid != stable:
                # Drop any pre-existing stable-id row to avoid a PK clash,
                # then promote the survivor to the stable id.
                self._conn.execute(
                    "DELETE FROM events WHERE event_id = ?", (stable,)
                )
                self._conn.execute(
                    "UPDATE events SET event_id = ? WHERE event_id = ?",
                    (stable, survivor_eid),
                )

    def _get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _parse_meta_dt(self, key: str) -> datetime | None:
        raw = self._get_meta(key)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    # -- tier / trial -------------------------------------------------------

    def tier_status(self, *, now_utc: datetime | None = None) -> TierStatus:
        now_utc = now_utc or datetime.now(tz=timezone.utc)
        first_seen = self._parse_meta_dt("first_seen_at_utc") or now_utc
        trial_ends = self._parse_meta_dt("trial_ends_at_utc") or (
            first_seen + timedelta(days=TRIAL_DAYS)
        )
        # Clock-rollback guard: tier checks always use max(now, last_seen).
        last_seen = self._parse_meta_dt("last_seen_at_utc") or now_utc
        effective_now = max(now_utc, last_seen)
        return TierStatus(
            first_seen_at_utc=first_seen,
            trial_ends_at_utc=trial_ends,
            now_utc=effective_now,
            advanced_enabled=self._get_meta("advanced_enabled") == "true",
            recording_opt_out=self._get_meta("recording_opt_out") == "true",
            coffee_purchased_at_utc=self._parse_meta_dt("coffee_purchased_at_utc"),
        )

    def set_advanced_enabled(
        self, enabled: bool, *, now_utc: datetime | None = None
    ) -> None:
        """Toggle the post-trial Advanced flag.

        Raises :class:`SupporterRequiredError` if the caller asks to enable
        Advanced recording after the free trial has ended without first
        marking the user as a supporter (via :meth:`mark_supporter_purchased`).
        During the trial the flag can be flipped freely; it only takes
        effect once the trial expires.
        """
        now_utc = now_utc or datetime.now(tz=timezone.utc)
        status = self.tier_status(now_utc=now_utc)
        if enabled and not status.in_trial and not status.supporter_purchased:
            raise SupporterRequiredError(
                "Advanced history requires a supporter unlock after the "
                "free trial. Mark the supporter as purchased first."
            )
        prior_recording = status.recording_enabled
        self._set_meta("advanced_enabled", "true" if enabled else "false")
        # An explicit re-enable should clear any prior opt-out so the user
        # doesn't have to flip two flags to start recording again.
        if enabled:
            self._set_meta("recording_opt_out", "false")
        self._maybe_advance_active_since(now_utc, prior_recording)

    def set_recording_opt_out(
        self, opted_out: bool, *, now_utc: datetime | None = None
    ) -> None:
        now_utc = now_utc or datetime.now(tz=timezone.utc)
        prior_recording = self.tier_status(now_utc=now_utc).recording_enabled
        self._set_meta("recording_opt_out", "true" if opted_out else "false")
        self._maybe_advance_active_since(now_utc, prior_recording)

    # -- supporter / coffee prompt -----------------------------------------

    def supporter_purchased(self) -> bool:
        return self._get_meta("coffee_purchased_at_utc") is not None

    def mark_supporter_purchased(
        self, *, now_utc: datetime | None = None
    ) -> None:
        """Idempotently record an honor-system "coffee bought" unlock.

        The first call stamps ``coffee_purchased_at_utc``; subsequent calls
        leave the original timestamp in place (so we don't reset it on
        "Restore supporter status" clicks). Also clears any prior
        ``recording_opt_out`` so the user doesn't have to flip two flags to
        get recording going.
        """
        now_utc = now_utc or datetime.now(tz=timezone.utc)
        if self._get_meta("coffee_purchased_at_utc") is None:
            self._set_meta("coffee_purchased_at_utc", _normalize_ts(now_utc))
        self._set_meta("recording_opt_out", "false")

    def should_show_coffee_prompt(
        self,
        *,
        now_utc: datetime | None = None,
        cadence_days: int = COFFEE_PROMPT_CADENCE_DAYS,
    ) -> bool:
        """Whether the tray should pop the "buy me a coffee" nag on startup.

        Returns False if: the user has already unlocked, they explicitly
        suppressed the prompt, they're still in the free trial (no nag
        during a free period), or it's been less than ``cadence_days``
        since the last time we showed it.
        """
        if self._get_meta("coffee_prompt_suppressed") == "true":
            return False
        if self.supporter_purchased():
            return False
        now_utc = now_utc or datetime.now(tz=timezone.utc)
        status = self.tier_status(now_utc=now_utc)
        if status.in_trial:
            return False
        last_shown = self._parse_meta_dt("coffee_prompt_last_shown_at_utc")
        if last_shown is None:
            return True
        # Use rollback-corrected effective_now so a backwards clock can't
        # re-fire the prompt sooner than the cadence.
        return (status.now_utc - last_shown) >= timedelta(days=cadence_days)

    def mark_coffee_prompt_shown(
        self, *, now_utc: datetime | None = None
    ) -> None:
        now_utc = now_utc or datetime.now(tz=timezone.utc)
        self._set_meta(
            "coffee_prompt_last_shown_at_utc", _normalize_ts(now_utc)
        )

    def set_coffee_prompt_suppressed(self, suppressed: bool) -> None:
        self._set_meta(
            "coffee_prompt_suppressed", "true" if suppressed else "false"
        )


    def _maybe_advance_active_since(
        self, now_utc: datetime, prior_recording_enabled: bool
    ) -> None:
        """If recording just transitioned off -> on, move the watermark to now.

        Events older than ``recording_active_since_utc`` are dropped by
        :meth:`ingest`, so this is what prevents a re-enable from
        retroactively persisting events that occurred while the user had
        recording disabled.

        Clock-rollback guard: the new watermark is the maximum of
        ``now_utc`` and the rollback-corrected ``effective_now`` (= max of
        wall-clock and the last-known timestamp). It is also never set
        below the existing watermark. Together these mean a rolled-back
        clock cannot expose older "paused" events.
        """
        if prior_recording_enabled:
            return
        new_status = self.tier_status(now_utc=now_utc)
        if not new_status.recording_enabled:
            return
        candidate = new_status.now_utc  # already max(now_utc, last_seen)
        existing = self._parse_meta_dt("recording_active_since_utc")
        if existing is not None and existing >= candidate:
            return
        self._set_meta("recording_active_since_utc", _normalize_ts(candidate))

    # -- ingest -------------------------------------------------------------

    def ingest(self, events: Iterable[UsageEvent]) -> int:
        """Insert events (idempotent). Returns rows actually added.

        Events with a timestamp earlier than ``recording_active_since_utc``
        are silently dropped: the tier promise is that we only persist
        events that occurred while recording was enabled, and the same
        log file may contain events that straddle a "user turned recording
        back on" boundary.

        Agency events flagged ``is_estimated=True`` (active sessions that
        have not yet emitted a ``session.shutdown`` rollup) are also
        dropped: persisting them would double-count once the rollup
        lands, since the estimated per-turn events have different
        ``event_id`` hashes than the canonical single rollup row.

        Events flagged ``is_rollup=True`` (per-session cumulative totals
        such as the Agency ``session.shutdown`` rollup) are keyed by
        session via :func:`_rollup_event_id` and inserted with ``INSERT
        OR REPLACE``. Their token counts grow across successive parses
        while the timestamp stays fixed, so a content-addressed id would
        store every snapshot as a new row and inflate the totals; keying
        by session makes the latest snapshot supersede the prior one.
        """
        cutoff = self._parse_meta_dt("recording_active_since_utc")
        insert_rows = []
        replace_rows = []
        for ev in events:
            if getattr(ev, "is_estimated", False):
                continue
            ts_raw = ev.timestamp
            if ts_raw.tzinfo is None:
                ts_aware = ts_raw.replace(tzinfo=timezone.utc)
            else:
                ts_aware = ts_raw
            if cutoff is not None and ts_aware < cutoff:
                continue
            ts_norm = _normalize_ts(ts_raw)
            sid = ev.session_id or ""
            host_app = getattr(ev, "host_app", None)
            row = (
                ts_norm,
                sid,
                int(ev.input_tokens),
                int(ev.output_tokens),
                int(ev.cache_read_tokens),
                int(ev.cache_write_tokens),
                host_app,
                getattr(ev, "model", None),
            )
            if getattr(ev, "is_rollup", False):
                replace_rows.append((_rollup_event_id(sid, host_app), *row))
            else:
                insert_rows.append((_event_id(ts_norm, sid, ev), *row))
        if not insert_rows and not replace_rows:
            return 0
        cols = (
            "INSERT {verb} INTO events("
            "event_id, ts_utc, session_id, "
            "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
            "host_app, model"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        before = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if insert_rows:
            # Per-turn events are immutable; never overwrite an existing row.
            self._conn.executemany(cols.format(verb="OR IGNORE"), insert_rows)
        if replace_rows:
            # Per-session rollups: a newer, larger snapshot supersedes the
            # prior one instead of accumulating a duplicate row.
            self._conn.executemany(cols.format(verb="OR REPLACE"), replace_rows)
        after = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return after - before

    def ingest_logs(
        self,
        log_dir: Path | None = None,
        *,
        agency_root: Path | None = None,
        session_state_root: Path | None = None,
    ) -> int:
        """Scan known log roots and ingest any new events.

        Walks ``log_dir`` (Copilot CLI process logs), ``agency_root``
        (``~/.agency/logs/``) and ``session_state_root``
        (``~/.copilot/session-state/``, the per-session event store the
        Copilot CLI 1.0.54+ writes instead of process-log telemetry) and
        ingests events from any source whose ``(size, mtime_ns)`` changed
        since the last successful ingest.

        The session-state store shares ``session_id`` with the process
        logs, so to avoid double-counting a session already persisted
        (e.g. from a process log that has since rotated away) the set of
        session_ids already in the DB is passed to
        :func:`iter_all_events` as ``extra_skip_session_ids`` - which
        applies it only to the session-state source, never to the
        incremental process-log source.

        Active session-state / Agency sessions (``is_estimated=True``)
        are not persisted by :meth:`ingest`; their files DO participate
        in change-detection so the next ingest after the rollup lands
        picks it up.

        Returns the number of rows actually added.
        """
        from usage_core import LOG_DIR  # local import avoids reload cycles in tests

        log_dir = log_dir or LOG_DIR
        agency_root = agency_root or AGENCY_LOG_ROOT
        session_state_root = session_state_root or SESSION_STATE_DIR

        current: dict[str, str] = {}
        if log_dir.exists():
            for log_path in sorted(log_dir.glob("*.log")):
                try:
                    st = log_path.stat()
                except OSError:
                    continue
                current[f"file:{log_path.name}"] = f"{st.st_size}:{st.st_mtime_ns}"
        if agency_root.exists():
            for sess_dir in sorted(agency_root.glob("session_*")):
                events_path = sess_dir / "events.jsonl"
                if not events_path.exists():
                    continue
                try:
                    st = events_path.stat()
                except OSError:
                    continue
                current[f"agency:{sess_dir.name}"] = f"{st.st_size}:{st.st_mtime_ns}"
        if session_state_root.exists():
            for sess_dir in sorted(session_state_root.glob("*")):
                if not sess_dir.is_dir():
                    continue
                events_path = sess_dir / "events.jsonl"
                if not events_path.exists():
                    continue
                try:
                    st = events_path.stat()
                except OSError:
                    continue
                current[f"sstate:{sess_dir.name}"] = f"{st.st_size}:{st.st_mtime_ns}"

        if not current:
            return 0

        # Cheap path: nothing in any source changed since last ingest.
        if all(self._get_meta(k) == v for k, v in current.items()):
            return 0

        try:
            # Re-walk every source via the unified iterator so any new
            # parser added to iter_all_events() is automatically picked
            # up here without further changes.
            from tokentray.parsers import copilot_logs as _cl
            from tokentray.parsers import agency_events as _ae
            from tokentray.parsers import copilot_session_state as _ss
            _orig_log_dir = _cl.LOG_DIR
            _orig_log_root = _ae.LOG_ROOT
            _orig_ss_dir = _ss.LOG_DIR
            try:
                _cl.LOG_DIR = log_dir
                _ae.LOG_ROOT = agency_root
                _ss.LOG_DIR = session_state_root
                events = list(
                    iter_all_events(
                        extra_skip_session_ids=self.existing_session_ids()
                    )
                )
            finally:
                _cl.LOG_DIR = _orig_log_dir
                _ae.LOG_ROOT = _orig_log_root
                _ss.LOG_DIR = _orig_ss_dir
        except Exception:
            return 0

        added = self.ingest(events)
        for k, v in current.items():
            self._set_meta(k, v)
        return added

    def existing_session_ids(self) -> set[str]:
        """Return the set of distinct session_ids already persisted.

        Used to keep the session-state rollup parser from re-counting a
        session that another source already recorded (the two share a
        session_id).
        """
        rows = self._conn.execute(
            "SELECT DISTINCT session_id FROM events"
        ).fetchall()
        return {r[0] for r in rows if r[0]}

    # -- queries / rollups --------------------------------------------------

    def _range_totals(self, start_utc: datetime, end_utc: datetime) -> Totals:
        start_iso = _normalize_ts(start_utc)
        end_iso = _normalize_ts(end_utc)
        cur = self._conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, session_id "
            "FROM events WHERE ts_utc >= ? AND ts_utc < ?",
            (start_iso, end_iso),
        )
        totals = Totals()
        for in_t, out_t, cr_t, cw_t, sid in cur:
            totals.input_tokens += in_t
            totals.output_tokens += out_t
            totals.cache_read_tokens += cr_t
            totals.cache_write_tokens += cw_t
            totals.events += 1
            if sid:
                totals.sessions.add(sid)
        return totals

    def summarize_today(self, *, tz=None) -> Totals:
        start, end = _today_range_utc(tz)
        return self._range_totals(start, end)

    def summarize_week(self, *, tz=None) -> Totals:
        start, end = _this_week_range_utc(tz)
        return self._range_totals(start, end)

    def summarize_month(self, *, tz=None) -> Totals:
        start, end = _this_month_range_utc(tz)
        return self._range_totals(start, end)

    def summarize_all_time(self) -> Totals:
        cur = self._conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, session_id FROM events"
        )
        totals = Totals()
        for in_t, out_t, cr_t, cw_t, sid in cur:
            totals.input_tokens += in_t
            totals.output_tokens += out_t
            totals.cache_read_tokens += cr_t
            totals.cache_write_tokens += cw_t
            totals.events += 1
            if sid:
                totals.sessions.add(sid)
        return totals

    def all_summaries(self, *, tz=None) -> dict[str, Totals]:
        """Convenience: compute all four rollups in one call."""
        return {
            "today": self.summarize_today(tz=tz),
            "week": self.summarize_week(tz=tz),
            "month": self.summarize_month(tz=tz),
            "all_time": self.summarize_all_time(),
        }

    # -- breakdowns (Advanced tab) -----------------------------------------

    def _grouped_totals(
        self,
        column: str,
        *,
        start_utc: datetime | None,
        end_utc: datetime | None,
    ) -> dict[str, Totals]:
        """Aggregate :class:`Totals` per distinct value of *column*.

        *column* MUST be one of ``"host_app"`` or ``"model"`` - this
        method is a private helper for the two public breakdowns and
        the column name is interpolated into SQL, so accepting an
        arbitrary string would be a SQL-injection footgun. Pre-Phase-3
        rows have NULL in the new columns; they roll up under
        :data:`UNKNOWN_LABEL` so the Advanced tab still shows their
        contribution rather than dropping them silently.
        """
        if column not in ("host_app", "model"):
            raise ValueError(f"Unsupported grouping column: {column!r}")
        sql = (
            f"SELECT COALESCE({column}, ?) AS bucket, "
            "input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, session_id FROM events"
        )
        params: list = [UNKNOWN_LABEL]
        if start_utc is not None and end_utc is not None:
            sql += " WHERE ts_utc >= ? AND ts_utc < ?"
            params.extend([_normalize_ts(start_utc), _normalize_ts(end_utc)])
        cur = self._conn.execute(sql, params)
        out: dict[str, Totals] = {}
        for bucket, in_t, out_t, cr_t, cw_t, sid in cur:
            t = out.setdefault(bucket or UNKNOWN_LABEL, Totals())
            t.input_tokens += in_t
            t.output_tokens += out_t
            t.cache_read_tokens += cr_t
            t.cache_write_tokens += cw_t
            t.events += 1
            if sid:
                t.sessions.add(sid)
        return out

    def totals_by_host(
        self,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        tz=None,
        period: str | None = None,
    ) -> dict[str, Totals]:
        """:class:`Totals` keyed by ``host_app`` over the requested range.

        Pass *period* as one of ``"today"`` / ``"week"`` / ``"month"`` /
        ``"all_time"`` to use the same local-tz boundary logic as
        :meth:`summarize_today` and friends. Pass explicit
        *start_utc* / *end_utc* to override. Omit both to summarize
        all-time. Unknown hosts (pre-Phase-3 rows) bucket as
        :data:`UNKNOWN_LABEL`.
        """
        if period is not None:
            start_utc, end_utc = _period_range_utc(period, tz=tz)
        return self._grouped_totals("host_app", start_utc=start_utc, end_utc=end_utc)

    def totals_by_model(
        self,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        tz=None,
        period: str | None = None,
    ) -> dict[str, Totals]:
        """:class:`Totals` keyed by ``model`` over the requested range.

        See :meth:`totals_by_host` for parameter semantics.
        """
        if period is not None:
            start_utc, end_utc = _period_range_utc(period, tz=tz)
        return self._grouped_totals("model", start_utc=start_utc, end_utc=end_utc)


__all__ = [
    "COFFEE_PROMPT_CADENCE_DAYS",
    "DB_FILENAME",
    "HistoryStore",
    "SCHEMA_VERSION",
    "SupporterRequiredError",
    "TRIAL_DAYS",
    "TierStatus",
    "Totals",
    "UNKNOWN_LABEL",
    "default_db_path",
]
