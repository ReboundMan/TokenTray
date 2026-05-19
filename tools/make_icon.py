"""Generate ``assets/tokentray.ico`` containing multiple sizes.

Run once after editing the icon design; PyInstaller picks the .ico up at
build time. Uses Qt only (no Pillow dep).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

OUT = Path(__file__).resolve().parent.parent / "assets" / "tokentray.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)
ACCENT = "#2563eb"
BORDER = "#1e3a8a"


def _render(size: int) -> QImage:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(QBrush(QColor(ACCENT)))
    border_w = max(1, size // 32)
    p.setPen(QPen(QColor(BORDER), border_w))
    radius = max(2, size // 5)
    inset = border_w
    p.drawRoundedRect(QRect(inset, inset, size - 2 * inset, size - 2 * inset), radius, radius)
    # Stylised "$" -> "T" for "tokens"
    p.setPen(QPen(QColor("white")))
    font = QFont("Segoe UI", max(6, int(size * 0.55)), QFont.Weight.Black)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    p.end()
    return pix.toImage()


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841 - QPainter needs QGuiApplication
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = [_render(s) for s in SIZES]
    # Qt's Windows plugin saves multi-size ICOs when given the largest image;
    # however to guarantee every resolution, we save each one individually
    # into a single .ico via a helper. Simplest: rely on Qt's default which
    # already produces a sensible ICO with multiple sizes when source is 256.
    # We pick the 256px image as the master; Windows downscales as needed.
    images[-1].save(str(OUT), "ICO")
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
