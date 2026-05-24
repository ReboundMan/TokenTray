"""Parser for GitHub Copilot CLI / Clawpilot telemetry logs.

Reads ``~/.copilot/logs/process-*.log`` files, finds
``[Telemetry] cli.telemetry: { ... }`` blocks, filters on
``kind == "assistant_usage"``, and yields one :class:`UsageEvent` per
block.

The same log directory is shared by:

* the interactive ``copilot`` CLI binary (events report
  ``client_type == "cli-interactive"`` -> host_app ``"Copilot CLI"``)
* Anthropic's Clawpilot agent runtime (events report
  ``client_type == "cli-server"`` -> host_app ``"Clawpilot"``)

so a single pass over the directory captures both. Agency (which wraps
the Copilot CLI binary) ALSO writes here, but its real per-turn data
lives in ``~/.agency/logs/session_*/events.jsonl`` - a separate parser
will land in Phase 2 alongside a de-duplication gate so we do not
double-count Agency turns.

Parser semantics are intentionally tolerant: a malformed JSON block is
skipped, never aborting the surrounding file; a missing line-prefix
timestamp falls back to the event-internal ``timestamp`` field, then to
the file mtime.
"""
from __future__ import annotations

import json
import os
import re
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from tokentray.parsers._common import UsageEvent
from tokentray.parsers.model_normalize import normalize_model


LOG_DIR = Path(os.path.expanduser("~/.copilot/logs"))
TELEMETRY_MARKER = "[Telemetry] cli.telemetry:"

# Line prefix like:  2026-05-18T15:39:28.130Z [INFO] [Telemetry] ...
_LINE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")

# Per-file cache key shape: dict[str, tuple[int, int, list[UsageEvent]]]
# (filename -> (size_bytes, mtime_ns, parsed events)). The caller owns
# the dict so its lifetime matches the caller's process lifetime; this
# module never holds module-level mutable cache state.
LogCache = dict

_PROFILE = os.environ.get("TOKENTRAY_PROFILE") == "1"


def _classify_host(client_type: str | None) -> str:
    """Map a Copilot CLI ``client_type`` to a host_app label.

    ``client_type`` values observed in real telemetry:

    * ``"cli-interactive"`` -> the standalone ``copilot`` CLI
    * ``"cli-server"`` -> Clawpilot's agent runtime
    * anything else (missing, future variants) -> ``"Copilot CLI"`` as a
      safe default so the field is never ``None`` for this source.

    ``client_type`` is carried on every event under
    ``ev["client"]["client_type"]`` (verified in real telemetry as of
    Nov 2026). Older logs and the test fixture also support the
    top-level / ``properties.client_type`` forms; see
    :func:`_parse_log_file` for the precedence rules.

    Agency-wrapped CLI sessions write to a different log root
    (``~/.agency/logs/``) and are parsed by ``agency_events.py``, so
    no path-based override is needed inside this module - everything
    under ``~/.copilot/logs/`` is Clawpilot or Copilot CLI proper.
    """
    if client_type == "cli-server":
        return "Clawpilot"
    if client_type == "cli-interactive":
        return "Copilot CLI"
    return "Copilot CLI"


