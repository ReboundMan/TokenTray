"""TokenUsageTray -- systray app for live Copilot CLI token usage."""
from __future__ import annotations

import sys
from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from icon_renderer import make_badge_icon
from popup_window import DEFAULT_WINDOW, PopupWindow
from usage_core import bucket_by_day, bucket_by_hour, fmt_tokens, iter_usage_events

REFRESH_MS = 120_000  # 120 seconds (matches user preference)


class TrayApp:
    def __init__(self) -> None:
        self.app = getattr(TrayApp, "_existing_app", None) or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self._window_days = DEFAULT_WINDOW

        self.popup = PopupWindow()
        self.popup.refresh_button.clicked.connect(self.refresh)
        self.popup.window_changed.connect(self._on_window_changed)

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(make_badge_icon("…"))
        self.tray.setToolTip("Copilot CLI tokens (loading…)")

        menu = QMenu()
        act_show = QAction("Show details", menu)
        act_refresh = QAction("Refresh now", menu)
        act_quit = QAction("Quit", menu)
        act_show.triggered.connect(self._show_popup_near_cursor)
        act_refresh.triggered.connect(self.refresh)
        act_quit.triggered.connect(self.app.quit)
        menu.addAction(act_show)
        menu.addAction(act_refresh)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

        self.timer = QTimer()
        self.timer.setInterval(REFRESH_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        # Initial refresh as soon as the event loop starts.
        QTimer.singleShot(50, self.refresh)

    # ------------------------------------------------------------------
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_popup_near_cursor()

    def _show_popup_near_cursor(self) -> None:
        self.refresh()
        self.popup.show_near(QCursor.pos())

    def _on_window_changed(self, days: int) -> None:
        self._window_days = days
        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            events = list(iter_usage_events())
            # Stats grid always reflects "today" regardless of chart window.
            today = bucket_by_day(events, days=1)[-1]
            if self._window_days == 1:
                chart_buckets = bucket_by_hour(events)
            else:
                chart_buckets = bucket_by_day(events, days=self._window_days)

            badge = fmt_tokens(today.total) if today.total else "0"
            self.tray.setIcon(make_badge_icon(badge))
            tip = (
                f"Today: {today.total:,} tokens "
                f"({today.events} turns, {len(today.sessions)} sessions)\n"
                f"Updated {datetime.now().strftime('%H:%M:%S')}"
            )
            self.tray.setToolTip(tip)
            self.popup.update_data(today, chart_buckets)
        except Exception:
            # Never let a transient log-parse or rendering error kill the tray.
            import traceback

            traceback.print_exc()
            self.tray.setToolTip(
                "Token usage: refresh failed; see tray_app.log"
            )

    # ------------------------------------------------------------------
    def run(self) -> int:
        return self.app.exec()


def main() -> int:
    # NOTE: QApplication must exist before any Qt static query that touches
    # the platform plugin. Calling QSystemTrayIcon.isSystemTrayAvailable()
    # without one crashes under pythonw.exe.
    app = QApplication(sys.argv)
    print(f"system tray available: {QSystemTrayIcon.isSystemTrayAvailable()}")
    if not QSystemTrayIcon.isSystemTrayAvailable():
        # On a freshly-booted session the shell tray may not be ready yet.
        import time

        for _ in range(20):
            if QSystemTrayIcon.isSystemTrayAvailable():
                break
            time.sleep(0.25)
            QApplication.processEvents()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            print("System tray is not available; aborting.")
            return 1
    TrayApp._existing_app = app  # type: ignore[attr-defined]
    return TrayApp().run()


if __name__ == "__main__":
    raise SystemExit(main())
