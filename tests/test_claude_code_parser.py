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

import itertools
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

# Each generated line gets unique requestId/message.id by default, matching
# the live format (both ids present on every usage line). Lines of the SAME
# API response share ids; tests exercising the per-request dedup pass
# req_id/msg_id explicitly.
_uniq = itertools.count()


def _assistant_line(sid: str, ts: str, model: str = "claude-opus-4-8",
                     it: int = 100, ot: int = 20, cr: int = 500, cw: int = 30,
                     content: str = "PRIVATE CONVERSATION CONTENT",
                     req_id: str | None = None,
                     msg_id: str | None = None) -> str:
    n = next(_uniq)
    return json.dumps({
        "type": "assistant",
        "sessionId": sid,
        "timestamp": ts,
        "requestId": req_id if req_id is not None else f"req_{n}",
        "message": {
            "id": msg_id if msg_id is not None else f"msg_{n}",
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
    size0, _, offset0, _, _, _ = cache[key]
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


def test_subagent_transcripts_are_parsed(projects: Path):
    """Round-2 Rookie High: subagent transcripts live one level deeper
    (<slug>/<session-id>/subagents/agent-*.jsonl) and were missed by the
    original single glob. On the machine that caught it that was 250 of
    283 files."""
    _write(projects / "p" / "main-sess.jsonl",
           _assistant_line("main-sess", "2026-08-04T10:00:00.000Z", it=1))
    _write(projects / "p" / "main-sess" / "subagents" / "agent-abc123.jsonl",
           _assistant_line("sub-sess", "2026-08-04T10:05:00.000Z", it=2))
    events = list(iter_claude_code_events(projects))
    assert sorted(e.input_tokens for e in events) == [1, 2]
    assert {e.session_id for e in events} == {"main-sess", "sub-sess"}


def test_torn_final_line_deferred_until_complete(projects: Path):
    """Round-2 Hawk H-1: a live writer can leave a torn final line. The
    parser must not consume it (no half-line skip count, no lost data);
    it parses whole once the newline lands."""
    f = projects / "p" / "s.jsonl"
    full = _assistant_line("s", "2026-08-04T10:00:00.000Z", it=1)
    torn = _assistant_line("s", "2026-08-04T10:01:00.000Z", it=2)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(full + "\n" + torn[: len(torn) // 2], encoding="utf-8", newline="\n")
    cache: dict = {}
    events = list(iter_claude_code_events(projects, cache=cache))
    assert [e.input_tokens for e in events] == [1]
    assert skipped_line_count(cache) == 0  # torn tail is deferred, not "malformed"

    # Writer finishes the line; the resume picks it up whole.
    with open(f, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(torn[len(torn) // 2:] + "\n")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    events = list(iter_claude_code_events(projects, cache=cache))
    assert [e.input_tokens for e in events] == [1, 2]
    assert skipped_line_count(cache) == 0


def test_nonfinite_token_counts_do_not_crash(projects: Path):
    """Round-2 Hawk M-1: NaN/Infinity are legal to Python's json loader
    and must degrade to 0, never crash the refresh."""
    line = _assistant_line("s", "2026-08-04T10:00:00.000Z")
    obj = json.loads(line)
    obj["message"]["usage"]["input_tokens"] = float("nan")
    obj["message"]["usage"]["output_tokens"] = float("inf")
    # json.dumps emits the non-standard NaN/Infinity tokens by default,
    # and json.loads accepts them back: exactly the hole Hawk flagged.
    _write(projects / "p" / "s.jsonl", json.dumps(obj))
    events = list(iter_claude_code_events(projects))
    assert len(events) == 1
    assert events[0].input_tokens == 0
    assert events[0].output_tokens == 0
    assert events[0].cache_read_tokens == 500  # sane fields still counted


def test_synthetic_assistant_lines_filtered(projects: Path):
    """Round-2 Rookie: '<synthetic>' assistant entries are system-
    generated zero-usage lines; they must not become events (inflating
    the Events stat) nor count as skipped."""
    _write(projects / "p" / "s.jsonl",
           _assistant_line("s", "2026-08-04T10:00:00.000Z",
                           model="<synthetic>", it=0, ot=0, cr=0, cw=0),
           _assistant_line("s", "2026-08-04T10:01:00.000Z", it=7))
    cache: dict = {}
    events = list(iter_claude_code_events(projects, cache=cache))
    assert [e.input_tokens for e in events] == [7]
    assert skipped_line_count(cache) == 0


def test_dict_valued_metadata_never_stringified(projects: Path):
    """Round-2 Hawk M-2: a dict smuggled into sessionId/model must not be
    str()-coerced into persistent metadata fields (content smuggling)."""
    line = _assistant_line("s", "2026-08-04T10:00:00.000Z")
    obj = json.loads(line)
    obj["sessionId"] = {"smuggled": "CONTENT"}
    obj["message"]["model"] = {"also": "CONTENT"}
    _write(projects / "p" / "sess-file.jsonl", json.dumps(obj))
    events = list(iter_claude_code_events(projects))
    assert len(events) == 1
    ev = events[0]
    assert ev.session_id == "sess-file"  # filename-stem fallback
    assert ev.model is None and ev.raw_model is None
    for value in vars(ev).values():
        assert "CONTENT" not in str(value)


def test_tool_split_line_and_tooltip_pure_functions(projects: Path):
    """The popup split line/tooltip are PyQt-free and testable
    (round-2 Rookie). Uses the parser's own events as input."""
    from tokentray.tool_split import tool_split_line, tool_split_tooltip
    from datetime import datetime as _dt
    _write(projects / "p" / "s.jsonl",
           _assistant_line("s", "2026-08-04T10:00:00.000Z", it=1000, ot=0, cr=0, cw=0))
    cache: dict = {}
    events = list(iter_claude_code_events(projects, cache=cache))
    fake_now = _dt.fromisoformat("2026-08-04T12:00:00+00:00")

    line = tool_split_line(events, claude_dir_exists=True, now=fake_now)
    assert line.startswith("Today by tool:")
    assert "Claude Code" in line and "no data found" not in line

    empty_line = tool_split_line([], claude_dir_exists=False, now=fake_now)
    assert "Claude Code: no data found" in empty_line

    tip = tool_split_tooltip(cache, events, now=fake_now)
    assert "1 transcript file(s) tracked" in tip
    assert "Today by model:" in tip


def test_multi_block_response_counted_once(projects: Path):
    """v0.7.1 double-count fix: Claude Code writes one line per CONTENT
    BLOCK and repeats the full usage object (same requestId/message.id)
    on every line of the response. The measured inflation was 2.1x."""
    _write(projects / "p" / "s.jsonl",
           _assistant_line("s", "2026-08-06T10:00:00.000Z", it=3, ot=50,
                           cr=1000, cw=10, req_id="req_A", msg_id="msg_A"),
           _assistant_line("s", "2026-08-06T10:00:01.000Z", it=3, ot=50,
                           cr=1000, cw=10, req_id="req_A", msg_id="msg_A"),
           _assistant_line("s", "2026-08-06T10:00:02.000Z", it=3, ot=50,
                           cr=1000, cw=10, req_id="req_A", msg_id="msg_A"),
           _assistant_line("s", "2026-08-06T10:01:00.000Z", it=5, ot=7,
                           cr=2000, cw=20, req_id="req_B", msg_id="msg_B"))
    events = list(iter_claude_code_events(projects))
    assert len(events) == 2
    a = next(e for e in events if e.output_tokens == 50)
    assert (a.input_tokens, a.cache_read_tokens, a.cache_write_tokens) == (3, 1000, 10)


def test_varying_usage_last_line_wins(projects: Path):
    """Where usage differs across a request's lines the last line carries
    the final totals (verified over all 1,718 varying requests on the
    machine that caught the bug)."""
    _write(projects / "p" / "s.jsonl",
           _assistant_line("s", "2026-08-06T10:00:00.000Z", ot=10, cw=5,
                           req_id="req_A"),
           _assistant_line("s", "2026-08-06T10:00:03.000Z", ot=90, cw=40,
                           req_id="req_A"))
    events = list(iter_claude_code_events(projects))
    assert len(events) == 1
    assert events[0].output_tokens == 90
    assert events[0].cache_write_tokens == 40


def test_resume_straddling_request_boundary(projects: Path):
    """A request's lines can straddle the offset-resume boundary (first
    block flushed on one tick, the rest on the next). The appended line
    must REPLACE the cached event, not append a duplicate."""
    f = projects / "p" / "s.jsonl"
    _write(f, _assistant_line("s", "2026-08-06T10:00:00.000Z", ot=10,
                              req_id="req_A"))
    cache: dict = {}
    first = list(iter_claude_code_events(projects, cache=cache))
    assert [e.output_tokens for e in first] == [10]

    with open(f, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(_assistant_line("s", "2026-08-06T10:00:04.000Z", ot=95,
                                 req_id="req_A") + "\n")
        fh.write(_assistant_line("s", "2026-08-06T10:01:00.000Z", ot=5,
                                 req_id="req_B") + "\n")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    second = list(iter_claude_code_events(projects, cache=cache))
    assert sorted(e.output_tokens for e in second) == [5, 95]
    # Unchanged third pass: served from cache, still deduped.
    third = list(iter_claude_code_events(projects, cache=cache))
    assert sorted(e.output_tokens for e in third) == [5, 95]


def test_request_repeated_across_files_counted_once(projects: Path):
    """Cross-file guard: no observed layout repeats a request id across
    files, but if one ever does (e.g. a fork feature copying history into
    a new session file) the copy must not double the count."""
    _write(projects / "p" / "s1.jsonl",
           _assistant_line("s1", "2026-08-06T10:00:00.000Z", ot=10,
                           req_id="req_A", msg_id="msg_A"))
    _write(projects / "p" / "s2.jsonl",
           _assistant_line("s2", "2026-08-06T10:00:00.000Z", ot=10,
                           req_id="req_A", msg_id="msg_A"),
           _assistant_line("s2", "2026-08-06T11:00:00.000Z", ot=3,
                           req_id="req_B"))
    events = list(iter_claude_code_events(projects))
    assert sorted(e.output_tokens for e in events) == [3, 10]
    # Every fresh pass dedupes anew; nothing leaks between passes.
    cache: dict = {}
    assert len(list(iter_claude_code_events(projects, cache=cache))) == 2
    assert len(list(iter_claude_code_events(projects, cache=cache))) == 2


def test_missing_request_id_falls_back_to_message_id(projects: Path):
    """requestId has been present on every live line, but the dedup must
    degrade gracefully: message.id fallback, then per-line counting when
    neither id is usable."""
    with_mid = json.loads(_assistant_line("s", "2026-08-06T10:00:00.000Z",
                                          ot=10, msg_id="msg_A"))
    del with_mid["requestId"]
    with_mid_2 = json.loads(_assistant_line("s", "2026-08-06T10:00:01.000Z",
                                            ot=60, msg_id="msg_A"))
    del with_mid_2["requestId"]
    no_ids_1 = json.loads(_assistant_line("s", "2026-08-06T11:00:00.000Z", ot=1))
    del no_ids_1["requestId"]
    no_ids_1["message"]["id"] = 42  # not a string: unusable
    no_ids_2 = json.loads(_assistant_line("s", "2026-08-06T11:00:01.000Z", ot=2))
    del no_ids_2["requestId"]
    no_ids_2["message"]["id"] = 42
    _write(projects / "p" / "s.jsonl",
           json.dumps(with_mid), json.dumps(with_mid_2),
           json.dumps(no_ids_1), json.dumps(no_ids_2))
    events = list(iter_claude_code_events(projects))
    # msg_A pair collapses (last wins); the id-less pair stays per-line.
    assert sorted(e.output_tokens for e in events) == [1, 2, 60]


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
