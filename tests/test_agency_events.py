"""Phase 2: Agency event-stream parser tests.

Covers the ``tokentray.parsers.agency_events`` module's contract plus
the ``iter_all_events`` chaining behavior that surfaces both Copilot
CLI logs and Agency sessions through one call.

Realistic fixtures mirror the actual ``~/.agency/logs/session_*/events.jsonl``
schema observed in production (session.start carries sessionId; per-turn
assistant.message events carry data.model and data.outputTokens only;
session.shutdown carries the full tokenDetails rollup with input,
output, cache_read, cache_write counts).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tokentray.parsers import iter_all_events  # noqa: E402
from tokentray.parsers._common import UsageEvent  # noqa: E402
from tokentray.parsers.agency_events import (  # noqa: E402
    _parse_events_jsonl,
    iter_agency_events,
)
from tokentray.parsers.model_normalize import normalize_model  # noqa: E402


def _write_events_jsonl(
    session_dir: Path,
    *,
    session_id: str,
    model: str = "claude-opus-4.7-1m-internal",
    include_shutdown: bool = True,
    input_tokens: int = 190363,
    output_tokens: int = 1314084,
    cache_read_tokens: int = 350940053,
    cache_write_tokens: int = 18181779,
    n_assistant_messages: int = 2,
    output_per_message: int = 176,
    start_ts: str = "2026-05-16T21:37:16.320Z",
    shutdown_ts: str = "2026-05-18T15:05:26.000Z",
) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    rows = [
        {
            "type": "session.start",
            "data": {"sessionId": session_id, "startTime": start_ts},
            "id": "ev-start",
            "timestamp": start_ts,
        },
        {
            "type": "session.model_change",
            "data": {"newModel": model},
            "id": "ev-model",
            "timestamp": start_ts,
        },
    ]
    for i in range(n_assistant_messages):
        rows.append(
            {
                "type": "assistant.message",
                "data": {
                    "messageId": f"msg-{i}",
                    "model": model,
                    "outputTokens": output_per_message,
                    "turnId": str(i),
                },
                "id": f"ev-msg-{i}",
                "timestamp": f"2026-05-16T21:4{i}:00.000Z",
            }
        )
    if include_shutdown:
        rows.append(
            {
                "type": "session.shutdown",
                "data": {
                    "shutdownType": "routine",
                    "tokenDetails": {
                        "input": {"tokenCount": input_tokens},
                        "output": {"tokenCount": output_tokens},
                        "cache_read": {"tokenCount": cache_read_tokens},
                        "cache_write": {"tokenCount": cache_write_tokens},
                    },
                    "totalPremiumRequests": 101,
                },
                "id": "ev-shutdown",
                "timestamp": shutdown_ts,
            }
        )
    events_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    return events_path


def test_completed_session_emits_one_rollup_event(tmp_path):
    sess_dir = tmp_path / "session_20260516_213716_5352"
    _write_events_jsonl(sess_dir, session_id="sid-rollup")
    events = list(iter_agency_events(log_root=tmp_path))
    assert len(events) == 1
    ev = events[0]
    assert ev.host_app == "Agency"
    assert ev.session_id == "sid-rollup"
    assert ev.input_tokens == 190363
    assert ev.output_tokens == 1314084
    assert ev.cache_read_tokens == 350940053
    assert ev.cache_write_tokens == 18181779
    assert ev.is_estimated is False
    assert ev.model == "claude-opus-4.7"
    assert ev.raw_model == "claude-opus-4.7-1m-internal"
    # Shutdown timestamp should be respected, not session start.
    assert ev.timestamp == datetime(2026, 5, 18, 15, 5, 26, tzinfo=timezone.utc)


def test_active_session_emits_per_turn_estimates(tmp_path):
    sess_dir = tmp_path / "session_20260516_213716_5352"
    _write_events_jsonl(
        sess_dir,
        session_id="sid-active",
        include_shutdown=False,
        n_assistant_messages=3,
        output_per_message=42,
    )
    events = list(iter_agency_events(log_root=tmp_path))
    assert len(events) == 3
    for ev in events:
        assert ev.host_app == "Agency"
        assert ev.session_id == "sid-active"
        assert ev.is_estimated is True
        assert ev.input_tokens == 0
        assert ev.output_tokens == 42
        assert ev.cache_read_tokens == 0
        assert ev.cache_write_tokens == 0
        assert ev.model == "claude-opus-4.7"


def test_no_session_start_yields_nothing(tmp_path):
    # events.jsonl without session.start cannot honor history_store
    # idempotency; the parser must skip it rather than emit events
    # with empty session_ids.
    sess_dir = tmp_path / "session_20260516_213716_5352"
    sess_dir.mkdir()
    (sess_dir / "events.jsonl").write_text(
        json.dumps({"type": "hook.start", "id": "x", "timestamp": "2026-05-16T21:37:16.320Z"}) + "\n",
        encoding="utf-8",
    )
    events = list(iter_agency_events(log_root=tmp_path))
    assert events == []


def test_missing_agency_root_does_not_raise(tmp_path):
    nowhere = tmp_path / "no-agency"
    events = list(iter_agency_events(log_root=nowhere))
    assert events == []


def test_malformed_jsonl_lines_are_skipped(tmp_path):
    sess_dir = tmp_path / "session_x"
    sess_dir.mkdir()
    (sess_dir / "events.jsonl").write_text(
        '\n'.join([
            'not json at all',
            json.dumps({"type": "session.start", "data": {"sessionId": "sid-malformed"}, "timestamp": "2026-05-16T21:37:16.320Z"}),
            '{"truncated',  # cut off
            json.dumps({
                "type": "session.shutdown",
                "data": {
                    "tokenDetails": {
                        "input": {"tokenCount": 1},
                        "output": {"tokenCount": 2},
                        "cache_read": {"tokenCount": 3},
                        "cache_write": {"tokenCount": 4},
                    },
                },
                "timestamp": "2026-05-16T21:37:20.000Z",
            }),
        ]) + '\n',
        encoding="utf-8",
    )
    events = list(iter_agency_events(log_root=tmp_path))
    assert len(events) == 1
    ev = events[0]
    assert ev.session_id == "sid-malformed"
    assert ev.input_tokens == 1
    assert ev.output_tokens == 2


def test_agency_cache_skips_unchanged_files(tmp_path, monkeypatch):
    sess_dir = tmp_path / "session_a"
    _write_events_jsonl(sess_dir, session_id="sid-cache")
    cache: dict = {}
    first = list(iter_agency_events(log_root=tmp_path, cache=cache))
    assert len(first) == 1
    assert "session_a" in cache

    # Block the parser; second call must satisfy from cache.
    def _boom(*_a, **_k):
        raise AssertionError("parser invoked on cache hit")
    from tokentray.parsers import agency_events as ae
    monkeypatch.setattr(ae, "_parse_events_jsonl", _boom)
    second = list(iter_agency_events(log_root=tmp_path, cache=cache))
    assert len(second) == 1
    assert second[0].session_id == "sid-cache"


def test_agency_cache_evicts_deleted_sessions(tmp_path):
    sess_a = tmp_path / "session_a"
    sess_b = tmp_path / "session_b"
    _write_events_jsonl(sess_a, session_id="sa")
    _write_events_jsonl(sess_b, session_id="sb")
    cache: dict = {}
    list(iter_agency_events(log_root=tmp_path, cache=cache))
    assert set(cache.keys()) == {"session_a", "session_b"}

    import shutil
    shutil.rmtree(sess_b)
    list(iter_agency_events(log_root=tmp_path, cache=cache))
    assert set(cache.keys()) == {"session_a"}


def test_iter_all_events_chains_copilot_and_agency(tmp_path, monkeypatch):
    # Set up a synthetic Copilot log AND a synthetic Agency session
    # then redirect both LOG roots and confirm iter_all_events yields
    # events from both.
    copilot_dir = tmp_path / "copilot"
    copilot_dir.mkdir()
    (copilot_dir / "process-1.log").write_text(
        "2026-05-18T15:39:29.000Z [INFO] [Telemetry] cli.telemetry:\n"
        + json.dumps({
            "kind": "assistant_usage",
            "session_id": "sid-cli",
            "client": {"client_type": "cli-interactive"},
            "properties": {"model": "gpt-5.5"},
            "metrics": {
                "input_tokens": 100, "output_tokens": 50,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
            },
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )
    agency_root = tmp_path / "agency"
    _write_events_jsonl(agency_root / "session_a", session_id="sid-agency")

    from tokentray.parsers import copilot_logs as cl, agency_events as ae
    monkeypatch.setattr(cl, "LOG_DIR", copilot_dir)
    monkeypatch.setattr(ae, "LOG_ROOT", agency_root)

    events = list(iter_all_events())
    by_host = {}
    for ev in events:
        by_host.setdefault(ev.host_app, []).append(ev)
    assert set(by_host) == {"Copilot CLI", "Agency"}
    assert by_host["Copilot CLI"][0].session_id == "sid-cli"
    assert by_host["Copilot CLI"][0].model == "gpt-5.5"
    assert by_host["Agency"][0].session_id == "sid-agency"
    assert by_host["Agency"][0].model == "claude-opus-4.7"


def _write_copilot_telemetry_log(
    log_path: Path,
    *,
    session_id: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 25,
    cache_write_tokens: int = 10,
    model: str = "claude-opus-4.7",
    client_type: str = "cli-server",
    timestamp: str = "2026-05-25T18:39:29.000Z",
) -> None:
    """Write one ``[Telemetry] cli.telemetry: ...`` assistant_usage block
    in the same format the Copilot CLI binary emits to
    ``~/.copilot/logs/`` (and that newer Agency builds capture into
    their session dir's ``process-*.log``)."""
    block = {
        "kind": "assistant_usage",
        "session_id": session_id,
        "client": {"client_type": client_type},
        "properties": {"model": model},
        "metrics": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        },
    }
    log_path.write_text(
        f"{timestamp} [INFO] [Telemetry] cli.telemetry:\n"
        f"{json.dumps(block, indent=2)}\n",
        encoding="utf-8",
    )


