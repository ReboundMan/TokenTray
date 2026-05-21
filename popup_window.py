"""Popup window showing today's token usage and a 7-day chart."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from PyQt6.QtCharts import (
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QStackedBarSeries,
    QValueAxis,
)
from PyQt6.QtCore import QMargins, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from usage_core import DayBucket, fmt_tokens

DEFAULT_WINDOW = 7  # Fixed look-back window in days


def _app_version() -> str:
    try:
        from importlib.metadata import version
        return version("tokentray")
    except Exception:
        return "0.1.0"


class _Stat(QWidget):
    def __init__(self, label: str, color: str = "#0f172a"):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        self._lbl = QLabel(label)
        self._lbl.setStyleSheet("color: #64748b; font-size: 11px;")
        self._val = QLabel("--")
        f = QFont("Segoe UI", 16, QFont.Weight.DemiBold)
        self._val.setFont(f)
        self._val.setStyleSheet(f"color: {color};")
        v.addWidget(self._lbl)
        v.addWidget(self._val)

    def set_value(self, text: str) -> None:
        self._val.setText(text)


class PopupWindow(QWidget):
    """Frameless popup anchored above/below the tray icon."""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(
            "QWidget#root { background:#ffffff; border:1px solid #cbd5e1; border-radius:10px; }"
            "QLabel { color:#0f172a; }"
        )
        self.resize(640, 480)

        root = QFrame(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        # Header
        header = QHBoxLayout()
        title = QLabel("Copilot CLI token usage")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.addWidget(title)
        header.addStretch(1)
        self._updated_lbl = QLabel("")
        self._updated_lbl.setStyleSheet("color:#64748b; font-size:11px;")
        header.addWidget(self._updated_lbl)
        v.addLayout(header)

        # Stat grid for "today"
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(2)
        self._stat_total = _Stat("Today total", "#2563eb")
        self._stat_in = _Stat("Uncached in", "#0f172a")
        self._stat_out = _Stat("Output", "#0f172a")
        self._stat_cache = _Stat("Cached in", "#0f172a")
        self._stat_sessions = _Stat("Sessions", "#0f172a")
        self._stat_events = _Stat("Turns", "#0f172a")
        grid.addWidget(self._stat_total, 0, 0)
        grid.addWidget(self._stat_in, 0, 1)
        grid.addWidget(self._stat_out, 0, 2)
        grid.addWidget(self._stat_cache, 1, 0)
        grid.addWidget(self._stat_sessions, 1, 1)
        grid.addWidget(self._stat_events, 1, 2)
        v.addLayout(grid)

        # Chart -- aggressively style away QtCharts' default dark frame /
        # background and reserve a left margin so the Y-axis labels never
        # ellipsize into "...".
        self._chart = QChart()
        self._chart.setBackgroundRoundness(0)
        self._chart.setBackgroundBrush(QBrush(QColor("white")))
        self._chart.setBackgroundPen(QPen(Qt.PenStyle.NoPen))
        self._chart.setMargins(QMargins(8, 4, 8, 4))
        self._chart.layout().setContentsMargins(0, 0, 0, 0)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self._chart.legend().setLabelColor(QColor("#0f172a"))
        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chart_view.setFrameShape(QFrame.Shape.NoFrame)
        self._chart_view.setStyleSheet("QChartView { background: white; border: none; }")
        self._chart_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self._chart_view, 1)

        # Footer buttons
        footer = QHBoxLayout()
        version_lbl = QLabel(f"v{_app_version()}")
        version_lbl.setStyleSheet("color:#94a3b8; font-size:11px;")
        footer.addWidget(version_lbl)
        footer.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._close_btn = QPushButton("Close")
        for b in (self._refresh_btn, self._close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(28)
            b.setStyleSheet(
                "QPushButton {"
                "  padding: 5px 16px;"
                "  border-radius: 6px;"
                "  background: #f1f5f9;"
                "  border: 1px solid #94a3b8;"
                "  color: #0f172a;"
                "  font-size: 12px;"
                "  font-weight: 600;"
                "}"
                "QPushButton:hover { background: #e2e8f0; border-color: #475569; }"
                "QPushButton:pressed { background: #cbd5e1; }"
            )
        footer.addWidget(self._refresh_btn)
        footer.addWidget(self._close_btn)
        v.addLayout(footer)

        self._close_btn.clicked.connect(self.hide)

    # ------------------------------------------------------------------
    @property
    def window_days(self) -> int:
        return DEFAULT_WINDOW

    # ------------------------------------------------------------------
    def update_data(
        self,
        today: DayBucket,
        chart_buckets: Sequence[Any],
    ) -> None:
        """Refresh the stats grid (always today) and the chart (selected window)."""
        self._stat_total.set_value(fmt_tokens(today.total))
        self._stat_in.set_value(fmt_tokens(today.input_tokens))
        self._stat_out.set_value(fmt_tokens(today.output_tokens))
        self._stat_cache.set_value(fmt_tokens(today.cache_read_tokens))
        self._stat_sessions.set_value(str(len(today.sessions)))
        self._stat_events.set_value(str(today.events))
        self._updated_lbl.setText("Updated " + datetime.now().strftime("%H:%M:%S"))
        self._render_chart(chart_buckets)

    def _render_chart(self, buckets: Sequence[Any]) -> None:
        self._chart.removeAllSeries()
        for ax in list(self._chart.axes()):
            self._chart.removeAxis(ax)

        # Auto-scale: render bars in K or M tokens so the Y-axis labels fit.
        max_total = max((b.total for b in buckets), default=0)
        if max_total >= 1_000_000:
            scale, unit = 1_000_000.0, "M"
        elif max_total >= 1_000:
            scale, unit = 1_000.0, "K"
        else:
            scale, unit = 1.0, ""

        set_cache = QBarSet("Cached in")
        set_in = QBarSet("Uncached in")
        set_out = QBarSet("Output")
        set_cache.setColor(QColor("#94a3b8"))
        set_in.setColor(QColor("#2563eb"))
        set_out.setColor(QColor("#16a34a"))

        categories: list[str] = []
        for b in buckets:
            set_cache.append(b.cache_read_tokens / scale)
            set_in.append(b.input_tokens / scale)
            set_out.append(b.output_tokens / scale)
            categories.append(getattr(b, "short_label", ""))

        stacked = QStackedBarSeries()
        stacked.append(set_cache)
        stacked.append(set_in)
        stacked.append(set_out)
        stacked.setLabelsVisible(False)
        self._chart.addSeries(stacked)

        ax_x = QBarCategoryAxis()
        ax_x.append(categories)
        ax_x.setLabelsFont(QFont("Segoe UI", 9))
        ax_x.setLabelsColor(QColor("#0f172a"))
        ax_x.setGridLineVisible(False)
        ax_y = QValueAxis()
        # Short label format ("6M" instead of "6.0M") + small tick count so
        # the labels stay short and never ellipsize.
        ax_y.setLabelFormat(f"%g{unit}" if unit else "%d")
        scaled_max = (max_total / scale) if scale else 0
        ax_y.setRange(0, max(scaled_max * 1.1, 1.0))
        ax_y.setTickCount(5)
        ax_y.applyNiceNumbers()
        ax_y.setLabelsFont(QFont("Segoe UI", 9))
        ax_y.setLabelsColor(QColor("#0f172a"))
        # Bump min-label-width via a wider min size hint isn't supported by
        # QValueAxis directly; instead we reserved chart.margins(left=8) plus
        # the layout naturally grows the axis area when labels are added.
        self._chart.addAxis(ax_x, Qt.AlignmentFlag.AlignBottom)
        self._chart.addAxis(ax_y, Qt.AlignmentFlag.AlignLeft)
        stacked.attachAxis(ax_x)
        stacked.attachAxis(ax_y)

    # ------------------------------------------------------------------
    def show_near(self, anchor_global_pos) -> None:
        """Show this popup anchored near a global QPoint (e.g. tray icon)."""
        screen = QApplication.screenAt(anchor_global_pos) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w = self.width()
        h = self.height()
        x = anchor_global_pos.x() - w // 2
        y = anchor_global_pos.y() - h - 12  # above the tray
        # Clamp into screen.
        x = max(geo.left() + 8, min(x, geo.right() - w - 8))
        y = max(geo.top() + 8, min(y, geo.bottom() - h - 8))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    @property
    def refresh_button(self) -> QPushButton:
        return self._refresh_btn
