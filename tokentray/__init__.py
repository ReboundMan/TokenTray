"""``tokentray`` - shared token-usage measurement library.

This package is the cross-repo public API. ``AgencyUsageReport`` and any
other consumer should pin to a version and import ``iter_all_events`` /
``UsageEvent`` from here so that all telemetry parsing happens through
one code path.

The TokenTray *application* (tray UI, history DB, popup) remains in the
top-level modules at the repo root (``usage_core.py``, ``history_store.py``,
``tray_app.py`` etc.) so existing imports keep working. Those modules
delegate parsing into this package.

Keep this module dependency-free: it MUST NOT import ``PyQt6`` or any
GUI/tray code. Headless consumers (CLI scripts, CI, AgencyUsageReport
running in a server environment) must be able to ``pip install tokentray``
and use this package without dragging in the GUI stack.
"""
from __future__ import annotations

from tokentray._version import __version__
from tokentray.parsers import UsageEvent, iter_all_events, iter_usage_events

__all__ = [
    "__version__",
    "UsageEvent",
    "iter_all_events",
    "iter_usage_events",
]
