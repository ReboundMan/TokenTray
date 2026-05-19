"""Popup window showing today's token usage and a switchable 1/7/30-day chart."""
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
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from usage_core import DayBucket, HourBucket, fmt_tokens

WINDOWS = (1, 7, 30)  # Selectable look-back windows in days
DEFAULT_WINDOW = 7


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

    # Emitted when the user changes the chart look-back (1 / 7 / 30 days).
    window_changed = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._window_days = DEFAULT_WINDOW
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

        # Segmented look-back selector (1 / 7 / 30 days)
        seg_row = QHBoxLayout()
        seg_label = QLabel("Range:")
        seg_label.setStyleSheet("color:#64748b; font-size:11px;")
        seg_row.addWidget(seg_label)
        self._window_group = QButtonGroup(self)
        self._window_group.setExclusive(True)
        for days in WINDOWS:
            label = f"{days} day" if days == 1 else f"{days} days"
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(24)
            btn.setStyleSheet(self._segmented_qss())
            if days == DEFAULT_WINDOW:
                btn.setChecked(True)
            self._window_group.addButton(btn, days)
            seg_row.addWidget(btn)
        seg_row.addStretch(1)
        v.addLayout(seg_row)
        self._window_group.idClicked.connect(self._on_window_clicked)

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
    @staticmethod
    def _segmented_qss() -> str:
        return (
            "QPushButton {"
            "  padding: 3px 12px;"
            "  border: 1px solid #94a3b8;"
            "  border-radius: 4px;"
            "  background: #ffffff;"
            "  color: #0f172a;"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background: #f1f5f9; }"
            "QPushButton:checked {"
            "  background: #2563eb;"
            "  color: #ffffff;"
            "  border-color: #1e40af;"
            "}"
        )

    def _on_window_clicked(self, days: int) -> None:
        if days == self._window_days:
            return
        self._window_days = days
        self.window_changed.emit(days)

    @property
    def window_days(self) -> int:
        return self._window_days

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

        # Pick label style based on count so they fit horizontally.
        n = len(buckets)
        if n <= 7:
            label_attr = "label"
            stride = 1
        elif n <= 14:
            label_attr = "short_label"
            stride = 1
        else:
            # 30-day daily or 24-hour: thin labels to every other / every Nth.
            label_attr = "short_label" if isinstance(buckets[0], DayBucket) else "label"
            stride = max(1, n // 10)

        categories: list[str] = []
        for idx, b in enumerate(buckets):
            set_cache.append(b.cache_read_tokens / scale)
            set_in.append(b.input_tokens / scale)
            set_out.append(b.output_tokens / scale)
            raw = getattr(b, label_attr, "")
            categories.append(raw if (idx % stride == 0) else "")

        stacked = QStackedBarSeries()
        stacked.append(set_cache)
        stacked.append(set_in)
        stacked.append(set_out)
        stacked.setLabelsVisible(False)
        self._chart.addSeries(stacked)

        ax_x = QBarCategoryAxis()
        ax_x.append(categories)
        ax_x.setLabelsFont(QFont("Segoe UI", 9))
        ax_y = QValueAxis()
        ax_y.setLabelFormat(f"%.1f{unit}" if unit else "%d")
        scaled_max = (max_total / scale) if scale else 0
        ax_y.setRange(0, max(scaled_max * 1.1, 1.0))
        ax_y.applyNiceNumbers()
        ax_y.setLabelsFont(QFont("Segoe UI", 9))
        if unit:
            ax_y.setTitleText(f"Tokens ({unit})")
            ax_y.setTitleFont(QFont("Segoe UI", 9))
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
