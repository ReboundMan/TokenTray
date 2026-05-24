"""Tests for ``usage_core.iter_usage_events`` per-file cache behavior.

The cache is the Phase 0 perf fix that takes popup-open latency from
~6 s to <10 ms on a warm tray. These tests guard the cache contract:

- An unchanged file is reused from cache and NOT re-parsed.
- A file whose ``(size, mtime_ns)`` changed IS re-parsed.
- A file that disappears is evicted from the cache.
- Without a cache argument the function behaves exactly like before.
- A repeated warm scan stays well under a coarse sanity bound.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import usage_core  # noqa: E402
from usage_core import UsageEvent, iter_usage_events  # noqa: E402


# Single telemetry event in the exact shape the Copilot CLI writes.
_TELEMETRY_TEMPLATE = """2026-05-24T12:00:{sec:02d}.000Z [INFO] [Telemetry] cli.telemetry:
{{
  "kind": "assistant_usage",
  "session_id": "{sid}",
  "metrics": {{
    "input_tokens": {it},
    "output_tokens": {ot},
    "cache_read_tokens": {cr},
    "cache_write_tokens": {cw}
  }}
}}
"""


def _write_log(p: Path, *events: tuple[str, int, int, int, int]) -> None:
    """Write a synthetic process-*.log with the given (sid, it, ot, cr, cw) tuples."""
    blocks = [
        _TELEMETRY_TEMPLATE.format(
            sec=i, sid=sid, it=it, ot=ot, cr=cr, cw=cw,
        )
        for i, (sid, it, ot, cr, cw) in enumerate(events)
    ]
    p.write_text("\n".join(blocks), encoding="utf-8")


def test_cache_skips_unchanged_files(tmp_path, monkeypatch):
    log = tmp_path / "process-abc.log"
    _write_log(log, ("s1", 10, 20, 30, 40))

    cache: dict = {}
    events1 = list(iter_usage_events(tmp_path, cache=cache))
    assert len(events1) == 1
    assert events1[0].input_tokens == 10
    assert cache.keys() == {"process-abc.log"}

    # Patch _parse_log_file to fail if it's called -- the second scan
    # must hit the cache and skip parsing.
    def _boom(*_a, **_kw):
        raise AssertionError("_parse_log_file should NOT be called on a cache hit")

    monkeypatch.setattr(usage_core, "_parse_log_file", _boom)
    events2 = list(iter_usage_events(tmp_path, cache=cache))
    # Same events served from cache; parser was never invoked.
    assert len(events2) == 1
    assert events2[0].input_tokens == 10


def test_cache_invalidates_on_size_change(tmp_path):
    log = tmp_path / "process-abc.log"
    _write_log(log, ("s1", 10, 20, 30, 40))

    cache: dict = {}
    list(iter_usage_events(tmp_path, cache=cache))
    assert cache["process-abc.log"][2][0].input_tokens == 10

    # Append a new event; size + mtime both change -> cache must invalidate.
    _write_log(log, ("s1", 10, 20, 30, 40), ("s1", 99, 1, 2, 3))
    events = list(iter_usage_events(tmp_path, cache=cache))
    assert len(events) == 2
    assert events[1].input_tokens == 99


def test_cache_invalidates_on_mtime_change_without_size_change(tmp_path):
    log = tmp_path / "process-abc.log"
    _write_log(log, ("s1", 10, 20, 30, 40))
    cache: dict = {}
    list(iter_usage_events(tmp_path, cache=cache))
    original_count = len(cache["process-abc.log"][2])

    # Overwrite with the same byte length but different content; size
    # stays equal, so the cache must rely on mtime to invalidate.
    time.sleep(0.05)  # ensure st_mtime_ns advances on coarse-grained FS
    same_len_other_content = log.read_text(encoding="utf-8")
    # Mutate ONE digit so byte count stays identical:
    mutated = same_len_other_content.replace('"input_tokens": 10,', '"input_tokens": 11,')
    assert len(mutated) == len(same_len_other_content), "test setup: lengths must match"
    log.write_text(mutated, encoding="utf-8")

    events = list(iter_usage_events(tmp_path, cache=cache))
    assert len(events) == original_count
    assert events[0].input_tokens == 11  # re-parsed, not from cache


def test_cache_evicts_deleted_files(tmp_path):
    a = tmp_path / "process-a.log"
    b = tmp_path / "process-b.log"
    _write_log(a, ("s1", 1, 1, 1, 1))
    _write_log(b, ("s2", 2, 2, 2, 2))
    cache: dict = {}
    list(iter_usage_events(tmp_path, cache=cache))
    assert set(cache.keys()) == {"process-a.log", "process-b.log"}

    b.unlink()
    list(iter_usage_events(tmp_path, cache=cache))
    assert set(cache.keys()) == {"process-a.log"}


def test_without_cache_still_works(tmp_path):
    """Backward-compat: legacy callers passing no cache get the original behavior."""
    log = tmp_path / "process-abc.log"
    _write_log(log, ("s1", 10, 20, 30, 40))
    events = list(iter_usage_events(tmp_path))
    assert len(events) == 1
    assert events[0].input_tokens == 10
    assert events[0].cache_read_tokens == 30


def test_warm_scan_is_fast(tmp_path):
    """Sanity bound: a warm cache scan over many files must stay tiny.

    Phase 0 acceptance is <250 ms on a warm cache for the popup-open
    path. This test uses 50 small files (worst case is stat overhead, not
    parse cost, on a warm cache) and asserts well under that bound. The
    bound is loose on purpose -- the test must not flake on slow CI.
    """
    for i in range(50):
        _write_log(tmp_path / f"process-{i:04d}.log", ("s", i, i, i, i))

    cache: dict = {}
    # Cold warm-up; bound only the warm scan.
    list(iter_usage_events(tmp_path, cache=cache))

    t0 = time.perf_counter()
    for _ in range(5):
        list(iter_usage_events(tmp_path, cache=cache))
    elapsed_ms = (time.perf_counter() - t0) * 1000 / 5

    assert elapsed_ms < 200, (
        f"warm scan averaged {elapsed_ms:.1f} ms over 5 iterations "
        f"with 50 files -- cache may be broken"
    )
