"""Popup window showing today's token usage and the last 7 days as a chart."""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCharts import (
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QStackedBarSeries,
    QValueAxis,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter
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
        self.resize(520, 400)

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

        # Chart
        self._chart = QChart()
        self._chart.setBackgroundRoundness(0)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
        self._chart_view = QChartView(self._chart)
        self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._chart_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        v.addWidget(self._chart_view, 1)

        # Footer buttons
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._refresh_btn = QPushButton("Refresh")
        self._close_btn = QPushButton("Close")
        for b in (self._refresh_btn, self._close_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton { padding:4px 12px; border-radius:6px;"
                " background:#f1f5f9; border:1px solid #cbd5e1; }"
                "QPushButton:hover { background:#e2e8f0; }"
            )
        footer.addWidget(self._refresh_btn)
        footer.addWidget(self._close_btn)
        v.addLayout(footer)

        self._close_btn.clicked.connect(self.hide)

    # ------------------------------------------------------------------
    def update_data(self, buckets: list[DayBucket]) -> None:
        if not buckets:
            return
        today = buckets[-1]
        self._stat_total.set_value(fmt_tokens(today.total))
        self._stat_in.set_value(fmt_tokens(today.input_tokens))
        self._stat_out.set_value(fmt_tokens(today.output_tokens))
        self._stat_cache.set_value(fmt_tokens(today.cache_read_tokens))
        self._stat_sessions.set_value(str(len(today.sessions)))
        self._stat_events.set_value(str(today.events))
        self._updated_lbl.setText("Updated " + datetime.now().strftime("%H:%M:%S"))
        self._render_chart(buckets)

    def _render_chart(self, buckets: list[DayBucket]) -> None:
        self._chart.removeAllSeries()
        for ax in list(self._chart.axes()):
            self._chart.removeAxis(ax)

        set_cache = QBarSet("Cached in")
        set_in = QBarSet("Uncached in")
        set_out = QBarSet("Output")
        set_cache.setColor(QColor("#94a3b8"))
        set_in.setColor(QColor("#2563eb"))
        set_out.setColor(QColor("#16a34a"))

        categories: list[str] = []
        max_total = 1
        for b in buckets:
            set_cache.append(b.cache_read_tokens)
            set_in.append(b.input_tokens)
            set_out.append(b.output_tokens)
            categories.append(b.day.strftime("%a\n%m/%d"))
            max_total = max(max_total, b.total)

        stacked = QStackedBarSeries()
        stacked.append(set_cache)
        stacked.append(set_in)
        stacked.append(set_out)
        stacked.setLabelsVisible(False)
        self._chart.addSeries(stacked)

        ax_x = QBarCategoryAxis()
        ax_x.append(categories)
        ax_x.setLabelsFont(QFont("Segoe UI", 8))
        ax_y = QValueAxis()
        ax_y.setLabelFormat("%d")
        ax_y.setRange(0, max(max_total * 1.1, 10))
        ax_y.applyNiceNumbers()
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
