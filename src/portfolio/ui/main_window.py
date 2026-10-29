import hashlib
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

from PySide6.QtCore import QDate, QSettings, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
)

from portfolio.db.repositories.trades import SORT_FIELDS
from portfolio.domain.models import Ledger, QueryScope, exact_text
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.export_service import export_page, export_report
from portfolio.services.fx_service import POLICY, FxStore
from portfolio.services.trades_service import TradesService
from portfolio.ui.interactions import InteractionMixin, batch_drill
from portfolio.ui.theme import ThemeManager
from portfolio.ui.workers import Worker


class MainWindow(InteractionMixin, QMainWindow):
    def __init__(self, *, settings=True):
        super().__init__()
        self.setWindowTitle("我要发财 · 投资账本")
        from portfolio.ui.icons import app_icon
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)
        self.service = None
        self.info = None
        self.current_page = None
        self.analytics = None
        self.analytics_service = None
        self.page_number = 0
        self.sort_column = 0
        self.descending = True
        self.jobs = {}
        self.restore_state = None
        self._filter_batch = False
        self._analytics_scope = None
        self._route = (0, None)
        self._history = []
        self._history_index = -1
        self._restoring_history = False
        self._pending_scroll = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.settings = QSettings("MoneyPortfolio", "Desktop") if settings else None
        from PySide6.QtCore import QStandardPaths
        from portfolio.paths import quote_cache_path
        self.fx_store = FxStore(quote_cache_path().with_name("fx.sqlite3")) if settings else None
        from portfolio.services.quote_store import QuoteStore
        from portfolio.paths import quote_cache_path
        self.quote_store = QuoteStore(quote_cache_path())
        self._fx_busy = False
        self._fx_attempt = None
        self.fx_timer = QTimer(self)
        self.fx_timer.setInterval(3600000)
        self.fx_timer.timeout.connect(lambda: self.update_fx())
        if self.fx_store:
            self.fx_timer.start()
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self.filters_changed)
        self._build_ui()
        self.history_coverage_button = QPushButton('历史行情：尚未检查')
        self.history_coverage_button.setToolTip('检查当月全部历史持仓的日收盘价是否落库')
        self.history_coverage_button.clicked.connect(self.show_history_coverage)
        self.statusBar().addPermanentWidget(self.history_coverage_button)
        self._history_coverage_details = '尚未连接数据库。'
        self._history_coverage_dialog = None
        self.theme_manager = ThemeManager(self)
        self.theme_manager.changed.connect(self.theme_changed)
        self._build_menu()
        self._history = [(self._route, self.capture_state())]
        self._history_index = 0
        self.update_history_buttons()
        if self.settings:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        self._set_theme(self.settings.value("theme", "light") if self.settings else "light")
        if self.settings:
            self.green_profit.setChecked(str(self.settings.value("green_profit", "true")).lower() == "true")
        QApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._set_theme(self.theme))
        self.navigation.select(3, '今日盈亏')

    def _build_ui(self):
        from portfolio.ui.workspace import build_workspace
        build_workspace(self)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("文件")
        for text, shortcut, callback in (("选择数据库…", "Ctrl+O", self.choose_database),
                                          ("刷新", QKeySequence.StandardKey.Refresh, self.reconnect),
                                          ("查找", QKeySequence.StandardKey.Find, self.focus_page_search),
                                          ("退出", QKeySequence.StandardKey.Quit, self.close)):
            action = QAction(text, self)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
            file_menu.addAction(action)
        file_menu.addAction("更新人民币汇率（补齐历史）", lambda: self.update_fx(force=True))
        file_menu.addAction('重新检查当月收盘价入库', self.check_monthly_history)
        view = self.menuBar().addMenu("外观")
        for title, mode in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            action = view.addAction(title)
            action.triggered.connect(lambda checked=False, mode=mode: self._set_theme(mode))
        settings_action = QAction("数据源设置", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(lambda: self.navigation.setCurrentRow(13))
        view.addAction(settings_action)
        self.back_action = QAction("上一页面", self)
        self.back_action.setShortcut(QKeySequence("Alt+Left"))
        self.back_action.triggered.connect(lambda: self.move_history(-1))
        self.addAction(self.back_action)
        self.forward_action = QAction("下一页面", self)
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(lambda: self.move_history(1))
        self.addAction(self.forward_action)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("关于 Money3", lambda: QMessageBox.information(self, "关于 Money3", "Money3 0.3.0 · macOS 本地只读投资管理\n财务值：Decimal/e8\n账本：人员权益与券商成交独立\n标准会计口径尚未开放；Excel 差异见异常/对账页。\n无遥测、无云同步、无下单。"))
        help_menu.addAction("查看本地运行日志", self.show_log)

    def show_log(self):
        from PySide6.QtCore import QStandardPaths
        path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "logs/money3.log"
        try:
            text = path.read_text(encoding="utf-8")[-12000:]
        except OSError:
            text = "暂无运行日志。"
        QMessageBox.information(self, "本地日志（不含交易明细/Token）", str(path) + "\n\n" + text)

    def _set_theme(self, mode):
        self.theme = mode
        self.theme_manager.apply(mode)
        self.theme_selector.blockSignals(True)
        self.theme_selector.setCurrentIndex(max(0, self.theme_selector.findData(mode)))
        self.theme_selector.blockSignals(False)
        self.table.viewport().update()

    def theme_changed(self):
        if self.analytics:
            self.dashboard.set_analytics(self.analytics)
        for table in self.findChildren(type(self.table).__bases__[0]):
            table.viewport().update()
        self.charts_page.canvas.update()
        for c in self.distribution_page.canvases:
            c.update()
        for c in self.trend_page.canvases.values():
            c.update()
        self.top_days_page.update_canvases()
        if hasattr(self, "monthly_realtime_page"):
            self.monthly_realtime_page.canvas.update()
        if hasattr(self, "holdings_page"):
            self.holdings_page.dist_canvas.update()
        self.earnings_page.draw()
        self.monitor_page.draw_table()

    def _submit(self, name, task, success):
        previous = self.jobs.get(name)
        if previous:
            previous.cancel.set()
        worker = Worker(task)
        self.jobs[name] = worker
        worker.signals.done.connect(lambda result: self._job_ready(name, worker, result, success),
                                    Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(lambda message: self._job_error(name, message) if self.jobs.get(name) is worker and not worker.cancel.is_set() else None,
                                     Qt.ConnectionType.QueuedConnection)
        self.pool.start(worker)

    def _job_ready(self, name, worker, result, success):
        if self.jobs.get(name) is not worker or worker.cancel.is_set():
            return
        try:
            success(result)
        except Exception as exc:
            import logging
            import traceback
            frames = [(Path(f.filename).name, f.lineno, f.name) for f in traceback.extract_tb(exc.__traceback__)]
            logging.getLogger('portfolio').error('Result display failure lane=%s type=%s frames=%s', name, type(exc).__name__, frames)
            self._job_error(name, '结果展示失败，请刷新重试；原始数据未改动。')

    def _job_error(self, lane, message):
        if lane == "source":
            self.source_label.setText("连接失败 · 点击重新选择数据源")
            self.source_details.setPlainText("数据源连接失败\n" + str(self.service.path) + "\n\n" + message)
        if lane in ("source", "trades"):
            self._error(message)
        elif lane == "analytics":
            self.clear_analytics()
            if self.pages.currentIndex() != 0:
                self.banner.show_status(message, "error")
            if message.startswith('原始数据或参考行情在计算期间变化'):
                self.banner.show_status('账本或收盘基准正在更新，正在自动重试持仓计算；已有五分钟曲线可独立读取。','loading')
                QTimer.singleShot(1500,self.ensure_analytics)
        elif lane == "provenance":
            self.detail.setPlainText(message)
        else:
            if lane == 'people':
                self._people_scope = None
            self.banner.show_status(message, "error")

    def _error(self, message):
        for name in ("trades", "provenance", "analytics", "people", "reconcile", "report_provenance"):
            if name in self.jobs:
                self.jobs[name].cancel.set()
        self.banner.show_status(message, "error")
        self.drawer.clear()
        self.table.show_empty("查询未完成，请刷新重试", "重试")
        for card in self.trade_cards:
            card.set_value(None)
        self.table.finance_model.replace(())
        self.current_page = None
        self.detail.clear()
        self.count_label.setText("查询未完成；没有使用旧数据代替当前筛选结果")
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.export_button.setEnabled(False)
        self.clear_analytics()

    def cancel_queries(self):
        for worker in self.jobs.values():
            worker.cancel.set()
        self._error("查询已取消。可调整条件后刷新重试。")

    def choose_database(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择只读 SQLite 数据库", "", "SQLite (*.sqlite3 *.sqlite *.db);;所有文件 (*)")
        if path:
            self.open_database(path)

    def open_database(self, path, *, restore=None):
        logger.info(f"[桌面端] 正在打开数据库源: {path}")
        self.monitor_page.clear()
        self.live_panel.stop()
        self.live_panel.invalidate()
        self.cancel_queries()
        self._fx_attempt = None
        self.sensitive.setChecked(False)
        self.service = TradesService(path)
        self.analytics_service = AnalyticsService(path, fx_store=self.fx_store, quote_store=self.quote_store)
        self.info = None
        self.restore_state = restore
        self.filters_widget.setEnabled(False)
        self.source_label.setText("正在只读连接…")
        from portfolio.paths import derived_path
        self.source_label.setToolTip(f"事实库：{self.service.path}\n派生库：{derived_path(self.service.path)}")
        self.banner.show_status("正在校验结构并读取筛选维度…", "loading")
        self.source_details.setPlainText("正在检查新选择的数据源；旧数据源信息已清除。")
        service = self.service
        self._submit("source", lambda cancel: service.source_info(cancel=cancel), self._source_ready)

    def _source_ready(self, info):
        logger.info(f"[桌面端] 数据源校验就绪: {info.path.name} (大小: {info.bytes} 字节, 演示模式: {info.is_demo})")
        self.info = info
        self.live_panel.source_changed(info)
        if not info.is_demo:
            from portfolio.services.intraday_pipeline import start_pipeline
            start_pipeline(info.path)
            self.check_monthly_history()
        else:
            self.history_coverage_button.setText('历史行情：合成演示不检查')
        if self.settings and not info.is_demo:
            self.settings.setValue('default_database', str(info.path))
            self.settings.sync()
        self.source_label.setText("源库只读 · " + ("合成演示数据" if info.is_demo else info.path.name))
        self.source_label.setToolTip(str(info.path) + "\n点击查看数据日期和结构检查")
        self.source_details.setPlainText(json.dumps({
            "文件路径": str(info.path), "文件字节数": info.bytes, "文件修改时间_ns": info.modified_at_ns,
            "访问模式": "READ_ONLY / query_only", "结构检查": info.schema,
            "最新账务日期（不是估值日）": info.latest_ledger_date,
            "最新行情交易日": info.latest_price_date, "最新报价UTC": info.latest_quote_utc,
            "最后成功导入UTC": info.last_successful_import_utc, "全库待审记录数（不同类型可能关联同一问题）": info.review_count,
            "估值日/收益最新日": "计算完成后在首页分别显示，不从交易日期推测", "合成演示库": info.is_demo,
        }, ensure_ascii=False, indent=2))
        self.filters_widget.setEnabled(False)
        saved = {}
        if self.settings and not self.restore_state:
            try:
                saved = json.loads(self.settings.value(self.filter_settings_key(info.path), "{}"))
            except (ValueError, TypeError):
                saved = {}
        state = self.restore_state or saved
        self._filter_batch = True
        self.currency.setCurrentIndex(max(0, self.currency.findData(state.get("currency", "USD"))))
        self.disabled.setChecked(state.get("disabled", False))
        self.audit.setChecked(state.get("audit", False))
        self.search.setText(state.get("search", ""))
        self.ledger.setCurrentIndex(max(0, self.ledger.findData(state.get("ledger", Ledger.PERSON.value))))
        self.category.setCurrentIndex(max(0, self.category.findData(state.get("category"))))
        self.person.blockSignals(True)
        self.person.clear()
        self.person.addItem("全部启用人员", None)
        self.person.blockSignals(False)
        self._populate_persons()
        self.accounts.populate(info.accounts)
        self.stock.blockSignals(True)
        self.stock.clear()
        self.stock.addItem("全部股票", None)
        for instrument in info.instruments:
            self.stock.addItem(f"{instrument['symbol']} · {instrument['market']}", instrument["id"])
        self.stock.blockSignals(False)
        if hasattr(self, "ai_assistant_page"):
            self.ai_assistant_page.populate_filters(info)
        if hasattr(self, "stock_analysis_page"):
            self.stock_analysis_page.populate_filters(info)
        # 打开的时候默认展示「个人」
        target_person = state.get("person")
        if not self.restore_state or target_person is None:
            geren_idx = self.person.findText("个人")
            if geren_idx >= 0:
                target_person = self.person.itemData(geren_idx)
        person_idx = self.person.findData(target_person) if target_person is not None else -1
        if person_idx < 0:
            person_idx = max(0, self.person.findText("个人"))
        self.person.setCurrentIndex(max(0, person_idx))
        self.stock.setCurrentIndex(max(0, self.stock.findData(state.get("stock"))))
        for identifier in state.get("accounts") or ():
            if identifier in self.accounts.actions_by_id:
                self.accounts.actions_by_id[identifier].setChecked(True)
        self.as_of.blockSignals(True)
        self.as_of.setMaximumDate(QDate.currentDate())
        saved_end = QDate.fromString(state.get('as_of', ''), 'yyyy-MM-dd')
        saved_start = QDate.fromString(state.get('start', ''), 'yyyy-MM-dd')
        is_drill = state.get('start_enabled') and saved_start.isValid() and saved_end.isValid() and saved_start == saved_end
        if not is_drill and saved_end.isValid() and state.get('start_enabled'):
            self.as_of.setDate(min(saved_end, QDate.currentDate()))
            self.start.setDate(saved_start if saved_start.isValid() else QDate(2025, 1, 1))
            self.start_enabled.setChecked(True)
        else:
            self.as_of.setDate(QDate.currentDate())
            self.start.setDate(QDate(2025, 1, 1))
            self.start_enabled.setChecked(True)
        self.as_of.blockSignals(False)
        self.apply_extra_state(state)
        self._filter_batch = False
        self.search_timer.stop()
        self.filters_widget.setEnabled(True)
        self.page_number = state.get("page_number", 0)
        if self.restore_state:
            self.sort_column = state.get("sort_column", 0)
            self.descending = state.get("descending", True)
        else:
            self.sort_column = 0
            self.descending = True
        order = Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder
        for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
            header.setSortIndicatorShown(True)
            header.setSortIndicator(self.sort_column, order)
        self._pending_scroll = (state.get("scroll", 0), state.get("horizontal_scroll", 0))
        self._analytics_scope = None
        self.refresh()

    def check_monthly_history(self):
        if self.info is None or self.info.is_demo:
            return
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from portfolio.services.history_coverage import cached_calendar, check_coverage, fetch_calendar
        today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
        start, source = today.replace(day=1), self.info.path
        cache = self.quote_store.path if self.quote_store else None
        credentials_path = self.live_panel.path
        self.history_coverage_button.setText('历史行情：正在检查当月…')
        self._history_coverage_details = '正在独立于当前页面筛选，检查全部渠道的当月历史持仓。'

        def task(cancel):
            try:
                try:
                    calendar = fetch_calendar(credentials_path, start, today)
                except Exception:
                    calendar = cached_calendar(cache, start, today)
                result = check_coverage(source, cache, start, today, calendar)
                return result, None
            except Exception:
                # SDK exceptions can contain auth information. Never put them in UI.
                return None, '未能完成当月历史行情检查：请检查数据库、长桥凭证及网络。交易日历必须覆盖当月，检查失败不能视为数据齐全。'

        def ready(payload):
            if self.info is None or self.info.path != source:
                return
            result, error = payload
            if error:
                text = '⚠ 历史行情检查失败'
                self._history_coverage_details = error
            else:
                text = (f'⚠ 当月收盘价缺失 {len(result.missing)} 条' if result.missing else
                        f'当月收盘价完整 · {result.required} 条')
                self._history_coverage_details = result.details()
            self.history_coverage_button.setText(text)
            self.history_coverage_button.setToolTip(self._history_coverage_details[:2000])
            # Coverage is advisory. Do not interrupt startup or conceal saved
            # curves behind a modal dialog; the toolbar still exposes details.

        self._submit('history_coverage', task, ready)

    def show_history_coverage(self, checked=False, *, warning=False):
        if self._history_coverage_dialog:
            self._history_coverage_dialog.close()
        box = QMessageBox(self)
        box.setWindowTitle('当月历史收盘价入库检查')
        box.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information)
        box.setText(self.history_coverage_button.text())
        box.setInformativeText('按全部渠道的历史持仓和交易所日历检查；点击详情查看缺失日期和股票。')
        box.setDetailedText(self._history_coverage_details)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setModal(False)
        self._history_coverage_dialog = box
        box.show()

    def _populate_persons(self):
        if self.info is None:
            return
        selected = self.person.currentData()
        self.person.blockSignals(True)
        self.person.clear()
        self.person.addItem("全部历史人员" if self.disabled.isChecked() else "全部启用人员", None)
        self.person.addItem('他人', 'OTHERS')
        for person in self.info.persons:
            if person["enabled"] or self.disabled.isChecked():
                self.person.addItem(person["name"] + ("（停用）" if not person["enabled"] else ""), person["id"])
        index = self.person.findData(selected)
        self.person.setCurrentIndex(max(0, index))
        self.person.blockSignals(False)

    @staticmethod
    def filter_settings_key(path):
        return "filters/" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:20]

    def disabled_changed(self):
        self._populate_persons()
        self.filters_changed()

    def ledger_changed(self):
        is_person = self.ledger.currentData() == Ledger.PERSON.value
        self.person.setEnabled(is_person)
        self.disabled.setEnabled(is_person)
        self.person.setToolTip("券商真实账本独立于人员份额；如需看人员交易，请切回人员权益账本。" if not is_person else "")
        self.filters_changed()

    def filters_changed(self, *_):
        if self._filter_batch:
            return
        self.update_filter_chips()
        self.page_number = 0
        if hasattr(self, "monthly_realtime_page"):
            self.monthly_realtime_page.sync_person_from_main(self.person.currentData())
        self.refresh()

    def make_scope(self):
        person = self.person.currentData()
        stock = self.stock.currentData()
        ledger = Ledger(self.ledger.currentData())
        if self.broker_people_view():
            person, ledger = None, Ledger.PERSON
        person_ids = None
        if ledger == Ledger.PERSON and person is not None:
            person_ids = (tuple(p['id'] for p in self.info.persons
                                if p['name'] != '个人' and (p['enabled'] or self.disabled.isChecked()))
                          if person == 'OTHERS'
                          else tuple(person) if isinstance(person, (tuple, list))
                          else (person,))
        end = self.as_of.date()
        start = self.start.date()
        if self.fixed_asset_period():
            end = QDate.currentDate()
            if self.today_snapshot() or self.broker_people_view():
                end = self.holding_date.date()
        return QueryScope(
            as_of=end.toPython(), ledger=ledger,
            person_ids=person_ids,
            account_ids=self.accounts.selected_ids(), instrument_ids=(stock,) if stock is not None else None,
            category=self.category.currentData(),
            start_date=start.toPython() if self.start_enabled.isChecked() else None,
            search=self.search.text().strip(), include_disabled=self.disabled.isChecked(), audit=self.audit.isChecked(),
            reporting_currency=self.currency.currentData(),
        )

    def refresh(self):
        if self._filter_batch or self.info is None or not self.filters_widget.isEnabled():
            return
        if hasattr(self, "live_panel") and self.live_panel:
            self.live_panel.refresh_channel_cash()
        self.update_filter_chips()
        if self.currency.currentData() == "CNY":
            self.update_fx()
        try:
            scope = self.make_scope()
        except ValueError as exc:
            self._error(str(exc))
            return
        self.banner.show_status("正在读取当前筛选的交易…", "loading")
        filters = self.make_trade_filters()
        for card in self.trade_cards:
            card.set_value(None)
        self.drawer.clear()
        self.table.show_empty("正在读取交易…")
        self.table.finance_model.replace(())
        self.current_page = None
        self.export_button.setEnabled(False)
        self.detail.clear()
        old = self.jobs.pop("provenance", None)
        if old:
            old.cancel.set()
        service, page, sort_column, descending = self.service, self.page_number, self.sort_column, self.descending
        self._submit("trades", lambda cancel: service.get_trades(scope, page=page, sort_column=sort_column,
                                                                descending=descending, filters=filters, cancel=cancel), self._page_ready)
        if self._analytics_scope != scope:
            previous = self.jobs.pop("analytics", None)
            if previous:
                previous.cancel.set()
            self._analytics_scope = None
        self.ensure_analytics()

    def _page_ready(self, page):
        if self.info is None or page.scope != self.make_scope() or page.filters != self.make_trade_filters():
            return  # 快速切换筛选时不能让旧请求覆盖新筛选画面。
        self.current_page = page
        self.table.empty.setVisible(not page.rows)
        if not page.rows:
            self.table.show_empty("没有匹配的交易", "重置筛选")
        self.table.finance_model.replace(page.rows)
        self.table._frozen_geometry()
        self.count_label.setText(f"共 {page.total:,} 条 · 第 {page.page + 1} / {max(1, (page.total + page.size - 1) // page.size)} 页 · 本页 {len(page.rows)} 条")
        self.previous.setEnabled(page.page > 0)
        self.next.setEnabled((page.page + 1) * page.size < page.total)
        self.export_button.setEnabled(bool(page.rows))
        scope = page.scope
        self.scope_label.setText(f"{self.ledger.currentText()} · {self.person.currentText() if scope.ledger == Ledger.PERSON else '独立物理账户成交'} · {self.accounts.text()} · {self.stock.currentText()} · 截至 {scope.as_of} · 仅 USD 原币 · EXCEL_COMPAT")
        stats = page.stats
        for card, value in zip(self.trade_cards, (stats.matched, stats.buys, stats.sells, stats.review)):
            card.set_value(value)
        notice = "合成演示数据 · 非真实资产。" if self.info.is_demo else ""
        notice += "按当前筛选统计 · 人员权益与券商成交独立。"
        if not page.rows:
            notice += " 该筛选组合暂无数据，请调整或重置筛选。"
        if self.include_reversed.isChecked() or self.include_reference.isChecked():
            notice += " 含审计记录，不代表有效持仓。"
        self._trade_notice = notice
        if self.pages.currentIndex() == 0:
            self.banner.show_status(notice)
            self.banner.setVisible(not page.rows or self.include_reversed.isChecked() or self.include_reference.isChecked())
        self.restore_scroll()


    def clear_analytics(self):
        self.ownership_alert.hide()
        self._people_scope = None
        self._preloaded_people_scope = None
        if hasattr(self, "earnings_page"):
            self.earnings_page.clear()
        self.live_panel.invalidate()
        self.analytics = None
        self._analytics_scope = None
        if hasattr(self, "dashboard"):
            self.dashboard.clear()
            self.charts_page.clear()
            self.distribution_page.clear()
            self.trend_page.clear()
            self.top_days_page.clear()
            if hasattr(self, "monthly_realtime_page"):
                self.monthly_realtime_page.clear()
            if hasattr(self, "asset_trend"):
                self.asset_trend.clear()
            self.stock_detail.clear()
            for page in self.report_pages:
                page.clear()
            self.reconcile_page.clear("选择 Excel 进行当前范围对账")

    def preload_people_bundle(self):
        if self.info is None or not self.analytics_service or self.broker_people_view():
            return
        scope = self.make_scope()
        if getattr(self, "_preloaded_people_scope", None) == scope:
            return
        if hasattr(self, "asset_page") and getattr(self.asset_page, "broker_page", None):
            bundle = self.asset_page.broker_page.bundle
            if bundle is not None and bundle.aggregate.scope == scope:
                return
        self._preloaded_people_scope = scope
        service = self.analytics_service
        def task(cancel):
            try:
                return service.calculate_people(scope, cancel=cancel)
            except Exception:
                return None
        def ready(bundle):
            if not bundle or not hasattr(self, "asset_page") or not getattr(self, "asset_page", None) or not self.asset_page.broker_page:
                return
            if self.broker_people_view():
                if bundle.aggregate.scope == self.make_scope():
                    self._people_ready(bundle)
                return
            if self.asset_page.broker_page.bundle is None and bundle.aggregate.scope.as_of == self.make_scope().as_of:
                self.asset_page.broker_page.bundle = bundle
                self._analytics_ready(bundle.aggregate) if self.analytics is None else None
        self._submit("preload_people", task, ready)

    def _people_ready(self, bundle):
        self._people_scope = None
        if self.info is None or bundle.aggregate.scope != self.make_scope() or not self.broker_people_view():
            return
        self.asset_page.broker_page.bundle = bundle
        self._analytics_ready(bundle.aggregate)

    def _analytics_ready(self, analytics):
        if self.info is None or analytics.scope != self.make_scope():
            return
        # [Antigravity] 负仓异常与归属数量告警联动检查，在启动及计算完成时第一时间通知用户
        negative_issues = [r for r in analytics.reports['数据异常'].rows if r.values[0] == 'DQ001' and '当前持仓为负数' in str(r.values[6])]
        ownership_issues = [r for r in analytics.reports['数据异常'].rows if r.values[0] == 'DQ002']
        alert_messages = []
        if negative_issues:
            neg_details = '；'.join(f"{r.values[3]} · {r.values[4]} · {r.values[5]}：{r.values[6]}" for r in negative_issues[:2])
            alert_messages.append(f"🚨 负仓异常（{len(negative_issues)}项）：{neg_details}。导致相关盈亏与未分配差异展示为“数据不完整”。（点击可查看明细）")
        if ownership_issues:
            own_details = '；'.join(f'{r.values[4]} · {r.values[5]}：{r.values[6]}' for r in ownership_issues[:3])
            alert_messages.append(f'⚠ 归属数量告警（{len(ownership_issues)}项）：{own_details}。不足1股按既定尾差规则忽略。（点击可查看明细）')
        if alert_messages:
            self.ownership_alert.show_status('\n'.join(alert_messages), 'error')
        else:
            self.ownership_alert.hide()
        self.analytics = analytics
        logger.info(f"[桌面端] 财务报表就绪: scope={analytics.scope}, 报表数={len(analytics.reports)}")
        if self._route == (2, "calendar"):
            QTimer.singleShot(0, lambda: self.dashboard.scroll.ensureWidgetVisible(self.dashboard.calendar_table))
        self._analytics_scope = analytics.scope
        self.dashboard.set_analytics(analytics)
        self.charts_page.set_analytics(analytics)
        if self._route and self._route[0] == 5 and isinstance(self._route[1], int):
            self.charts_page.selector.setCurrentIndex(self._route[1])
        self.distribution_page.set_analytics(analytics)
        self.trend_page.set_analytics(analytics)
        # 全量启动预热（Eager Startup Preload）：打开软件时，后台并发预热走势图、极值汇总、财报日程与人员账本
        if hasattr(self, "top_days_page"):
            self.top_days_page.set_analytics(analytics)
        if hasattr(self, "monthly_realtime_page"):
            self.monthly_realtime_page.sync_person_from_main(self.person.currentData())
            QTimer.singleShot(0, lambda: self.monthly_realtime_page.set_analytics(analytics) if hasattr(self, "monthly_realtime_page") else None)
        if hasattr(self, "asset_trend") and self.asset_trend.isVisible():
            self.asset_trend.sync_person_from_main(self.person.currentData())
            QTimer.singleShot(0, lambda: self.asset_trend.set_analytics(analytics))
        if hasattr(self, "earnings_page"):
            self.earnings_page.activate()
        self.preload_people_bundle()
        if hasattr(self, "holdings_page"):
            self.holdings_page.set_analytics(analytics)
        self.stock_detail.set_analytics(analytics)
        for page in self.report_pages:
            page.set_reports(analytics.reports)
        self.live_panel.bind(analytics)
        if self.pages.currentIndex() != 0:
            if self.info.is_demo:
                self.banner.show_status("合成演示数据", "normal")
            elif not self.analytics_service.derived.available:
                self.banner.show_status("历史派生库尚未初始化，当前使用原财务引擎；请按双数据库操作文档完成首次生成。", "normal")
            else:
                self.banner.hide()


    def report_provenance(self, source_id, target):
        if self.service is None:
            return
        current_text = target.toPlainText()
        self._submit("report_provenance", lambda cancel: self.service.get_trade_provenance(source_id, cancel=cancel),
                     lambda value: target.setPlainText(current_text + "\n\n首条原始来源（敏感字段已遮盖）：\n" + json.dumps(value, ensure_ascii=False, indent=2, default=exact_text)))

    def export_financial_report(self, report, path):
        if not self.analytics:
            self.banner.setText("当前计算尚未完成，请稍后导出。")
            return
        parameters = {"scope": str(self.analytics.scope), "source_identity": self.analytics.source_identity,
                      "valuation_days": str(self.analytics.valuation_days), "latest_profit_day": self.analytics.latest_profit_day,
                      "calculated_at_utc": self.analytics.calculated_at, "metric_mode": "EXCEL_COMPAT"}
        self._submit("export", lambda cancel: export_report(report, path, parameters=parameters),
                     lambda _: self.banner.setText(f"已导出 {len(report.rows):,} 条到 {path.name}；包含当前筛选和计算口径。"))

    @batch_drill
    def drill_record(self, metadata):
        identifier = metadata.get("instrument_id")
        account = metadata.get("account_id")
        person = metadata.get("person_id")
        if person is not None and self.ledger.currentData() == Ledger.PERSON.value:
            self.person.setCurrentIndex(max(0, self.person.findData(person)))
        if identifier is not None:
            self.stock.setCurrentIndex(max(0, self.stock.findData(identifier)))
        if account is not None:
            for id, action in self.accounts.actions_by_id.items():
                action.setChecked(id == account)
        self.stock_detail.report.selector.setCurrentText("交易回放")
        self.navigation.setCurrentRow(10)

    @batch_drill
    def drill_chart(self, kind, key):
        if kind == "live_day":
            self.holding_date.setDate(QDate.currentDate())
            self.navigation.select(3, "今日盈亏")
            return
        if kind == "metric":
            index = int(key)
            target = "清仓收益" if index == 3 else "每月微操收益" if index in (12, 13) else "每日持仓收益"
            self.performance_page.selector.setCurrentText(target)
            self.navigation.setCurrentRow(4)
            return
        if kind in ("day", "month"):
            from calendar import monthrange
            day = key[:10] if kind == "day" else key + "-01"
            start = QDate.fromString(day, "yyyy-MM-dd")
            end = start if kind == "day" else QDate(start.year(), start.month(), monthrange(start.year(), start.month())[1])
            self.start.setDate(start)
            self.start_enabled.setChecked(True)
            self.as_of.setDate(min(end, self.as_of.date()))
            self.performance_page.selector.setCurrentText("每日持仓收益")
            self.navigation.setCurrentRow(4)
        elif kind in ("symbol", "symbol_suffix"):
            symbol = key.split(" · ")[-1]
            for index in range(self.stock.count()):
                if self.stock.itemText(index).split(" · ")[0] == symbol:
                    self.stock.setCurrentIndex(index)
                    break
            self.stock_detail.report.selector.setCurrentText("交易回放")
            self.navigation.setCurrentRow(10)
        elif kind == "category":
            self.category.setCurrentText(key)
        elif kind == "broker" or (kind == "contribution" and key.startswith("渠道")):
            code = key.replace("渠道", "").strip()
            for id, action in self.accounts.actions_by_id.items():
                account = next((a for a in self.info.accounts if a["id"] == id), {})
                action.setChecked(account.get("broker_code") == code)
        elif kind == "contribution" and key.startswith("分类"):
            self.category.setCurrentText(key.replace("分类", "").strip())

    def run_reconciliation(self):
        if self.analytics is None:
            self.banner.setText("先连接数据库，选中与原 Excel 对应的单个人员和日期。")
            return
        if not self.analytics.scope.person_ids or len(self.analytics.scope.person_ids) != 1:
            self.banner.setText("为避免范围错误，请先选择原 Excel 对应的单个人员，再开始对账。")
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择只读参考 Excel", "", "Excel (*.xlsx)")
        if path:
            from portfolio.reconcile import reconcile
            analytics = self.analytics
            person_name = self.person.currentText()
            self.banner.setText("正在逐单元格核对 Excel；不会重算或修改原工作簿…")
            self._submit("reconcile", lambda cancel: reconcile(analytics, Path(path), person_name=person_name, cancel=cancel), self._reconciliation_ready)

    def _reconciliation_ready(self, report):
        self.reconcile_page.set_reports({report.name: report})
        self.banner.setText(f"对账完成：{len(report.rows):,} 项。请按状态查看差异，可导出 CSV/JSON/XLSX。")

    def turn_page(self, step):
        self.page_number = max(0, self.page_number + step)
        self.refresh()

    def sort(self, column):
        if column not in SORT_FIELDS:
            self.banner.setText("此列属于回放计算结果；请在收益报表的交易回放中查看和排序。")
            return
        if column == self.sort_column:
            self.descending = not self.descending
        else:
            self.descending = True if column == 0 else False
        self.sort_column = column
        order = Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder
        for header in (self.table.horizontalHeader(), self.table.frozen.horizontalHeader()):
            header.setSortIndicatorShown(True)
            header.setSortIndicator(column, order)
        self.page_number = 0
        self.refresh()

    def load_provenance(self):
        index = self.table.currentIndex()
        if not index.isValid() or self.current_page is None or index.row() >= len(self.current_page.rows):
            return
        record = self.current_page.rows[index.row()]
        sensitive, service = self.sensitive.isChecked(), self.service
        if self.drawer.isHidden() or self.drawer.property("record_key") != record.record_id:
            self.drawer.show_record(record)
            self.drawer.setProperty("record_key", record.record_id)
        self.detail.setPlainText("正在读取来源…")
        def show(provenance):
            self.drawer.set_provenance(record.metadata, provenance)
        self._submit("provenance", lambda cancel: service.get_trade_provenance(record.source_record_id, sensitive=sensitive, cancel=cancel), show)

    def update_fx(self, *, force=False):
        if not self.fx_store or not self.info or self.info.is_demo or self._fx_busy:
            return
        if not force and self.currency.currentData() != "CNY":
            return
        # At most once per hour while CNY is active; retry publication/network failures.
        from time import monotonic
        if not force and self._fx_attempt and monotonic() - self._fx_attempt < 3600:
            return
        self._fx_busy = True
        self._fx_attempt = monotonic()
        source = self.service.path
        store = self.fx_store
        self.currency.setToolTip(POLICY + "\n正在后台补齐历史汇率…")
        def task(cancel):
            from portfolio.db.connection import readonly_connection
            with readonly_connection(source) as c:
                starts = [c.execute(f"SELECT MIN(business_date) FROM {table}").fetchone()[0]
                          for table in ("broker_trades", "person_operations")]
            start = min([d for d in starts if d] + ["2025-01-01"])
            return store.sync(start, date.today(), force=force)
        def success(result):
            self._fx_busy = False
            self.currency.setToolTip(POLICY + f"\n本地汇率库：{store.path}\n最新公布日：{result['latest']}；本次读取 {result['count']} 条。")
            if self.service and self.service.path == source:
                self.clear_analytics()
                self.refresh()
            self.statusBar().showMessage(f"人民币汇率已更新；最新公布日 {result['latest']}。非发布日沿用此前最近值（最多7天）。", 20000)
        def failure(message):
            self._fx_busy = False
            self.currency.setToolTip(POLICY + "\n" + message)
            self.statusBar().showMessage(message, 30000)
        worker = Worker(task)
        self.jobs["fx_sync"] = worker
        worker.signals.done.connect(success, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(failure, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(lambda: setattr(self, "_fx_busy", False), Qt.ConnectionType.QueuedConnection)
        self.pool.start(worker)

    def reconnect(self):
        if self.service:
            state = self.capture_state()
            self.open_database(self.service.path, restore=state)
        else:
            self.choose_database()

    def export_current_page(self):
        if self.current_page is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出当前已查询的一页（保留 Decimal 精度）", "交易明细.csv", "CSV (*.csv)")
        if path:
            try:
                export_page(self.current_page, Path(path))
                self.banner.setText(f"已导出本页 {len(self.current_page.rows)} 条；基准日 {self.current_page.scope.as_of}；未覆盖源文件。")
            except OSError:
                QMessageBox.warning(self, "导出未完成", "目标文件已存在或不可写。请选择新文件名；程序不会覆盖已有文件。")

    def closeEvent(self, event):
        self.monitor_page.clear()
        self.live_panel.stop()
        for worker in self.jobs.values():
            worker.cancel.set()
        if self.settings:
            self.settings.setValue("geometry", self.saveGeometry())
            state = self.capture_state()
            if self.service:
                self.settings.setValue(self.filter_settings_key(self.service.path), json.dumps(state))
            self.settings.setValue("theme", self.theme)
            self.settings.setValue("green_profit", self.green_profit.isChecked())
        self.pool.waitForDone(1500)
        super().closeEvent(event)