def test_agency_process_log_fallback_when_events_jsonl_missing(tmp_path):
    """Newer Agency builds (verified May 2026) often skip the
    events.jsonl writer entirely. The captured copilot subprocess log
    under the session dir still carries the same assistant_usage
    telemetry blocks the standalone CLI emits, so iter_agency_events
    must fall back to parsing those and stamp host_app="Agency"."""
    sess_dir = tmp_path / "session_20260525_133850_67256"
    sess_dir.mkdir()
    # No events.jsonl, but a process-*.log with two assistant_usage
    # blocks for the same session.
    proc = sess_dir / "process-1779741539721-64332.log"
    proc.write_text(
        "2026-05-25T20:39:29.000Z [INFO] [Telemetry] cli.telemetry:\n"
        + json.dumps({
            "kind": "assistant_usage",
            "session_id": "agency-fallback-sid",
            "client": {"client_type": "cli-interactive"},
            "properties": {"model": "claude-opus-4.7-1m-internal"},
            "metrics": {"input_tokens": 100, "output_tokens": 50,
                        "cache_read_tokens": 25, "cache_write_tokens": 10},
        }, indent=2)
        + "\n"
        + "2026-05-25T20:40:00.000Z [INFO] [Telemetry] cli.telemetry:\n"
        + json.dumps({
            "kind": "assistant_usage",
            "session_id": "agency-fallback-sid",
            "client": {"client_type": "cli-interactive"},
            "properties": {"model": "claude-opus-4.7-1m-internal"},
            "metrics": {"input_tokens": 200, "output_tokens": 75,
                        "cache_read_tokens": 0, "cache_write_tokens": 5},
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )

    events = list(iter_agency_events(log_root=tmp_path))
    assert len(events) == 2
    for ev in events:
        assert ev.host_app == "Agency"
        assert ev.session_id == "agency-fallback-sid"
        assert ev.model == "claude-opus-4.7"
        assert ev.raw_model == "claude-opus-4.7-1m-internal"
        assert ev.is_estimated is False
    assert events[0].input_tokens == 100
    assert events[1].input_tokens == 200


def test_agency_events_jsonl_takes_precedence_over_process_log(tmp_path):
    """When BOTH events.jsonl and process-*.log exist, the parser must
    use ONLY events.jsonl. Parsing both would double-count once the
    session closes (events.jsonl has session-level rollups; the
    process log has per-turn events for the same session)."""
    sess_dir = tmp_path / "session_dual"
    _write_events_jsonl(sess_dir, session_id="sid-dual")
    # Drop in a process-*.log that, if read, would emit an extra event
    # for a different (and obvious) session_id so we'd notice.
    _write_copilot_telemetry_log(
        sess_dir / "process-9.log",
        session_id="must-not-appear",
        input_tokens=99999,
    )
    events = list(iter_agency_events(log_root=tmp_path))
    assert len(events) == 1
    assert events[0].session_id == "sid-dual"
    assert all(ev.session_id != "must-not-appear" for ev in events)


def test_agency_fallback_cache_independent_per_process_log(tmp_path, monkeypatch):
    """Fallback cache entries are keyed per (session_dir, process_log)
    so multiple process logs in one session dir cache independently
    and a later events.jsonl-keyed entry never collides."""
    sess_dir = tmp_path / "session_multi_proc"
    sess_dir.mkdir()
    _write_copilot_telemetry_log(
        sess_dir / "process-1.log", session_id="sid-multi-a"
    )
    _write_copilot_telemetry_log(
        sess_dir / "process-2.log", session_id="sid-multi-b"
    )
    cache: dict = {}
    first = list(iter_agency_events(log_root=tmp_path, cache=cache))
    assert {ev.session_id for ev in first} == {"sid-multi-a", "sid-multi-b"}
    # Two cache entries, both tuple-keyed under the same session dir.
    proc_keys = [k for k in cache if isinstance(k, tuple) and k[0] == "proc"]
    assert len(proc_keys) == 2

    # Second call must satisfy from cache; if the parser is invoked
    # we fail the test.
    from tokentray.parsers import copilot_logs as cl
    def _boom(*_a, **_k):
        raise AssertionError("fallback parser invoked on cache hit")
    monkeypatch.setattr(cl, "_parse_log_file", _boom)
    from tokentray.parsers import agency_events as ae
    monkeypatch.setattr(ae, "_parse_log_file", _boom)
    second = list(iter_agency_events(log_root=tmp_path, cache=cache))
    assert {ev.session_id for ev in second} == {"sid-multi-a", "sid-multi-b"}


def test_normalize_model_collapses_aliases():
    assert normalize_model("claude-opus-4.7-1m-internal") == "claude-opus-4.7"
    assert normalize_model("claude-opus-4.7-internal") == "claude-opus-4.7"
    assert normalize_model("Claude-Opus-4.7") == "claude-opus-4.7"
    assert normalize_model("GPT-5.5 -> gpt-5.5") == "gpt-5.5"
    assert normalize_model("gpt-4.1-2025-04-01") == "gpt-4.1"
    # Unknown models pass through (lower-cased only) so future model
    # families don't silently misbucket.
    assert normalize_model("unknown-future-model") == "unknown-future-model"
    assert normalize_model(None) is None
    assert normalize_model("") is None
    assert normalize_model("   ") is None
