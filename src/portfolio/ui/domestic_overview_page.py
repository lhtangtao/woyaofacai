"""国内资产 · 资产总览页面：风格与美股资产总览完全对齐，合并基金与同花顺 A 股严格估值。"""
from __future__ import annotations

import calendar
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domestic.overview import build_domestic_overview
from portfolio.ui.metric_card import MetricCard
from portfolio.ui.report_page import ReportPage
from portfolio.ui.theme import tokens


def _fmt_money(val: Decimal | None, *, signed: bool = False) -> str:
    if val is None:
        return "—"
    if signed:
        return f"{val:+,.2f}"
    return f"{val:,.2f}"


def _fmt_pct(val: Decimal | None, *, signed: bool = False) -> str:
    if val is None:
        return "—"
    if signed:
        return f"{val:+.2%}"
    return f"{val:.2%}"


def _tint_cell(item: QTableWidgetItem, val: Decimal | None, widget: QWidget) -> None:
    t = tokens(widget)
    if val is None or val == 0:
        item.setForeground(QColor(t.muted if val is None else t.heading))
        return
    if val > 0:
        item.setForeground(QColor(t.profit))
        item.setBackground(QColor(t.buy_bg))
    else:
        item.setForeground(QColor(t.loss))
        item.setBackground(QColor(t.sell_bg))


