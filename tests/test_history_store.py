"""Unit tests for history_store.HistoryStore."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

# Make project root importable when running ``pytest`` from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from history_store import (  # noqa: E402
    COFFEE_PROMPT_CADENCE_DAYS,
    SCHEMA_VERSION,
    TRIAL_DAYS,
    HistoryStore,
    SupporterRequiredError,
    _normalize_ts,
)
from usage_core import UsageEvent  # noqa: E402


def _ev(ts: datetime, sid: str = "s1", *, i=1, o=2, cr=3, cw=4) -> UsageEvent:
    return UsageEvent(
        timestamp=ts,
        session_id=sid,
        input_tokens=i,
        output_tokens=o,
        cache_read_tokens=cr,
        cache_write_tokens=cw,
    )


# ---------------------------------------------------------------------------
# Schema / open
# ---------------------------------------------------------------------------

def test_open_creates_schema_and_stamps_trial(tmp_path):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=now)
    assert store._get_meta("schema_version") == SCHEMA_VERSION
    status = store.tier_status(now_utc=now)
    assert status.first_seen_at_utc == now
    assert status.trial_ends_at_utc == now + timedelta(days=TRIAL_DAYS)
    assert status.in_trial
    assert status.recording_enabled
    store.close()


def test_reopen_preserves_trial_anchor(tmp_path):
    db = tmp_path / "h.db"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    HistoryStore.open(db, now_utc=t0).close()

    later = t0 + timedelta(days=30)
    store = HistoryStore.open(db, now_utc=later)
    status = store.tier_status(now_utc=later)
    assert status.first_seen_at_utc == t0
    assert status.trial_ends_at_utc == t0 + timedelta(days=TRIAL_DAYS)
    assert status.trial_days_remaining == TRIAL_DAYS - 30
    store.close()


def test_corrupt_db_is_quarantined(tmp_path):
    db = tmp_path / "h.db"
    db.write_bytes(b"this is not a sqlite database, sorry")
    store = HistoryStore.open(db, now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))
    # Fresh DB now works.
    assert store._get_meta("schema_version") == SCHEMA_VERSION
    # The quarantine file should exist alongside.
    quarantined = list(tmp_path.glob("h.db.corrupt-*"))
    assert len(quarantined) == 1
    store.close()


# ---------------------------------------------------------------------------
# Trial / tier state machine
# ---------------------------------------------------------------------------

def test_trial_expiry_disables_recording(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    expired = t0 + timedelta(days=TRIAL_DAYS, hours=1)
    status = store.tier_status(now_utc=expired)
    assert not status.in_trial
    assert not status.recording_enabled
    assert status.trial_days_remaining == 0
    store.close()


def test_advanced_toggle_reenables_recording_after_trial(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    # v0.4: advanced recording post-trial now also requires a supporter unlock.
    store.mark_supporter_purchased(now_utc=t0 + timedelta(days=1))
    store.set_advanced_enabled(True)
    expired = t0 + timedelta(days=TRIAL_DAYS + 5)
    status = store.tier_status(now_utc=expired)
    assert not status.in_trial
    assert status.advanced_enabled
    assert status.supporter_purchased
    assert status.recording_enabled
    store.close()


def test_recording_opt_out_overrides_trial(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    store.set_recording_opt_out(True)
    status = store.tier_status(now_utc=t0 + timedelta(days=5))
    assert status.in_trial
    assert not status.recording_enabled
    store.close()


def test_clock_rollback_does_not_extend_trial(tmp_path):
    """Turning the system clock backward must not reset/extend the trial."""
    db = tmp_path / "h.db"
    t_late = datetime(2026, 4, 1, tzinfo=timezone.utc)  # well past 60d from t0
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    HistoryStore.open(db, now_utc=t0).close()

    # User runs the app on t_late -> last_seen_at_utc advances.
    HistoryStore.open(db, now_utc=t_late).close()

    # Now the user rolls the clock back inside the original trial window.
    rolled_back = t0 + timedelta(days=10)
    store = HistoryStore.open(db, now_utc=rolled_back)
    status = store.tier_status(now_utc=rolled_back)
    # effective_now should be max(now, last_seen) = t_late, so trial expired.
    assert not status.in_trial
    assert not status.recording_enabled
    store.close()


def test_set_advanced_enabled_clears_opt_out(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    store.set_recording_opt_out(True)
    # Toggle is called with default now (real time), well past trial.
    # Post-trial advanced requires supporter; satisfy that precondition.
    store.mark_supporter_purchased()
    store.set_advanced_enabled(True)
    status = store.tier_status(now_utc=t0 + timedelta(days=100))
    assert status.recording_enabled
    assert not status.recording_opt_out
    store.close()


# ---------------------------------------------------------------------------
# Ingest idempotency
# ---------------------------------------------------------------------------

def test_ingest_dedupes_identical_events(tmp_path):
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    ts = datetime(2026, 1, 5, 10, 30, 15, 250_000, tzinfo=timezone.utc)
    events = [_ev(ts, "s1", i=100, o=200, cr=50, cw=10)]
    assert store.ingest(events) == 1
    assert store.ingest(events) == 0  # idempotent
    assert store.ingest(events * 5) == 0
    store.close()


def test_ingest_distinguishes_different_token_counts(tmp_path):
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    ts = datetime(2026, 1, 5, 10, 30, 15, 250_000, tzinfo=timezone.utc)
    # Same ts + session but different token metrics = different events.
    e1 = _ev(ts, "s1", i=100, o=200, cr=50, cw=10)
    e2 = _ev(ts, "s1", i=101, o=200, cr=50, cw=10)
    assert store.ingest([e1, e2]) == 2
    store.close()


def test_ingest_handles_naive_timestamps(tmp_path):
    """UsageEvent may carry naive datetimes (mtime fallback); store normalizes."""
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    ts_naive = datetime(2026, 1, 5, 10, 30, 15)
    ts_utc = ts_naive.replace(tzinfo=timezone.utc)
    e_naive = _ev(ts_naive, "s1", i=1, o=2, cr=3, cw=4)
    e_utc = _ev(ts_utc, "s1", i=1, o=2, cr=3, cw=4)
    # Both should hash to the same event_id => 1 row total.
    assert store.ingest([e_naive]) == 1
    assert store.ingest([e_utc]) == 0
    store.close()


# ---------------------------------------------------------------------------
# Range summarization
# ---------------------------------------------------------------------------

def test_summarize_today_uses_local_boundaries(tmp_path, monkeypatch):
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    # Pretend "now" is 2026-06-15 12:00 UTC. We pass a fixed UTC tz so the
    # test is timezone-agnostic: today's UTC window = [00:00, 24:00).
    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
            return base if tz is None else base.astimezone(tz)

    monkeypatch.setattr("history_store.datetime", _FixedNow)

    today_morning = datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc)
    today_evening = datetime(2026, 6, 15, 22, 0, tzinfo=timezone.utc)
    yesterday = datetime(2026, 6, 14, 23, 0, tzinfo=timezone.utc)
    tomorrow = datetime(2026, 6, 16, 1, 0, tzinfo=timezone.utc)

    store.ingest([
        _ev(today_morning, "s1", i=10, o=20, cr=30, cw=0),
        _ev(today_evening, "s2", i=100, o=200, cr=300, cw=0),
        _ev(yesterday, "s3", i=999, o=999, cr=999, cw=0),
        _ev(tomorrow, "s4", i=999, o=999, cr=999, cw=0),
    ])

    totals = store.summarize_today(tz=timezone.utc)
    assert totals.input_tokens == 110
    assert totals.output_tokens == 220
    assert totals.cache_read_tokens == 330
    assert totals.events == 2
    assert totals.sessions == {"s1", "s2"}
    store.close()


def test_summarize_week_uses_iso_monday_start(tmp_path, monkeypatch):
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    # Pretend "now" is Wed 2026-06-17 12:00 UTC. ISO week = Mon Jun 15..Sun Jun 21.
    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)
            return base if tz is None else base.astimezone(tz)

    monkeypatch.setattr("history_store.datetime", _FixedNow)

    in_week_mon = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
    in_week_sun = datetime(2026, 6, 21, 23, 0, tzinfo=timezone.utc)
    before_week = datetime(2026, 6, 14, 23, 0, tzinfo=timezone.utc)  # Sunday before
    after_week = datetime(2026, 6, 22, 0, 30, tzinfo=timezone.utc)   # next Monday

    store.ingest([
        _ev(in_week_mon, "a", i=1, o=0, cr=0, cw=0),
        _ev(in_week_sun, "b", i=10, o=0, cr=0, cw=0),
        _ev(before_week, "c", i=100, o=0, cr=0, cw=0),
        _ev(after_week, "d", i=1000, o=0, cr=0, cw=0),
    ])

    totals = store.summarize_week(tz=timezone.utc)
    assert totals.input_tokens == 11
    assert totals.sessions == {"a", "b"}
    store.close()


def test_summarize_month_handles_year_rollover(tmp_path, monkeypatch):
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            base = datetime(2026, 12, 20, 10, 0, tzinfo=timezone.utc)
            return base if tz is None else base.astimezone(tz)

    monkeypatch.setattr("history_store.datetime", _FixedNow)

    in_month = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
    out_of_month = datetime(2027, 1, 1, 0, 30, tzinfo=timezone.utc)
    store.ingest([
        _ev(in_month, "a", i=5, o=0, cr=0, cw=0),
        _ev(out_of_month, "b", i=500, o=0, cr=0, cw=0),
    ])

    totals = store.summarize_month(tz=timezone.utc)
    assert totals.input_tokens == 5
    store.close()


def test_summarize_all_time_includes_everything(tmp_path):
    # First-seen anchor must predate the events, otherwise the
    # recording_active_since_utc watermark would (correctly) drop the
    # earlier event.
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    store.ingest([
        _ev(datetime(2025, 1, 1, tzinfo=timezone.utc), "a", i=1, o=0, cr=0, cw=0),
        _ev(datetime(2030, 6, 1, tzinfo=timezone.utc), "b", i=2, o=0, cr=0, cw=0),
    ])
    totals = store.summarize_all_time()
    assert totals.input_tokens == 3
    assert totals.events == 2
    assert totals.sessions == {"a", "b"}
    store.close()


# ---------------------------------------------------------------------------
# Tier banner text (smoke; for UI integration)
# ---------------------------------------------------------------------------

def test_tier_status_banner_strings(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    assert "trial" in store.tier_status(now_utc=t0).banner_text.lower()

    store.set_recording_opt_out(True)
    assert "disabled" in store.tier_status(now_utc=t0).banner_text.lower()

    store.set_recording_opt_out(False)
    store.mark_supporter_purchased(now_utc=t0 + timedelta(days=10))
    store.set_advanced_enabled(True)
    past = t0 + timedelta(days=TRIAL_DAYS + 1)
    assert "advanced" in store.tier_status(now_utc=past).banner_text.lower()

    store.set_advanced_enabled(False)
    assert "trial ended" in store.tier_status(now_utc=past).banner_text.lower()
    store.close()


def test_normalize_ts_round_trips():
    ts = datetime(2026, 5, 23, 19, 7, 5, 123_456, tzinfo=timezone.utc)
    norm = _normalize_ts(ts)
    assert norm == "2026-05-23T19:07:05.123456Z"
    # And naive UTC datetimes produce the same string.
    assert _normalize_ts(ts.replace(tzinfo=None)) == norm


# ---------------------------------------------------------------------------
# ingest_logs watermark behavior
# ---------------------------------------------------------------------------

def test_ingest_logs_skips_unchanged_dir(tmp_path, monkeypatch):
    import history_store
    import usage_core

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "x.log"
    # Realistic-looking telemetry block.
    log.write_text(
        "2026-05-18T15:39:28.130Z [INFO] [Telemetry] cli.telemetry: \n"
        "{\n"
        "  \"kind\": \"assistant_usage\",\n"
        "  \"session_id\": \"sess-1\",\n"
        "  \"metrics\": {\"input_tokens\": 10, \"output_tokens\": 20, "
        "\"cache_read_tokens\": 5, \"cache_write_tokens\": 0}\n"
        "}\n",
        encoding="utf-8",
    )

    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    added = store.ingest_logs(log_dir)
    assert added == 1

    # No file change -> no work.
    added2 = store.ingest_logs(log_dir)
    assert added2 == 0
    store.close()


# ---------------------------------------------------------------------------
# Watermark / no-backfill behavior
# ---------------------------------------------------------------------------

def test_ingest_empty_event_list_is_noop(tmp_path):
    store = HistoryStore.open(
        tmp_path / "h.db",
        now_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert store.ingest([]) == 0
    assert store.summarize_all_time().events == 0
    store.close()


def test_ingest_drops_events_predating_trial_start(tmp_path):
    """Pre-install log entries must not be backfilled when the trial begins."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)

    pre_install = _ev(
        datetime(2025, 12, 31, 23, 59, tzinfo=timezone.utc), "old"
    )
    during_trial = _ev(datetime(2026, 1, 5, tzinfo=timezone.utc), "new")
    # Only the in-trial event lands.
    assert store.ingest([pre_install, during_trial]) == 1
    totals = store.summarize_all_time()
    assert totals.sessions == {"new"}
    store.close()


