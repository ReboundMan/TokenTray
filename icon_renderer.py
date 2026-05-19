"""Render a tray-friendly QIcon with a token-count badge."""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap


def make_badge_icon(text: str, *, accent: str = "#2563eb") -> QIcon:
    """Return a 64x64 icon with ``text`` drawn over a rounded accent badge.

    Designed to be readable in Windows 11 system tray at the default DPI; we
    render at 64x64 and let the shell down-scale to 16x16/20x20.
    """
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Rounded square background.
    p.setBrush(QBrush(QColor(accent)))
    p.setPen(QPen(QColor("#1e3a8a"), 2))
    p.drawRoundedRect(QRect(2, 2, size - 4, size - 4), 14, 14)

    # Text.
    p.setPen(QPen(QColor("white")))
    font = QFont("Segoe UI", 22, QFont.Weight.Bold)
    # Shrink font for longer strings so it always fits.
    if len(text) >= 4:
        font.setPointSize(18)
    if len(text) >= 5:
        font.setPointSize(15)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, text)
    p.end()

    return QIcon(pix)
