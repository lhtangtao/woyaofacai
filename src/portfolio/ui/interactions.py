"""Navigation and UI filter state, independent of financial calculations."""
from functools import wraps

from PySide6.QtCore import QDate, Qt, QTimer

from portfolio.domain.models import TradeFilters

TITLES = ("交易明细", "数据源检查", "资产总览", "持仓与资产", "收益报表", "图表分析", "数据中心", "异常中心", "Excel 对账", "指标说明", "证券详情", "数据同步", "开发进度", "设置", "持仓财报日历", "持仓分布", "走势图", "Top日/月汇总", "月度走势详情", "AI 投资问答", "AI 股票分析")
DESCRIPTIONS = ("查看每笔交易，追溯原始记录与人员归属。", "查看完整数据源路径、结构检查及数据日期。", "查看资产、收益与资金变化。", "按人员、账户和证券查看资产与历史成本。", "查看每日、每月、微操及清仓收益。", "查看当前范围的趋势、分布与贡献。", "检查行情、资金和导入数据。", "核对缺失信息与待审核记录。", "按人员与日期对照原工作簿。", "查看计算口径、取值逻辑与限制。", "查看选中证券的交易、持仓与收益。", "使用Money3本地同步脚本更新数据库后刷新。", "查看可用功能与尚待验收内容。", "调整外观和数据源设置。", "按月份查看当前持仓公司的财报日期和发布时间。", "查看当前与月末的股票市值、渠道资产及分类持仓占比分布。", "查看每月收益、收益率、净投入及资产累计变化走势。", "查看历史最高与最低单日/单月持仓收益与收益率汇总（最爽与最惨日/月极值）。", "按高频采样精细展示月度（半小时采样）与年度（2小时采样）累计盈亏走势与极值。", "基于本地 Codex 订阅驱动，结合持仓成本与盈亏进行智能诊断与复盘问答。", "纯粹聚焦标的公司的基本面、财报数据、商业前景与竞争护城河，脱敏且不受个人账本数据干扰。")
COMBOS = ("person", "stock", "ledger", "category", "currency", "side", "link_status")
TITLES += ("行情监控中心", "持仓管理")
DESCRIPTIONS += ("当前持仓自动导入，观察底层标的与技术指标；默认全部暂停，不创建提醒规则。", "统一查看人员、券商、股票的持仓与收益，支持多维度切换和筛选。")
CHECKS = ("disabled", "audit", "start_enabled", "include_reversed", "include_reference")


def batch_drill(callback):
    @wraps(callback)
    def run(self, *args):
        self._drill_origin = self.capture_state()
        self._filter_batch = True
        try:
            return callback(self, *args)
        finally:
            self._filter_batch = False
            self._drill_origin = None
            self.search_timer.stop()
            self.page_number = 0
            self.refresh()
    return run