def test_reenable_during_trial_does_not_backfill(tmp_path):
    """Opt-out then opt-in: events that occurred while opted out are dropped.

    This is the core privacy/tier promise: the user pressing "Pause" must
    create a genuine gap in the history, not a deferred persistence.
    """
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)

    # Day 1: recording is on, capture an event.
    day1 = _ev(datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc), "s-day1")
    assert store.ingest([day1]) == 1

    # Day 3: user opts out.
    opt_out_at = datetime(2026, 1, 3, 9, 0, tzinfo=timezone.utc)
    store.set_recording_opt_out(True, now_utc=opt_out_at)

    # Day 4: user re-enables. Watermark advances to *re-enable time*.
    reenable_at = datetime(2026, 1, 4, 8, 0, tzinfo=timezone.utc)
    store.set_recording_opt_out(False, now_utc=reenable_at)

    # Now a refresh happens. The log on disk still contains BOTH the
    # day1 event (already stored) AND a day3-while-paused event AND a
    # post-resume event. Only the brand-new post-resume event must land.
    while_paused = _ev(
        datetime(2026, 1, 3, 18, 0, tzinfo=timezone.utc), "s-while-paused"
    )
    after_resume = _ev(
        datetime(2026, 1, 4, 12, 0, tzinfo=timezone.utc), "s-after-resume"
    )
    added = store.ingest([day1, while_paused, after_resume])
    assert added == 1  # only after_resume is new + post-watermark

    sessions = store.summarize_all_time().sessions
    assert sessions == {"s-day1", "s-after-resume"}
    store.close()