class DomesticOverviewPage(QWidget):
    """国内资产总览页：18 KPI 卡片 + 收益交易日历 + 本年每月/季度矩阵 + 4 个底部汇总页签。"""

    jump_to_day = Signal(str)

    def __init__(self, host: Any, parent=None):
        super().__init__(parent)
        self.host = host
        self._data: dict[str, Any] | None = None
        self._loading = False
        self._loaded_once = False
        self.cards: list[MetricCard] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # 筛选栏（无人员下拉框，主窗口顶部已有页面标题与全局刷新）
        filter_bar = QFrame()
        filter_bar.setObjectName("card")
        f_layout = QHBoxLayout(filter_bar)
        f_layout.setContentsMargins(12, 8, 12, 8)
        f_layout.setSpacing(10)

        f_layout.addWidget(QLabel("平台/渠道"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("全部渠道")
        self.platform_combo.setMinimumWidth(130)
        self.platform_combo.currentTextChanged.connect(lambda *_: self._reload_filtered())
        f_layout.addWidget(self.platform_combo)

        f_layout.addWidget(QLabel("资产类型"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("全部类型")
        self.type_combo.setMinimumWidth(150)
        self.type_combo.currentTextChanged.connect(lambda *_: self._reload_filtered())
        f_layout.addWidget(self.type_combo)

        self.reset_btn = QPushButton("重置筛选")
        self.reset_btn.clicked.connect(self._reset_filters)
        f_layout.addWidget(self.reset_btn)
        f_layout.addStretch(1)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("secondary")
        f_layout.addWidget(self.status_lbl)
        outer.addWidget(filter_bar)

        # 主滚动内容区
        scroll = QScrollArea()
        scroll.setStyleSheet("QScrollArea { border: 0; }")
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)

        # 18 张 KPI 卡片
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        for _ in range(18):
            card = MetricCard("—")
            card.setMinimumHeight(94)
            card.setObjectName("metricCard")
            self.cards.append(card)
        body.addLayout(self.grid)
        self._arrange()

        # 中间：左 收益交易日历 + 右 本年每月盈亏
        calendars = QHBoxLayout()
        calendars.setSpacing(16)
        daily_panel, yearly_panel = QWidget(), QWidget()
        daily_layout = QVBoxLayout(daily_panel)
        yearly_layout = QVBoxLayout(yearly_panel)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        yearly_layout.setContentsMargins(0, 0, 0, 0)
        calendars.addWidget(daily_panel, 3)
        calendars.addWidget(yearly_panel, 2)
        body.addLayout(calendars)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("收益交易日历"))
        self.month_combo = QComboBox()
        self.month_combo.setMinimumWidth(160)
        month_view = QListView(self.month_combo)
        month_view.setMinimumWidth(180)
        month_view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.month_combo.setView(month_view)
        self.month_combo.setMaxVisibleItems(12)
        self.month_combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.month_combo.currentTextChanged.connect(self._render_calendar)
        controls.addWidget(self.month_combo)
        controls.addStretch()
        daily_layout.addLayout(controls)

        self.calendar_table = QTableWidget(0, 6)
        self.calendar_table.setHorizontalHeaderLabels(["周一", "周二", "周三", "周四", "周五", "本周合计"])
        self.calendar_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.calendar_table.verticalHeader().hide()
        self.calendar_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.calendar_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar_table.setFixedHeight(336)
        self.calendar_table.setStyleSheet("QTableView::item { padding:0 4px; } QHeaderView::section { padding:0 4px; }")
        self.calendar_table.cellClicked.connect(self._on_calendar_clicked)
        daily_layout.addWidget(self.calendar_table)

        daily_note = QLabel("点击日期可联动跳转至「国内资产 · 每日持仓收益」查看当日明细；无数据日留白，不当作零。")
        daily_note.setObjectName("secondary")
        daily_note.setWordWrap(True)
        daily_layout.addWidget(daily_note)
        daily_layout.addStretch()

        annual_heading = QHBoxLayout()
        self.annual_title = QLabel("本年每月盈亏")
        annual_heading.addWidget(self.annual_title)
        self.year_combo = QComboBox()
        self.year_combo.setMinimumWidth(120)
        year_view = QListView(self.year_combo)
        year_view.setMinimumWidth(140)
        self.year_combo.setView(year_view)
        self.year_combo.setStyleSheet("QComboBox { combobox-popup: 0; }")
        self.year_combo.currentTextChanged.connect(self._render_annual_table)
        annual_heading.addWidget(self.year_combo)
        annual_heading.addStretch()
        yearly_layout.addLayout(annual_heading)

        self.monthly_table = QTableWidget(4, 4)
        self.monthly_table.setStyleSheet("QTableView::item { padding:0 4px; }")
        self.monthly_table.horizontalHeader().hide()
        self.monthly_table.setVerticalHeaderLabels(["一季度", "二季度", "三季度", "四季度"])
        self.monthly_table.verticalHeader().setFixedWidth(60)
        self.monthly_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.monthly_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.monthly_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.monthly_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.monthly_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.monthly_table.setFixedHeight(336)
        yearly_layout.addWidget(self.monthly_table)

        self.annual_total = QLabel("年度合计：—")
        yearly_layout.addWidget(self.annual_total)
        annual_note = QLabel("按已有每日及月度严格估值汇总；无数据不当作零。")
        annual_note.setObjectName("secondary")
        annual_note.setWordWrap(True)
        yearly_layout.addWidget(annual_note)

        # 底部 4 个汇总视图（交互与美股资产总览完全一致，且全部居中展示）
        self.summaries = ReportPage(("平台汇总", "分类汇总", "季度矩阵", "月度趋势"), show_details=False, center_aligned=True)
        self.summaries.expand_rows = True
        self.summaries.setMinimumHeight(480)
        if hasattr(self.host, "export_report"):
            self.summaries.export_requested.connect(self.host.export_report)
        body.addWidget(self.summaries)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

    def _arrange(self):
        columns = 6 if self.width() >= 1160 else 3 if self.width() >= 720 else 2
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._loaded_once and not self._loading:
            self.reload()

    def _reset_filters(self):
        self.platform_combo.blockSignals(True)
        self.type_combo.blockSignals(True)
        self.platform_combo.setCurrentIndex(0)
        self.type_combo.setCurrentIndex(0)
        self.platform_combo.blockSignals(False)
        self.type_combo.blockSignals(False)
        self._reload_filtered()

    def _reload_filtered(self):
        self.reload(force=True)

    def reload(self, *, force: bool = False):
        if self._loading and not force:
            return
        self._loading = True
        self.status_lbl.setText("正在加载国内资产总览…")
        for card in self.cards:
            card.setText("计算中…")
        sel_platform = self.platform_combo.currentText()
        sel_type = self.type_combo.currentText()

        def task(_cancel):
            return build_domestic_overview(
                platform=None if sel_platform == "全部渠道" else sel_platform,
                asset_type=None if sel_type == "全部类型" else sel_type,
            )

        def ready(result: dict[str, Any]):
            self._loading = False
            self._loaded_once = True
            self._data = result
            self._sync_filter_options(result)
            self._render_all(result)

        if hasattr(self.host, "_submit"):
            self.host._submit("domestic_overview", task, ready)
        else:
            ready(task(lambda: False))

    def _sync_filter_options(self, data: dict[str, Any]):
        cur_p = self.platform_combo.currentText()
        cur_t = self.type_combo.currentText()

        self.platform_combo.blockSignals(True)
        self.platform_combo.clear()
        self.platform_combo.addItem("全部渠道")
        for p in data.get("platforms", []):
            self.platform_combo.addItem(p)
        idx_p = self.platform_combo.findText(cur_p)
        self.platform_combo.setCurrentIndex(max(0, idx_p))
        self.platform_combo.blockSignals(False)

        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        self.type_combo.addItems(["全部类型", "基金", "股票"])
        for t_name in data.get("asset_types", []):
            if t_name not in ("基金", "股票"):
                self.type_combo.addItem(t_name)
        idx_t = self.type_combo.findText(cur_t)
        self.type_combo.setCurrentIndex(max(0, idx_t))
        self.type_combo.blockSignals(False)

    def _render_all(self, data: dict[str, Any]):
        latest = data.get("latest_day") or "—"
        self.status_lbl.setText(f"最新已确认估值日：{latest}")
        t = tokens(self)

        # 1. 18 张 KPI 卡片
        for card, kpi in zip(self.cards, data.get("kpis", [])):
            val: Decimal | None = kpi.get("value")
            is_ratio = bool(kpi.get("ratio"))
            neutral = bool(kpi.get("neutral"))
            if val is None:
                text_val = "—"
                color = t.muted
            elif neutral:
                text_val = _fmt_pct(val) if is_ratio else f"¥{_fmt_money(val)}"
                color = t.heading
            else:
                text_val = _fmt_pct(val, signed=True) if is_ratio else f"{'+' if val > 0 else '−' if val < 0 else ''}¥{_fmt_money(abs(val))}"
                color = t.profit if val > 0 else t.loss if val < 0 else t.muted
            card.setText(f"{kpi['name']}\n{text_val}\n{kpi.get('day', '—')}")
            card.setToolTip(kpi.get("formula", ""))
            card.setStyleSheet(
                f"QPushButton {{text-align:left; padding:12px; font-size:14px; border-radius:12px; "
                f"border:1px solid {t.border}; background:{t.panel}; color:{color};}}"
            )

        # 2. 月份 & 年份下拉框
        months = data.get("months", [])
        years = data.get("years", [])
        sel_m = self.month_combo.currentText()
        sel_y = self.year_combo.currentText()

        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        self.month_combo.addItems(months)
        if sel_m in months:
            self.month_combo.setCurrentText(sel_m)
        self.month_combo.blockSignals(False)

        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItems(years)
        if sel_y in years:
            self.year_combo.setCurrentText(sel_y)
        self.year_combo.blockSignals(False)
        self.year_combo.setEnabled(bool(years))

        self._render_calendar()
        self._render_annual_table()
        self._render_summaries(data)

    def _render_calendar(self, *_):
        if not self._data or not self.month_combo.currentText():
            self.calendar_table.setRowCount(0)
            return
        day_agg = self._data.get("day_agg", {})
        y, m = map(int, self.month_combo.currentText().split("-"))
        weeks = calendar.Calendar().monthdatescalendar(y, m)
        self.calendar_table.setRowCount(len(weeks))
        row_h = max(64, self.calendar_table.fontMetrics().lineSpacing() * 3 + 12, 332 // max(1, len(weeks)))
        t = tokens(self)

        for r_idx, week in enumerate(weeks):
            self.calendar_table.setRowHeight(r_idx, row_h)
            week_vals: list[Decimal] = []
            for c_idx, day_obj in enumerate(week[:5]):
                iso = day_obj.isoformat()
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if day_obj.month != m:
                    item.setText("")
                    item.setForeground(QColor(t.muted))
                else:
                    info = day_agg.get(iso)
                    if info and info.get("pnl") is not None:
                        pnl: Decimal = info["pnl"]
                        rate: Decimal | None = info.get("rate")
                        week_vals.append(pnl)
                        rate_str = _fmt_pct(rate, signed=True) if rate is not None else "—"
                        item.setText(f"{day_obj.day}\n{_fmt_money(pnl, signed=True)}\n{rate_str}")
                        item.setData(Qt.ItemDataRole.UserRole, iso)
                        _tint_cell(item, pnl, self)
                    else:
                        item.setText(f"{day_obj.day}\n—")
                        item.setForeground(QColor(t.muted))
                self.calendar_table.setItem(r_idx, c_idx, item)

            # 第 6 列：本周合计
            total_item = QTableWidgetItem()
            total_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if week_vals:
                w_sum = sum(week_vals, Decimal(0))
                total_item.setText(f"周合计\n{_fmt_money(w_sum, signed=True)}")
                _tint_cell(total_item, w_sum, self)
            else:
                total_item.setText("—")
                total_item.setForeground(QColor(t.muted))
            self.calendar_table.setItem(r_idx, 5, total_item)

    def _on_calendar_clicked(self, row: int, col: int):
        if col >= 5:
            return
        item = self.calendar_table.item(row, col)
        if not item:
            return
        iso = item.data(Qt.ItemDataRole.UserRole)
        if iso:
            self.jump_to_day.emit(str(iso))

    def _render_annual_table(self, *_):
        self.monthly_table.clearContents()
        if not self._data or not self.year_combo.currentText():
            self.annual_title.setText("本年每月盈亏")
            self.annual_total.setText("年度合计：—")
            return
        y_str = self.year_combo.currentText()
        self.annual_title.setText(f"{y_str} 年每月盈亏")
        month_agg = self._data.get("month_agg", {})
        year_agg = self._data.get("year_agg", {})
        t = tokens(self)

        for q_idx in range(4):
            q_vals: list[Decimal] = []
            for m_offset in range(3):
                m_num = q_idx * 3 + m_offset + 1
                m_key = f"{y_str}-{m_num:02d}"
                info = month_agg.get(m_key)
                item = QTableWidgetItem()
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if info and info.get("pnl") is not None:
                    pnl: Decimal = info["pnl"]
                    rate: Decimal | None = info.get("rate")
                    q_vals.append(pnl)
                    r_str = _fmt_pct(rate, signed=True) if rate is not None else "—"
                    item.setText(f"{m_num}月\n{_fmt_money(pnl, signed=True)}\n{r_str}")
                    _tint_cell(item, pnl, self)
                else:
                    item.setText(f"{m_num}月\n—")
                    item.setForeground(QColor(t.muted))
                self.monthly_table.setItem(q_idx, m_offset, item)

            q_item = QTableWidgetItem()
            q_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if q_vals:
                q_sum = sum(q_vals, Decimal(0))
                q_item.setText(f"Q{q_idx + 1}合计\n{_fmt_money(q_sum, signed=True)}")
                _tint_cell(q_item, q_sum, self)
            else:
                q_item.setText("—")
                q_item.setForeground(QColor(t.muted))
            self.monthly_table.setItem(q_idx, 3, q_item)

        y_info = year_agg.get(y_str, {})
        y_pnl = y_info.get("pnl")
        y_rate = y_info.get("rate")
        if y_pnl is not None:
            r_str = f"（{_fmt_pct(y_rate, signed=True)}）" if y_rate is not None else ""
            self.annual_total.setText(f"年度合计：{_fmt_money(y_pnl, signed=True)} CNY {r_str}")
        else:
            self.annual_total.setText("年度合计：—")

    def _render_summaries(self, data: dict[str, Any]):
        def _group_report(report_name: str, first_col: str, rows: list[dict[str, Any]]) -> Report:
            cols = (first_col, "当前资产(¥)", "持仓成本(¥)", "持仓数", "资产占比", "已清仓收益(¥)",
                    "浮动盈亏(¥)", "本年盈亏(¥)", "总盈亏(¥)", "盈利数", "亏损数", "回本缺口(¥)")
            r_rows = tuple(
                ReportRow(
                    (
                        r["name"],
                        r["asset"],
                        r["cost"],
                        r["open_count"],
                        r["weight"],
                        r["closed_pnl"],
                        r["floating_pnl"],
                        r["year_pnl"],
                        r["total_pnl"],
                        r["win_count"],
                        r["loss_count"],
                        r["gap"],
                    ),
                    {},
                )
                for r in rows
            )
            return Report(report_name, cols, r_rows)

        q_cols = ("年份", "一季度盈亏(¥)", "二季度盈亏(¥)", "三季度盈亏(¥)", "四季度盈亏(¥)", "全年盈亏(¥)", "全年收益率")
        q_rows = tuple(
            ReportRow(
                (r["year"], r["q1"], r["q2"], r["q3"], r["q4"], r["year_pnl"], r["year_rate"]),
                {},
            )
            for r in data.get("quarterly_rows", [])
        )

        m_cols = ("月份", "月初市值(¥)", "月末市值(¥)", "当月买入(¥)", "当月卖出(¥)", "当月净投入(¥)", "当月收益(¥)", "当月收益率", "累计收益(¥)")
        m_rows = tuple(
            ReportRow(
                (
                    r["month"],
                    r["begin_mv"],
                    r["end_mv"],
                    r["buy_amount"],
                    r["sell_amount"],
                    r["net_flow"],
                    r["pnl"],
                    r["rate"],
                    r["cum_pnl"],
                ),
                {},
            )
            for r in data.get("monthly_trend", [])
        )

        self.summaries.set_reports({
            "平台汇总": _group_report("平台汇总", "平台", data.get("platform_summary", [])),
            "分类汇总": _group_report("分类汇总", "分类", data.get("category_summary", [])),
            "季度矩阵": Report("季度矩阵", q_cols, q_rows),
            "月度趋势": Report("月度趋势", m_cols, m_rows),
        })
