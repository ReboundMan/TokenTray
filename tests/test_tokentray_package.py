"""Phase 1 contract tests for the ``tokentray`` package public API.

These tests verify the cross-repo surface that AgencyUsageReport
(and any other consumer) will pin against:

* ``import tokentray`` works without a GUI stack installed.
* ``tokentray.__version__`` is exposed and is a non-empty string.
* ``from tokentray.parsers import iter_all_events`` resolves and yields
  :class:`UsageEvent` instances from a synthetic log directory.
* ``UsageEvent`` has the new optional provenance fields with safe
  defaults so old call sites (e.g. ``history_store._event_id`` and
  test factories that build ``UsageEvent(timestamp=..., session_id=...)``
  positionally) keep working.
* The Copilot CLI parser populates ``host_app`` as ``"Copilot CLI"``
  regardless of ``client_type`` and stamps ``source_path``.
* The legacy ``usage_core`` shim still re-exports the same names.
* No PyQt import is reachable from ``import tokentray`` or
  ``import tokentray.parsers``.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import tokentray  # noqa: E402
from tokentray import UsageEvent, iter_all_events, iter_usage_events  # noqa: E402
from tokentray.parsers import UsageEvent as ParsersUsageEvent  # noqa: E402
from tokentray.parsers.copilot_logs import _classify_host  # noqa: E402
from tokentray.usage_buckets import (  # noqa: E402
    DayBucket,
    HourBucket,
    bucket_by_day,
    bucket_by_hour,
    fmt_tokens,
)


def _write_assistant_usage_log(
    path: Path,
    *,
    session_id: str,
    client_type: str | None = "cli-server",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 25,
    cache_write_tokens: int = 10,
    model: str | None = "claude-opus-4.6",
    timestamp: str = "2026-05-18T15:39:28.130Z",
) -> None:
    """Write a realistic CLI telemetry ``assistant_usage`` block.

    Real CLI logs (verified Nov 2026) put ``client_type`` at
    ``ev["client"]["client_type"]`` ON THE SAME EVENT, and ``model``
    at ``ev["properties"]["model"]``. This fixture matches that
    layout so the test exercises the same code paths a production
    log walks through."""
    import json
    block = {
        "kind": "assistant_usage",
        "session_id": session_id,
        "properties": {} if model is None else {"model": model},
        "metrics": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        },
    }
    if client_type is not None:
        block["client"] = {"client_type": client_type}
    path.write_text(
        f"{timestamp} [INFO] [Telemetry] cli.telemetry:\n"
        f"{json.dumps(block, indent=2)}\n",
        encoding="utf-8",
    )


def test_version_is_exposed():
    assert isinstance(tokentray.__version__, str)
    assert tokentray.__version__.strip()
    parts = tokentray.__version__.split(".")
    assert len(parts) >= 2
    assert parts[0].isdigit()


def test_usage_event_new_fields_have_safe_defaults():
    """Positional ``UsageEvent(timestamp=..., session_id=...)`` must
    still work and the new provenance fields must default to safe
    sentinels so ``history_store._event_id`` and test factories that
    pre-date Phase 1 are unaffected."""
    ev = UsageEvent(
        timestamp=datetime(2026, 5, 18, 15, 39, 28, tzinfo=timezone.utc),
        session_id="sess-a",
    )
    assert ev.host_app is None
    assert ev.model is None
    assert ev.raw_model is None
    assert ev.source_path is None
    assert ev.is_estimated is False
    assert ev.total == 0


def test_usage_event_identity_across_subpackages():
    """``tokentray.UsageEvent`` and ``tokentray.parsers.UsageEvent`` must
    be the same class; otherwise isinstance checks in downstream code
    would silently fail."""
    assert UsageEvent is ParsersUsageEvent


def test_legacy_usage_core_shim_still_works():
    """Pre-Phase-1 import paths (``from usage_core import UsageEvent``)
    must continue to resolve to the same class."""
    import usage_core
    assert usage_core.UsageEvent is UsageEvent
    assert usage_core.iter_usage_events is iter_usage_events
    assert usage_core.DayBucket is DayBucket
    assert usage_core.HourBucket is HourBucket
    assert usage_core.bucket_by_day is bucket_by_day
    assert usage_core.bucket_by_hour is bucket_by_hour
    assert usage_core.fmt_tokens is fmt_tokens
    assert hasattr(usage_core, "fetch_active_session")
    assert hasattr(usage_core, "SESSION_STORE")


def test_iter_all_events_yields_usage_events_from_log_dir(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_assistant_usage_log(
        log_dir / "process-001.log",
        session_id="s1",
        client_type="cli-server",
    )
    _write_assistant_usage_log(
        log_dir / "process-002.log",
        session_id="s2",
        client_type="cli-interactive",
        input_tokens=200,
    )
    # iter_all_events must accept the same log_dir indirection by
    # going through copilot_logs.iter_usage_events under the hood. For
    # Phase 1 we test that path directly with an explicit dir, since
    # the public ``iter_all_events`` defaults to ``~/.copilot/logs/``.
    events = list(iter_usage_events(log_dir=log_dir))
    assert len(events) == 2
    by_session = {ev.session_id: ev for ev in events}
    assert by_session["s1"].host_app == "Copilot CLI"
    assert by_session["s2"].host_app == "Copilot CLI"
    assert by_session["s1"].raw_model == "claude-opus-4.6"
    assert by_session["s1"].model == "claude-opus-4.6"
    assert by_session["s1"].source_path == str(log_dir / "process-001.log")
    assert by_session["s2"].input_tokens == 200


def test_copilot_parser_reads_legacy_client_type_locations(tmp_path):
    """Older test fixtures and possibly older CLI versions surfaced
    ``client_type`` at the top level or under ``properties`` rather
    than under ``client``. The parser must still extract the right
    host attribution from those layouts via its file-local
    state-tracking fallback."""
    import json
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Leading context block uses the legacy top-level shape; the
    # assistant_usage block that follows has no client_type of its own.
    (log_dir / "process-legacy.log").write_text(
        "2026-05-18T15:39:28.000Z [INFO] [Telemetry] cli.telemetry:\n"
        + json.dumps({"kind": "session_usage_info", "client_type": "cli-interactive"}, indent=2)
        + "\n"
        + "2026-05-18T15:39:29.000Z [INFO] [Telemetry] cli.telemetry:\n"
        + json.dumps({
            "kind": "assistant_usage",
            "session_id": "legacy-1",
            "properties": {"model": "gpt-5.5"},
            "metrics": {"input_tokens": 1, "output_tokens": 2,
                        "cache_read_tokens": 0, "cache_write_tokens": 0},
        }, indent=2)
        + "\n",
        encoding="utf-8",
    )
    events = list(iter_usage_events(log_dir=log_dir))
    assert len(events) == 1
    assert events[0].host_app == "Copilot CLI"
    assert events[0].model == "gpt-5.5"


def test_iter_all_events_default_path_does_not_raise_when_dir_missing(monkeypatch, tmp_path):
    """If none of ``~/.copilot/logs/``, ``~/.agency/logs/`` or
    ``~/.copilot/session-state/`` exist, ``iter_all_events()`` must
    silently yield nothing, not raise. This protects AgencyUsageReport
    when it runs on a machine that has never had the Copilot CLI or
    Agency installed."""
    nowhere_cli = tmp_path / "does-not-exist-cli"
    nowhere_agency = tmp_path / "does-not-exist-agency"
    nowhere_sstate = tmp_path / "does-not-exist-sstate"
    from tokentray.parsers import copilot_logs as cl
    from tokentray.parsers import agency_events as ae
    from tokentray.parsers import copilot_session_state as ss
    monkeypatch.setattr(cl, "LOG_DIR", nowhere_cli)
    monkeypatch.setattr(ae, "LOG_ROOT", nowhere_agency)
    monkeypatch.setattr(ss, "LOG_DIR", nowhere_sstate)
    events = list(iter_all_events())
    assert events == []


def test_classify_host_matrix():
    # The cli-server / cli-interactive distinction discriminates CLI
    # versions, not host apps - Clawpilot is an Electron desktop app
    # that does not write to ~/.copilot/logs/. Everything in
    # ~/.copilot/logs/ is therefore "Copilot CLI".
    assert _classify_host("cli-server") == "Copilot CLI"
    assert _classify_host("cli-interactive") == "Copilot CLI"
    assert _classify_host(None) == "Copilot CLI"
    assert _classify_host("future-variant") == "Copilot CLI"


def test_tokentray_import_does_not_require_pyqt():
    """The ``tokentray`` package must remain GUI-free so that
    AgencyUsageReport (and other headless consumers) can install it
    without PyQt6. We walk the public tokentray modules and AST-check
    that none of them actually ``import PyQt*`` (string search is too
    weak: our own docstrings mention PyQt6 to explain *why* we don't
    import it)."""
    import ast
    import importlib
    for name in (
        "tokentray",
        "tokentray.parsers",
        "tokentray.parsers._common",
        "tokentray.parsers.copilot_logs",
        "tokentray.usage_buckets",
    ):
        mod = importlib.import_module(name)
        src = Path(mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src, filename=mod.__file__)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("PyQt"), (
                        f"{name} must not import {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("PyQt"), (
                    f"{name} must not import from {node.module}"
                )


def test_bucket_helpers_still_work_via_package_path():
    """Smoke test: ``tokentray.usage_buckets`` produces the same bucket
    output as the legacy ``usage_core`` path."""
    tz = timezone.utc
    today = datetime.now(tz=tz).date()
    ev = UsageEvent(
        timestamp=datetime.now(tz=tz),
        session_id="s",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=5,
    )
    days = bucket_by_day([ev], tz=tz, days=3)
    assert len(days) == 3
    assert days[-1].day == today
    assert days[-1].total == 35
    assert days[-1].events == 1
    hours = bucket_by_hour([ev], tz=tz)
    assert len(hours) == 24
    assert sum(h.events for h in hours) == 1
    assert fmt_tokens(35) == "35"
    assert fmt_tokens(1234) == "1.2k"
    assert fmt_tokens(12345) == "12k"