def test_advanced_enable_after_trial_does_not_backfill(tmp_path):
    """Post-trial: flipping the Advanced toggle on must not import the gap."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)

    # Trial event (recording auto-on).
    trial_ev = _ev(datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc), "trial")
    assert store.ingest([trial_ev]) == 1

    # Trial ends. Many days of CLI usage accumulate in raw logs without
    # being persisted -- recording_enabled is False.
    post_trial_gap = _ev(
        datetime(2026, 3, 5, 14, 0, tzinfo=timezone.utc), "gap"
    )
    after_trial = t0 + timedelta(days=TRIAL_DAYS + 5)
    assert store.tier_status(now_utc=after_trial).recording_enabled is False

    # User finally enables Advanced (after donating).
    enable_at = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
    store.mark_supporter_purchased(now_utc=enable_at - timedelta(minutes=1))
    store.set_advanced_enabled(True, now_utc=enable_at)

    # New event after re-enable.
    fresh = _ev(datetime(2026, 4, 1, 18, 0, tzinfo=timezone.utc), "fresh")
    # Caller passes everything currently visible in logs; only the
    # post-enable event must land.
    added = store.ingest([trial_ev, post_trial_gap, fresh])
    assert added == 1
    assert store.summarize_all_time().sessions == {"trial", "fresh"}
    store.close()


def test_clock_rollback_does_not_lower_active_since(tmp_path):
    """A rolled-back clock when re-enabling must not expose paused-window events.

    Sequence: trial begins at t0. User opts out 1h later (last_seen now
    anchored). System clock is then rolled back to before t0 and the user
    re-enables. The new watermark should clamp to the rollback-corrected
    ``effective_now`` (i.e. last_seen), never below the existing watermark.
    """
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    store.set_recording_opt_out(True, now_utc=t0 + timedelta(hours=1))
    rolled_back = t0 - timedelta(days=30)
    store.set_recording_opt_out(False, now_utc=rolled_back)

    # The watermark must be >= t0 (the original anchor), regardless of the
    # rollback attempt.
    raw = store._get_meta("recording_active_since_utc")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    assert parsed >= t0

    pre_trial = _ev(datetime(2026, 5, 1, tzinfo=timezone.utc), "old")
    post = _ev(t0 + timedelta(days=1), "good")
    added = store.ingest([pre_trial, post])
    assert added == 1
    assert store.summarize_all_time().sessions == {"good"}
    store.close()


# ---------------------------------------------------------------------------
# DST handling
# ---------------------------------------------------------------------------

def test_local_midnight_utc_uses_per_date_offset_for_default_tz(monkeypatch):
    """Default-tz path must consult the OS for DST on the specific date.

    We can't easily fake the OS tz, but we can assert that the default-tz
    code path produces a result consistent with attaching the date's *own*
    local offset (i.e. it goes through ``astimezone`` on a naive datetime).
    """
    from history_store import _local_midnight_utc

    # Pick a date and confirm: the result is the local midnight rendered
    # as UTC, which equals "naive_midnight - utcoffset_for_that_date".
    d = date(2024, 11, 5)  # post-fall-back in US locales
    result = _local_midnight_utc(d)
    naive_midnight = datetime.combine(d, datetime.min.time())
    expected = naive_midnight.astimezone(timezone.utc)
    assert result == expected


# ---------------------------------------------------------------------------
# v0.4 — Supporter unlock & coffee prompt cadence
# ---------------------------------------------------------------------------

def test_supporter_unlock_persists_across_reopen(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = tmp_path / "h.db"
    store = HistoryStore.open(db, now_utc=t0)
    assert not store.supporter_purchased()
    store.mark_supporter_purchased(now_utc=t0 + timedelta(hours=2))
    assert store.supporter_purchased()
    store.close()

    reopened = HistoryStore.open(db, now_utc=t0 + timedelta(days=10))
    assert reopened.supporter_purchased()
    status = reopened.tier_status(now_utc=t0 + timedelta(days=10))
    assert status.coffee_purchased_at_utc is not None
    reopened.close()


def test_advanced_toggle_requires_supporter_after_trial(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    with pytest.raises(SupporterRequiredError):
        store.set_advanced_enabled(True, now_utc=post_trial)
    # State must remain unchanged on failure.
    assert not store.tier_status(now_utc=post_trial).advanced_enabled
    store.close()


def test_advanced_toggle_allowed_during_trial_without_supporter(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    store.set_advanced_enabled(True, now_utc=t0 + timedelta(days=5))
    status = store.tier_status(now_utc=t0 + timedelta(days=5))
    assert status.advanced_enabled
    assert status.in_trial
    assert status.recording_enabled  # trial covers recording either way
    store.close()


def test_advanced_toggle_succeeds_post_trial_once_supporter(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    store.mark_supporter_purchased(now_utc=post_trial)
    store.set_advanced_enabled(True, now_utc=post_trial)
    status = store.tier_status(now_utc=post_trial)
    assert status.recording_enabled
    store.close()


def test_should_show_coffee_prompt_during_trial_returns_false(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    assert store.should_show_coffee_prompt(now_utc=t0 + timedelta(days=10)) is False
    store.close()


def test_should_show_coffee_prompt_after_trial_first_time_returns_true(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    assert store.should_show_coffee_prompt(now_utc=post_trial) is True
    store.close()


def test_should_show_coffee_prompt_respects_21_day_cadence(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    store.mark_coffee_prompt_shown(now_utc=post_trial)
    # 1 day later: still suppressed.
    assert store.should_show_coffee_prompt(
        now_utc=post_trial + timedelta(days=1)
    ) is False
    # 20 days later: still under cadence.
    assert store.should_show_coffee_prompt(
        now_utc=post_trial + timedelta(days=20)
    ) is False
    # 21 days later: due again.
    assert store.should_show_coffee_prompt(
        now_utc=post_trial + timedelta(days=COFFEE_PROMPT_CADENCE_DAYS)
    ) is True


def test_should_show_coffee_prompt_respects_suppression(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    store.set_coffee_prompt_suppressed(True)
    assert store.should_show_coffee_prompt(now_utc=post_trial) is False
    # Un-suppressing brings it back.
    store.set_coffee_prompt_suppressed(False)
    assert store.should_show_coffee_prompt(now_utc=post_trial) is True
    store.close()


def test_should_show_coffee_prompt_returns_false_when_purchased(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    store.mark_supporter_purchased(now_utc=post_trial)
    assert store.should_show_coffee_prompt(now_utc=post_trial + timedelta(days=365)) is False
    store.close()


def test_mark_supporter_purchased_clears_opt_out(tmp_path):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    store.set_recording_opt_out(True)
    assert store.tier_status(now_utc=t0).recording_opt_out
    store.mark_supporter_purchased(now_utc=t0 + timedelta(days=1))
    assert not store.tier_status(now_utc=t0 + timedelta(days=1)).recording_opt_out
    store.close()


def test_mark_supporter_purchased_is_idempotent(tmp_path):
    """A second 'restore' click should not overwrite the original timestamp."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    first_call_at = t0 + timedelta(days=1)
    store.mark_supporter_purchased(now_utc=first_call_at)
    original = store.tier_status(now_utc=first_call_at).coffee_purchased_at_utc

    later = t0 + timedelta(days=30)
    store.mark_supporter_purchased(now_utc=later)
    second = store.tier_status(now_utc=later).coffee_purchased_at_utc
    assert second == original
    store.close()


def test_coffee_prompt_clock_rollback_does_not_refire(tmp_path):
    """A backwards clock can't make the prompt re-fire sooner than cadence."""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = HistoryStore.open(tmp_path / "h.db", now_utc=t0)
    post_trial = t0 + timedelta(days=TRIAL_DAYS + 1)
    # Establish last_seen anchor at post_trial and stamp the prompt.
    store.set_recording_opt_out(False, now_utc=post_trial)
    store.mark_coffee_prompt_shown(now_utc=post_trial)
    rolled_back = post_trial - timedelta(days=60)
    # Even with the clock rolled back, effective_now should clamp to >= post_trial,
    # so we are still inside cadence and prompt should NOT show.
    assert store.should_show_coffee_prompt(now_utc=rolled_back) is False
    store.close()
