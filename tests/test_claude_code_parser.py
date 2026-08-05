"""Tests for the Claude Code transcript parser (spec 10017).

Guards the contracts the spec makes binding:

- Only ``message.usage``, ``message.model``, and line metadata are
  extracted; message CONTENT never reaches a UsageEvent (content-blind).
- Malformed lines are skipped and counted, never a crash.
- The offset cache resumes appended files from the stored byte offset
  and falls back to a full re-parse when a file shrinks.
- An absent projects directory yields nothing.
- Events chain into ``iter_all_events`` with host_app "Claude Code".
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tokentray.parsers import iter_all_events  # noqa: E402
from tokentray.parsers.claude_code import (  # noqa: E402
    iter_claude_code_events,
    skipped_line_count,
)


def _assistant_line(sid: str, ts: str, model: str = "claude-opus-4-8",
                     it: int = 100, ot: int = 20, cr: int = 500, cw: int = 30,
                     content: str = "PRIVATE CONVERSATION CONTENT") -> str:
    return json.dumps({
        "type": "assistant",
        "sessionId": sid,
        "timestamp": ts,
        "message": {
            "id": "msg_x",
            "model": model,
            "role": "assistant",
            "content": [{"type": "text", "text": content}],
            "usage": {
                "input_tokens": it,
                "output_tokens": ot,
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw,
            },
        },
    })


def _user_line(sid: str, ts: str) -> str:
    return json.dumps({
        "type": "user", "sessionId": sid, "timestamp": ts,
        "message": {"role": "user", "content": "user text"},
    })


def _write(path: Path, *lines: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


@pytest.fixture()
def projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


def test_extracts_usage_and_skips_non_assistant_lines(projects: Path):
    f = projects / "proj-a" / "sess-1.jsonl"
    _write(f,
           _user_line("sess-1", "2026-08-04T10:00:00.000Z"),
           _assistant_line("sess-1", "2026-08-04T10:00:05.000Z"))
    events = list(iter_claude_code_events(projects))
    assert len(events) == 1
    ev = events[0]
    assert ev.host_app == "Claude Code"
    assert ev.session_id == "sess-1"
    assert (ev.input_tokens, ev.output_tokens) == (100, 20)
    assert (ev.cache_read_tokens, ev.cache_write_tokens) == (500, 30)
    assert ev.raw_model == "claude-opus-4-8"
    assert ev.source_path == str(f)


def test_content_blind(projects: Path):
    """The spec's hard rule: conversation content never reaches an event."""
    secret = "THE SECRET CONTENT STRING"
    _write(projects / "p" / "s.jsonl",
           _assistant_line("s", "2026-08-04T10:00:00.000Z", content=secret))
    events = list(iter_claude_code_events(projects))
    assert len(events) == 1
    for value in vars(events[0]).values():
        assert secret not in str(value)


def test_malformed_lines_skip_and_count(projects: Path):
    f = projects / "p" / "s.jsonl"
    _write(f,
           "{not json at all",
           _assistant_line("s", "2026-08-04T10:00:00.000Z"),
           json.dumps({"type": "assistant", "timestamp": "2026-08-04T10:00:01.000Z",
                        "message": "not-a-dict"}))
    cache: dict = {}
    events = list(iter_claude_code_events(projects, cache=cache))
    assert len(events) == 1
    assert skipped_line_count(cache) == 2


def test_offset_resume_parses_only_appended_bytes(projects: Path):
    f = projects / "p" / "s.jsonl"
    _write(f, _assistant_line("s", "2026-08-04T10:00:00.000Z", it=1))
    cache: dict = {}
    first = list(iter_claude_code_events(projects, cache=cache))
    assert len(first) == 1
    key = next(iter(cache))
    size0, _, offset0, _, _ = cache[key]
    assert offset0 == size0  # offset advanced to EOF

    # Append a second assistant line; ensure mtime_ns changes even on
    # coarse filesystem clocks.
    with open(f, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(_assistant_line("s", "2026-08-04T11:00:00.000Z", it=2) + "\n")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    second = list(iter_claude_code_events(projects, cache=cache))
    assert [e.input_tokens for e in second] == [1, 2]
    # Third call with nothing changed: served from cache, same events.
    third = list(iter_claude_code_events(projects, cache=cache))
    assert [e.input_tokens for e in third] == [1, 2]


def test_shrunk_file_reparses_from_scratch(projects: Path):
    f = projects / "p" / "s.jsonl"
    _write(f,
           _assistant_line("s", "2026-08-04T10:00:00.000Z", it=1),
           _assistant_line("s", "2026-08-04T10:01:00.000Z", it=2))
    cache: dict = {}
    assert len(list(iter_claude_code_events(projects, cache=cache))) == 2

    _write(f, _assistant_line("s", "2026-08-04T12:00:00.000Z", it=9))
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    events = list(iter_claude_code_events(projects, cache=cache))
    assert [e.input_tokens for e in events] == [9]


def test_absent_directory_yields_nothing(tmp_path: Path):
    assert list(iter_claude_code_events(tmp_path / "nope")) == []


def test_deleted_file_evicted_from_cache(projects: Path):
    f = projects / "p" / "s.jsonl"
    _write(f, _assistant_line("s", "2026-08-04T10:00:00.000Z"))
    cache: dict = {}
    list(iter_claude_code_events(projects, cache=cache))
    assert cache
    f.unlink()
    list(iter_claude_code_events(projects, cache=cache))
    assert not cache


def test_chains_into_iter_all_events(projects: Path, tmp_path: Path, monkeypatch):
    _write(projects / "p" / "s.jsonl",
           _assistant_line("s", "2026-08-04T10:00:00.000Z"))
    import tokentray.parsers.claude_code as cc
    monkeypatch.setattr(cc, "PROJECTS_DIR", projects)
    # Point the other sources at empty roots so only Claude events flow.
    import tokentray.parsers.copilot_logs as cl
    import tokentray.parsers.agency_events as ae
    import tokentray.parsers.copilot_session_state as ss
    monkeypatch.setattr(cl, "LOG_DIR", tmp_path / "no-copilot")
    monkeypatch.setattr(ae, "LOG_ROOT", tmp_path / "no-agency")
    monkeypatch.setattr(ss, "LOG_DIR", tmp_path / "no-session-state")
    events = list(iter_all_events())
    assert len(events) == 1
    assert events[0].host_app == "Claude Code"
