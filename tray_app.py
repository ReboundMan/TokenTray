"""TokenUsageTray -- systray app for live Copilot CLI token usage."""
from __future__ import annotations

import sys
from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from history_store import HistoryStore, SupporterRequiredError
from icon_renderer import make_badge_icon
from popup_window import DEFAULT_WINDOW, PopupWindow
from usage_core import bucket_by_day, fmt_tokens, iter_usage_events

REFRESH_MS = 120_000  # 120 seconds (matches user preference)


class TrayApp:
    def __init__(self) -> None:
        self.app = getattr(TrayApp, "_existing_app", None) or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # Open the local history store. Failure here must never block the
        # tray -- the popup degrades gracefully when self.store is None.
        try:
            self.store: HistoryStore | None = HistoryStore.open()
        except Exception:
            import traceback

            traceback.print_exc()
            self.store = None

        self.popup = PopupWindow()
        self.popup.refresh_button.clicked.connect(self.refresh)

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

        settings_menu = menu.addMenu("Settings")
        self._act_startup = QAction("Start at login", settings_menu)
        self._act_startup.setCheckable(True)
        self._act_startup.setChecked(self._startup_is_installed())
        self._act_startup.toggled.connect(self._on_startup_toggled)
        settings_menu.addAction(self._act_startup)

        # Advanced history (paid-tier-in-waiting) toggle. During the free
        # trial recording is on regardless; this toggle gates recording
        # after the trial ends. Unchecking it also opts the user out of
        # recording during the trial (privacy escape hatch).
        self._act_history = QAction("Advanced history (record locally)", settings_menu)
        self._act_history.setCheckable(True)
        self._sync_history_menu_state()
        self._act_history.toggled.connect(self._on_history_toggled)
        settings_menu.addAction(self._act_history)

        settings_menu.addSeparator()
        self._act_coffee = QAction("Buy me a coffee ☕", settings_menu)
        self._act_coffee.triggered.connect(self._on_coffee_menu)
        settings_menu.addAction(self._act_coffee)

        self._act_restore = QAction("Restore supporter status", settings_menu)
        self._act_restore.triggered.connect(self._on_restore_supporter)
        settings_menu.addAction(self._act_restore)

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
        # Startup nag: defer until the event loop is running so any modal
        # dialog has a proper parent (the tray) and doesn't race the
        # initial refresh. Cadence is checked inside the helper.
        QTimer.singleShot(750, self._maybe_show_startup_nag)

    # ------------------------------------------------------------------
    def _startup_is_installed(self) -> bool:
        try:
            from install_startup import is_installed
            return is_installed()
        except BaseException:
            return False

    def _on_startup_toggled(self, checked: bool) -> None:
        """Install or remove the Windows Startup shortcut to match the menu state."""
        try:
            from install_startup import install, remove
            if checked:
                install()
            else:
                remove()
        except BaseException as exc:  # SystemExit from install_startup helpers
            # Revert the menu check state on failure, and surface the error
            # rather than silently leaving the menu out of sync.
            self._act_startup.blockSignals(True)
            self._act_startup.setChecked(self._startup_is_installed())
            self._act_startup.blockSignals(False)
            QMessageBox.warning(
                None,
                "TokenTray",
                f"Failed to {'enable' if checked else 'disable'} start at login:\n{exc}",
            )
            return

        # Re-sync the check state from disk so the menu reflects reality even
        # if install/remove silently no-op'd (e.g. missing APPDATA).
        actual = self._startup_is_installed()
        if actual != checked:
            self._act_startup.blockSignals(True)
            self._act_startup.setChecked(actual)
            self._act_startup.blockSignals(False)

    # ------------------------------------------------------------------
    def _sync_history_menu_state(self) -> None:
        """Reflect the current recording-enabled state in the menu checkbox."""
        if self.store is None:
            self._act_history.setEnabled(False)
            self._act_history.setChecked(False)
            self._act_history.setToolTip("History store unavailable.")
            return
        status = self.store.tier_status()
        self._act_history.blockSignals(True)
        self._act_history.setChecked(status.recording_enabled)
        self._act_history.blockSignals(False)
        if status.recording_opt_out:
            # Opt-out takes precedence over the in_trial label so the
            # tooltip never falsely implies recording is happening.
            tip = (
                "Local recording is paused (you opted out). "
                "Check to resume capturing new events."
            )
        elif status.in_trial:
            tip = (
                f"Free trial: {status.trial_days_remaining} days remaining. "
                "Uncheck to opt out of local recording."
            )
        elif status.advanced_enabled and status.supporter_purchased:
            tip = "Advanced history active — recording locally. Thanks for the coffee!"
        elif status.advanced_enabled and not status.supporter_purchased:
            tip = (
                "Advanced is enabled but locked. "
                "Buy me a coffee to resume recording new events."
            )
        else:
            tip = (
                "Trial ended. Check to resume local recording (requires a "
                "one-time coffee). Existing history remains viewable either way."
            )
        self._act_history.setToolTip(tip)

    def _on_history_toggled(self, checked: bool) -> None:
        if self.store is None:
            return
        status = self.store.tier_status()
        try:
            if status.in_trial and not checked:
                # User is explicitly opting out during the trial.
                self.store.set_recording_opt_out(True)
            elif not status.in_trial:
                # Post-trial: the toggle directly drives advanced_enabled.
                # This may raise SupporterRequiredError if the user hasn't
                # bought a coffee yet -- in which case we open the dialog.
                self.store.set_advanced_enabled(checked)
            else:
                # In trial + checked: ensure opt-out is cleared.
                self.store.set_recording_opt_out(False)
        except SupporterRequiredError:
            # Revert the check before showing the dialog so the menu state
            # is honest if the user dismisses without unlocking.
            self._sync_history_menu_state()
            try:
                from coffee_dialog import show_coffee_dialog
                outcome = show_coffee_dialog(
                    None, self.store, reason="advanced_toggle"
                )
            except Exception:
                import traceback
                traceback.print_exc()
                outcome = "cancelled"
            if outcome == "unlocked":
                # Now safe to flip the flag for real.
                try:
                    self.store.set_advanced_enabled(True)
                except Exception:
                    import traceback
                    traceback.print_exc()
            self._sync_history_menu_state()
            self.refresh()
            return

        self._sync_history_menu_state()
        # Refresh popup so the banner updates immediately.
        self.refresh()

    # ------------------------------------------------------------------
    def _on_coffee_menu(self) -> None:
        if self.store is None:
            return
        try:
            from coffee_dialog import show_coffee_dialog
            show_coffee_dialog(None, self.store, reason="menu")
        except Exception:
            import traceback
            traceback.print_exc()
        self._sync_history_menu_state()
        self.refresh()

    def _on_restore_supporter(self) -> None:
        """One-click honor-system flip for users who reinstall or wipe state."""
        if self.store is None:
            return
        if self.store.supporter_purchased():
            QMessageBox.information(
                None,
                "TokenTray",
                "Supporter status is already active. Thanks for the coffee! ☕",
            )
            return
        choice = QMessageBox.question(
            None,
            "Restore supporter status",
            (
                "This flips the local supporter flag without opening the "
                "Buy Me a Coffee page. Use it only if you've previously "
                "donated (e.g. after reinstalling).\n\nContinue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice == QMessageBox.StandardButton.Yes:
            self.store.mark_supporter_purchased()
            self._sync_history_menu_state()
            self.refresh()

    def _maybe_show_startup_nag(self) -> None:
        """Post-trial 21-day reminder. Silently no-ops during the trial."""
        if self.store is None:
            return
        try:
            if not self.store.should_show_coffee_prompt():
                return
            from coffee_dialog import show_coffee_dialog
            show_coffee_dialog(None, self.store, reason="startup_nag")
        except Exception:
            import traceback
            traceback.print_exc()
        self._sync_history_menu_state()

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

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        try:
            events = list(iter_usage_events())
            # Stats grid always reflects "today"; chart shows the fixed window.
            today = bucket_by_day(events, days=1)[-1]
            chart_buckets = bucket_by_day(events, days=DEFAULT_WINDOW)

            history_snapshot = None
            tier_snapshot = None
            if self.store is not None:
                tier_snapshot = self.store.tier_status()
                if tier_snapshot.recording_enabled:
                    try:
                        self.store.ingest(events)
                    except Exception:
                        import traceback
                        traceback.print_exc()
                try:
                    history_snapshot = self.store.all_summaries()
                except Exception:
                    import traceback
                    traceback.print_exc()
                # Tier state can change implicitly when the trial expires
                # mid-session; keep the menu in sync on every refresh.
                self._sync_history_menu_state()

            badge = fmt_tokens(today.total) if today.total else "0"
            self.tray.setIcon(make_badge_icon(badge))
            tip = (
                f"Today: {today.total:,} tokens "
                f"({today.events} turns, {len(today.sessions)} sessions)\n"
                f"Updated {datetime.now().strftime('%H:%M:%S')}"
            )
            self.tray.setToolTip(tip)
            self.popup.update_data(
                today,
                chart_buckets,
                history=history_snapshot,
                tier=tier_snapshot,
            )
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
    # CLI flags work in both the source `python tray_app.py` and the frozen
    # `TokenTray.exe` so end users can manage autostart without installing
    # Python.
    if "--install-startup" in sys.argv:
        from install_startup import install
        return install()
    if "--uninstall-startup" in sys.argv or "--remove-startup" in sys.argv:
        from install_startup import remove
        return remove()
    if "--version" in sys.argv:
        try:
            from _version import __version__
            print(f"tokentray {__version__}")
        except Exception:
            try:
                from importlib.metadata import version
                print(f"tokentray {version('tokentray')}")
            except Exception:
                print("tokentray 0.0.0")
        return 0

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
