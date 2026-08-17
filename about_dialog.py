"""About TokenTray dialog: app info, links, and the honor-system tip/unlock.

Replaces the old ``coffee_dialog`` module. The layout follows WordMD's About
dialog model (name + version, one-line description, product/GitHub links,
then a support section), adapted for TokenTray's honor-system gating: after
the free trial, Advanced history recording asks for a one-time tip, and the
unlock stays what it always was, a local flag with no license server.

Why a separate module: keeps Qt out of ``history_store`` (which stays
unit-testable without PyQt) and keeps the dialog text in one place. Outbound
URLs live in ``support_links`` so a destination change is a one-line edit.

The return-value and store-side-effect contract is unchanged from
``coffee_dialog.show_coffee_dialog`` so callers and the nag cadence behave
identically.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

import support_links
from _version import __version__

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from history_store import HistoryStore


_TAGLINE = (
    "Live Copilot CLI, Agency, and Claude Code token usage in your "
    "Windows system tray."
)

# Gate reasons get a gate title; the deliberate About open keeps the About
# title (fleet finding, Lens: a paywall dialog titled "About TokenTray"
# fits neither role).
_TITLES = {
    "advanced_toggle": "Unlock Advanced history",
    "advanced_tab": "Unlock Advanced history",
}

_HEADLINES = {
    "startup_nag": "Enjoying TokenTray?",
    "advanced_toggle": "Advanced history needs a quick unlock",
    "advanced_tab": "Unlock per-tool and per-model breakdowns",
    "menu": "Support TokenTray",
}

_SUPPORT_BODIES = {
    "advanced_tab": (
        "A one-time tip unlocks the <b>Advanced</b> tab: your tokens broken "
        "out per tool used (Copilot CLI, Agency, Claude Code) and per model."
        "<br><br>"
        "Everything still stays local to this machine. No telemetry, no "
        "account, no license server. The unlock is a flag stored in the "
        "local history database."
    ),
    # The toggle gate only fires post-trial, so the copy must not talk about
    # the trial as a future event (fleet finding, Lens).
    "advanced_toggle": (
        "Your free trial has ended, so local history recording is paused. "
        "A one-time tip resumes recording and unlocks the <b>Advanced</b> "
        "per-tool and per-model breakdowns."
        "<br><br>"
        "Everything still stays local to this machine. No telemetry, no "
        "account, no license server. The unlock is a flag stored in the "
        "local history database."
    ),
}

_SUPPORT_BODY = (
    "TokenTray is free to use, ad-free, and everything stays local to this "
    "machine. If it has earned a place in your tray, a one-time tip says "
    "thanks. After the 60-day free trial it also keeps local history "
    "recording running and unlocks <b>Advanced</b> per-tool and per-model "
    "breakdowns; the live tray badge and Today view work regardless."
    "<br><br>"
    "There's no account, no license key, and no telemetry. The unlock is an "
    "honor-system flag stored in your local history database."
)


def _open_url(url: str) -> None:
    """Open an http(s) URL in the default browser, never in-app.

    The scheme check mirrors WordMD's ``OpenExternal``: current callers all
    pass constants from ``support_links``, but a future caller must not be
    able to hand a non-http(s) scheme straight to the OS.
    """
    q = QUrl(url)
    if q.scheme() not in ("http", "https"):
        return
    QDesktopServices.openUrl(q)


def show_about_dialog(
    parent: "QWidget | None",
    store: "HistoryStore",
    *,
    reason: str = "menu",
) -> str:
    """Show the About/support dialog and return what the user did.

    Returns one of:
        - ``"unlocked"``   — user clicked the honor-system unlock.
        - ``"open_tip"``   — user opened the tip page (treat as pending).
        - ``"later"``      — user dismissed with "Maybe later".
        - ``"suppressed"`` — user dismissed AND ticked "Don't show again".
        - ``"cancelled"``  — user closed the dialog via the window close button.

    Side effects on the store:
        - On ``"unlocked"``: calls ``mark_supporter_purchased()``.
        - On any dismissal (``"later"``, ``"suppressed"``, ``"cancelled"``):
          stamps ``mark_coffee_prompt_shown()`` so the 21-day cadence resets.
        - On ``"suppressed"``: also calls ``set_coffee_prompt_suppressed(True)``.

    The ``"open_tip"`` path also stamps the prompt-shown timestamp, so a user
    who opens the page but doesn't immediately click unlock won't be nagged
    again tomorrow.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(_TITLES.get(reason, "About TokenTray"))
    # No setModal() call: exec() below is always application-modal, so a
    # setModal() here would be dead code (fleet finding, Sage).
    # Force a complete light-theme palette on the dialog: white surface +
    # dark text + explicitly-styled buttons and checkbox indicator. Relying
    # on the OS theme once produced invisible white-on-white buttons against
    # the forced-white background; styling everything explicitly keeps the
    # dialog readable in any parent / OS theme combination.
    dlg.setStyleSheet(
        "QDialog { background:#ffffff; }"
        "QLabel { color:#0f172a; background:transparent; }"
        "QLabel#secondary { color:#475569; }"
        "QCheckBox { color:#0f172a; background:transparent; }"
        # #64748b, not lighter grays: WCAG 1.4.11 wants >= 3:1 for the
        # checkbox affordance against the white surface (fleet, Beacon).
        "QCheckBox::indicator { width:16px; height:16px; border:1px solid #64748b; "
        "  background:#ffffff; border-radius:3px; }"
        "QCheckBox::indicator:checked { background:#0078d4; border-color:#0078d4; "
        "  image:none; }"
        "QDialogButtonBox { background:transparent; }"
        "QPushButton { color:#0f172a; background:#f1f5f9; border:1px solid #cbd5e1; "
        "  padding:6px 14px; border-radius:6px; font-size:12px; }"
        "QPushButton:hover { background:#e2e8f0; }"
        "QPushButton:pressed { background:#cbd5e1; }"
        # Visible keyboard-focus indicator via border-color (Qt style sheets
        # do not reliably render `outline`); link buttons reserve a
        # transparent border so focus doesn't shift layout (fleet, Beacon).
        "QPushButton:focus { border:1px solid #0078d4; }"
        "QPushButton:default { color:#ffffff; background:#0078d4; border-color:#0078d4; "
        "  font-weight:600; }"
        "QPushButton:default:hover { background:#106ebe; border-color:#106ebe; }"
        "QPushButton:default:pressed { background:#005a9e; border-color:#005a9e; }"
        "QPushButton:default:focus { border:1px solid #003f70; }"
        "QPushButton#link { color:#0b62a4; background:transparent; "
        "  border:1px solid transparent; padding:2px 0; text-align:left; "
        "  font-size:12px; }"
        "QPushButton#link:hover { color:#084c80; text-decoration:underline; }"
        "QPushButton#link:focus { border:1px solid #0078d4; }"
    )

    layout = QVBoxLayout(dlg)

    title = QLabel(f"<h3 style='margin:0'>TokenTray v{__version__}</h3>")
    layout.addWidget(title)

    tagline = QLabel(_TAGLINE)
    tagline.setWordWrap(True)
    tagline.setMinimumWidth(420)
    layout.addWidget(tagline)

    def _link_button(text: str, url: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("link")
        btn.setFlat(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(url)
        btn.setAccessibleDescription(f"Opens {url} in your browser")
        btn.clicked.connect(lambda: _open_url(url))
        return btn

    layout.addWidget(_link_button("reboundman.com/tokentray", support_links.PRODUCT_PAGE))
    layout.addWidget(_link_button("View on GitHub", support_links.GITHUB))

    layout.addSpacing(8)

    headline = QLabel(f"<b>{_HEADLINES.get(reason, _HEADLINES['menu'])}</b>")
    layout.addWidget(headline)

    body = QLabel(_SUPPORT_BODIES.get(reason, _SUPPORT_BODY))
    body.setWordWrap(True)
    layout.addWidget(body)

    disclaimer = QLabel(support_links.TIP_DISCLAIMER)
    disclaimer.setObjectName("secondary")
    disclaimer.setWordWrap(True)
    layout.addWidget(disclaimer)

    # Only the startup nag honors the suppression flag, so only it shows the
    # checkbox; offering it on a dialog the user opened deliberately promised
    # something the store would not deliver (fleet finding, Lens).
    suppress: QCheckBox | None = None
    if reason == "startup_nag":
        suppress = QCheckBox("Don't show this reminder again")
        layout.addWidget(suppress)

    btn_box = QDialogButtonBox()
    btn_tip = QPushButton(support_links.TIP_LABEL)
    btn_tip.setToolTip(support_links.TIP_DISCLAIMER)
    # A screen-reader user who tabs straight to the button gets the payment
    # note without reading the label above (fleet, Beacon; same fix WordMD's
    # About made via AutomationProperties.SetHelpText).
    btn_tip.setAccessibleDescription(support_links.TIP_DISCLAIMER)
    btn_unlock = QPushButton("I already tipped - unlock now")
    btn_later = QPushButton("Maybe later")
    # "Maybe later" is the Enter-key default everywhere: the unlock flips a
    # persistent flag and must be a deliberate click, not an accidental
    # Enter on a dialog that popped up (fleet, Lens + Beacon).
    btn_later.setDefault(True)
    btn_box.addButton(btn_tip, QDialogButtonBox.ButtonRole.ActionRole)
    btn_box.addButton(btn_unlock, QDialogButtonBox.ButtonRole.AcceptRole)
    btn_box.addButton(btn_later, QDialogButtonBox.ButtonRole.RejectRole)
    layout.addWidget(btn_box)
    btn_later.setFocus()

    # Track the action the user chose so we can persist it after exec().
    action = {"value": "cancelled"}

    def _on_unlock() -> None:
        action["value"] = "unlocked"
        dlg.accept()

    def _on_later() -> None:
        action["value"] = "later"
        dlg.reject()

    def _on_tip() -> None:
        _open_url(support_links.TIP_URL)
        action["value"] = "open_tip"
        # Don't close the dialog: the user may still want to confirm unlock
        # after they've completed the tip in the browser.
        btn_unlock.setFocus()

    btn_unlock.clicked.connect(_on_unlock)
    btn_later.clicked.connect(_on_later)
    btn_tip.clicked.connect(_on_tip)

    dlg.exec()

    chose = action["value"]
    if chose == "unlocked":
        store.mark_supporter_purchased()
        # Even on unlock, stamp the prompt timestamp for completeness.
        store.mark_coffee_prompt_shown()
        return "unlocked"

    # Any non-unlock outcome stamps the prompt so we honour the cadence.
    store.mark_coffee_prompt_shown()
    if suppress is not None and suppress.isChecked():
        store.set_coffee_prompt_suppressed(True)
        return "suppressed"
    return chose
