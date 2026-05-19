"""Render the PopupWindow to a PNG for the README.

Run from the project root with the project venv:
    python tools\make_screenshot.py

Produces assets/screenshot.png at the popup's natural size, using the user's
real telemetry data so the chart looks authentic. The 7-day view is captured
because that's the default and most-representative state.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from popup_window import PopupWindow  # noqa: E402
from usage_core import bucket_by_day, iter_usage_events  # noqa: E402

OUT = ROOT / "assets" / "screenshot.png"


def main() -> int:
    app = QApplication(sys.argv)
    pw = PopupWindow()
    evs = list(iter_usage_events())
    today = bucket_by_day(evs, days=1)[-1]
    pw.update_data(today, bucket_by_day(evs, days=7))

    # Force a layout pass so the chart paints before we grab.
    pw.show()
    pw.hide()
    app.processEvents()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pix = pw.grab()
    if pix.isNull():
        print("ERROR: grab() returned a null pixmap", file=sys.stderr)
        return 1
    pix.save(str(OUT), "PNG")
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes, {pix.width()}x{pix.height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
