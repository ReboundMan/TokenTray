"""Headless smoke tests for the About dialog module (WordMD-model rework)."""
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


@pytest.mark.parametrize(
    "reason", ["menu", "startup_nag", "advanced_toggle", "advanced_tab"]
)
def test_about_dialog_constructs(qapp, tmp_path, reason):
    """The dialog should construct cleanly for each reason value."""
    from about_dialog import show_about_dialog  # noqa: F401
    from history_store import HistoryStore

    store = HistoryStore.open(tmp_path / "h.db")
    try:
        import about_dialog as ad

        # Monkeypatch QDialog.exec to immediately return without blocking.
        original_exec = ad.QDialog.exec
        ad.QDialog.exec = lambda self: 0
        try:
            outcome = show_about_dialog(None, store, reason=reason)
        finally:
            ad.QDialog.exec = original_exec

        # With exec stubbed, no button was clicked -> default "cancelled"
        # path executes, which stamps the prompt-shown timestamp.
        assert outcome == "cancelled"
        assert (
            store._get_meta("coffee_prompt_last_shown_at_utc") is not None
        )
    finally:
        store.close()


def test_dialog_shows_current_version(qapp, tmp_path):
    """The About header must carry the real version, not a stale literal."""
    from _version import __version__
    import about_dialog as ad
    from history_store import HistoryStore
    from PyQt6.QtWidgets import QLabel

    store = HistoryStore.open(tmp_path / "h.db")
    captured: list[str] = []
    original_exec = ad.QDialog.exec

    def _capture_and_return(dlg_self):
        captured.extend(
            lbl.text() for lbl in dlg_self.findChildren(QLabel)
        )
        return 0

    ad.QDialog.exec = _capture_and_return
    try:
        ad.show_about_dialog(None, store, reason="menu")
    finally:
        ad.QDialog.exec = original_exec
        store.close()
    assert any(__version__ in text for text in captured)


def test_support_links_are_https():
    import support_links

    assert support_links.GITHUB.startswith("https://")
    assert support_links.PRODUCT_PAGE.startswith("https://")
    # The app-side tip URL is the stable reboundman.com redirect; the
    # Caddyfile owns the real destination (currently Buy Me a Coffee). The
    # label and disclaimer must describe that destination; both change only
    # via the flip checklist in support_links' docstring.
    assert support_links.TIP_URL == "https://reboundman.com/tokentray-tip"
    assert "coffee" in support_links.TIP_LABEL.lower()
    assert "buymeacoffee.com" in support_links.TIP_DISCLAIMER


def test_suppress_checkbox_only_on_startup_nag(qapp, tmp_path):
    """Only the startup nag honors the suppression flag, so only it may
    offer the checkbox (fleet finding, Lens)."""
    import about_dialog as ad
    from history_store import HistoryStore
    from PyQt6.QtWidgets import QCheckBox

    store = HistoryStore.open(tmp_path / "h.db")
    seen: dict[str, int] = {}
    original_exec = ad.QDialog.exec

    def _count_checkboxes(dlg_self):
        seen[dlg_self.windowTitle() + str(len(seen))] = len(
            dlg_self.findChildren(QCheckBox)
        )
        return 0

    ad.QDialog.exec = _count_checkboxes
    try:
        ad.show_about_dialog(None, store, reason="menu")
        ad.show_about_dialog(None, store, reason="startup_nag")
    finally:
        ad.QDialog.exec = original_exec
        store.close()
    counts = list(seen.values())
    assert counts == [0, 1]


def test_open_url_rejects_non_http_schemes(qapp, monkeypatch):
    """The browser-open helper must drop non-http(s) schemes outright."""
    import about_dialog as ad

    opened: list = []
    monkeypatch.setattr(
        ad.QDesktopServices, "openUrl", lambda url: opened.append(url)
    )
    ad._open_url("file:///C:/Windows/System32/calc.exe")
    ad._open_url("javascript:alert(1)")
    assert opened == []
    ad._open_url("https://example.com")
    assert len(opened) == 1
