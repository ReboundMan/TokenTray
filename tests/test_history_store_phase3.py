"""Phase 3: history_store schema migration and breakdown query tests."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from history_store import (  # noqa: E402
    SCHEMA_VERSION,
    UNKNOWN_LABEL,
    HistoryStore,
    _normalize_ts,
)
from usage_core import UsageEvent  # noqa: E402


def _ev(
    ts: datetime,
    sid: str,
    *,
    host_app: str | None = None,
    model: str | None = None,
    i: int = 1, o: int = 2, cr: int = 3, cw: int = 4,
) -> UsageEvent:
    return UsageEvent(
        timestamp=ts,
        session_id=sid,
        input_tokens=i,
        output_tokens=o,
        cache_read_tokens=cr,
        cache_write_tokens=cw,
        host_app=host_app,
        model=model,
    )


T0 = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)


def test_new_db_lands_on_current_schema(tmp_path):
    store = HistoryStore.open(tmp_path / "h.db", now_utc=T0)
    assert store._get_meta("schema_version") == SCHEMA_VERSION
    cols = [
        row[1]
        for row in store._conn.execute("PRAGMA table_info(events)").fetchall()
    ]
    assert "host_app" in cols
    assert "model" in cols
    store.close()


def test_v1_database_migrates_in_place(tmp_path):
    """A legacy v1 DB (no host_app/model columns) must keep its rows and
    pick up the new columns on first open with the v2 code."""
    db_path = tmp_path / "h.db"
    # Hand-create a v1 DB with one row already in events.
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.executescript(
        """
        CREATE TABLE events (
            event_id           TEXT PRIMARY KEY,
            ts_utc             TEXT NOT NULL,
            session_id         TEXT NOT NULL,
            input_tokens       INTEGER NOT NULL DEFAULT 0,
            output_tokens      INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_events_ts ON events(ts_utc);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', '1'),"
        "('first_seen_at_utc', ?),"
        "('trial_ends_at_utc', ?),"
        "('last_seen_at_utc', ?),"
        "('recording_active_since_utc', ?)",
        tuple(_normalize_ts(T0) for _ in range(4)),
    )
    conn.execute(
        "INSERT INTO events VALUES('legacy-id', ?, 'legacy-sid', 100, 50, 25, 10)",
        (_normalize_ts(T0),),
    )
    conn.close()

    store = HistoryStore.open(db_path, now_utc=T0)
    try:
        assert store._get_meta("schema_version") == "2"
        cols = [
            row[1]
            for row in store._conn.execute("PRAGMA table_info(events)").fetchall()
        ]
        assert "host_app" in cols
        assert "model" in cols
        # Existing row preserved with NULL in the new columns.
        row = store._conn.execute(
            "SELECT input_tokens, host_app, model FROM events WHERE event_id='legacy-id'"
        ).fetchone()
        assert row == (100, None, None)
        # totals_by_host buckets legacy rows under UNKNOWN_LABEL.
        by_host = store.totals_by_host()
        assert UNKNOWN_LABEL in by_host
        assert by_host[UNKNOWN_LABEL].input_tokens == 100
    finally:
        store.close()


def test_migration_is_idempotent(tmp_path):
    """Re-opening a freshly migrated DB must not re-bump version or
    redo ALTER TABLE work."""
    db_path = tmp_path / "h.db"
    HistoryStore.open(db_path, now_utc=T0).close()
    # Second open is a no-op for migrations.
    store = HistoryStore.open(db_path, now_utc=T0)
    try:
        assert store._get_meta("schema_version") == SCHEMA_VERSION
    finally:
        store.close()


def test_ingest_writes_host_app_and_model(tmp_path):
    store = HistoryStore.open(tmp_path / "h.db", now_utc=T0)
    try:
        added = store.ingest([
            _ev(T0, "s1", host_app="Clawpilot", model="claude-opus-4.7"),
            _ev(T0.replace(minute=5), "s2", host_app="Copilot CLI", model="gpt-5.5"),
            _ev(T0.replace(minute=10), "s3", host_app="Agency", model="claude-opus-4.7"),
        ])
        assert added == 3
        rows = store._conn.execute(
            "SELECT session_id, host_app, model FROM events ORDER BY session_id"
        ).fetchall()
        assert rows == [
            ("s1", "Clawpilot", "claude-opus-4.7"),
            ("s2", "Copilot CLI", "gpt-5.5"),
            ("s3", "Agency", "claude-opus-4.7"),
        ]
    finally:
        store.close()


