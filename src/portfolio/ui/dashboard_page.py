import calendar
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import strict_sum
from portfolio.engine.dashboard import (
    annual_rate,
    compound_rates,
    daily_rates,
    live_daily_rows,
    metric_gap,
    monthly_gains,
    monthly_rates,
    quarterly_gains,
    quarterly_rates,
)
from portfolio.paths import quote_cache_path
from portfolio.services.channel_cash import (
    is_cash_visible_scope,
    resolve_channel_cash_details,
)
from portfolio.services.quote_store import QuoteStore
from portfolio.ui.metric_card import MetricCard
from portfolio.ui.report_page import ReportPage
from portfolio.ui.theme import numeric_font, tokens

_CHANNEL_DISPLAY = (
    ("LONGBRIDGE", "长桥"),
    ("SCHWAB", "嘉信"),
    ("BBAE", "BBAE"),
    ("FIRSTRADE", "第一证券"),
)
_MANUAL_ANCHOR_CHANNELS = (
    ("BBAE", "BBAE"),
    ("FIRSTRADE", "第一证券"),
)


class CashAnchorDialog(QDialog):
    """手动校准 BBAE / 第一证券现金余额锚点弹窗（仅存本地行情缓存库，不改原始账本）。"""

    def __init__(self, store: QuoteStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("校准券商现金锚点（BBAE / 第一证券）")
        self.setMinimumWidth(460)
        anchors = store.load_cash_anchors()
        now_bj_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "长桥与嘉信通过券商 API 自动获取实时现金；BBAE 与第一证券无开放余额接口，"
            "请输入记录现金余额的北京时间（精确到时分秒，如 2026-11-23 18:45:00），"
            "系统将精确叠加该时刻之后的每一笔成交持续推算。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #475569; font-size: 12px;")
        layout.addWidget(intro)

        self._inputs: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        for code, label in _MANUAL_ANCHOR_CHANNELS:
            box = QFrame(self)
            box.setStyleSheet("QFrame { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; }")
            form = QFormLayout(box)
            form.setContentsMargins(12, 10, 12, 10)
            form.setSpacing(8)

            hdr = QLabel(f"{label}（{code}）")
            hdr.setStyleSheet("font-weight: 700; font-size: 13px; color: #0F172A; border: none; background: transparent;")
            form.addRow(hdr)

            cur = anchors.get(code, {})
            date_edit = QLineEdit(str(cur.get("anchor_date") or now_bj_str), box)
            date_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS（北京时间）")
            amt_val = cur.get("amount")
            amt_edit = QLineEdit(f"{amt_val:.2f}" if isinstance(amt_val, Decimal) else "", box)
            amt_edit.setPlaceholderText("例如 248.00（留空则清除该渠道锚点）")

            form.addRow("锚点时间 (北京)：", date_edit)
            form.addRow("现金余额 (USD)：", amt_edit)
            self._inputs[code] = (date_edit, amt_edit)
            layout.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并重算")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self):
        parsed: list[tuple[str, str, Decimal | None]] = []
        for code, (date_edit, amt_edit) in self._inputs.items():
            raw_amt = amt_edit.text().strip().replace(",", "").replace("$", "")
            raw_date = date_edit.text().strip()
            if not raw_amt:
                parsed.append((code, raw_date, None))
                continue
            try:
                if len(raw_date) == 10:
                    date.fromisoformat(raw_date)
                else:
                    datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                QMessageBox.warning(
                    self,
                    "时间格式错误",
                    f"{code} 的锚点时间格式应为 YYYY-MM-DD HH:MM:SS（或 YYYY-MM-DD）。",
                )
                return
            try:
                val = Decimal(raw_amt)
            except InvalidOperation:
                QMessageBox.warning(self, "金额格式错误", f"{code} 的现金余额请输入有效数字。")
                return
            parsed.append((code, raw_date, val))

        for code, anchor_date, val in parsed:
            if val is None:
                self.store.delete_cash_anchor(code)
            else:
                self.store.save_cash_anchor(code, anchor_date, val, currency="USD", note="用户手动校准现金锚点")
        self.accept()


