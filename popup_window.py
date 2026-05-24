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
from PyQt6.QtCore import QEvent, QMargins, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from history_store import HistoryStore, TierStatus, Totals, UNKNOWN_LABEL
from usage_core import DayBucket, fmt_tokens

DEFAULT_WINDOW = 7  # Fixed look-back window in days


def _app_version() -> str:
    try:
        from _version import __version__
        return __version__
    except Exception:
        try:
            from importlib.metadata import version
            return version("tokentray")
        except Exception:
            return "0.0.0"


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
            "QTabWidget::pane { border: none; top: -1px; }"
            "QTabBar::tab { padding: 6px 14px; margin-right: 2px; "
            "  border: 1px solid #cbd5e1; border-bottom: none; "
            "  border-top-left-radius: 6px; border-top-right-radius: 6px; "
            "  background: #f1f5f9; color: #475569; font-size: 12px; "
            "  font-weight: 600; }"
            "QTabBar::tab:selected { background: #ffffff; color: #0f172a; }"
        )
        self.resize(640, 520)

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

        # Tab container
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_today_tab(), "Today")
        self._tabs.addTab(self._build_history_tab(), "History")
        # Advanced tab is registered now but built lazily on first activation
        # so the popup-open path stays fast even when the supporter has lots
        # of historical events to roll up.
        self._advanced_placeholder = QWidget()
        QVBoxLayout(self._advanced_placeholder)  # empty layout, replaced on demand
        self._tabs.addTab(self._advanced_placeholder, "Advanced")
        self._advanced_built = False
        self._advanced_period = "today"
        self._store: HistoryStore | None = None
        self._latest_tier: TierStatus | None = None
        self._tabs.currentChanged.connect(self._on_tab_changed)
        v.addWidget(self._tabs, 1)

        # Footer buttons (shared across tabs)
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
    def _build_today_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)

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
        return page

    # ------------------------------------------------------------------
    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)

        self._hist_banner = QLabel("History recording is initializing…")
        self._hist_banner.setWordWrap(True)
        self._hist_banner.setStyleSheet(
            "QLabel {"
            "  background:#eff6ff; border:1px solid #bfdbfe;"
            "  border-radius:6px; padding:8px 10px;"
            "  color:#1e3a8a; font-size:11px;"
            "}"
        )
        v.addWidget(self._hist_banner)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self._hist_today = _Stat("Today", "#2563eb")
        self._hist_week = _Stat("This week (Mon-Sun)", "#0f172a")
        self._hist_month = _Stat("This month", "#0f172a")
        self._hist_all = _Stat("All time", "#0f172a")
        self._hist_today_turns = _Stat("Today turns", "#475569")
        self._hist_week_turns = _Stat("Week turns", "#475569")
        self._hist_month_turns = _Stat("Month turns", "#475569")
        self._hist_all_turns = _Stat("All-time turns", "#475569")
        grid.addWidget(self._hist_today, 0, 0)
        grid.addWidget(self._hist_week, 0, 1)
        grid.addWidget(self._hist_month, 0, 2)
        grid.addWidget(self._hist_all, 0, 3)
        grid.addWidget(self._hist_today_turns, 1, 0)
        grid.addWidget(self._hist_week_turns, 1, 1)
        grid.addWidget(self._hist_month_turns, 1, 2)
        grid.addWidget(self._hist_all_turns, 1, 3)
        v.addLayout(grid)

        v.addStretch(1)

        privacy = QLabel(
            "Stored locally on this PC — never sent over the network. "
            "Manage in Settings → Advanced history."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color:#64748b; font-size:11px;")
        v.addWidget(privacy)
        return page

    # ------------------------------------------------------------------
    # Advanced tab (BMC-gated: per-host + per-model breakdowns).
    #
    # The widgets are constructed lazily on first activation so the cold
    # popup-open path doesn't pay for two QTableWidgets the user may never
    # look at. Once built, both the locked and unlocked states live in a
    # QStackedWidget; the gate just picks the right index at refresh time.
    # ------------------------------------------------------------------
    _PERIOD_LABELS = (
        ("today", "Today"),
        ("week", "Week"),
        ("month", "Month"),
        ("all_time", "All"),
    )

    def set_history_store(self, store: HistoryStore | None) -> None:
        """Inject the store the Advanced tab queries for breakdowns."""
        self._store = store

    def _on_tab_changed(self, idx: int) -> None:
        if idx != self._tabs.indexOf(self._advanced_placeholder):
            return
        if not self._advanced_built:
            self._build_advanced_tab_in_place()
            self._advanced_built = True
        self._refresh_advanced()

    def _build_advanced_tab_in_place(self) -> None:
        # Replace the placeholder's empty layout with the real Advanced UI.
        old_layout = self._advanced_placeholder.layout()
        if old_layout is not None:
            # Detach the placeholder layout so we can install the real one.
            QWidget().setLayout(old_layout)
        v = QVBoxLayout(self._advanced_placeholder)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)

        self._adv_stack = QStackedWidget()
        self._adv_locked = self._build_advanced_locked()
        self._adv_unlocked = self._build_advanced_unlocked()
        self._adv_stack.addWidget(self._adv_locked)
        self._adv_stack.addWidget(self._adv_unlocked)
        v.addWidget(self._adv_stack, 1)

    def _build_advanced_locked(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)
        v.addStretch(1)

        headline = QLabel("Per-tool and per-model breakdowns are locked.")
        headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        headline.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        v.addWidget(headline)

        body = QLabel(
            "TokenTray captures which host app (Clawpilot, Copilot CLI, "
            "Agency, VS Code) and which model produced every event. "
            "Buy me a coffee to unlock the breakdown tables on this tab."
        )
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setStyleSheet("color:#475569; font-size:12px;")
        v.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._adv_unlock_btn = QPushButton("Buy me a coffee to unlock ☕")
        self._adv_unlock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._adv_unlock_btn.setMinimumHeight(34)
        self._adv_unlock_btn.setStyleSheet(
            "QPushButton {"
            "  padding: 6px 18px;"
            "  border-radius: 6px;"
            "  background: #2563eb;"
            "  color: white;"
            "  border: none;"
            "  font-size: 12px;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background: #1d4ed8; }"
            "QPushButton:pressed { background: #1e40af; }"
        )
        self._adv_unlock_btn.clicked.connect(self._on_advanced_unlock_clicked)
        btn_row.addWidget(self._adv_unlock_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)
        v.addStretch(2)
        return page

    def _build_advanced_unlocked(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        # Period selector (Today / Week / Month / All)
        period_row = QHBoxLayout()
        period_row.setSpacing(6)
        period_lbl = QLabel("Period:")
        period_lbl.setStyleSheet("color:#475569; font-size:11px;")
        period_row.addWidget(period_lbl)
        self._adv_period_group = QButtonGroup(self)
        self._adv_period_group.setExclusive(True)
        for key, label in self._PERIOD_LABELS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("period_key", key)
            btn.setStyleSheet(
                "QPushButton {"
                "  padding: 4px 12px;"
                "  border-radius: 4px;"
                "  background: #f1f5f9;"
                "  border: 1px solid #cbd5e1;"
                "  color: #475569;"
                "  font-size: 11px;"
                "  font-weight: 600;"
                "}"
                "QPushButton:hover { background: #e2e8f0; }"
                "QPushButton:checked { background: #2563eb; color: white; border-color: #1d4ed8; }"
            )
            if key == self._advanced_period:
                btn.setChecked(True)
            self._adv_period_group.addButton(btn)
            period_row.addWidget(btn)
            btn.clicked.connect(
                lambda _checked, k=key: self._on_advanced_period_changed(k)
            )
        period_row.addStretch(1)
        v.addLayout(period_row)

        # Tables row: By tool used | By model
        tables_row = QHBoxLayout()
        tables_row.setSpacing(10)

        self._adv_host_table = self._make_breakdown_table("Tool used")
        host_box = self._wrap_table("By tool used", self._adv_host_table)
        tables_row.addWidget(host_box, 1)

        self._adv_model_table = self._make_breakdown_table("Model")
        model_box = self._wrap_table("By model", self._adv_model_table)
        tables_row.addWidget(model_box, 1)

        v.addLayout(tables_row, 1)
        return page

    @staticmethod
    def _make_breakdown_table(first_col_header: str) -> QTableWidget:
        t = QTableWidget(0, 3)
        t.setHorizontalHeaderLabels([first_col_header, "Tokens", "Turns"])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setStyleSheet(
            "QTableWidget { background: white; alternate-background-color: #f8fafc; "
            "  font-size: 11px; border: 1px solid #e2e8f0; border-radius: 4px; }"
            "QHeaderView::section { background: #f1f5f9; color: #475569; "
            "  font-size: 11px; font-weight: 600; padding: 4px 6px; "
            "  border: none; border-bottom: 1px solid #cbd5e1; }"
        )
        hdr = t.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        return t

    @staticmethod
    def _wrap_table(title: str, table: QTableWidget) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        lbl = QLabel(title)
        lbl.setStyleSheet("color:#0f172a; font-size:11px; font-weight:600;")
        v.addWidget(lbl)
        v.addWidget(table, 1)
        return box

    def _on_advanced_period_changed(self, key: str) -> None:
        self._advanced_period = key
        self._refresh_advanced()

    def _on_advanced_unlock_clicked(self) -> None:
        # Late import keeps coffee_dialog (and its Qt deps) out of the
        # popup-open hot path.
        if self._store is None:
            return
        from coffee_dialog import show_coffee_dialog

        outcome = show_coffee_dialog(self, self._store, reason="advanced_tab")
        if outcome == "unlocked":
            # Re-pull tier so the gate re-evaluates immediately.
            try:
                self._latest_tier = self._store.tier_status()
            except Exception:
                pass
            self._refresh_advanced()

    def _advanced_unlocked_now(self) -> bool:
        tier = self._latest_tier
        if tier is None or self._store is None:
            return False
        return bool(tier.advanced_enabled and tier.supporter_purchased)

    def _refresh_advanced(self) -> None:
        if not self._advanced_built:
            return
        unlocked = self._advanced_unlocked_now()
        self._adv_stack.setCurrentIndex(1 if unlocked else 0)
        if not unlocked or self._store is None:
            return
        try:
            by_host = self._store.totals_by_host(period=self._advanced_period)
            by_model = self._store.totals_by_model(period=self._advanced_period)
        except Exception:
            import traceback
            traceback.print_exc()
            return
        self._fill_breakdown_table(self._adv_host_table, by_host)
        self._fill_breakdown_table(self._adv_model_table, by_model)

    @staticmethod
    def _fill_breakdown_table(
        table: QTableWidget, data: dict[str, Totals]
    ) -> None:
        # Sort by tokens descending; UNKNOWN_LABEL sinks to the bottom so
        # the pre-Phase-3 backlog doesn't dominate the visual hierarchy.
        rows = sorted(
            data.items(),
            key=lambda kv: (kv[0] == UNKNOWN_LABEL, -kv[1].total),
        )
        table.setRowCount(len(rows))
        for r, (name, totals) in enumerate(rows):
            name_item = QTableWidgetItem(name or UNKNOWN_LABEL)
            tokens_item = QTableWidgetItem(fmt_tokens(totals.total))
            turns_item = QTableWidgetItem(f"{totals.events:,}")
            tokens_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            turns_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            table.setItem(r, 0, name_item)
            table.setItem(r, 1, tokens_item)
            table.setItem(r, 2, turns_item)

    # ------------------------------------------------------------------
    def event(self, e: QEvent) -> bool:
        # Light dismiss: hide when the popup loses activation (user clicked
        # somewhere else, switched apps, etc.).
        if e.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide()
        return super().event(e)

    # ------------------------------------------------------------------
    @property
    def window_days(self) -> int:
        return DEFAULT_WINDOW

    # ------------------------------------------------------------------
    def update_data(
        self,
        today: DayBucket,
        chart_buckets: Sequence[Any],
        *,
        history: dict[str, Totals] | None = None,
        tier: TierStatus | None = None,
    ) -> None:
        """Refresh the stats grid (always today) and the chart (selected window).

        ``history`` is the dict returned by ``HistoryStore.all_summaries()``;
        ``tier`` is the snapshot from ``HistoryStore.tier_status()``. Both are
        optional so the popup degrades gracefully if the store isn't ready.
        """
        self._stat_total.set_value(fmt_tokens(today.total))
        self._stat_in.set_value(fmt_tokens(today.input_tokens))
        self._stat_out.set_value(fmt_tokens(today.output_tokens))
        self._stat_cache.set_value(fmt_tokens(today.cache_read_tokens))
        self._stat_sessions.set_value(str(len(today.sessions)))
        self._stat_events.set_value(str(today.events))
        self._updated_lbl.setText("Updated " + datetime.now().strftime("%H:%M:%S"))
        self._render_chart(chart_buckets)
        self._update_history(history, tier)
        self._latest_tier = tier
        # Keep the Advanced tab in lockstep with periodic refreshes once it
        # has been opened at least once; first-open builds it on demand.
        if self._advanced_built:
            self._refresh_advanced()

    def _update_history(
        self,
        history: dict[str, Totals] | None,
        tier: TierStatus | None,
    ) -> None:
        if tier is not None:
            self._hist_banner.setText(tier.banner_text)
        else:
            self._hist_banner.setText("History store unavailable.")

        if history is None:
            for tile in (
                self._hist_today,
                self._hist_week,
                self._hist_month,
                self._hist_all,
                self._hist_today_turns,
                self._hist_week_turns,
                self._hist_month_turns,
                self._hist_all_turns,
            ):
                tile.set_value("--")
            return

        pairs = [
            (self._hist_today, self._hist_today_turns, history.get("today")),
            (self._hist_week, self._hist_week_turns, history.get("week")),
            (self._hist_month, self._hist_month_turns, history.get("month")),
            (self._hist_all, self._hist_all_turns, history.get("all_time")),
        ]
        for tile, turns_tile, totals in pairs:
            if totals is None:
                tile.set_value("--")
                turns_tile.set_value("--")
            else:
                tile.set_value(fmt_tokens(totals.total))
                turns_tile.set_value(str(totals.events))

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
