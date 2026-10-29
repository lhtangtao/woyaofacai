"""Qt controller: 10-second render timer, network work isolated in QuoteFeed."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from portfolio.domain.live_quotes import UTC
from portfolio.engine.live_valuation import live_holdings, live_metrics, live_summaries
from portfolio.engine.today_holdings import lifetime_summary, today_holdings
from portfolio.services.live_quotes import QuoteFeed
from portfolio.paths import CREDENTIALS_PATH, PROJECT_DIR


class LiveQuotesPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.feed, self.path = window, None, ''
        self.analytics = None
        self.instruments = {}
        self.source_path = None
        self.universe_key, self.universe_symbols = None, set()
        self.observation_symbols = set()
        self.history_signature = None
        self.intraday_busy = False
        self.intraday_requests = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.enabled = QCheckBox('长桥实时行情 · 10秒')
        self.choose = QPushButton('行情配置')
        self.choose.setObjectName('quiet')
        self.status = QLabel('休市时暂停；历史报表使用收盘数据')
        self.status.setObjectName('secondary')
        self.status.setWordWrap(True)
        self.refresh_time = QLabel('')
        self.refresh_time.setObjectName('secondary')
        row.addWidget(self.enabled)
        row.addWidget(self.choose)
        row.addWidget(self.status)
        row.addSpacing(12)
        row.addWidget(self.refresh_time)
        row.addStretch(1)
        # 今日盈亏走势周期切换：当日走势（默认）/ 当月 / 当年
        from PySide6.QtWidgets import QTabBar
        self.trend_tabs = QTabBar(self)
        self.trend_tabs.setDrawBase(False)
        self.trend_tabs.setExpanding(True)
        self.trend_tabs.setMinimumWidth(420)
        for text in ('当日走势', '当月', '当年'):
            self.trend_tabs.addTab(text)
        self.trend_tabs.setToolTip('切换今日盈亏页的走势图：当日五分钟走势、当月累计走势、当年累计走势')
        self.trend_tabs.hide()
        row.addWidget(self.trend_tabs, 2)
        layout.addLayout(row)
        self.summary = QLabel('实时估值尚未启用')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.choose.clicked.connect(self.choose_config)
        self.enabled.toggled.connect(self.toggle)
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.render)
        self.saved_chart_timer = QTimer(self)
        self.saved_chart_timer.setInterval(5000)
        self.saved_chart_timer.timeout.connect(self.refresh_saved_charts)
        self.saved_chart_timer.start()
        settings = window.settings
        saved_config = Path(str(settings.value('live_credentials', ''))) if settings else None
        self.path = str(saved_config) if saved_config and saved_config.is_file() and saved_config.resolve().is_relative_to(PROJECT_DIR) else (str(CREDENTIALS_PATH) if CREDENTIALS_PATH.is_file() else '')
        self.enabled.setChecked(str(settings.value('live_quotes_enabled', 'true')).lower() == 'true' if settings else False)
        self._last_cash_fetch = 0
        self.hide()

    def refresh_saved_charts(self):
        """Saved curves must not wait for full financial reports or a live feed."""
        window=self.window
        if not getattr(window,'info',None) or not getattr(self,'source_path',None) or getattr(self,'is_demo',True) or not window.today_snapshot():return
        scope=window.make_scope()
        if scope.reporting_currency!='USD':return
        from types import SimpleNamespace
        from portfolio.domain.live_quotes import market_trade_day,us_session
        now=datetime.now(UTC)
        _,days,halves,_=window.quote_store.load(now)
        curve_scope=scope
        current=scope.as_of==QDate.currentDate().toPython()
        if current:curve_scope=replace(scope,as_of=market_trade_day(now,days,halves))
        page=window.asset_page
        mode=getattr(page,'trend_mode',0)
        if mode==0:
            page.intraday_chart.bind(window.quote_store,self.source_path,curve_scope,None,
                                     us_session(now,days,halves) if current else None,now,
                                     trading_days=tuple(d.isoformat() for d in sorted(days)))
            page.intraday_chart.setVisible(True)
            page.intraday_chart.draw(page.local_search.text(),page.channel_overview.selected_channel)
        elif hasattr(window,'asset_trend'):
            window.asset_trend.set_parent_filters(page.channel_overview.selected_channel,page.local_search.text())
            if not window.analytics or window.analytics.scope!=scope:
                window.asset_trend.set_analytics(SimpleNamespace(scope=scope,source_identity=str(self.source_path)))
            window.asset_trend.show()

    def refresh_channel_cash(self, force=False):
        """后台异步获取长桥和嘉信的最新实时现金，写入 QuoteStore 并刷新渠道汇总。"""
        import time
        now = time.time()
        if not force and now - getattr(self, "_last_cash_fetch", 0) < 300:
            return
        self._last_cash_fetch = now
        store = self.window.quote_store
        if not store:
            return

        def task(cancel):
            try:
                from portfolio.services.channel_cash import fetch_all_channel_cash
                return fetch_all_channel_cash()
            except Exception:
                return {}

        def ready(cash_dict):
            if not cash_dict:
                return
            for ch, val in cash_dict.items():
                store.save_channel_cash(ch, val)
            if self.analytics and hasattr(self.window, "dashboard"):
                dash_sum = self.window.dashboard.summaries
                if dash_sum.selector.currentText() == "渠道汇总":
                    rep = self.window.dashboard.analytics.reports.get("渠道汇总")
                    if rep:
                        dash_sum.reports["渠道汇总"] = rep
                        dash_sum.report = rep
                        dash_sum.apply_local_filter()
                        dash_sum.show_page()

        self.window._submit("channel_cash_fetch", task, ready)

    def source_changed(self, info):
        self.observation_symbols = set()
        self.stop()
        self.analytics = None
        self.universe_key, self.universe_symbols = None, set()
        self.history_signature = None
        self.source_path = info.path
        self.instruments = {i['id']: i for i in info.instruments}
        if not self.path or not Path(self.path).is_file():
            candidate = CREDENTIALS_PATH
            if candidate.is_file():
                self.path = str(candidate)
        self.is_demo = info.is_demo
        self.render()

    def choose_config(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择本机长桥凭证（不上传、不复制）', self.path,
                                             '长桥配置 (*.py *.json)')
        if path:
            self.path = path
            if self.window.settings:
                self.window.settings.setValue('live_credentials', path)
            self.stop()
            self.toggle(self.enabled.isChecked())

    def toggle(self, checked):
        if self.window.settings:
            self.window.settings.setValue('live_quotes_enabled', checked)
        if checked:
            self.timer.start()
            self.warm_universe()
        else:
            self.stop()
            if self.analytics:
                dash_sum = self.window.dashboard.summaries
                dash_sum.reports = self.analytics.reports
                if dash_sum.selector.currentText() in dash_sum.reports:
                    dash_sum.report = dash_sum.reports[dash_sum.selector.currentText()]
                    dash_sum.apply_local_filter()
                    dash_sum.show_page()
            self.window.asset_page.reports = {k: v for k, v in self.window.asset_page.reports.items() if k != '实时持仓'}
            if self.window.asset_page.selector.currentText() == '实时持仓':
                self.window.asset_page.selector.setCurrentText('当前持仓')
        self.render()

    def bind(self, analytics):
        self.analytics = analytics
        now = datetime.now(UTC)
        if not self.enabled.isChecked() or analytics.scope.as_of != QDate.currentDate().toPython() or analytics.scope.reporting_currency != 'USD' or getattr(self, 'is_demo', True):
            reference, *_ = live_holdings(analytics, self.instruments, {}, None, now)
            self.update_today(reference, None)
        self.warm_universe()
        self.render()
        page = self.window.asset_page
        if '实时持仓' in page.reports and page.selector.currentText() == '当前持仓':
            page.selector.setCurrentText('实时持仓')

    def warm_universe(self):
        """Subscribe all held symbols once, so a new owner can reuse quotes."""
        if not self.enabled.isChecked() or self.analytics is None or getattr(self, 'is_demo', True) or self.analytics.scope.as_of != QDate.currentDate().toPython():
            return
        from portfolio.domain.models import Ledger
        service = self.window.analytics_service
        if service is None:
            return
        key = (service.identity(), QDate.currentDate().toString('yyyy-MM-dd'))
        if key == self.universe_key:
            return
        self.universe_key = key
        scope = replace(self.analytics.scope, ledger=Ledger.PERSON, person_ids=None, account_ids=None, instrument_ids=None, category=None, search='', include_disabled=True, reporting_currency='USD')
        def task(cancel):
            try:
                return service.calculate(scope, cancel=cancel).reports['当前持仓'].rows
            except Exception:
                return None
        def ready(rows):
            if self.universe_key != key:
                return
            if rows is None:
                self.universe_key = None
                return
            self.universe_symbols = {self.instruments[r.metadata['instrument_id']]['symbol']+'.US' for r in rows if self.instruments.get(r.metadata['instrument_id'], {}).get('market') == 'US'}
            if self.analytics is not None:
                self.render()
        self.window._submit('quote_universe', task, ready)

    def invalidate(self):
        self.analytics = None
        pending = self.window.jobs.get('intraday_history')
        if pending:
            pending.cancel.set()
        self.intraday_busy = False
        self.summary.setText('等待当前筛选的持仓计算完成')

    def stop(self):
        self.timer.stop()
        if self.feed:
            self.feed.stop()
            self.feed = None
        self.refresh_time.setText('')

    def update_today(self, report, session, quotes=None, now=None):
        today = today_holdings(self.analytics, report, session)
        page = self.window.asset_page
        page.cumulative_totals = lifetime_summary(self.analytics, report)
        page.snapshot_date = self.analytics.scope.as_of.isoformat()
        page.reports = {**page.reports, '今日盈亏': today}
        page.intraday_chart.bind(self.window.quote_store, self.source_path, self.analytics.scope,
                                 today, session, now or datetime.now(UTC),
                                 record=self.enabled.isChecked() and not getattr(self, 'is_demo', True)
                                 and self.analytics.scope.reporting_currency == 'USD',
                                 trading_days=self.analytics.trading_days if self.analytics else ())
        if session is None and page.intraday_chart.history and not page.intraday_chart.is_fallback:
            from portfolio.engine.today_holdings import align_with_intraday_history
            today = align_with_intraday_history(today, page.intraday_chart.history)
            page.reports = {**page.reports, '今日盈亏': today}
        if page.broker_page and page.broker_page.bundle is not None and page.broker_page.bundle.aggregate.scope == self.analytics.scope:
            page.broker_page.render(self.instruments, quotes or {}, session, now or datetime.now(UTC))
        if page.selector.currentText() == '今日盈亏':
            scroll, number = page.table.verticalScrollBar().value(), page.page
            page.report = today
            page.note.setText(today.note)
            if tuple(page.table.finance_model.columns) != today.columns:
                page.select_report('今日盈亏')
            page.apply_local_filter()
            page.page = min(number, max(0, (len(page.sorted_rows) - 1) // 200))
            page.show_page()
            page.table.verticalScrollBar().setValue(scroll)

    def backfill_intraday(self, today, now):
        # Kept for callers from older UI code; the application producer owns all
        # collection and historical calculation, independently of this panel.
        return

    def refresh_history(self):
        """Rebind on rollover/new completed snapshots, outside the quote loop."""
        if not self.window.quote_store or getattr(self.window, 'info', None) is None or self.analytics is None or getattr(self, 'is_demo', True):
            return False
        local_day = QDate.currentDate().toString('yyyy-MM-dd')
        from portfolio.services.derived_dependencies import source_identity
        signature = (local_day,self.window.quote_store.history_identity(),source_identity(self.source_path,financial_only=True))
        prior, self.history_signature = self.history_signature, signature
        if prior is None or prior == signature:
            return False
        if prior[0] != local_day and self.window.holding_date.date().toString('yyyy-MM-dd') == prior[0]:
            self.window.holding_date.blockSignals(True)
            self.window.holding_date.setDate(QDate.currentDate())
            self.window.holding_date.blockSignals(False)
        self.window.clear_analytics()
        self.window.refresh()
        return True

    def update_closed_prices(self, quotes=None, session=None):
        page = getattr(self.window, 'performance_page', None)
        if not page or page.selector.currentText() != '清仓收益' or '清仓收益' not in page.reports:
            return
        from portfolio.services.closed_quotes import closed_current_prices
        now = datetime.now(UTC)
        cached = self.window.quote_store.load(now)[0] if self.window.quote_store else {}
        report = closed_current_prices(page.reports['清仓收益'], self.instruments,
                                       {**cached, **(quotes or {})}, now, session,
                                       self.window.currency.currentData())
        page.reports = {**page.reports, '清仓收益': report}
        page.report = report
        page.note.setText(report.note)
        number = page.page
        if tuple(page.table.finance_model.columns) != report.columns:
            page.select_report('清仓收益')
        else:
            page.apply_local_filter()
        page.page = min(number, max(0, (len(page.sorted_rows) - 1) // 200))
        page.show_page()

    def render(self):
        self.update_closed_prices()
        self.refresh_channel_cash()
        if self.refresh_history():
            return
        if not self.enabled.isChecked():
            if self.window.dashboard.analytics:
                self.window.dashboard.set_metrics(self.window.dashboard.analytics.metrics)
                dash_sum = self.window.dashboard.summaries
                dash_sum.reports = self.window.dashboard.analytics.reports
                if dash_sum.selector.currentText() in dash_sum.reports:
                    dash_sum.report = dash_sum.reports[dash_sum.selector.currentText()]
                    dash_sum.apply_local_filter()
                    dash_sum.show_page()
            if self.analytics is not None:
                reference, *_ = live_holdings(self.analytics, self.instruments, {}, None, datetime.now(UTC))
                self.update_today(reference, None)
                asset_page = self.window.asset_page
                asset_page.reports = {k: v for k, v in self.analytics.reports.items() if k != '实时持仓'}
                if asset_page.selector.currentText() in asset_page.reports:
                    asset_page.report = asset_page.reports[asset_page.selector.currentText()]
                    if tuple(asset_page.table.finance_model.columns) != asset_page.report.columns:
                        asset_page.select_report(asset_page.selector.currentText())
                    else:
                        asset_page.apply_local_filter()
                        asset_page.show_page()
            self.status.setText('实时行情已关闭')
            self.summary.setText('历史持仓和收益报表继续使用数据库数据')
            return
        if self.analytics is None:
            self.status.setText('等待当前筛选的持仓计算完成')
            self.summary.setText('等待当前筛选的持仓计算完成')
            return
        if getattr(self, 'is_demo', True) or self.analytics.scope.as_of != QDate.currentDate().toPython():
            if self.feed:
                self.feed.set_symbols(())
            self.status.setText('演示或历史截止日期：不连接实时行情')
            self.summary.setText('请选择今天作为截止日期查看实时估值')
            return
        if self.analytics.scope.reporting_currency != 'USD':
            if self.feed:
                self.feed.set_symbols(())
            self.status.setText('实时估值暂限USD原币，请切换币种')
            self.summary.setText('当前显示历史报表，未混入其他币种实时价格')
            return
        if not self.path:
            self.status.setText('尚未配置长桥凭证，请点击“行情配置”')
            self.summary.setText('历史持仓与收益报表继续可用')
            return
        holdings = list(self.analytics.reports['当前持仓'].rows)
        if self.window.performance_page.selector.currentText() == '清仓收益':
            holdings.extend(self.window.performance_page.reports.get('清仓收益', self.analytics.reports['清仓收益']).rows)
        people = self.window.asset_page.broker_page
        if self.window.broker_people_view() and people and people.bundle is not None:
            holdings.extend(people.bundle.broker.reports['当前持仓'].rows)
        symbols = {self.instruments[r.metadata['instrument_id']]['symbol'] + '.US'
                   for r in holdings
                   if self.instruments.get(r.metadata['instrument_id'], {}).get('market') == 'US'}
        symbols |= self.universe_symbols
        symbols |= self.observation_symbols
        if len(symbols) > 500:
            self.status.setText('持仓超过长桥500只订阅上限，请缩小筛选范围')
            return
        if self.feed is None:
            self.feed = QuoteFeed(self.path, store=self.window.quote_store)
            self.feed.set_symbols(symbols)
            self.feed.start()
        else:
            self.feed.set_symbols(symbols)
        self.timer.start()
        status, session, quotes = self.feed.snapshot()
        self.status.setText(status)
        if self.window.quote_store:
            self.status.setToolTip('本机行情缓存：'+str(self.window.quote_store.path)+'\n每10秒保存最新报价；原始交易及正式收盘数据库保持原口径。')
        if session is None and '休市' not in status:
            self.window.dashboard.waiting_live()
            self.summary.setText('正在读取本时段报价，当前盈亏计算中…')
            return
        now = datetime.now(UTC)
        self.update_closed_prices(quotes, session)
        report, total, floating, count = live_holdings(self.analytics, self.instruments, quotes, session, now)
        self.window.dashboard.set_metrics(live_metrics(self.analytics, report, session, now))
        self.update_today(report, session, quotes, now)
        def money(value):
            return f'{value:,.2f}' if value is not None else '— 数据未完整'
        time_str = now.astimezone().strftime("%H:%M:%S")
        self.refresh_time.setText(f'最近刷新时间: {time_str}')
        self.summary.setText(f'实时/参考估值（USD）：持仓市值 {money(total)}　浮动盈亏 {money(floating)}　新鲜报价 {count}/{len(report.rows)} 条持仓')
        self.summary.setToolTip('新鲜报价为本交易时段且时间差不超过3分钟；其余使用已标记的参考价格。此摘要不替换历史收益和正式收盘统计。')
        page = self.window.asset_page
        summaries = live_summaries(self.analytics, report, total, session, now)
        dash_sum = self.window.dashboard.summaries
        dash_sum.reports = {**dash_sum.reports, **summaries}
        if dash_sum.selector.currentText() in summaries:
            scroll = dash_sum.table.verticalScrollBar().value()
            dash_sum.report = summaries[dash_sum.selector.currentText()]
            dash_sum.apply_local_filter()
            dash_sum.show_page()
            dash_sum.table.verticalScrollBar().setValue(scroll)
        page.reports = {**page.reports, '实时持仓': report, **summaries}
        if page.selector.currentText() in summaries:
            scroll = page.table.verticalScrollBar().value()
            page.report = summaries[page.selector.currentText()]
            if tuple(page.table.finance_model.columns) != page.report.columns:
                page.select_report(page.selector.currentText())
            else:
                page.apply_local_filter()
                page.show_page()
            page.table.verticalScrollBar().setValue(scroll)
        elif page.selector.currentText() == '实时持仓':
            scroll = page.table.verticalScrollBar().value()
            current_page = page.page
            page.report = report
            if tuple(page.table.finance_model.columns) != report.columns:
                page.select_report('实时持仓')
            page.apply_local_filter()
            page.page = min(current_page, max(0, (len(page.sorted_rows) - 1) // 200))
            page.show_page()
            page.table.verticalScrollBar().setValue(scroll)

        # 同步更新主财务分析对象的报表缓存（含盘中实时估值的人员资产、渠道汇总等）
        self.analytics.reports = {**self.analytics.reports, '实时持仓': report, **summaries}

        # 联动更新现代持仓管理页（HoldingsPage）
        if hasattr(self.window, "holdings_page") and self.window.holdings_page:
            try:
                self.window.holdings_page.analytics = self.analytics
                if self.window.holdings_page.isVisible():
                    if getattr(self.window.holdings_page, "view_mode", "dashboard") == "dashboard":
                        self.window.holdings_page.refresh_dashboard()
                    else:
                        self.window.holdings_page.refresh_detail()
            except Exception as e:
                pass