def test_ingest_drops_estimated_events(tmp_path):
    """Active-session per-turn estimates must not be persisted, so the
    canonical session.shutdown rollup that lands later doesn't double-
    count against them."""
    store = HistoryStore.open(tmp_path / "h.db", now_utc=T0)
    try:
        est = UsageEvent(
            timestamp=T0,
            session_id="active-1",
            input_tokens=0,
            output_tokens=42,
            cache_read_tokens=0,
            cache_write_tokens=0,
            host_app="Agency",
            model="claude-opus-4.7",
            is_estimated=True,
        )
        canonical = _ev(T0, "done-1", host_app="Agency", model="claude-opus-4.7")
        added = store.ingest([est, canonical])
        assert added == 1
        rows = store._conn.execute("SELECT session_id FROM events").fetchall()
        assert rows == [("done-1",)]
    finally:
        store.close()


def test_totals_by_host_breakdown(tmp_path):
    store = HistoryStore.open(tmp_path / "h.db", now_utc=T0)
    try:
        store.ingest([
            _ev(T0, "s1", host_app="Clawpilot", model="claude-opus-4.7", i=10, o=5, cr=100, cw=0),
            _ev(T0.replace(minute=5), "s2", host_app="Clawpilot", model="claude-opus-4.7", i=20, o=10, cr=0, cw=0),
            _ev(T0.replace(minute=10), "s3", host_app="Agency", model="claude-opus-4.7", i=5, o=2, cr=0, cw=0),
            _ev(T0.replace(minute=15), "s4", host_app=None, model=None, i=1, o=1, cr=0, cw=0),
        ])
        by_host = store.totals_by_host()
        assert set(by_host) == {"Clawpilot", "Agency", UNKNOWN_LABEL}
        assert by_host["Clawpilot"].input_tokens == 30
        assert by_host["Clawpilot"].output_tokens == 15
        assert by_host["Clawpilot"].cache_read_tokens == 100
        assert by_host["Clawpilot"].events == 2
        assert by_host["Clawpilot"].session_count == 2
        assert by_host["Agency"].input_tokens == 5
        assert by_host[UNKNOWN_LABEL].events == 1
    finally:
        store.close()


def test_totals_by_model_breakdown(tmp_path):
    store = HistoryStore.open(tmp_path / "h.db", now_utc=T0)
    try:
        store.ingest([
            _ev(T0, "s1", host_app="Clawpilot", model="claude-opus-4.7", i=10),
            _ev(T0.replace(minute=5), "s2", host_app="Clawpilot", model="claude-opus-4.7", i=20),
            _ev(T0.replace(minute=10), "s3", host_app="Copilot CLI", model="gpt-5.5", i=5),
            _ev(T0.replace(minute=15), "s4", host_app="Clawpilot", model=None, i=7),
        ])
        by_model = store.totals_by_model()
        assert set(by_model) == {"claude-opus-4.7", "gpt-5.5", UNKNOWN_LABEL}
        assert by_model["claude-opus-4.7"].input_tokens == 30
        assert by_model["gpt-5.5"].input_tokens == 5
        assert by_model[UNKNOWN_LABEL].input_tokens == 7
    finally:
        store.close()


def test_grouped_totals_rejects_arbitrary_columns(tmp_path):
    """SQL injection guard: the column name is interpolated into the
    query string, so anything other than the known whitelist must
    raise rather than execute."""
    store = HistoryStore.open(tmp_path / "h.db", now_utc=T0)
    try:
        with pytest.raises(ValueError):
            store._grouped_totals("ts_utc; DROP TABLE events--", start_utc=None, end_utc=None)
    finally:
        store.close()


def test_totals_by_host_respects_period(tmp_path):
    """Period-scoped breakdowns must match the same local-tz boundary
    logic as summarize_today / summarize_week."""
    today = datetime.now(tz=timezone.utc).replace(microsecond=0)
    yesterday = today.replace(hour=2) if today.hour > 2 else today
    store = HistoryStore.open(tmp_path / "h.db", now_utc=today)
    try:
        store.ingest([
            _ev(today, "s-today", host_app="Clawpilot", model="claude-opus-4.7"),
        ])
        by_host_today = store.totals_by_host(period="today")
        assert "Clawpilot" in by_host_today
        all_time = store.totals_by_host(period="all_time")
        assert "Clawpilot" in all_time
    finally:
        store.close()
