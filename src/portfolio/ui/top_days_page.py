from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import ZERO, ratio, strict_sum
from portfolio.engine.dashboard import daily_rates
from portfolio.services.charts_service import Chart, Series, aggregate
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.theme import tokens


class TopDaysPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.x = 10
        self.sort_by = "amount"
        self.display_mode = "both"
        self.view_mode = "day"

        self._daily_summary_text = ""
        self._month_summary_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # 顶部控制栏：X 数量输入框、排序方式、展示内容、查看板块与导出按钮
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        label_x = QLabel("展示数量 X：")
        top_bar.addWidget(label_x)

        self.spin_x = QSpinBox()
        self.spin_x.setRange(1, 50)
        self.spin_x.setValue(10)
        self.spin_x.setFixedWidth(70)
        self.spin_x.setToolTip("手动输入最爽与最惨展示的数量 X（默认 10，范围 1 ~ 50）")
        self.spin_x.valueChanged.connect(self._on_x_changed)
        top_bar.addWidget(self.spin_x)

        label_sort = QLabel("排序方式：")
        label_sort.setContentsMargins(8, 0, 0, 0)
        top_bar.addWidget(label_sort)

        self.sort_combo = QComboBox()
        self.sort_combo.addItem("按金额排序", "amount")
        self.sort_combo.addItem("按收益率排序", "rate")
        self.sort_combo.setMinimumWidth(120)
        self.sort_combo.setToolTip("选择极值排名的排序规则：按单期盈亏金额或单期收益率（%）排序")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_bar.addWidget(self.sort_combo)

        label_display = QLabel("展示内容：")
        label_display.setContentsMargins(8, 0, 0, 0)
        top_bar.addWidget(label_display)

        self.display_combo = QComboBox()
        self.display_combo.addItem("两者都展示", "both")
        self.display_combo.addItem("仅收益金额", "amount")
        self.display_combo.addItem("仅收益率", "rate")
        self.display_combo.setMinimumWidth(120)
        self.display_combo.setToolTip("控制日汇总与月汇总图表展示收益金额、收益率或两者都展示")
        self.display_combo.currentIndexChanged.connect(self._on_display_changed)
        top_bar.addWidget(self.display_combo)

        label_view = QLabel("查看板块：")
        label_view.setContentsMargins(8, 0, 0, 0)
        top_bar.addWidget(label_view)

        self.view_combo = QComboBox()
        self.view_combo.addItem("Top 日汇总", "day")
        self.view_combo.addItem("Top 月汇总", "month")
        self.view_combo.addItem("全部展示（日+月）", "all")
        self.view_combo.setMinimumWidth(130)
        self.view_combo.setToolTip("选择展示板块：查看 Top 日汇总、Top 月汇总或同屏展示两者")
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        top_bar.addWidget(self.view_combo)

        # 顶部常驻摘要标签（紧凑跟随在控制项右侧）
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("muted")
        self.summary_label.setContentsMargins(12, 0, 0, 0)
        top_bar.addWidget(self.summary_label)

        top_bar.addStretch()

        export_btn = QPushButton("导出 PNG…")
        export_btn.clicked.connect(self.export)
        top_bar.addWidget(export_btn)
        layout.addLayout(top_bar)

        # 滚动区域包装卡片容器
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(2, 4, 2, 16)
        container_layout.setSpacing(12)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ========= 第 1 部分：Top 日汇总 =========
        self.daily_section = QWidget()
        daily_section_layout = QVBoxLayout(self.daily_section)
        daily_section_layout.setContentsMargins(0, 0, 0, 0)
        daily_section_layout.setSpacing(8)
        daily_section_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 仅在“全部展示（日+月）”模式下显示的日汇总标题行
        self.daily_header_widget = QWidget()
        self.daily_header_widget.setFixedHeight(28)
        daily_header = QHBoxLayout(self.daily_header_widget)
        daily_header.setContentsMargins(2, 0, 2, 0)
        daily_header.setSpacing(10)
        daily_title = QLabel("Top 日汇总")
        daily_title.setObjectName("sectionTitle")
        daily_header.addWidget(daily_title)
        self.daily_inline_summary = QLabel("")
        self.daily_inline_summary.setObjectName("muted")
        daily_header.addWidget(self.daily_inline_summary)
        daily_header.addStretch()
        daily_section_layout.addWidget(self.daily_header_widget)

        daily_cards_layout = QHBoxLayout()
        daily_cards_layout.setContentsMargins(0, 0, 0, 0)
        daily_cards_layout.setSpacing(16)

        # 左卡片（最爽 X 天）
        self.gain_wrapper = QWidget()
        gain_layout = QVBoxLayout(self.gain_wrapper)
        gain_layout.setContentsMargins(0, 0, 0, 0)
        gain_layout.setSpacing(6)

        self.gain_canvas = ChartCanvas(card_mode=True)
        self.gain_canvas.selected.connect(self.drill)
        gain_layout.addWidget(self.gain_canvas)

        self.gain_stats = QLabel("")
        self.gain_stats.setObjectName("muted")
        self.gain_stats.setContentsMargins(10, 0, 10, 0)
        gain_layout.addWidget(self.gain_stats)
        daily_cards_layout.addWidget(self.gain_wrapper, 1)

        # 右卡片（最惨 X 天）
        self.loss_wrapper = QWidget()
        loss_layout = QVBoxLayout(self.loss_wrapper)
        loss_layout.setContentsMargins(0, 0, 0, 0)
        loss_layout.setSpacing(6)

        self.loss_canvas = ChartCanvas(card_mode=True)
        self.loss_canvas.selected.connect(self.drill)
        loss_layout.addWidget(self.loss_canvas)

        self.loss_stats = QLabel("")
        self.loss_stats.setObjectName("muted")
        self.loss_stats.setContentsMargins(10, 0, 10, 0)
        loss_layout.addWidget(self.loss_stats)
        daily_cards_layout.addWidget(self.loss_wrapper, 1)

        daily_section_layout.addLayout(daily_cards_layout)
        container_layout.addWidget(self.daily_section)

        # 分割线（仅全部展示模式使用）
        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.Shape.HLine)
        self.sep.setFrameShadow(QFrame.Shadow.Plain)
        self.sep.setFixedHeight(1)
        container_layout.addWidget(self.sep)

        # ========= 第 2 部分：Top 月汇总 =========
        self.month_section = QWidget()
        month_section_layout = QVBoxLayout(self.month_section)
        month_section_layout.setContentsMargins(0, 0, 0, 0)
        month_section_layout.setSpacing(8)
        month_section_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 仅在“全部展示（日+月）”模式下显示的小标题行
        self.month_header_widget = QWidget()
        self.month_header_widget.setFixedHeight(28)
        month_header = QHBoxLayout(self.month_header_widget)
        month_header.setContentsMargins(2, 0, 2, 0)
        month_header.setSpacing(10)
        month_title = QLabel("Top 月汇总")
        month_title.setObjectName("sectionTitle")
        month_header.addWidget(month_title)
        self.month_inline_summary = QLabel("")
        self.month_inline_summary.setObjectName("muted")
        month_header.addWidget(self.month_inline_summary)
        month_header.addStretch()
        month_section_layout.addWidget(self.month_header_widget)

        month_cards_layout = QHBoxLayout()
        month_cards_layout.setContentsMargins(0, 0, 0, 0)
        month_cards_layout.setSpacing(16)

        # 左卡片（最爽 X 月）
        self.gain_month_wrapper = QWidget()
        gain_month_layout = QVBoxLayout(self.gain_month_wrapper)
        gain_month_layout.setContentsMargins(0, 0, 0, 0)
        gain_month_layout.setSpacing(6)

        self.gain_month_canvas = ChartCanvas(card_mode=True)
        self.gain_month_canvas.selected.connect(self.drill)
        gain_month_layout.addWidget(self.gain_month_canvas)

        self.gain_month_stats = QLabel("")
        self.gain_month_stats.setObjectName("muted")
        self.gain_month_stats.setContentsMargins(10, 0, 10, 0)
        gain_month_layout.addWidget(self.gain_month_stats)
        month_cards_layout.addWidget(self.gain_month_wrapper, 1)

        # 右卡片（最惨 X 月）
        self.loss_month_wrapper = QWidget()
        loss_month_layout = QVBoxLayout(self.loss_month_wrapper)
        loss_month_layout.setContentsMargins(0, 0, 0, 0)
        loss_month_layout.setSpacing(6)

        self.loss_month_canvas = ChartCanvas(card_mode=True)
        self.loss_month_canvas.selected.connect(self.drill)
        loss_month_layout.addWidget(self.loss_month_canvas)

        self.loss_month_stats = QLabel("")
        self.loss_month_stats.setObjectName("muted")
        self.loss_month_stats.setContentsMargins(10, 0, 10, 0)
        loss_month_layout.addWidget(self.loss_month_stats)
        month_cards_layout.addWidget(self.loss_month_wrapper, 1)

        month_section_layout.addLayout(month_cards_layout)
        container_layout.addWidget(self.month_section)

        # 弹性空间防止空白被撑开在组件之间
        container_layout.addStretch(1)

        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)

        # 初始应用视图显示
        self._apply_view_mode()

    def showEvent(self, event):
        super().showEvent(event)
        main_win = self.window()
        analytics = getattr(main_win, "analytics", None)
        if analytics and self.analytics is not analytics:
            self.set_analytics(analytics)

    def set_view_mode(self, mode: str):
        if mode in ("all", "day", "month"):
            self.view_mode = mode
            idx = self.view_combo.findData(mode)
            if idx >= 0:
                self.view_combo.blockSignals(True)
                self.view_combo.setCurrentIndex(idx)
                self.view_combo.blockSignals(False)
            self._apply_view_mode()

    def _on_view_changed(self, index):
        self.view_mode = self.view_combo.currentData()
        self._apply_view_mode()

    def _apply_view_mode(self):
        is_all = self.view_mode == "all"
        is_day = self.view_mode == "day"
        is_month = self.view_mode == "month"

        # 在单板块查看模式下隐藏内嵌小标题，紧凑展示图表卡片
        self.daily_header_widget.setVisible(is_all)
        self.month_header_widget.setVisible(is_all)
        self.sep.setVisible(is_all)

        self.daily_section.setVisible(is_all or is_day)
        self.month_section.setVisible(is_all or is_month)

        # 顶部控制栏中的 summary_label：单板块模式直接在控制栏常驻显示，全部模式则由各自内嵌小标题呈现
        if is_day:
            self.summary_label.setText(self._daily_summary_text)
            self.summary_label.setVisible(True)
        elif is_month:
            self.summary_label.setText(self._month_summary_text)
            self.summary_label.setVisible(True)
        else:
            self.summary_label.setText("")
            self.summary_label.setVisible(False)

    def _on_x_changed(self, value):
        self.x = value
        self.update_charts()

    def _on_sort_changed(self, index):
        self.sort_by = self.sort_combo.currentData()
        self.update_charts()

    def _on_display_changed(self, index):
        self.display_mode = self.display_combo.currentData()
        self.update_charts()

    def _format_label(self, amt: Decimal, rate: Decimal | None, is_rate_sort: bool) -> str:
        if self.display_mode == "amount":
            return f"{amt:+,.2f}"
        if self.display_mode == "rate":
            return f"{rate:+.2%}" if rate is not None else "—"
        # "both" 两者都展示
        if is_rate_sort:
            return f"{rate:+.2%} ({amt:+,.2f})" if rate is not None else f"{amt:+,.2f}"
        return f"{amt:+,.2f} ({rate:+.2%})" if rate is not None else f"{amt:+,.2f}"

    def clear(self):
        self.analytics = None
        self._rendered_cache_key = None
        self.gain_canvas.chart = None
        self.loss_canvas.chart = None
        self.gain_month_canvas.chart = None
        self.loss_month_canvas.chart = None
        self.gain_canvas.update()
        self.loss_canvas.update()
        self.gain_month_canvas.update()
        self.loss_month_canvas.update()
        self._daily_summary_text = ""
        self._month_summary_text = ""
        self.summary_label.setText("")
        self.daily_inline_summary.setText("")
        self.month_inline_summary.setText("")
        self.gain_stats.setText("")
        self.loss_stats.setText("")
        self.gain_month_stats.setText("")
        self.loss_month_stats.setText("")

    def set_analytics(self, analytics, force=False):
        cache_key = (getattr(analytics, "calculated_at", None), getattr(analytics, "source_identity", None))
        if not force and getattr(self, "_rendered_cache_key", None) == cache_key:
            return
        self.analytics = analytics
        self.update_charts()
        self._rendered_cache_key = cache_key

    def update_canvases(self):
        self.gain_canvas.update()
        self.loss_canvas.update()
        self.gain_month_canvas.update()
        self.loss_month_canvas.update()

    def update_charts(self):
        if not self.analytics:
            self.clear()
            return

        t = tokens(self)
        self.sep.setStyleSheet(f"background-color: {t.border}; border: none;")

        reports = self.analytics.reports
        currency = self.analytics.scope.reporting_currency
        x = self.x
        is_rate_sort = self.sort_by == "rate"

        # ----------------------------------------------------
        # 1. Top 日汇总数据构建与渲染
        # ----------------------------------------------------
        if "每日持仓收益" not in reports:
            self.gain_canvas.chart = None
            self.loss_canvas.chart = None
            self.gain_canvas.update()
            self.loss_canvas.update()
            self._daily_summary_text = "当前筛选组合暂无有效每日收益数据"
            self.daily_inline_summary.setText(self._daily_summary_text)
            self.gain_stats.setText("")
            self.loss_stats.setText("")
        else:
            daily_rows = reports["每日持仓收益"].rows
            daily_gain = aggregate(daily_rows, 0, 19)
            daily_rate_map = daily_rates(daily_rows, self.analytics.metrics, self.analytics.latest_profit_day)

            valid_days = [(day, daily_gain[day], daily_rate_map.get(day)) for day in daily_gain if daily_gain[day] is not None]

            if not valid_days:
                self.gain_canvas.chart = None
                self.loss_canvas.chart = None
                self.gain_canvas.update()
                self.loss_canvas.update()
                self._daily_summary_text = "当前筛选组合暂无有效每日收益数据"
                self.daily_inline_summary.setText(self._daily_summary_text)
                self.gain_stats.setText("")
                self.loss_stats.setText("")
            else:
                if is_rate_sort:
                    valid_rates = [p for p in valid_days if p[2] is not None]
                    top_gain = sorted(valid_rates, key=lambda p: p[2], reverse=True)[:x]
                    top_loss = sorted(valid_rates, key=lambda p: p[2])[:x]

                    gain_points = tuple((day, rate) for day, _, rate in top_gain)
                    loss_points = tuple((day, rate) for day, _, rate in top_loss)

                    gain_title = f"最爽 {x} 天（按收益率最高）"
                    loss_title = f"最惨 {x} 天（按收益率最低）"
                    gain_series_name = "最高日收益率"
                    loss_series_name = "最低日收益率"
                    ratio_mode = True
                else:
                    top_gain = sorted(valid_days, key=lambda p: p[1], reverse=True)[:x]
                    top_loss = sorted(valid_days, key=lambda p: p[1])[:x]

                    gain_points = tuple((day, amt) for day, amt, _ in top_gain)
                    loss_points = tuple((day, amt) for day, amt, _ in top_loss)

                    gain_title = f"最爽 {x} 天（单日最高收益）"
                    loss_title = f"最惨 {x} 天（单日最低收益）"
                    gain_series_name = "最高日收益"
                    loss_series_name = "最低日收益"
                    ratio_mode = False

                gain_labels = {day: self._format_label(amt, rate, is_rate_sort) for day, amt, rate in top_gain}
                loss_labels = {day: self._format_label(amt, rate, is_rate_sort) for day, amt, rate in top_loss}

                daily_note = (
                    f"EXCEL_COMPAT · {currency} · 基准 {self.analytics.scope.as_of} · "
                    f"人员 {self.analytics.scope.person_ids or '全部启用'} · "
                    f"账户 {self.analytics.scope.account_ids or '全部'} · "
                    + ("含待审/口径提示" if self.analytics.issues else "完整")
                    + " · 点击条目下钻查看当日明细"
                )

                gain_chart = Chart(
                    id=22,
                    title=gain_title,
                    kind="horizontal",
                    series=(Series(gain_series_name, gain_points),),
                    note=daily_note,
                    ratio=ratio_mode,
                    link="day",
                    point_labels=gain_labels,
                )

                loss_chart = Chart(
                    id=23,
                    title=loss_title,
                    kind="horizontal",
                    series=(Series(loss_series_name, loss_points),),
                    note=daily_note,
                    ratio=ratio_mode,
                    link="day",
                    point_labels=loss_labels,
                )

                canvas_h = max(340, 110 + max(len(top_gain), len(top_loss)) * 36)
                self.gain_canvas.setFixedHeight(canvas_h)
                self.loss_canvas.setFixedHeight(canvas_h)

                self.gain_canvas.chart = gain_chart
                self.loss_canvas.chart = loss_chart
                self.gain_canvas.update()
                self.loss_canvas.update()

                gain_amts = [amt for _, amt, _ in top_gain]
                loss_amts = [amt for _, amt, _ in top_loss]
                gain_rates = [rate for _, _, rate in top_gain if rate is not None]
                loss_rates = [rate for _, _, rate in top_loss if rate is not None]

                gain_total = strict_sum(gain_amts) if gain_amts else ZERO
                loss_total = strict_sum(loss_amts) if loss_amts else ZERO
                diff_total = (gain_total or ZERO) + (loss_total or ZERO)

                gain_avg_amt = (gain_total / Decimal(len(gain_amts))) if gain_amts and gain_total is not None else ZERO
                loss_avg_amt = (loss_total / Decimal(len(loss_amts))) if loss_amts and loss_total is not None else ZERO
                gain_avg_rate = (sum(gain_rates, ZERO) / Decimal(len(gain_rates))) if gain_rates else ZERO
                loss_avg_rate = (sum(loss_rates, ZERO) / Decimal(len(loss_rates))) if loss_rates else ZERO

                self._daily_summary_text = (
                    f"最爽合计：<b style='color:{t.profit}'>+{gain_total:,.2f}</b>  |  "
                    f"最惨合计：<b style='color:{t.loss}'>{loss_total:,.2f}</b>  |  "
                    f"盈亏相抵：<b>{diff_total:+,.2f}</b>"
                )
                self.daily_inline_summary.setText(self._daily_summary_text)

                if is_rate_sort:
                    self.gain_stats.setText(
                        f"最爽合计盈利：+{gain_total:,.2f} {currency} · "
                        f"日均收益率：+{gain_avg_rate:.2%} · "
                        f"日均盈利：+{gain_avg_amt:,.2f} {currency} · "
                        f"最高单日：{top_gain[0][0]} (+{top_gain[0][2]:.2%}, +{top_gain[0][1]:,.2f})" if top_gain else ""
                    )
                    self.loss_stats.setText(
                        f"最惨合计亏损：{loss_total:,.2f} {currency} · "
                        f"日均亏损率：{loss_avg_rate:.2%} · "
                        f"日均亏损：{loss_avg_amt:,.2f} {currency} · "
                        f"最大亏损：{top_loss[0][0]} ({top_loss[0][2]:.2%}, {top_loss[0][1]:,.2f})" if top_loss else ""
                    )
                else:
                    rate_gain_str = f" (+{top_gain[0][2]:.2%})" if top_gain and top_gain[0][2] is not None else ""
                    rate_loss_str = f" ({top_loss[0][2]:.2%})" if top_loss and top_loss[0][2] is not None else ""
                    self.gain_stats.setText(
                        f"最爽合计盈利：+{gain_total:,.2f} {currency} · "
                        f"日均盈利：+{gain_avg_amt:,.2f} {currency} · "
                        f"最高单日：{top_gain[0][0]} (+{top_gain[0][1]:,.2f}{rate_gain_str})" if top_gain else ""
                    )
                    self.loss_stats.setText(
                        f"最惨合计亏损：{loss_total:,.2f} {currency} · "
                        f"日均亏损：{loss_avg_amt:,.2f} {currency} · "
                        f"最大亏损：{top_loss[0][0]} ({top_loss[0][1]:,.2f}{rate_loss_str})" if top_loss else ""
                    )

        # ----------------------------------------------------
        # 2. Top 月汇总数据构建与渲染
        # ----------------------------------------------------
        if "每月持仓收益" not in reports:
            self.gain_month_canvas.chart = None
            self.loss_month_canvas.chart = None
            self.gain_month_canvas.update()
            self.loss_month_canvas.update()
            self._month_summary_text = "当前筛选组合暂无有效每月收益数据"
            self.month_inline_summary.setText(self._month_summary_text)
            self.gain_month_stats.setText("")
            self.loss_month_stats.setText("")
        else:
            monthly_rows = reports["每月持仓收益"].rows
            month_gain = aggregate(monthly_rows, 0, 20)
            month_end = {mo: [r for r in monthly_rows if r.values[0] == mo] for mo in month_gain}
            values = {mo: strict_sum(r.values[11] for r in rows) for mo, rows in month_end.items()}
            month_rate_map = {
                mo: ratio(gain, values[mo] - gain if values.get(mo) is not None and gain is not None else None)
                for mo, gain in month_gain.items()
            }

            valid_months = [(mo, month_gain[mo], month_rate_map.get(mo)) for mo in month_gain if month_gain[mo] is not None]

            if not valid_months:
                self.gain_month_canvas.chart = None
                self.loss_month_canvas.chart = None
                self.gain_month_canvas.update()
                self.loss_month_canvas.update()
                self._month_summary_text = "当前筛选组合暂无有效每月收益数据"
                self.month_inline_summary.setText(self._month_summary_text)
                self.gain_month_stats.setText("")
                self.loss_month_stats.setText("")
            else:
                if is_rate_sort:
                    valid_month_rates = [p for p in valid_months if p[2] is not None]
                    top_month_gain = sorted(valid_month_rates, key=lambda p: p[2], reverse=True)[:x]
                    top_month_loss = sorted(valid_month_rates, key=lambda p: p[2])[:x]

                    gain_month_points = tuple((mo, rate) for mo, _, rate in top_month_gain)
                    loss_month_points = tuple((mo, rate) for mo, _, rate in top_month_loss)

                    gain_month_title = f"最爽 {x} 月（按收益率最高）"
                    loss_month_title = f"最惨 {x} 月（按收益率最低）"
                    gain_month_series_name = "最高月收益率"
                    loss_month_series_name = "最低月收益率"
                    month_ratio_mode = True
                else:
                    top_month_gain = sorted(valid_months, key=lambda p: p[1], reverse=True)[:x]
                    top_month_loss = sorted(valid_months, key=lambda p: p[1])[:x]

                    gain_month_points = tuple((mo, amt) for mo, amt, _ in top_month_gain)
                    loss_month_points = tuple((mo, amt) for mo, amt, _ in top_month_loss)

                    gain_month_title = f"最爽 {x} 月（单月最高收益）"
                    loss_month_title = f"最惨 {x} 月（单月最低收益）"
                    gain_month_series_name = "最高月收益"
                    loss_month_series_name = "最低月收益"
                    month_ratio_mode = False

                gain_month_labels = {mo: self._format_label(amt, rate, is_rate_sort) for mo, amt, rate in top_month_gain}
                loss_month_labels = {mo: self._format_label(amt, rate, is_rate_sort) for mo, amt, rate in top_month_loss}

                month_note = (
                    f"EXCEL_COMPAT · {currency} · 基准 {self.analytics.scope.as_of} · "
                    f"人员 {self.analytics.scope.person_ids or '全部启用'} · "
                    f"账户 {self.analytics.scope.account_ids or '全部'} · "
                    + ("含待审/口径提示" if self.analytics.issues else "完整")
                    + " · 点击条目下钻查看当月明细"
                )

                gain_month_chart = Chart(
                    id=28,
                    title=gain_month_title,
                    kind="horizontal",
                    series=(Series(gain_month_series_name, gain_month_points),),
                    note=month_note,
                    ratio=month_ratio_mode,
                    link="month",
                    point_labels=gain_month_labels,
                )

                loss_month_chart = Chart(
                    id=29,
                    title=loss_month_title,
                    kind="horizontal",
                    series=(Series(loss_month_series_name, loss_month_points),),
                    note=month_note,
                    ratio=month_ratio_mode,
                    link="month",
                    point_labels=loss_month_labels,
                )

                month_canvas_h = max(340, 110 + max(len(top_month_gain), len(top_month_loss)) * 36)
                self.gain_month_canvas.setFixedHeight(month_canvas_h)
                self.loss_month_canvas.setFixedHeight(month_canvas_h)

                self.gain_month_canvas.chart = gain_month_chart
                self.loss_month_canvas.chart = loss_month_chart
                self.gain_month_canvas.update()
                self.loss_month_canvas.update()

                gain_month_amts = [amt for _, amt, _ in top_month_gain]
                loss_month_amts = [amt for _, amt, _ in top_month_loss]
                gain_month_rates = [rate for _, _, rate in top_month_gain if rate is not None]
                loss_month_rates = [rate for _, _, rate in top_month_loss if rate is not None]

                gain_month_total = strict_sum(gain_month_amts) if gain_month_amts else ZERO
                loss_month_total = strict_sum(loss_month_amts) if loss_month_amts else ZERO
                diff_month_total = (gain_month_total or ZERO) + (loss_month_total or ZERO)

                gain_month_avg_amt = (gain_month_total / Decimal(len(gain_month_amts))) if gain_month_amts and gain_month_total is not None else ZERO
                loss_month_avg_amt = (loss_month_total / Decimal(len(loss_month_amts))) if loss_month_amts and loss_month_total is not None else ZERO
                gain_month_avg_rate = (sum(gain_month_rates, ZERO) / Decimal(len(gain_month_rates))) if gain_month_rates else ZERO
                loss_month_avg_rate = (sum(loss_month_rates, ZERO) / Decimal(len(loss_month_rates))) if loss_month_rates else ZERO

                self._month_summary_text = (
                    f"最爽合计：<b style='color:{t.profit}'>+{gain_month_total:,.2f}</b>  |  "
                    f"最惨合计：<b style='color:{t.loss}'>{loss_month_total:,.2f}</b>  |  "
                    f"盈亏相抵：<b>{diff_month_total:+,.2f}</b>"
                )
                self.month_inline_summary.setText(self._month_summary_text)

                if is_rate_sort:
                    self.gain_month_stats.setText(
                        f"最爽合计盈利：+{gain_month_total:,.2f} {currency} · "
                        f"月均收益率：+{gain_month_avg_rate:.2%} · "
                        f"月均盈利：+{gain_month_avg_amt:,.2f} {currency} · "
                        f"最高单月：{top_month_gain[0][0]} (+{top_month_gain[0][2]:.2%}, +{top_month_gain[0][1]:,.2f})" if top_month_gain else ""
                    )
                    self.loss_month_stats.setText(
                        f"最惨合计亏损：{loss_month_total:,.2f} {currency} · "
                        f"月均亏损率：{loss_month_avg_rate:.2%} · "
                        f"月均亏损：{loss_month_avg_amt:,.2f} {currency} · "
                        f"最大亏损：{top_month_loss[0][0]} ({top_month_loss[0][2]:.2%}, {top_month_loss[0][1]:,.2f})" if top_month_loss else ""
                    )
                else:
                    rate_gain_month_str = f" (+{top_month_gain[0][2]:.2%})" if top_month_gain and top_month_gain[0][2] is not None else ""
                    rate_loss_month_str = f" ({top_month_loss[0][2]:.2%})" if top_month_loss and top_month_loss[0][2] is not None else ""
                    self.gain_month_stats.setText(
                        f"最爽合计盈利：+{gain_month_total:,.2f} {currency} · "
                        f"月均盈利：+{gain_month_avg_amt:,.2f} {currency} · "
                        f"最高单月：{top_month_gain[0][0]} (+{top_month_gain[0][1]:,.2f}{rate_gain_month_str})" if top_month_gain else ""
                    )
                    self.loss_month_stats.setText(
                        f"最惨合计亏损：{loss_month_total:,.2f} {currency} · "
                        f"月均亏损：{loss_month_avg_amt:,.2f} {currency} · "
                        f"最大亏损：{top_month_loss[0][0]} ({top_month_loss[0][1]:,.2f}{rate_loss_month_str})" if top_month_loss else ""
                    )

        # 刷新视图显隐与对应位置的摘要文案
        self._apply_view_mode()

    def export(self):
        view_name = {"all": "日月汇总", "day": "日汇总", "month": "月汇总"}.get(self.view_mode, "汇总")
        suffix = "按收益率" if self.sort_by == "rate" else "按金额"
        disp_suffix = {"both": "金额与收益率", "amount": "仅金额", "rate": "仅收益率"}.get(self.display_mode, "")
        title = f"Top{view_name}_最爽最惨{self.x}期_{suffix}_{disp_suffix}"
        path, _ = QFileDialog.getSaveFileName(self, f"导出 Top {view_name}图表", f"{title}.png", "PNG (*.png)")
        if path:
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                target_widget = (
                    self.daily_section if self.view_mode == "day"
                    else self.month_section if self.view_mode == "month"
                    else self.container
                )
                if not target_widget.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except Exception:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