class InteractionMixin:
    def monthly_filter(self):
        return self.pages.currentIndex() == 4 and self.performance_page.selector.currentText() in ('每月持仓收益', '每月微操收益', '月度净投入明细')

    def fixed_asset_period(self):
        return self.pages.currentIndex() in (2, 3, 14, 15, 16, 17, 18)

    def today_snapshot(self):
        return self.pages.currentIndex() == 3 and self.asset_page.selector.currentText() == '今日盈亏'

    def ownership_view(self):
        return self.pages.currentIndex()==3 and self.asset_page.selector.currentText()=='股票归属'

    def broker_people_view(self):
        return self.pages.currentIndex() == 3 and self.asset_page.selector.currentText() == '渠道汇总'

    def sync_date_granularity(self, *_):
        self.holding_date.setVisible(self.today_snapshot() or self.broker_people_view())
        self.person.setVisible(not self.broker_people_view())
        self.ledger.setEnabled(not self.broker_people_view())
        self.live_panel.summary.setVisible(not self.broker_people_view())
        self.date_button.setVisible(not self.fixed_asset_period())
        if self.fixed_asset_period():
            self.date_button.menu().hide()
        monthly = self.monthly_filter()
        self.start.set_month_mode(monthly)
        self.as_of.set_month_mode(monthly, month_end=True)
        self.start_enabled.setText('限制起始月份' if monthly else '限制起始日期')
        self.update_filter_chips()

    def make_trade_filters(self):
        return TradeFilters(self.side.currentData(), self.link_status.currentData(), self.source_file.text().strip(), self.include_reversed.isChecked(), self.include_reference.isChecked())

    def update_filter_chips(self):
        monthly = self.monthly_filter()
        fmt = 'yyyy-MM' if monthly else 'yyyy-MM-dd'
        start = self.start.date().toString(fmt) if self.start_enabled.isChecked() else ('2025-01' if monthly else '2025-01-01')
        end = self.as_of.date().toString(fmt)
        self.date_button.setText(f"{start} – {end}")
        chips = []
        for name in COMBOS:
            if self.broker_people_view() and name in ('person', 'ledger'):
                continue
            control = getattr(self, name)
            if control.currentIndex():
                chips.append((name, control.currentText()))
        if self.accounts.selected_ids():
            chips.append(("accounts", self.accounts.text()))
        for name in CHECKS:
            if name == 'start_enabled' and self.fixed_asset_period():
                continue
            control = getattr(self, name)
            if control.isChecked() and control.text():
                chips.append((name, control.text()))
        for name in ("search", "source_file"):
            value = getattr(self, name).text().strip()
            if value:
                chips.append((name, value[:18] + ("…" if len(value) > 18 else "")))
        self.chips.set_filters(chips)

    def clear_filter(self, name):
        control = getattr(self, name)
        self._filter_batch = True
        if name == "accounts":
            control.reset()
        elif name in COMBOS:
            control.setCurrentIndex(0)
        elif name in CHECKS:
            control.setChecked(False)
            if name == "start_enabled":
                self.start.setDate(QDate(2025, 1, 1))
                self.as_of.setDate(QDate.currentDate())
        else:
            control.clear()
        self._filter_batch = False
        self.search_timer.stop()
        self.filters_changed()

    def reset_filters(self):
        self._filter_batch = True
        for name in COMBOS:
            getattr(self, name).setCurrentIndex(0)
        personal = -1
        for i in range(self.person.count()):
            text = self.person.itemText(i)
            if text == "个人" or text.startswith("个人"):
                personal = i
                break
        if personal >= 0:
            self.person.setCurrentIndex(personal)
        for name in CHECKS:
            getattr(self, name).setChecked(False)
        self.search.clear()
        if hasattr(self, "stock"):
            self.stock.setCurrentIndex(0)
            if hasattr(self.stock, "lineEdit") and self.stock.lineEdit():
                self.stock.lineEdit().clear()
        self.source_file.clear()
        self.accounts.reset()
        self.start.setDate(QDate(2025, 1, 1))
        self.as_of.setDate(QDate.currentDate())
        self.holding_date.setDate(QDate.currentDate())
        self.start_enabled.setChecked(True)
        self._filter_batch = False
        self.search_timer.stop()
        self.filters_changed()

    def reset_asset_filters(self):
        if self.ownership_view():
            self.asset_page.ownership_page.reset();return
        if hasattr(self, "dashboard"):
            month = self.dashboard.month.findText(QDate.currentDate().toString("yyyy-MM"))
            year = self.dashboard.year.findText(QDate.currentDate().toString("yyyy"))
            if month >= 0:
                self.dashboard.month.setCurrentIndex(month)
            if year >= 0:
                self.dashboard.year.setCurrentIndex(year)
        if hasattr(self, "asset_page"):
            if hasattr(self.asset_page, "local_search"):
                self.asset_page.local_search.clear()
            if hasattr(self.asset_page, "channel_overview"):
                self.asset_page.channel_overview.selected_channel = None
                self.asset_page.channel_overview.update()
            if hasattr(self.asset_page, "stock_status_filter"):
                self.asset_page.stock_status_filter.setCurrentIndex(0)
            if getattr(self.asset_page, "broker_page", None):
                broker = self.asset_page.broker_page
                if broker.stock.count():
                    broker.stock.setCurrentIndex(0)
                broker.chart.person = None
                if hasattr(broker, "summary"):
                    broker.summary.clearSelection()
                if hasattr(broker, "detail"):
                    broker.detail.clearSelection()
            self.asset_page.apply_local_filter()
        if hasattr(self, "earnings_page"):
            self.earnings_page.month.setDate(QDate.currentDate())
        self.reset_filters()
        self.ensure_analytics()

    def capture_state(self):
        state = {name: getattr(self, name).currentData() for name in COMBOS}
        state.update({name: getattr(self, name).isChecked() for name in CHECKS})
        state.update({name: getattr(self, name).text() for name in ("search", "source_file")})
        state.update({name: getattr(self, name).date().toString("yyyy-MM-dd") for name in ("as_of", "start", "holding_date")})
        state.update(accounts=self.accounts.selected_ids(), page_number=self.page_number, sort_column=self.sort_column, descending=self.descending,
                     scroll=self.table.verticalScrollBar().value(), horizontal_scroll=self.table.horizontalScrollBar().value(),
                     reports=(self.asset_page.selector.currentText(), self.performance_page.selector.currentText()))
        return state

    def apply_extra_state(self, state):
        for name in ("side", "link_status"):
            control = getattr(self, name)
            control.setCurrentIndex(max(0, control.findData(state.get(name))))
        self.source_file.setText(state.get("source_file", ""))
        for name in ("include_reversed", "include_reference"):
            getattr(self, name).setChecked(state.get(name, False))
        self.update_filter_chips()

    def apply_state(self, state):
        self._filter_batch = True
        self.disabled.setChecked(state.get("disabled", False))
        self._populate_persons()
        for name in COMBOS:
            control = getattr(self, name)
            control.setCurrentIndex(max(0, control.findData(state.get(name))))
        for name in CHECKS:
            getattr(self, name).setChecked(state.get(name, False))
        for name in ("search", "source_file"):
            getattr(self, name).setText(state.get(name, ""))
        for name in ("as_of", "start", "holding_date"):
            if state.get(name):
                getattr(self, name).setDate(QDate.fromString(state[name], "yyyy-MM-dd"))
        for identifier, action in self.accounts.actions_by_id.items():
            action.setChecked(identifier in (state.get("accounts") or ()))
        self.page_number = state.get("page_number", 0)
        self.sort_column = state.get("sort_column", 0)
        self.descending = state.get("descending", True)
        order = Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder
        for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
            header.setSortIndicatorShown(True)
            header.setSortIndicator(self.sort_column, order)
        for page, report in zip((self.asset_page, self.performance_page), state.get("reports", ())):
            page.selector.setCurrentText(report)
        self._filter_batch = False
        self.search_timer.stop()
        self.update_filter_chips()

    def route_changed(self, page, target):
        if page == 3 and target in ('当前持仓', '股票汇总', '人员资产', '券商资产', '渠道汇总'):
            # 兼容旧导航入口，自动映射跳转至统一「持仓管理」对应的视图与筛选
            page = 22
            if hasattr(self, 'holdings_page'):
                if target == '当前持仓':
                    self.holdings_page.set_view("detail", dimension="person", status="持有中")
                elif target == '股票汇总':
                    self.holdings_page.set_view("detail", dimension="stock")
                elif target == '人员资产':
                    self.holdings_page.set_view("detail", dimension="person")
                elif target in ('券商资产', '渠道汇总'):
                    self.holdings_page.set_view("detail", dimension="broker")
        route = (page, target)
        if not self._restoring_history and route != self._route:
            if self._history:
                self._history[self._history_index] = (self._route, getattr(self, "_drill_origin", None) or self.capture_state())
            self._history = self._history[:self._history_index + 1]
            self._history.append((route, self.capture_state()))
            self._history_index = len(self._history) - 1
        self._route = route
        self.pages.setCurrentIndex(page)
        if page in (5, 16, 17, 18) and self.start_enabled.isChecked() and self.start.date() == self.as_of.date():
            self._filter_batch = True
            self.start_enabled.setChecked(False)
            self.start.setDate(QDate(2025, 1, 1))
            self.as_of.setDate(QDate.currentDate())
            self._filter_batch = False
        self.live_panel.setVisible(page in (2, 3))
        title = TITLES[page]
        if page in (3, 4) and isinstance(target, str):
            report = self.asset_page if page == 3 else self.performance_page
            report.selector.setCurrentText("交易回放" if target == "replay" else target)
            if page == 3 and target == '当前持仓' and self.live_panel.enabled.isChecked() and '实时持仓' in report.reports:
                report.selector.setCurrentText('实时持仓')
            if page == 3 and target == '股票归属' and report.broker_page and report.broker_page.mode.currentText() != '股票归属':
                report.broker_page.mode.setCurrentText('股票归属')
            elif page == 3 and target == '渠道汇总' and report.broker_page and report.broker_page.mode.currentText() == '股票归属':
                report.broker_page.mode.setCurrentIndex(0)
            title = report.selector.currentText()
        elif page in (3, 4):
            title = (self.asset_page if page == 3 else self.performance_page).selector.currentText()
        if page == 5 and isinstance(target, int):
            self.charts_page.selector.setCurrentIndex(target)
        if page == 17:
            if isinstance(target, str):
                self.top_days_page.set_view_mode(target)
                title = "Top月汇总" if target == "month" else "Top日汇总"
            else:
                title = "Top日/月汇总"
        self.heading.setText(title)
        if self.broker_people_view():
            self.heading.setText('股票归属' if self.asset_page.selector.currentText() == '股票归属' else '券商资产 · 人员盈亏')
        if self.ownership_view():
            self.heading.setText('股票归属与成本分析')
            self.description.setText('查看单只股票在不同人员名下的持仓分布、成本与收益表现。')
            self.filters_widget.hide();self.live_panel.hide();self.banner.hide()
            self.asset_reset_button.show();self.asset_page.ownership_page.activate()
            self.update_history_buttons();return
        self.sync_date_granularity()
        self.description.setText("按日期查看持仓、当日盈亏与累计盈亏；今天每10秒刷新行情，历史日期使用收盘数据。" if self.today_snapshot() else DESCRIPTIONS[page])
        if page == 17:
            if target == "month":
                self.description.setText("查看历史最高与最低单月持仓收益与收益率汇总（最爽与最惨月份极值）。")
            elif target == "day":
                self.description.setText("查看历史最高与最低单日持仓收益与收益率汇总（最爽与最惨交易日极值）。")
        if self.broker_people_view():
            self.description.setText('查看某只股票在各券商渠道各人员的持有股数与占比。' if self.asset_page.selector.currentText() == '股票归属' else '选择渠道与日期，查看人员当日/累计盈亏；切换“股票归属”查看某只股票各人持有股数与占比。')
        self.stat_widget.setVisible(page == 0)
        self.side.setVisible(page == 0)
        self.export_button.setVisible(page == 0)
        is_asset = page == 2 or page == 14 or (
            page == 3 and (
                target in ('今日盈亏', '当前持仓', '实时持仓', '股票汇总', '人员资产', '券商资产', '渠道汇总', '股票归属') or
                self.asset_page.selector.currentText() in ('今日盈亏', '当前持仓', '实时持仓', '股票汇总', '人员资产', '券商资产', '渠道汇总', '股票归属')
            )
        )
        self.asset_reset_button.setVisible(is_asset)
        if hasattr(self.live_panel, 'trend_tabs'):
            self.live_panel.trend_tabs.setVisible(self.today_snapshot())
        self.filters_widget.setVisible(page in range(2, 11) or page in (0, 14, 17))
        if page == 0 and self.current_page:
            self.banner.setText(getattr(self, "_trade_notice", ""))
            self.banner.setVisible(not self.current_page.rows or self.include_reversed.isChecked() or self.include_reference.isChecked())
        elif page not in range(2, 11):
            self.banner.hide()
        if page == 2 and target == "calendar":
            QTimer.singleShot(0, lambda: self.dashboard.scroll.ensureWidgetVisible(self.dashboard.calendar_table))
        if page == 17 and self.analytics:
            self.top_days_page.set_analytics(self.analytics)
        if page == 18 and hasattr(self, 'monthly_realtime_page'):
            self.monthly_realtime_page.sync_person_from_main(self.person.currentData())
            self.monthly_realtime_page.set_analytics(self.analytics)
        if page == 22 and hasattr(self, 'holdings_page') and self.analytics:
            self.holdings_page.set_analytics(self.get_holdings_analytics())
        self.ensure_analytics()
        if page == 0 and self.info is not None and (self.current_page is None or self.current_page.scope != self.make_scope()):
            self.refresh()
        self.update_history_buttons()

    def update_history_buttons(self):
        values = (self._history_index > 0, self._history_index + 1 < len(self._history))
        for button, enabled in zip(self.history_buttons, values):
            button.setEnabled(enabled)
        if hasattr(self, "back_action"):
            self.back_action.setEnabled(values[0])
            self.forward_action.setEnabled(values[1])

    def move_history(self, step):
        index = self._history_index + step
        if not 0 <= index < len(self._history):
            return
        self._history[self._history_index] = (self._route, self.capture_state())
        self._history_index = index
        route, state = self._history[index]
        self._restoring_history = True
        self.apply_state(state)
        self.navigation.select(*route)
        for page, report in zip((self.asset_page, self.performance_page), state.get("reports", ())):
            page.selector.setCurrentText(report)
        self._pending_scroll = (state.get("scroll", 0), state.get("horizontal_scroll", 0))
        self.refresh()
        self._restoring_history = False
        self.update_history_buttons()

    def focus_page_search(self):
        target = getattr(self.pages.currentWidget(), "local_search", None) or self.search
        if not target.isVisible():
            self.navigation.setCurrentRow(0)
            target = self.search
        target.setFocus()
        target.selectAll()

    def ensure_analytics(self):
        if self.ownership_view():
            self.asset_page.ownership_page.activate();return
        if not self._filter_batch and self.pages.currentIndex() == 21:
            self.monitor_page.activate()
            return
        if not self._filter_batch and self.pages.currentIndex() == 14:
            self.earnings_page.activate()
            return
        if self._filter_batch or self.info is None or not self.filters_widget.isEnabled() or self.pages.currentIndex() not in (*range(2, 11), 15, 16, 17, 18):
            return
        scope = self.make_scope()
        if self.broker_people_view():
            bundle = self.asset_page.broker_page.bundle
            if bundle is not None and bundle.aggregate.scope == scope:
                if self.analytics is not bundle.aggregate or self.asset_page.broker_page.result is None:
                    self._analytics_ready(bundle.aggregate)
                else:
                    self.asset_page.broker_page.show_result()
                return
            if getattr(self, '_people_scope', None) == scope:
                return
            self.asset_page.broker_page.clear()
            self._people_scope = scope
            self.banner.show_status('正在按人员回放持仓与收益…', 'loading')
            self._submit('people', lambda cancel: self.analytics_service.calculate_people(scope, cancel=cancel), self._people_ready)
            return
        if (self.analytics and self.analytics.scope == scope) or self._analytics_scope == scope:
            if self.analytics and self.pages.currentIndex() == 17 and self.top_days_page.analytics is not self.analytics:
                self.top_days_page.set_analytics(self.analytics)
            if self.analytics and self.pages.currentIndex() == 18 and hasattr(self, 'monthly_realtime_page'):
                self.monthly_realtime_page.set_analytics(self.analytics)
            return
        self._analytics_scope = scope
        self.banner.show_status("正在计算当前范围的持仓与收益…", "loading")
        service = self.analytics_service
        self._submit("analytics", lambda cancel: service.calculate(scope, cancel=cancel), self._analytics_ready)

    def restore_scroll(self):
        value = self._pending_scroll
        if value is not None:
            self._pending_scroll = None
            QTimer.singleShot(0, lambda: (self.table.verticalScrollBar().setValue(value[0]), self.table.horizontalScrollBar().setValue(value[1])))
