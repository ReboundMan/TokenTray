"""Honor-system "buy me a coffee" unlock dialog for Advanced history.

Why a separate module: keeps Qt out of ``history_store`` (which stays
unit-testable without PyQt) and keeps the dialog text + URL in one place so
the BMC slug can be tweaked without touching the tray code.

This is intentionally low-tech: there is no backend, no license check, and
no PII collection. The user clicking "I bought you a coffee" flips a local
SQLite flag. The honest framing is that this is a tip jar, not a paywall.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from history_store import HistoryStore

# NOTE: confirm the actual slug with the maintainer before shipping.
# Default guessed from pyproject.toml author name.
BUY_ME_A_COFFEE_URL = "https://www.buymeacoffee.com/jeffjame"


_HEADLINES = {
    "startup_nag": "Enjoying TokenTray?",
    "advanced_toggle": "Advanced history needs a quick unlock",
    "menu": "Support TokenTray",
}

_BODY = (
    "TokenTray is free, ad-free, and stores everything locally on your "
    "machine. If it has saved you any token-watching headaches, a one-time "
    "coffee keeps the project moving and unlocks <b>Advanced history</b> "
    "(unlimited local retention) after the free trial."
    "<br><br>"
    "There's no account, no license key, no telemetry — just an honor-system "
    "flag stored in your local history database."
)


def show_coffee_dialog(
    parent: "QWidget | None",
    store: "HistoryStore",
    *,
    reason: str = "menu",
) -> str:
    """Show the BMC nag dialog and return what the user did.

    Returns one of:
        - ``"unlocked"``   — user clicked "I bought you a coffee".
        - ``"open_bmc"``   — user opened the BMC page (treat as pending).
        - ``"later"``      — user dismissed with "Maybe later".
        - ``"suppressed"`` — user dismissed AND ticked "Don't show again".
        - ``"cancelled"``  — user closed the dialog via the window close button.

    Side effects on the store:
        - On ``"unlocked"``: calls ``mark_supporter_purchased()``.
        - On any dismissal (``"later"``, ``"suppressed"``, ``"cancelled"``):
          stamps ``mark_coffee_prompt_shown()`` so the 21-day cadence resets.
        - On ``"suppressed"``: also calls ``set_coffee_prompt_suppressed(True)``.

    The "open_bmc" path also stamps the prompt-shown timestamp, so a user
    who opens the page but doesn't immediately click "unlock" won't be
    nagged again tomorrow.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle("TokenTray — Buy me a coffee ☕")
    dlg.setModal(reason == "advanced_toggle")  # block only when toggling

    layout = QVBoxLayout(dlg)

    headline = QLabel(f"<h3>{_HEADLINES.get(reason, _HEADLINES['menu'])}</h3>")
    layout.addWidget(headline)

    body = QLabel(_BODY)
    body.setWordWrap(True)
    body.setMinimumWidth(420)
    layout.addWidget(body)

    suppress = QCheckBox("Don't show this reminder again")
    # Default unchecked. Only meaningful for the startup nag path but we
    # show it everywhere for consistency.
    layout.addWidget(suppress)

    btn_box = QDialogButtonBox()
    btn_open = QPushButton("Open Buy Me a Coffee page")
    btn_unlock = QPushButton("I bought you a coffee — unlock now")
    btn_unlock.setDefault(True)
    btn_later = QPushButton("Maybe later")
    btn_box.addButton(btn_open, QDialogButtonBox.ButtonRole.ActionRole)
    btn_box.addButton(btn_unlock, QDialogButtonBox.ButtonRole.AcceptRole)
    btn_box.addButton(btn_later, QDialogButtonBox.ButtonRole.RejectRole)
    layout.addWidget(btn_box)

    # Track the action the user chose so we can persist it after exec().
    action = {"value": "cancelled"}

    def _on_unlock() -> None:
        action["value"] = "unlocked"
        dlg.accept()

    def _on_later() -> None:
        action["value"] = "later"
        dlg.reject()

    def _on_open_bmc() -> None:
        QDesktopServices.openUrl(QUrl(BUY_ME_A_COFFEE_URL))
        action["value"] = "open_bmc"
        # Don't close the dialog: the user may still want to confirm unlock
        # after they've completed the donation in the browser.
        btn_unlock.setFocus()

    btn_unlock.clicked.connect(_on_unlock)
    btn_later.clicked.connect(_on_later)
    btn_open.clicked.connect(_on_open_bmc)

    dlg.exec()

    chose = action["value"]
    if chose == "unlocked":
        store.mark_supporter_purchased()
        # Even on unlock, stamp the prompt timestamp for completeness.
        store.mark_coffee_prompt_shown()
        return "unlocked"

    # Any non-unlock outcome stamps the prompt so we honour the cadence.
    store.mark_coffee_prompt_shown()
    if suppress.isChecked():
        store.set_coffee_prompt_suppressed(True)
        return "suppressed"
    return chose
