"""Tests for the Copilot CLI ``session-state`` event-store parser.

Covers the parser introduced when Copilot CLI 1.0.54+ stopped writing
``process-*.log`` telemetry and moved token usage into
``~/.copilot/session-state/<id>/events.jsonl``.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tokentray.parsers import iter_all_events  # noqa: E402
from tokentray.parsers.copilot_session_state import (  # noqa: E402
    is_session_state_event,
    iter_copilot_session_state_events,
)


def _write_session(
    root,
    sid,
    *,
    producer="agency",
    model="claude-opus-4.8",
    shutdown_metrics=None,
    assistant_outputs=None,
):
    """Write a synthetic ``session-state/<sid>/events.jsonl``."""
    d = root / sid
    d.mkdir(parents=True)
    lines = [
        {
            "type": "session.start",
            "data": {"sessionId": sid, "producer": producer, "copilotVersion": "1.0.54"},
            "timestamp": "2026-05-31T10:00:00.000Z",
        }
    ]
    for i, out in enumerate(assistant_outputs or []):
        lines.append(
            {
                "type": "assistant.message",
                "data": {"model": model, "outputTokens": out},
                "timestamp": f"2026-05-31T10:0{i}:30.000Z",
            }
        )
    if shutdown_metrics is not None:
        lines.append(
            {
                "type": "session.shutdown",
                "data": {"currentModel": model, "modelMetrics": shutdown_metrics},
                "timestamp": "2026-05-31T10:30:00.000Z",
            }
        )
    (d / "events.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    return d


def _usage(inp=0, out=0, cr=0, cw=0):
    return {
        "usage": {
            "inputTokens": inp,
            "outputTokens": out,
            "cacheReadTokens": cr,
            "cacheWriteTokens": cw,
            "reasoningTokens": 0,
        }
    }


def test_shutdown_rollup_emits_exact_event(tmp_path):
    _write_session(
        tmp_path,
        "sid-done",
        producer="copilot-agent",
        shutdown_metrics={"claude-opus-4.8": _usage(inp=1000, out=200, cr=5000, cw=10)},
    )
    events = list(iter_copilot_session_state_events(tmp_path))
    assert len(events) == 1
    ev = events[0]
    assert ev.is_estimated is False
    assert ev.session_id == "sid-done"
    assert ev.input_tokens == 1000
    assert ev.output_tokens == 200
    assert ev.cache_read_tokens == 5000
    assert ev.cache_write_tokens == 10
    assert ev.host_app == "Copilot CLI"  # producer "copilot-agent"
    assert ev.model == "claude-opus-4.8"


def test_multi_model_shutdown_emits_one_event_per_nonzero_model(tmp_path):
    _write_session(
        tmp_path,
        "sid-multi",
        shutdown_metrics={
            "claude-opus-4.8": _usage(inp=10, out=20, cr=30),
            "gpt-5.5": _usage(inp=1, out=2),
            "gpt-5.4-mini": _usage(),  # all-zero -> skipped
        },
    )
    events = list(iter_copilot_session_state_events(tmp_path))
    models = sorted(ev.raw_model for ev in events)
    assert models == ["claude-opus-4.8", "gpt-5.5"]
    assert all(ev.is_estimated is False for ev in events)
    assert all(ev.host_app == "Agency" for ev in events)  # producer "agency"


def test_active_session_emits_output_only_estimates(tmp_path):
    _write_session(
        tmp_path,
        "sid-active",
        assistant_outputs=[50, 75],
        shutdown_metrics=None,
    )
    events = list(iter_copilot_session_state_events(tmp_path))
    assert len(events) == 2
    assert all(ev.is_estimated is True for ev in events)
    assert [ev.output_tokens for ev in events] == [50, 75]
    assert all(ev.input_tokens == 0 and ev.cache_read_tokens == 0 for ev in events)


def test_skip_session_ids_drops_whole_session(tmp_path):
    _write_session(
        tmp_path, "keep", shutdown_metrics={"m": _usage(inp=1, out=1)}
    )
    _write_session(
        tmp_path, "drop", shutdown_metrics={"m": _usage(inp=9, out=9)}
    )
    events = list(
        iter_copilot_session_state_events(tmp_path, skip_session_ids={"drop"})
    )
    assert {ev.session_id for ev in events} == {"keep"}


def test_cache_reparses_only_on_change(tmp_path):
    d = _write_session(
        tmp_path, "sid-c", shutdown_metrics={"m": _usage(inp=1, out=1)}
    )
    cache: dict = {}
    first = list(iter_copilot_session_state_events(tmp_path, cache=cache))
    assert len(first) == 1
    assert "sid-c" in cache
    # Unchanged -> served from cache (same object identity in entry).
    cached_events = cache["sid-c"][2]
    list(iter_copilot_session_state_events(tmp_path, cache=cache))
    assert cache["sid-c"][2] is cached_events


def test_is_session_state_event_detects_source(tmp_path):
    sstate = tmp_path / "session-state"
    _write_session(sstate, "sid", shutdown_metrics={"m": _usage(inp=1, out=1)})
    ev = list(iter_copilot_session_state_events(sstate))[0]
    assert is_session_state_event(ev) is True


def test_iter_all_events_dedups_shared_session_id(tmp_path, monkeypatch):
    """A session present in BOTH a process log and the session-state
    store (they share session_id) must be counted once: the process-log
    source wins and the session-state rollup is suppressed."""
    copilot_dir = tmp_path / "logs"
    copilot_dir.mkdir()
    (copilot_dir / "process-1.log").write_text(
        "2026-05-31T10:00:00.000Z [INFO] [Telemetry] cli.telemetry:\n"
        + json.dumps(
            {
                "kind": "assistant_usage",
                "session_id": "shared",
                "client": {"client_type": "cli-server"},
                "properties": {"model": "gpt-5.5"},
                "metrics": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    sstate = tmp_path / "session-state"
    _write_session(
        sstate, "shared", shutdown_metrics={"gpt-5.5": _usage(inp=999, out=999)}
    )
    _write_session(
        sstate, "only-sstate", shutdown_metrics={"gpt-5.5": _usage(inp=7, out=3)}
    )

    from tokentray.parsers import copilot_logs as cl, agency_events as ae
    from tokentray.parsers import copilot_session_state as ss
    monkeypatch.setattr(cl, "LOG_DIR", copilot_dir)
    monkeypatch.setattr(ae, "LOG_ROOT", tmp_path / "no-agency")
    monkeypatch.setattr(ss, "LOG_DIR", sstate)

    events = list(iter_all_events())
    by_sid: dict[str, list] = {}
    for ev in events:
        by_sid.setdefault(ev.session_id, []).append(ev)

    # "shared" counted once, from the process log (input 100), NOT the
    # session-state rollup (input 999).
    assert len(by_sid["shared"]) == 1
    assert by_sid["shared"][0].input_tokens == 100
    # Session that only exists in session-state still flows through.
    assert len(by_sid["only-sstate"]) == 1
    assert by_sid["only-sstate"][0].input_tokens == 7


def test_iter_all_events_extra_skip_applies_to_session_state(tmp_path, monkeypatch):
    sstate = tmp_path / "session-state"
    _write_session(
        sstate, "already-counted", shutdown_metrics={"m": _usage(inp=5, out=5)}
    )
    _write_session(sstate, "fresh", shutdown_metrics={"m": _usage(inp=6, out=6)})

    from tokentray.parsers import copilot_logs as cl, agency_events as ae
    from tokentray.parsers import copilot_session_state as ss
    monkeypatch.setattr(cl, "LOG_DIR", tmp_path / "no-logs")
    monkeypatch.setattr(ae, "LOG_ROOT", tmp_path / "no-agency")
    monkeypatch.setattr(ss, "LOG_DIR", sstate)

    events = list(
        iter_all_events(extra_skip_session_ids={"already-counted"})
    )
    assert {ev.session_id for ev in events} == {"fresh"}
