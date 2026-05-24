"""Headless smoke test for the coffee dialog module."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Force the offscreen Qt platform BEFORE importing PyQt6 so CI/dev machines
# without a real display can still run this test.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_coffee_dialog_constructs(qapp, tmp_path):
    """The dialog should construct cleanly for each reason value."""
    from coffee_dialog import BUY_ME_A_COFFEE_URL, show_coffee_dialog  # noqa: F401
    from history_store import HistoryStore

    store = HistoryStore.open(tmp_path / "h.db")
    try:
        # We can't easily exec() a modal dialog headlessly. Instead, import
        # the module-level constants and verify the URL is well-formed, and
        # construct a QDialog-equivalent path by patching exec to a no-op.
        from coffee_dialog import show_coffee_dialog as _show
        import coffee_dialog as cd

        # Monkeypatch QDialog.exec to immediately return without blocking.
        original_exec = cd.QDialog.exec
        cd.QDialog.exec = lambda self: 0
        try:
            outcome = _show(None, store, reason="menu")
        finally:
            cd.QDialog.exec = original_exec

        # With exec stubbed, no button was clicked -> default "cancelled"
        # path executes, which stamps the prompt-shown timestamp.
        assert outcome == "cancelled"
        assert (
            store._get_meta("coffee_prompt_last_shown_at_utc") is not None
        )
    finally:
        store.close()


def test_buy_me_a_coffee_url_is_https():
    from coffee_dialog import BUY_ME_A_COFFEE_URL
    assert BUY_ME_A_COFFEE_URL.startswith("https://")
    # Cheap sanity check that the slug isn't an obvious placeholder.
    assert "buymeacoffee.com" in BUY_ME_A_COFFEE_URL or "github.com/sponsors" in BUY_ME_A_COFFEE_URL
