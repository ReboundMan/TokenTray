"""Phase 3 - Advanced tab UI gating + breakdown rendering.

These tests construct a real PopupWindow against an in-memory HistoryStore.
We skip cleanly when a Qt platform plugin can't be loaded (CI without a
display server). On Windows the offscreen plugin ships with PyQt6, so the
suite runs locally without any extra setup.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "minimal")

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PyQt6 missing
    pytest.skip("PyQt6 not installed", allow_module_level=True)

from history_store import HistoryStore, UNKNOWN_LABEL
from tokentray.parsers import UsageEvent


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication(sys.argv)
        except Exception as exc:  # pragma: no cover - no display available
            pytest.skip(f"QApplication unavailable: {exc}")
    return app


@pytest.fixture()
def popup(qapp):
    # Import lazily so the module-level skip above takes precedence when
    # Qt isn't importable at all.
    from popup_window import PopupWindow

    pw = PopupWindow()
    yield pw
    pw.deleteLater()


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "hist.db"
    s = HistoryStore.open(db, now_utc=datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc))
    # Seed two events split across hosts/models so the breakdown tables
    # have something non-trivial to render.
    s.ingest([
        UsageEvent(
            timestamp=datetime(2026, 5, 24, 13, 0, tzinfo=timezone.utc),
            session_id="sess-claw",
            input_tokens=500,
            output_tokens=200,
            cache_read_tokens=100,
            host_app="Clawpilot",
            model="claude-opus-4.6",
        ),
        UsageEvent(
            timestamp=datetime(2026, 5, 24, 14, 0, tzinfo=timezone.utc),
            session_id="sess-agency",
            input_tokens=300,
            output_tokens=80,
            cache_read_tokens=20,
            host_app="Agency",
            model="gpt-5.4",
        ),
    ])
    yield s
    s.close()


def _find_advanced_tab_index(popup) -> int:
    tabs = popup._tabs
    for i in range(tabs.count()):
        if tabs.tabText(i) == "Advanced":
            return i
    raise AssertionError("Advanced tab not registered")


def test_advanced_tab_is_registered(popup):
    idx = _find_advanced_tab_index(popup)
    assert idx >= 0
    # Tab content is intentionally NOT built until first activation.
    assert popup._advanced_built is False


def test_advanced_tab_builds_lazily_on_activation(popup, store):
    popup.set_history_store(store)
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)
    assert popup._advanced_built is True
    # The stacked widget should exist with locked + unlocked pages.
    assert popup._adv_stack.count() == 2


def test_advanced_tab_locked_when_supporter_not_purchased(popup, store):
    popup.set_history_store(store)
    tier = store.tier_status()
    assert tier.supporter_purchased is False
    popup._latest_tier = tier
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)
    # Locked state = stack index 0.
    assert popup._adv_stack.currentIndex() == 0
    # No data populated.
    assert popup._adv_host_table.rowCount() == 0


def test_advanced_tab_locked_when_advanced_not_enabled(popup, store):
    popup.set_history_store(store)
    store.mark_supporter_purchased()
    tier = store.tier_status()
    assert tier.supporter_purchased is True
    assert tier.advanced_enabled is False
    popup._latest_tier = tier
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)
    assert popup._adv_stack.currentIndex() == 0  # still locked


def test_advanced_tab_unlocks_when_supporter_and_advanced_enabled(popup, store):
    popup.set_history_store(store)
    store.mark_supporter_purchased()
    store.set_advanced_enabled(True)
    tier = store.tier_status()
    assert tier.supporter_purchased is True
    assert tier.advanced_enabled is True
    popup._latest_tier = tier
    # Use all_time so seeded fixture timestamps (May 2026) aren't filtered
    # out by the real-wallclock "today" boundary on whichever date the
    # test happens to run.
    popup._advanced_period = "all_time"
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)
    # Unlocked state = stack index 1.
    assert popup._adv_stack.currentIndex() == 1
    # Both tables have the two seeded rows.
    assert popup._adv_host_table.rowCount() == 2
    assert popup._adv_model_table.rowCount() == 2
    host_names = {
        popup._adv_host_table.item(r, 0).text()
        for r in range(popup._adv_host_table.rowCount())
    }
    assert host_names == {"Clawpilot", "Agency"}
    model_names = {
        popup._adv_model_table.item(r, 0).text()
        for r in range(popup._adv_model_table.rowCount())
    }
    assert model_names == {"claude-opus-4.6", "gpt-5.4"}


def test_advanced_tab_period_selector_drives_query(popup, store, monkeypatch):
    popup.set_history_store(store)
    store.mark_supporter_purchased()
    store.set_advanced_enabled(True)
    popup._latest_tier = store.tier_status()
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)

    captured: list[str] = []
    real_host = store.totals_by_host
    real_model = store.totals_by_model

    def spy_host(*, period=None, **kw):
        captured.append(f"host:{period}")
        return real_host(period=period, **kw)

    def spy_model(*, period=None, **kw):
        captured.append(f"model:{period}")
        return real_model(period=period, **kw)

    monkeypatch.setattr(store, "totals_by_host", spy_host)
    monkeypatch.setattr(store, "totals_by_model", spy_model)

    popup._on_advanced_period_changed("week")
    assert popup._advanced_period == "week"
    assert "host:week" in captured
    assert "model:week" in captured

    popup._on_advanced_period_changed("all_time")
    assert popup._advanced_period == "all_time"
    assert "host:all_time" in captured
    assert "model:all_time" in captured


def test_unknown_label_sinks_to_bottom(popup, store):
    # Seed a legacy NULL-host row plus the two known-host rows from the
    # fixture. Bypass ingest() to dodge the recording_active_since_utc
    # cutoff guard that drops events with timestamps earlier than the
    # store's open time.
    s = store
    conn = s._conn  # private but stable for this test
    conn.execute(
        "INSERT INTO events VALUES('legacy', ?, 'sess-legacy', 5000, 1000, 0, 0, NULL, NULL)",
        (datetime(2026, 5, 24, 15, 0, tzinfo=timezone.utc).isoformat(),),
    )
    conn.commit()

    popup.set_history_store(s)
    s.mark_supporter_purchased()
    s.set_advanced_enabled(True)
    popup._latest_tier = s.tier_status()
    popup._advanced_period = "all_time"
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)

    # Three rows total: two seeded + one legacy.
    assert popup._adv_host_table.rowCount() == 3
    last_row = popup._adv_host_table.rowCount() - 1
    assert popup._adv_host_table.item(last_row, 0).text() == UNKNOWN_LABEL


def test_advanced_tab_bmc_button_unlocks_both_flags(popup, store, monkeypatch):
    """Clicking the Advanced tab's BMC unlock button should flip BOTH
    ``coffee_purchased_at_utc`` AND ``advanced_enabled``.

    Regression: a previous build only stamped supporter_purchased, so the
    user still saw the locked card after paying because the gate is
    ``advanced_enabled AND supporter_purchased`` and ``advanced_enabled``
    defaults to false.
    """
    popup.set_history_store(store)
    popup._latest_tier = store.tier_status()
    idx = _find_advanced_tab_index(popup)
    popup._tabs.setCurrentIndex(idx)
    assert popup._adv_stack.currentIndex() == 0  # locked

    # Patch the coffee dialog so the test doesn't actually pop a modal:
    # simulate the "I bought you a coffee" outcome by marking the supporter
    # purchased (as the real dialog would) and returning "unlocked".
    def _fake_dialog(parent, store, *, reason="menu"):
        store.mark_supporter_purchased()
        return "unlocked"

    import coffee_dialog
    monkeypatch.setattr(coffee_dialog, "show_coffee_dialog", _fake_dialog)

    popup._advanced_period = "all_time"
    popup._on_advanced_unlock_clicked()

    tier = store.tier_status()
    assert tier.supporter_purchased is True, "BMC click should mark supporter purchased"
    assert tier.advanced_enabled is True, (
        "BMC click from Advanced tab should also flip advanced_enabled "
        "(otherwise the gate stays closed and the user sees the lock screen)"
    )
    assert popup._adv_stack.currentIndex() == 1, "Advanced card should be unlocked"