def _parse_ts(line: str) -> datetime | None:
    m = _LINE_TS_RE.match(line)
    if not m:
        return None
    s = m.group(1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_log_file(log_path: Path) -> list[UsageEvent]:
    """Parse a single ``*.log`` into a list of :class:`UsageEvent`.

    Pulled out of :func:`iter_usage_events` so the caller-provided cache
    can amortize the cost across refresh ticks. Parsing semantics
    (brace-balanced multi-line JSON, ``assistant_usage`` filter,
    ``session_id``-required, three-tier timestamp fallback) match the
    pre-Phase-1 implementation byte-for-byte; Phase 1 adds the
    ``host_app`` / ``raw_model`` / ``model`` / ``source_path``
    provenance fields on the way out.

    In real CLI telemetry (verified Nov 2026), ``client_type`` lives at
    ``ev["client"]["client_type"]`` on every event - including
    ``assistant_usage`` - not at the top level or under
    ``properties``. We still capture from any encountered carrier and
    track the most-recently-seen value as a safety net for malformed
    or pre-existing logs that lack the nested form.

    ``model`` lives under ``properties.model`` in the CLI telemetry
    schema, NOT at the top of the JSON block (which is where it sits
    in Agency's events.jsonl) - watch out when reading sample logs.
    """
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    src = str(log_path)
    out: list[UsageEvent] = []
    lines = text.splitlines()
    n = len(lines)
    i = 0
    current_client_type: str | None = None
    while i < n:
        if TELEMETRY_MARKER not in lines[i]:
            i += 1
            continue
        ts = _parse_ts(lines[i])
        # JSON object starts on the next non-empty line that begins with "{"
        j = i + 1
        while j < n and not lines[j].lstrip().startswith("{"):
            j += 1
        if j >= n:
            break
        # Brace-balanced read across multiple lines.
        depth = 0
        start = j
        in_str = False
        esc = False
        done = False
        while j < n:
            for ch in lines[j]:
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        done = True
                        break
            if done:
                break
            j += 1
        if not done:
            break
        block = "\n".join(lines[start : j + 1])
        try:
            ev = json.loads(block)
        except json.JSONDecodeError:
            i = j + 1
            continue
        # Capture client_type from this event's own `client` block when
        # present, falling back to legacy locations (top-level or under
        # `properties`). Whatever we find updates the file-local
        # tracker so any later event missing the marker still inherits
        # the right host attribution.
        client_obj = ev.get("client")
        if isinstance(client_obj, dict):
            ct = client_obj.get("client_type")
            if isinstance(ct, str) and ct:
                current_client_type = ct
        else:
            ct = ev.get("client_type")
            if isinstance(ct, str) and ct:
                current_client_type = ct
            elif isinstance(ev.get("properties"), dict):
                ct2 = ev["properties"].get("client_type")
                if isinstance(ct2, str) and ct2:
                    current_client_type = ct2
        if ev.get("kind") != "assistant_usage":
            i = j + 1
            continue
        sid = ev.get("session_id")
        properties = ev.get("properties") or {}
        metrics = ev.get("metrics") or {}
        if not sid:
            i = j + 1
            continue
        # Fall back to event-internal timestamp if line prefix lacked one.
        if ts is None:
            inner = ev.get("timestamp") or ev.get("created_at")
            if isinstance(inner, str):
                try:
                    ts = datetime.fromisoformat(inner.replace("Z", "+00:00"))
                except ValueError:
                    ts = None
        if ts is None:
            try:
                ts = datetime.fromtimestamp(log_path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                ts = datetime.now(tz=timezone.utc)
        host = _classify_host(current_client_type)
        raw_model = properties.get("model") or ev.get("model")
        if raw_model is not None and not isinstance(raw_model, str):
            raw_model = str(raw_model)
        out.append(
            UsageEvent(
                timestamp=ts,
                session_id=sid,
                input_tokens=int(metrics.get("input_tokens") or 0),
                output_tokens=int(metrics.get("output_tokens") or 0),
                cache_read_tokens=int(metrics.get("cache_read_tokens") or 0),
                cache_write_tokens=int(metrics.get("cache_write_tokens") or 0),
                host_app=host,
                model=normalize_model(raw_model),
                raw_model=raw_model,
                source_path=src,
            )
        )
        i = j + 1
    return out


def iter_usage_events(
    log_dir: Path | None = None,
    *,
    cache: LogCache | None = None,
) -> Iterable[UsageEvent]:
    """Yield UsageEvent records from every ``*.log`` under ``log_dir``.

    When *cache* is provided (a dict the caller owns) files whose
    ``(size, mtime_ns)`` is unchanged since the last call are not
    re-parsed - their previously-parsed events are yielded from the
    cache instead. Files no longer present on disk are evicted. This
    keeps repeated refresh ticks cheap once the cache is warm: a typical
    refresh re-parses only the single actively-being-written log.

    Set ``TOKENTRAY_PROFILE=1`` in the environment to log per-file parse
    timings to stderr; useful for diagnosing scan regressions without
    requiring callers to instrument the function.
    """
    log_dir = log_dir or LOG_DIR
    if not log_dir.exists():
        return

    seen: set[str] = set()
    for log_path in sorted(log_dir.glob("*.log")):
        try:
            st = log_path.stat()
        except OSError:
            continue
        key = log_path.name
        seen.add(key)
        if cache is not None:
            entry = cache.get(key)
            if entry is not None and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                if _PROFILE:
                    import sys as _sys
                    _sys.stderr.write(f"[tokentray.profile] cache-hit {key}\n")
                yield from entry[2]
                continue
        if _PROFILE:
            import sys as _sys
            t0 = _time.perf_counter()
            events = _parse_log_file(log_path)
            _sys.stderr.write(
                f"[tokentray.profile] parsed {key} "
                f"({st.st_size/1e6:.1f} MB) -> {len(events)} events "
                f"in {(_time.perf_counter()-t0)*1000:.0f} ms\n"
            )
        else:
            events = _parse_log_file(log_path)
        if cache is not None:
            cache[key] = (st.st_size, st.st_mtime_ns, events)
        yield from events

    # Evict cache entries for files that disappeared (log rotation /
    # manual cleanup). Keeps memory bounded over long-running tray
    # processes.
    if cache is not None:
        for stale in [k for k in cache if k not in seen]:
            del cache[stale]