class CashSummaryBanner(QFrame):
    """资产总览顶部「账户现金与仓位概览」卡片（仅个人与全部人员视角可见）。"""

    calibrate_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CashSummaryBanner")
        self.setStyleSheet("""
            QFrame#CashSummaryBanner {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 12px;
            }
        """)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(16)

        # 左侧总览指标：账户总现金 / 总权益 / 股票仓位
        self.lbl_total_cash = QLabel("账户总现金\n—")
        self.lbl_total_equity = QLabel("总权益(市值+现金)\n—")
        self.lbl_position_ratio = QLabel("当前股票仓位\n—")
        for lbl in (self.lbl_total_cash, self.lbl_total_equity, self.lbl_position_ratio):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet("border: none; background: transparent;")
            outer.addWidget(lbl)

        sep = QFrame(self)
        sep.setFixedWidth(1)
        sep.setStyleSheet("background: #E2E8F0; border: none;")
        outer.addWidget(sep)

        # 中间 4 家券商分渠道现金胶囊
        self.channel_labels: dict[str, QLabel] = {}
        ch_wrap = QHBoxLayout()
        ch_wrap.setSpacing(10)
        for code, name in _CHANNEL_DISPLAY:
            pill = QLabel(self)
            pill.setTextFormat(Qt.TextFormat.RichText)
            pill.setStyleSheet(
                "background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; "
                "padding: 6px 10px; font-size: 12px; color: #0F172A;"
            )
            self.channel_labels[code] = pill
            ch_wrap.addWidget(pill)
        outer.addLayout(ch_wrap, 1)

        # 右侧校准按钮
        self.calibrate_btn = QPushButton("校准现金锚点", self)
        self.calibrate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.calibrate_btn.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE;
                border-radius: 8px; padding: 6px 12px; font-size: 12px; font-weight: 600;
            }
            QPushButton:hover { background: #DBEAFE; border-color: #93C5FD; }
        """)
        self.calibrate_btn.clicked.connect(self.calibrate_clicked.emit)
        outer.addWidget(self.calibrate_btn)

    def update_cash(self, analytics, stock_market_value: Decimal | None):
        if analytics is None or not is_cash_visible_scope(getattr(analytics, "scope", None)):
            self.hide()
            return

        details = resolve_channel_cash_details(analytics)
        self.show()

        total_cash = sum((item["amount"] for item in details.values() if item.get("amount") is not None), Decimal("0"))
        has_any_cash = bool(details)

        if has_any_cash:
            c_col = "#DC2626" if total_cash < 0 else "#0F172A"
            c_txt = f"${total_cash:+,.2f}" if total_cash < 0 else f"${total_cash:,.2f}"
        else:
            c_col, c_txt = "#94A3B8", "暂无快照"
        self.lbl_total_cash.setText(
            f"<div style='font-size:11px;color:#64748B;'>账户总现金 (USD)</div>"
            f"<div style='font-size:16px;font-weight:700;color:{c_col};margin-top:2px;'>{c_txt}</div>"
        )

        if has_any_cash and stock_market_value is not None:
            equity = stock_market_value + total_cash
            eq_txt = f"${equity:,.2f}"
            if equity > 0:
                pos_pct = (stock_market_value / equity) * Decimal("100")
                pos_col = "#DC2626" if pos_pct > Decimal("100") else "#2563EB"
                pos_suffix = " (含融资)" if pos_pct > Decimal("100") else ""
                pos_txt = f"{pos_pct:.1f}%{pos_suffix}"
            else:
                pos_col, pos_txt = "#64748B", "—"
        else:
            eq_txt = "—"
            pos_col, pos_txt = "#64748B", "—"

        self.lbl_total_equity.setText(
            f"<div style='font-size:11px;color:#64748B;'>总权益 (市值+现金)</div>"
            f"<div style='font-size:16px;font-weight:700;color:#0F172A;margin-top:2px;'>{eq_txt}</div>"
        )
        self.lbl_position_ratio.setText(
            f"<div style='font-size:11px;color:#64748B;'>当前股票仓位</div>"
            f"<div style='font-size:16px;font-weight:700;color:{pos_col};margin-top:2px;'>{pos_txt}</div>"
        )

        for code, name in _CHANNEL_DISPLAY:
            pill = self.channel_labels[code]
            entry = details.get(code)
            if entry and entry.get("amount") is not None:
                amt = entry["amount"]
                src = entry.get("source") or ""
                col = "#DC2626" if amt < 0 else "#16A34A" if amt > 0 else "#0F172A"
                amt_txt = f"${amt:+,.2f}" if amt < 0 else f"${amt:,.2f}"
                src_badge = f"<span style='font-size:10px;color:#64748B;margin-left:4px;'>{src}</span>"
                pill.setText(
                    f"<div><b style='color:#0F172A;'>{name}</b>{src_badge}</div>"
                    f"<div style='font-size:13px;font-weight:700;color:{col};margin-top:2px;'>{amt_txt}</div>"
                )
                if src == "锚点推算":
                    pill.setToolTip(
                        f"{name} 锚点推算现金：{amt_txt}\n"
                        f"基准日期：{entry.get('anchor_date')}\n"
                        f"基准金额：${entry.get('anchor_amount'):,.2f}\n"
                        f"后续叠加成交：{entry.get('trades_applied', 0)} 笔"
                    )
                else:
                    pill.setToolTip(f"{name} 券商 API 实时现金：{amt_txt}\n更新时间：{entry.get('updated_at') or '—'}")
            else:
                hint = "待校准" if code in ("BBAE", "FIRSTRADE") else "待拉取"
                pill.setText(
                    f"<div><b style='color:#0F172A;'>{name}</b>"
                    f"<span style='font-size:10px;color:#94A3B8;margin-left:4px;'>{hint}</span></div>"
                    f"<div style='font-size:13px;font-weight:600;color:#94A3B8;margin-top:2px;'>—</div>"
                )
                pill.setToolTip(f"{name} 暂无现金数据，可点击右侧「校准现金锚点」设置。")


class DashboardPage(QWidget):
    drill = Signal(str, str)
    cash_anchors_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.daily_rows, self.live_day = (), None
        self.cards, self.widgets = [], []
        self._calendar_display_mode = "both"  # "amount" | "rate" | "both"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setStyleSheet('QScrollArea { border: 0; }')
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self.cash_banner = CashSummaryBanner(content)
        self.cash_banner.calibrate_clicked.connect(self._open_cash_anchor_dialog)
        self.cash_banner.hide()
        body.addWidget(self.cash_banner)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        for index in range(18):
            card = MetricCard("—")
            card.setMinimumHeight(94)
            card.setObjectName("metricCard")
            card.clicked.connect(lambda checked=False, n=index: self.drill.emit("metric", str(n)))
            self.cards.append(card)
        body.addLayout(self.grid)
        self._arrange()
        calendars = QHBoxLayout()
        calendars.setSpacing(16)
        daily_panel, yearly_panel = QWidget(), QWidget()
        daily_layout, yearly_layout = QVBoxLayout(daily_panel), QVBoxLayout(yearly_panel)
        for layout in (daily_layout, yearly_layout):
            layout.setContentsMargins(0, 0, 0, 0)
        calendars.addWidget(daily_panel, 3)
        calendars.addWidget(yearly_panel, 2)
        body.addLayout(calendars)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("收益交易日历"))
        self.month = QComboBox()
        self.month.setMinimumWidth(160)
        self.month.setMinimumContentsLength(9)
        self.month.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        month_view = QListView()
        month_view.setMinimumWidth(180)
        month_view.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.month.setView(month_view)
        self.month.setMaxVisibleItems(12)
        self.month.setStyleSheet('QComboBox { combobox-popup: 0; }')
        self.month.currentTextChanged.connect(self.calendar)
        controls.addWidget(self.month)
        controls.addStretch()
        self._cal_mode_btns: dict[str, QPushButton] = {}
        for mode_key, mode_lbl in (("amount", "金额"), ("rate", "收益率"), ("both", "都展示")):
            btn = QPushButton(mode_lbl, daily_panel)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=mode_key: self.set_calendar_display_mode(k))
            self._cal_mode_btns[mode_key] = btn
            controls.addWidget(btn)
        self._update_calendar_mode_styles()
        daily_layout.addLayout(controls)
        self.calendar_table = QTableWidget(0, 6)
        self.calendar_table.setHorizontalHeaderLabels(["周一", "周二", "周三", "周四", "周五", "本周合计"])
        self.calendar_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.calendar_table.verticalHeader().hide()
        self.calendar_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.calendar_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.calendar_table.setFixedHeight(336)
        self.calendar_table.setStyleSheet('QTableView::item { padding:0 4px; } QHeaderView::section { padding:0 4px; }')
        self.calendar_table.cellClicked.connect(self.calendar_clicked)
        self.calendar_table.viewport().installEventFilter(self)
        daily_layout.addWidget(self.calendar_table)
        daily_note = QLabel('点击日期查看收益；实时估值计入日、周、月汇总，收盘记录更新后自动替换。')
        daily_note.setObjectName('secondary')
        daily_note.setWordWrap(True)
        daily_layout.addWidget(daily_note)
        annual_heading = QHBoxLayout()
        self.annual_title = QLabel('本年每月盈亏')
        annual_heading.addWidget(self.annual_title)
        self.year = QComboBox()
        self.year.setMinimumWidth(120)
        self.year.setMinimumContentsLength(6)
        self.year.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        year_view = QListView()
        year_view.setMinimumWidth(140)
        self.year.setView(year_view)
        self.year.setStyleSheet('QComboBox { combobox-popup: 0; }')
        self.year.currentTextChanged.connect(self.annual_year_changed)
        annual_heading.addWidget(self.year)
        annual_heading.addStretch()
        self.annual_total = QLabel('年度合计：—')
        yearly_layout.addLayout(annual_heading)
        self.monthly_table = QTableWidget(4, 4)
        self.monthly_table.setStyleSheet('QTableView::item { padding:0 4px; }')
        self.monthly_table.horizontalHeader().hide()
        self.monthly_table.setVerticalHeaderLabels(['一季度', '二季度', '三季度', '四季度'])
        self.monthly_table.verticalHeader().setFixedWidth(60)
        self.monthly_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.monthly_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.monthly_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.monthly_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.monthly_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.monthly_table.setFixedHeight(336)
        self.monthly_table.viewport().installEventFilter(self)
        yearly_layout.addWidget(self.monthly_table)
        yearly_layout.addWidget(self.annual_total)
        annual_note = QLabel('按已有每日盈亏及当日实时估值汇总；无数据不当作零。')
        annual_note.setObjectName('secondary')
        annual_note.setWordWrap(True)
        yearly_layout.addWidget(annual_note)
        daily_layout.addStretch()
        self.summaries = ReportPage(("渠道汇总", "分类汇总", "季度矩阵", "月度趋势"), show_details=False, center_aligned=True)
        self.summaries.expand_rows = True
        self.summaries.setMinimumHeight(480)
        body.addWidget(self.summaries)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _arrange(self):
        columns = 6 if self.width() >= 1160 else 3 if self.width() >= 720 else 2
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        self._arrange()
        super().resizeEvent(event)

    def clear(self):
        self.analytics = None
        self.daily_rows, self.live_day = (), None
        self.day_rates = {}
        self.metrics = None
        self.cash_banner.hide()
        for card in self.cards:
            card.setText("计算中…")
            card.setToolTip("")
        self.summaries.clear()
        self.calendar_table.setRowCount(0)
        self.monthly_table.clearContents()
        self.year.setEnabled(False)
        self.annual_title.setText('本年每月盈亏')
        self.annual_total.setText('年度合计：—')

    def show_error(self, message: str):
        """持仓/收益计算失败时明确展示错误状态，避免卡片长期停留在“计算中…”。"""
        self.clear()
        t = tokens(self)
        for card in self.cards:
            card.setText("加载失败")
            card.setToolTip(message)
            card.setStyleSheet(
                f"QPushButton {{text-align:left; padding:12px; font-size:14px; border-radius:12px; "
                f"border:1px solid {t.border}; background:{t.panel}; color:{t.loss};}}"
            )

    def _open_cash_anchor_dialog(self):
        store = QuoteStore(quote_cache_path())
        dlg = CashAnchorDialog(store, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.cash_anchors_updated.emit()

    def set_analytics(self, analytics):
        self.analytics = analytics
        self.set_metrics(analytics.metrics)
        self.summaries.set_reports(analytics.reports)
    def set_metrics(self, metrics):
        if not self.analytics:
            return
        analytics = self.analytics
        from portfolio.services.overview_metrics import overview_metrics
        metrics=overview_metrics(analytics,metrics)
        stock_mv = next((m.value for m in metrics if m.name == "当前总资产" and m.value is not None), None)
        self.cash_banner.update_cash(analytics, stock_mv)
        for card, metric in zip(self.cards, metrics):
            gap, _ = metric_gap(metric.name, analytics.reports['每日持仓收益'].rows, analytics.scope.as_of) if metric.value is None else ('', '')
            if metric.value is None and metric.formula.startswith('长桥'):
                gap = '等待报价或收盘基准'
            always_neutral = metric.name in ("当前总资产", "当前持仓总成本")
            if always_neutral:
                value = f"— {gap}" if metric.value is None else f"{metric.value:.2%}" if metric.ratio else f"{metric.value:,.2f}"
            else:
                value = f"— {gap}" if metric.value is None else f"{metric.value:+.2%}" if metric.ratio else f"{metric.value:+,.2f}"
            card.setText(f"{metric.name}\n{value}\n{metric.day}")
            t = tokens(self)
            if always_neutral:
                color = t.heading
            else:
                is_performance = metric.ratio or any(word in metric.name for word in ("盈亏", "收益", "微操"))
                color = (t.profit if metric.value > 0 else t.loss if metric.value < 0 else t.muted) if is_performance and metric.value is not None else t.heading
            card.setStyleSheet(f"QPushButton {{text-align:left; padding:12px; font-size:14px; border-radius:12px; border:1px solid {t.border}; background:{t.panel}; color:{color};}}")

        self.update_daily_views(metrics)

    def waiting_live(self):
        if self.analytics:
            from portfolio.services.overview_metrics import overview_metrics
            keep = {'已清仓收益', '上月盈亏', '去年盈亏', '本月微操收益', '本年微操收益','当前持仓总成本'}
            for card, metric in zip(self.cards, overview_metrics(self.analytics)):
                if metric.name not in keep:
                    card.setText(metric.name+'\n— 正在读取本时段行情')

    def update_daily_views(self, metrics):
        self.metrics = metrics
        self.daily_rows, self.live_day = live_daily_rows(self.analytics.reports['每日持仓收益'].rows, metrics)
        self.day_rates = daily_rates(self.daily_rows, metrics, self.live_day)
        self.estimate_days = {r.values[0] for r in self.daily_rows if r.metadata.get('historical_estimate')}
        months = sorted({r.values[0][:7] for r in self.daily_rows}, reverse=True)
        selected_month, selected_year = self.month.currentText(), self.year.currentText()
        self.month.blockSignals(True)
        self.month.clear()
        self.month.addItems(months)
        if selected_month in months:
            self.month.setCurrentText(selected_month)
        self.month.blockSignals(False)
        years = sorted({month[:4] for month in months}, reverse=True)
        self.year.blockSignals(True)
        self.year.clear()
        self.year.addItems(years)
        if selected_year in years:
            self.year.setCurrentText(selected_year)
        self.year.blockSignals(False)
        self.year.setEnabled(bool(years))
        self.calendar()
        self.annual_year_changed(self.year.currentText())

    def set_calendar_display_mode(self, mode: str):
        """切换交易日历与月度盈亏展示内容：amount(仅金额) / rate(仅收益率) / both(都展示)。"""
        target = mode if mode in ("amount", "rate", "both") else "both"
        if target == self._calendar_display_mode:
            self._update_calendar_mode_styles()
            return
        self._calendar_display_mode = target
        self._update_calendar_mode_styles()
        self.calendar()
        if self.year.currentText():
            self.annual_year_changed(self.year.currentText())

    def _update_calendar_mode_styles(self):
        for key, btn in self._cal_mode_btns.items():
            active = key == self._calendar_display_mode
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            if active:
                btn.setStyleSheet(
                    "QPushButton { background: #2563EB; color: #FFFFFF; border: 1px solid #2563EB; "
                    "border-radius: 6px; padding: 3px 10px; font-size: 12px; font-weight: 700; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #F8FAFC; color: #475569; border: 1px solid #CBD5E1; "
                    "border-radius: 6px; padding: 3px 10px; font-size: 12px; font-weight: 600; }"
                    "QPushButton:hover { background: #EFF6FF; color: #2563EB; border-color: #93C5FD; }"
                )

    def _format_calendar_val(self, gain, rate, *, missing_text: str = "— 无数据") -> str:
        mode = self._calendar_display_mode
        if mode == "amount":
            return f"{gain:+,.2f}" if gain is not None else missing_text
        if mode == "rate":
            if rate is not None:
                return f"{rate:+.2%}"
            return missing_text if gain is None else "—"
        if gain is None:
            return missing_text
        rate_str = f"\n{rate:+.2%}" if rate is not None else ""
        return f"{gain:+,.2f}{rate_str}"

    def calendar(self, *_):
        if not self.analytics or not self.month.currentText():
            return
        y, m = map(int, self.month.currentText().split("-"))
        days = defaultdict(list)
        for row in self.daily_rows:
            days[row.values[0]].append(row.values[19])
        weeks = calendar.Calendar().monthdatescalendar(y, m)
        self.calendar_table.setRowCount(len(weeks))
        row_height = max(64, self.calendar_table.fontMetrics().lineSpacing() * 3 + 12, 332 // len(weeks))
        for week_index, week in enumerate(weeks):
            totals = []
            week_days = []
            for column, day in enumerate(week[:5]):
                key = day.isoformat()
                gain = strict_sum(days[key]) if key in days and day.month == m else None
                rate = self.day_rates.get(key) if key in days and day.month == m else None
                if day.month == m:
                    val_txt = self._format_calendar_val(gain, rate, missing_text="— 无数据")
                    text = f"{day.month}/{day.day}\n{val_txt}"
                else:
                    text = ""
                item = QTableWidgetItem(text)
                if key in getattr(self, 'estimate_days', set()) and day.month == m:
                    est_txt = self._format_calendar_val(gain, rate, missing_text="— 数据未完整")
                    item.setText(f"{day.month}/{day.day} · 历史估值\n{est_txt}")
                if key == self.live_day and day.month == m:
                    live_txt = self._format_calendar_val(gain, rate, missing_text="— 等待报价/基准")
                    item.setText(f"{day.month}/{day.day} · 实时估值\n{live_txt}")
                item.setData(Qt.ItemDataRole.UserRole, key if day.month == m else "")
                color_ref = rate if (self._calendar_display_mode == "rate" and rate is not None) else gain
                if color_ref is not None:
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor(tokens(self).profit if color_ref > 0 else tokens(self).loss if color_ref < 0 else tokens(self).muted))
                self.calendar_table.setItem(week_index, column, item)
                if day.month == m and key in days:
                    totals.append(gain)
                    week_days.append(key)
            total = strict_sum(totals) if totals else None
            week_rate = compound_rates(week_days, self.day_rates) if week_days else None
            text = self._format_calendar_val(total, week_rate, missing_text="—")
            week_item = QTableWidgetItem(text)
            week_ref = week_rate if (self._calendar_display_mode == "rate" and week_rate is not None) else total
            if week_ref is not None:
                from PySide6.QtGui import QColor
                week_item.setForeground(QColor(tokens(self).profit if week_ref > 0 else tokens(self).loss if week_ref < 0 else tokens(self).muted))
            self.calendar_table.setItem(week_index, 5, week_item)
            self.calendar_table.setRowHeight(week_index, row_height)
        # Include the header and frame as well as every week. Hiding the inner
        # scrollbar alone would otherwise leave the first/last dates clipped.
        height = (self.calendar_table.horizontalHeader().height()
                  + sum(self.calendar_table.rowHeight(i) for i in range(len(weeks)))
                  + self.calendar_table.frameWidth() * 2)
        self.calendar_table.setFixedHeight(height)
        self.calendar_table.scrollToTop()
        self.monthly_table.setFixedHeight(max(336, height))

    def annual_year_changed(self, text):
        if self.analytics and text:
            self.annual_months(int(text))

    def annual_months(self, year):
        from PySide6.QtGui import QColor

        daily = self.daily_rows
        gains, total = monthly_gains(daily, year)
        m_rates = monthly_rates(daily, year, getattr(self, 'day_rates', {}), getattr(self, 'metrics', None), self.live_day)
        q_rates = quarterly_rates(daily, year, getattr(self, 'day_rates', {}))
        a_rate = annual_rate(daily, year, getattr(self, 'day_rates', {}), getattr(self, 'metrics', None), self.live_day)
        self.annual_title.setText(f'{year} 年每月盈亏（{self.analytics.scope.reporting_currency}）')
        annual_text = '年度合计：'
        if self._calendar_display_mode == "rate":
            annual_text += f"{a_rate:+.2%}" if a_rate is not None else ("— 数据未完整" if total is None else "—")
        elif self._calendar_display_mode == "amount":
            annual_text += f"{total:+,.2f}" if total is not None else "— 数据未完整"
        else:
            if total is not None:
                annual_text += f'{total:+,.2f}'
                if a_rate is not None:
                    annual_text += f'（{a_rate:+.2%}）'
            else:
                annual_text += '— 数据未完整'
        self.annual_total.setText(annual_text)
        for index, gain in enumerate(gains):
            rate = m_rates[index]
            val_txt = self._format_calendar_val(gain, rate, missing_text="— 无数据/未完整")
            label = f"{index + 1}月\n{val_txt}"
            item = QTableWidgetItem(label)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t = tokens(self)
            ref = rate if (self._calendar_display_mode == "rate" and rate is not None) else gain
            item.setForeground(QColor(t.profit if ref is not None and ref > 0 else t.loss if ref is not None and ref < 0 else t.muted))
            self.monthly_table.setItem(index // 3, index % 3, item)
        for quarter, gain in enumerate(quarterly_gains(daily, year)):
            rate = q_rates[quarter]
            value = self._format_calendar_val(gain, rate, missing_text="— 无数据/未完整")
            item = QTableWidgetItem('季度合计\n' + value)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t = tokens(self)
            ref = rate if (self._calendar_display_mode == "rate" and rate is not None) else gain
            item.setForeground(QColor(t.profit if ref is not None and ref > 0 else t.loss if ref is not None and ref < 0 else t.muted))
            self.monthly_table.setItem(quarter, 3, item)

    def eventFilter(self, watched, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.ToolTip:
            return True
        return super().eventFilter(watched, event)

    def calendar_clicked(self, row, col):
        item = self.calendar_table.item(row, col)
        if item and item.data(Qt.ItemDataRole.UserRole):
            key = item.data(Qt.ItemDataRole.UserRole)
            self.drill.emit("live_day" if key == self.live_day else "day", key)
