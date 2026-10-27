"""High-frequency PnL trend across month (30-min sampling) and year (2-hour sampling)."""
import calendar
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
from portfolio.paths import DATABASE_PATH
import sqlite3
from zoneinfo import ZoneInfo

from PySide6.QtCore import QBuffer, QIODevice, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.live_quotes import NY, UTC
from portfolio.services.charts_service import Chart, Series
from portfolio.ui.charts_page import ChartCanvas


class ChannelFilter(QToolButton):
    """Channel multi-select filter styled identically to AccountFilter."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setText("全部渠道")
        self.setMinimumWidth(110)
        self.setToolTip("筛选券商渠道（点击单选或多选；选择全部渠道恢复全量）")
        self.actions_by_channel = {}
        self._menu = None

    def populate(self, channels):
        curr_selected = self.selected_channels()
        self._menu = QMenu(self)
        reset = self._menu.addAction("全部渠道")
        reset.triggered.connect(self.reset)
        self._menu.addSeparator()
        self.actions_by_channel = {}

        display_names = {
            "FIRSTRADE": "第一证券",
            "第一证券": "第一证券",
            "BBAE": "BBAE",
            "LONGBRIDGE": "长桥",
            "长桥": "长桥",
            "SCHWAB": "嘉信",
            "嘉信": "嘉信",
        }
        order_map = {"第一证券": 0, "BBAE": 1, "长桥": 2, "嘉信": 3}
        sorted_channels = sorted(channels, key=lambda c: order_map.get(display_names.get(c, c), 99))
        for ch in sorted_channels:
            name = display_names.get(ch, ch)
            act = QAction(name, self)
            act.setCheckable(True)
            if curr_selected and ch in curr_selected:
                act.setChecked(True)
            act.toggled.connect(self._on_action_toggled)
            self._menu.addAction(act)
            self.actions_by_channel[ch] = act
        self.setMenu(self._menu)
        self._sync_text()

    def reset(self):
        for act in self.actions_by_channel.values():
            act.blockSignals(True)
            act.setChecked(False)
            act.blockSignals(False)
        self._sync_text()
        self.changed.emit()

    def _on_action_toggled(self):
        self._sync_text()
        self.changed.emit()

    def _sync_text(self):
        selected = [ch for ch, act in self.actions_by_channel.items() if act.isChecked()]
        if not selected:
            self.setText("全部渠道")
        elif len(selected) == 1:
            display_names = {
                "FIRSTRADE": "第一证券",
                "第一证券": "第一证券",
                "BBAE": "BBAE",
                "LONGBRIDGE": "长桥",
                "长桥": "长桥",
                "SCHWAB": "嘉信",
                "嘉信": "嘉信",
            }
            self.setText(display_names.get(selected[0], selected[0]))
        else:
            self.setText(f"已选 {len(selected)} 个渠道")

    def selected_channels(self):
        selected = {ch for ch, act in self.actions_by_channel.items() if act.isChecked()}
        return selected or None


class StockFilter(QComboBox):
    """Stock ticker filter with completer and clean dropdown selection."""
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(125)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setPlaceholderText("全部股票")
        self.lineEdit().setClearButtonEnabled(True)
        self.setToolTip("输入代码自动联想，或直接从下拉列表选择特定股票；清空恢复全部股票。")

        self.completer = QCompleter(self.model(), self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.popup().setMinimumWidth(160)
        self.setCompleter(self.completer)

        self.currentIndexChanged.connect(self._on_index_changed)
        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.lineEdit().editingFinished.connect(self._on_editing_finished)

    def populate(self, tickers):
        curr_ticker = self.selected_ticker()
        self.blockSignals(True)
        self.clear()
        self.addItem("全部股票", None)
        for t in sorted(tickers):
            self.addItem(t, t)
        if curr_ticker:
            idx = self.findData(curr_ticker)
            if idx >= 0:
                self.setCurrentIndex(idx)
            else:
                self.setCurrentIndex(0)
        else:
            self.setCurrentIndex(0)
        self.blockSignals(False)

    def selected_ticker(self):
        data = self.currentData()
        if data:
            return str(data).strip().upper()
        text = self.lineEdit().text().strip().upper()
        if text and text != "全部股票":
            for i in range(1, self.count()):
                if self.itemText(i).upper() == text:
                    return self.itemText(i)
            return text
        return None

    def _on_index_changed(self, index):
        self.changed.emit(self.selected_ticker() or "")

    def _on_text_edited(self, text):
        if not text.strip():
            self.blockSignals(True)
            self.setCurrentIndex(0)
            self.blockSignals(False)
            self.changed.emit("")

    def _on_editing_finished(self):
        ticker = self.selected_ticker()
        self.changed.emit(ticker or "")


class MonthlyRealtimePnlPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.window = parent
        self.analytics = None

        # 月度高频数据缓存
        self._cached_month = ""
        self._cached_by_day = {}
        self._cached_all_trading_days = []
        self._cached_channels = []
        self._cached_tickers = []
        self._rebuild_pending = False
        self._rebuild_attempted = set()

        # 年度高频数据缓存
        self._cached_year = ""
        self._cached_year_by_day = {}
        self._cached_year_all_trading_days = []
        self._cached_year_channels = []
        self._cached_year_tickers = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)

        # 顶部 Tab 栏：月度走势详情 vs 年度走势详情
        self.tab_bar = QTabBar(self)
        self.tab_bar.setDrawBase(False)
        self.tab_bar.addTab("月度走势详情")
        self.tab_bar.addTab("年度走势详情")
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tab_bar)

        # 顶部控制栏
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        # 月份选择器（月度 Tab 显示）
        self.lbl_month = QLabel("月份：")
        top_bar.addWidget(self.lbl_month)
        self.month_combo = QComboBox()
        self.month_combo.setMinimumWidth(115)
        self.month_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.month_combo.view().setMinimumWidth(125)
        self.month_combo.currentIndexChanged.connect(self._on_month_changed)
        top_bar.addWidget(self.month_combo)

        # 年份选择器（年度 Tab 显示）
        self.lbl_year = QLabel("年份：")
        self.lbl_year.setVisible(False)
        top_bar.addWidget(self.lbl_year)
        self.year_combo = QComboBox()
        self.year_combo.setMinimumWidth(95)
        self.year_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.year_combo.view().setMinimumWidth(105)
        self.year_combo.setVisible(False)
        self.year_combo.currentIndexChanged.connect(self._on_year_changed)
        top_bar.addWidget(self.year_combo)

        # 走势模式下拉框
        lbl_mode = QLabel("走势模式：")
        lbl_mode.setContentsMargins(6, 0, 0, 0)
        top_bar.addWidget(lbl_mode)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("当月累计盈亏走势", "cumulative")
        self.mode_combo.addItem("每日日内盈亏走势", "intraday")
        self.mode_combo.setMinimumWidth(150)
        self.mode_combo.setToolTip("选择查看累计盈亏曲线，或各交易日各自的日内波动走势")
        self.mode_combo.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.mode_combo)

        # 人员筛选下拉框（风格与主界面一致）
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(125)
        self.person_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.person_combo.view().setMinimumWidth(135)
        self.person_combo.setToolTip("归属人员筛选（查看全部启用人员、他人、个人或具体某位人员的高频收益走势）")
        self.person_combo.addItem("全部启用人员", None)
        self.person_combo.addItem("他人", "OTHERS")
        self.person_combo.addItem("个人", 1)
        self.person_combo.currentIndexChanged.connect(self._on_person_changed)
        top_bar.addWidget(self.person_combo)

        # 渠道筛选按钮（继承 QToolButton，多选下拉，与主界面 AccountFilter 风格一致）
        self.channel_filter = ChannelFilter(self)
        self.channel_filter.changed.connect(self._on_filter_changed)
        top_bar.addWidget(self.channel_filter)

        # 特定股票筛选框（支持下拉与代码联想自动完成）
        self.stock_combo = StockFilter(self)
        self.stock_combo.changed.connect(self._on_filter_changed)
        top_bar.addWidget(self.stock_combo)

        # 顶部常驻简要摘要徽标
        self.summary_badge = QLabel("")
        self.summary_badge.setObjectName("muted")
        self.summary_badge.setContentsMargins(10, 0, 0, 0)
        top_bar.addWidget(self.summary_badge)

        top_bar.addStretch()

        export_btn = QPushButton("导出 PNG…")
        export_btn.clicked.connect(self.export)
        top_bar.addWidget(export_btn)
        layout.addLayout(top_bar)

        # 滚动区域与画布容器
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.container = QWidget()
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(2, 4, 2, 8)
        container_layout.setSpacing(10)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.canvas = ChartCanvas(card_mode=True)
        self.canvas.setMinimumHeight(480)
        self.canvas.selected.connect(self.drill)
        container_layout.addWidget(self.canvas)

        # 底部状态栏与说明
        self.status_label = QLabel("正在加载高频行情数据…")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("sectionTitle")
        self.status_label.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px 0;")
        container_layout.addWidget(self.status_label)

        self.notes_label = QLabel(
            "数据说明：展示所选整月全部美股交易日时间轴（夏令时北京时间每日夜盘 08:00 至次日 08:00 盘后）；"
            "实盘曲线绘制截至当前时点，未来时段或缺少历史点的日期均留白；"
            "垂直虚线按每日夜盘（08:00）、盘前（16:00）、盘中（21:30）、盘后（04:00）精确划分各时段；"
            "横坐标清晰标注各交易日日期与开盘时间；两端高亮圆点与防遮挡金额胶囊标注全月最高与最低收益极值。"
        )
        self.notes_label.setWordWrap(True)
        self.notes_label.setObjectName("secondary")
        self.notes_label.setStyleSheet("font-size: 12px; color: #64748B; line-height: 1.4;")
        container_layout.addWidget(self.notes_label)

        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(60000)
        self.refresh_timer.timeout.connect(self._refresh_active_period)
        self.refresh_timer.start()

    def _refresh_active_period(self):
        if not self.analytics or not self.isVisible():
            return
        today = date.today()
        if self.tab_bar.currentIndex() == 1:
            if self.year_combo.currentText() == str(today.year):
                self._load_year_data(self.year_combo.currentText())
        else:
            if self.month_combo.currentText() == today.strftime("%Y-%m"):
                self._load_month_data(self.month_combo.currentText())

    def _history_keys(self, source_path, days, person_ids):
        """Use the same source and filters that wrote the daily history."""
        from portfolio.domain.models import Ledger, QueryScope
        from portfolio.services.intraday_history import scope_key

        base = self.analytics.scope if self.analytics else QueryScope(as_of=date.today())
        start = base.start_date or date(2025, 1, 1)
        paths = {str(source_path), str(Path(source_path).resolve())}
        keys = set()
        for day_text in days:
            day = date.fromisoformat(day_text)
            if day < start:
                continue
            scope = replace(base, as_of=day, start_date=start,
                            ledger=Ledger.PERSON, person_ids=person_ids,
                            reporting_currency="USD")
            keys.update(scope_key(path, scope) for path in paths)
        return keys

    def _restore_missing_month_days(self, source_path, days, person_ids, loaded_days):
        """Recalculate missing days from this Money3 ledger and cached candles."""
        if (not days or self._rebuild_pending or not self.analytics or not self.window
                or not hasattr(self.window, "_submit")):
            return
        from portfolio.services.intraday_history import source_version

        start = self.analytics.scope.start_date or date(2025, 1, 1)
        version = source_version(source_path)
        store = self.window.quote_store
        try:
            with sqlite3.connect(f"file:{store.path}?mode=ro", uri=True) as cache:
                cached_days = {row[0] for row in cache.execute(
                    "SELECT DISTINCT day FROM intraday_candles WHERE day LIKE ?", (days[0][:7] + "%",)
                )}
        except (OSError, sqlite3.Error):
            cached_days = set()
        missing = [day for day in days if start <= date.fromisoformat(day) <= date.today()
                   and day in cached_days and day not in loaded_days
                   and (source_path, version, self.analytics.scope, person_ids, day) not in self._rebuild_attempted]
        if not missing:
            return
        for day in missing:
            self._rebuild_attempted.add((source_path, version, self.analytics.scope, person_ids, day))
        self._rebuild_pending = True
        self.status_label.setText(self.status_label.text() + " · 正从当前 Money3 账本与已缓存 K 线补算缺失日期…")
        base = self.analytics.scope
        credentials = getattr(self.window.live_panel, "path", None)

        def task(cancel):
            from portfolio.domain.models import Ledger
            from portfolio.services.analytics_service import AnalyticsService
            from portfolio.services.intraday_history import rebuild_history, scope_key

            service = AnalyticsService(source_path, quote_store=store)
            restored = []
            for day in missing:
                if cancel.is_set():
                    break
                scope = replace(base, as_of=date.fromisoformat(day), start_date=start,
                                ledger=Ledger.PERSON, person_ids=person_ids,
                                reporting_currency="USD")
                try:
                    analytics = service.calculate(scope, cancel=cancel)
                    rebuild_history(source_path, analytics, credentials, store, day,
                                    datetime.now(ZoneInfo("Asia/Shanghai")), cancel=cancel)
                    if store.load_historical_pnl(scope_key(source_path, scope), day, source_version(source_path)):
                        restored.append(day)
                except Exception:
                    continue
            return restored

        def ready(restored):
            self._rebuild_pending = False
            if restored and self.month_combo.currentText() == missing[0][:7]:
                self._load_month_data(self.month_combo.currentText())
            elif not restored:
                self.status_label.setText(self.status_label.text() + " · 缺失日期尚未补齐，请检查行情缓存或凭证。")

        self.window._submit("monthly-history", task, ready)

    def _resolve_db_path(self):
        """Locate live_quotes.sqlite3 safely from window quote store or local application data."""
        if hasattr(self.window, "quote_store") and self.window.quote_store and self.window.quote_store.path.exists():
            return self.window.quote_store.path
        candidates = [
            Path.home() / "Library/Application Support/MoneyPortfolio/Money Portfolio/live_quotes.sqlite3",
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "live_quotes.sqlite3",
            Path("live_quotes.sqlite3"),
        ]
        for p in candidates:
            if p.exists() and p.stat().st_size > 10000000:
                return p
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    def _resolve_source_path(self):
        """Locate the main money.sqlite3 database path."""
        if self.window and hasattr(self.window, "info") and self.window.info and self.window.info.path:
            return str(self.window.info.path)
        if self.window and hasattr(self.window, "service") and self.window.service and self.window.service.path:
            return str(self.window.service.path)
        candidates = [
            DATABASE_PATH,
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return ""

    def _get_connection(self):
        db_path = self._resolve_db_path()
        if not db_path.exists():
            return None
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)

    def clear(self):
        self.analytics = None
        self._rendered_cache_key = None
        self._cached_month = ""
        self._cached_by_day.clear()
        self._cached_all_trading_days.clear()
        self._cached_year = ""
        self._cached_year_by_day.clear()
        self._cached_year_all_trading_days.clear()
        self._rebuild_attempted.clear()
        self.canvas.chart = None
        self.canvas.update()
        self.summary_badge.setText("")
        self.status_label.setText("未连接数据源或无可用行情记录。")

    def set_analytics(self, analytics, force=False):
        person_val = self.person_combo.currentData() if hasattr(self, "person_combo") else None
        cache_key = (
            getattr(analytics, "calculated_at", None),
            getattr(analytics, "source_identity", None),
            getattr(analytics, "scope", None),
            person_val,
        )
        if not force and getattr(self, "_rendered_cache_key", None) == cache_key:
            return
        self.analytics = analytics
        self._populate_persons()
        self._init_available_months()
        self._init_available_years()
        self._rendered_cache_key = (
            getattr(analytics, "calculated_at", None),
            getattr(analytics, "source_identity", None),
            getattr(analytics, "scope", None),
            self.person_combo.currentData() if hasattr(self, "person_combo") else None,
        )

    def _get_persons(self):
        """Retrieve persons list from window info or fallback from money.sqlite3."""
        if self.window and hasattr(self.window, "info") and self.window.info:
            persons = getattr(self.window.info, "persons", None)
            if persons:
                return persons
        source_path = self._resolve_source_path()
        if source_path and Path(source_path).exists():
            try:
                import sqlite3
                con = sqlite3.connect(source_path)
                cur = con.cursor()
                rows = cur.execute("SELECT id, name, enabled FROM persons ORDER BY id").fetchall()
                con.close()
                return [{"id": r[0], "name": r[1], "enabled": bool(r[2])} for r in rows]
            except Exception:
                pass
        return []

    def _populate_persons(self):
        """Populate person dropdown matching main_window order:
        1. 全部启用人员 (None)
        2. 他人 ('OTHERS')
        3. 个人 (1)
        4. 具体某个他人 (华, 巴小, 张薇...)
        """
        persons = self._get_persons()
        if not persons:
            return
        preferred_val = self.person_combo.currentData()
        if self.window and hasattr(self.window, "person"):
            main_val = self.window.person.currentData()
            if self.window.person.count() > 0:
                preferred_val = main_val

        self.person_combo.blockSignals(True)
        self.person_combo.clear()
        self.person_combo.addItem("全部启用人员", None)
        self.person_combo.addItem("他人", "OTHERS")

        # 3. 个人
        geren = next((p for p in persons if p.get("id") == 1 or p.get("name") == "个人"), None)
        if geren:
            self.person_combo.addItem(geren.get("name", "个人"), geren.get("id", 1))
        else:
            self.person_combo.addItem("个人", 1)

        # 4. 具体某个他人
        for p in persons:
            if p.get("id") != 1 and p.get("name") != "个人" and p.get("enabled", True):
                self.person_combo.addItem(p["name"], p["id"])

        idx = self.person_combo.findData(preferred_val)
        if idx >= 0:
            self.person_combo.setCurrentIndex(idx)
        else:
            self.person_combo.setCurrentIndex(0)
        self.person_combo.blockSignals(False)

    def sync_person_from_main(self, person_val):
        """Synchronize person selection from main window global filter."""
        if getattr(self, "_syncing_person", False):
            return
        curr_val = self.person_combo.currentData()
        if curr_val == person_val:
            return
        idx = self.person_combo.findData(person_val)
        if idx >= 0:
            self._syncing_person = True
            try:
                self.person_combo.blockSignals(True)
                self.person_combo.setCurrentIndex(idx)
                self.person_combo.blockSignals(False)
                if self.tab_bar.currentIndex() == 1:
                    self._load_year_data(self.year_combo.currentText())
                else:
                    self._load_month_data(self.month_combo.currentText())
            finally:
                self._syncing_person = False

    def _init_available_months(self):
        """Populate the month dropdown with available months from live_quotes.sqlite3."""
        conn = self._get_connection()
        if not conn:
            self.status_label.setText("行情缓存数据库不存在或尚未初始化。")
            return
        months = []
        try:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT substr(day, 1, 7) FROM historical_pnl_points ORDER BY 1 DESC")
            months = [r[0] for r in cur.fetchall() if r[0]]
        except Exception as e:
            self.error.emit(f"读取行情月份列表失败：{e}")
        finally:
            conn.close()

        if not months:
            months = ["2026-10"]

        curr_month = self.month_combo.currentText()
        self.month_combo.blockSignals(True)
        self.month_combo.clear()
        for m in months:
            self.month_combo.addItem(m, m)
        if curr_month in months:
            self.month_combo.setCurrentText(curr_month)
        else:
            self.month_combo.setCurrentIndex(0)
        self.month_combo.blockSignals(False)

        if self.tab_bar.currentIndex() == 0:
            self._load_month_data(self.month_combo.currentText())

    def _init_available_years(self):
        """Populate the year dropdown with available years from live_quotes.sqlite3."""
        conn = self._get_connection()
        if not conn:
            return
        years = []
        try:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT substr(day, 1, 4) FROM historical_pnl_points ORDER BY 1 DESC")
            years = [r[0] for r in cur.fetchall() if r[0]]
        except Exception as e:
            self.error.emit(f"读取行情年份列表失败：{e}")
        finally:
            conn.close()

        if not years:
            years = ["2026"]

        curr_year = self.year_combo.currentText()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for y in years:
            self.year_combo.addItem(y, y)
        if curr_year in years:
            self.year_combo.setCurrentText(curr_year)
        else:
            self.year_combo.setCurrentIndex(0)
        self.year_combo.blockSignals(False)

        if self.tab_bar.currentIndex() == 1:
            self._load_year_data(self.year_combo.currentText())

    def _on_tab_changed(self, index):
        is_year = (index == 1)
        self.lbl_month.setVisible(not is_year)
        self.month_combo.setVisible(not is_year)
        self.lbl_year.setVisible(is_year)
        self.year_combo.setVisible(is_year)

        mode_data = self.mode_combo.currentData() or "cumulative"
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        if is_year:
            self.mode_combo.addItem("当年累计盈亏走势", "cumulative")
            self.mode_combo.addItem("每日日内盈亏走势", "intraday")
        else:
            self.mode_combo.addItem("当月累计盈亏走势", "cumulative")
            self.mode_combo.addItem("每日日内盈亏走势", "intraday")
        idx = self.mode_combo.findData(mode_data)
        self.mode_combo.setCurrentIndex(max(0, idx))
        self.mode_combo.blockSignals(False)

        if is_year:
            if not self.year_combo.count():
                self._init_available_years()
            elif not self._cached_year_by_day or self._cached_year != self.year_combo.currentText():
                self._load_year_data(self.year_combo.currentText())
            else:
                self.channel_filter.populate(self._cached_year_channels)
                self.stock_combo.populate(self._cached_year_tickers)
                self.update_chart()
        else:
            if not self.month_combo.count():
                self._init_available_months()
            elif not self._cached_by_day or self._cached_month != self.month_combo.currentText():
                self._load_month_data(self.month_combo.currentText())
            else:
                self.channel_filter.populate(self._cached_channels)
                self.stock_combo.populate(self._cached_tickers)
                self.update_chart()

    def _on_month_changed(self):
        if self.tab_bar.currentIndex() == 0:
            self._load_month_data(self.month_combo.currentText())

    def _on_year_changed(self):
        if self.tab_bar.currentIndex() == 1:
            self._load_year_data(self.year_combo.currentText())

    def _on_person_changed(self):
        if getattr(self, "_syncing_person", False):
            return
        selected_val = self.person_combo.currentData()
        if self.tab_bar.currentIndex() == 1:
            self._load_year_data(self.year_combo.currentText())
        else:
            self._load_month_data(self.month_combo.currentText())

        if self.window and hasattr(self.window, "person"):
            curr_main_val = self.window.person.currentData()
            if curr_main_val != selected_val:
                idx = self.window.person.findData(selected_val)
                if idx >= 0:
                    self._syncing_person = True
                    try:
                        self.window.person.blockSignals(True)
                        self.window.person.setCurrentIndex(idx)
                        self.window.person.blockSignals(False)
                        self.window.filters_changed()
                    finally:
                        self._syncing_person = False

    def _load_month_data(self, month_str):
        if not month_str:
            return
        conn = self._get_connection()
        if not conn:
            self.clear()
            return

        month_pattern = f"{month_str}%"
        all_month_trading_days = []
        try:
            cur = conn.cursor()
            cur.execute("SELECT day FROM us_calendar WHERE day LIKE ? ORDER BY day", (month_pattern,))
            all_month_trading_days = [r[0] for r in cur.fetchall() if r[0]]
        except Exception:
            pass

        if not all_month_trading_days:
            try:
                y, m = map(int, month_str.split("-"))
                _, last_day = calendar.monthrange(y, m)
                all_month_trading_days = [
                    date(y, m, d).isoformat()
                    for d in range(1, last_day + 1)
                    if date(y, m, d).weekday() < 5
                ]
            except Exception:
                all_month_trading_days = []

        selected_person_val = self.person_combo.currentData()
        all_persons = self._get_persons()
        if selected_person_val == "OTHERS":
            person_ids = tuple(p["id"] for p in all_persons if p.get("id") != 1 and p.get("name") != "个人" and p.get("enabled", True))
            if not person_ids:
                person_ids = (2, 3, 4, 5, 6, 7, 8, 9)
        elif selected_person_val is not None:
            person_ids = (selected_person_val,)
        else:
            person_ids = None
        source_path = self._resolve_source_path()

        raw_rows = []
        try:
            cur = conn.cursor()
            if source_path:
                target_keys = self._history_keys(source_path, all_month_trading_days, person_ids)
                if target_keys:
                    placeholders = ",".join("?" for _ in target_keys)
                    query = f"""
                        SELECT day, stamp, session, payload_json
                        FROM historical_pnl_points
                        WHERE scope_key IN ({placeholders}) AND day LIKE ?
                        ORDER BY stamp;
                    """
                    cur.execute(query, (*target_keys, month_pattern))
                    raw_rows = cur.fetchall()
        except Exception as e:
            self.error.emit(f"查询 {month_str} 历史行情失败：{e}")
        finally:
            conn.close()

        by_day = defaultdict(list)
        channels = set()
        tickers = set()
        for day, stamp_str, session, payload_json in raw_rows:
            try:
                dt = datetime.fromisoformat(stamp_str)
                items = json.loads(payload_json)
                for item in items:
                    ch = item.get("channel")
                    if ch:
                        channels.add(ch)
                    tk = item.get("ticker")
                    if tk:
                        tickers.add(tk.upper())
                by_day[day].append((dt, items))
            except Exception:
                continue

        self._cached_month = month_str
        self._cached_by_day = dict(by_day)
        self._cached_all_trading_days = all_month_trading_days
        self._cached_channels = sorted(channels)
        self._cached_tickers = sorted(tickers)

        self.channel_filter.populate(self._cached_channels)
        self.stock_combo.populate(self._cached_tickers)

        self.update_chart()
        self._restore_missing_month_days(source_path, all_month_trading_days, person_ids, by_day)

    def _load_year_data(self, year_str):
        if not year_str:
            return
        conn = self._get_connection()
        if not conn:
            self.clear()
            return

        year_pattern = f"{year_str}%"
        all_year_trading_days = []
        try:
            cur = conn.cursor()
            cur.execute("SELECT day FROM us_calendar WHERE day LIKE ? ORDER BY day", (year_pattern,))
            all_year_trading_days = [r[0] for r in cur.fetchall() if r[0]]
        except Exception:
            pass

        if not all_year_trading_days:
            try:
                y = int(year_str)
                start_d = date(y, 1, 1)
                end_d = date(y, 12, 31)
                cur_d = start_d
                while cur_d <= end_d:
                    if cur_d.weekday() < 5:
                        all_year_trading_days.append(cur_d.isoformat())
                    cur_d += timedelta(days=1)
            except Exception:
                all_year_trading_days = []

        selected_person_val = self.person_combo.currentData()
        all_persons = self._get_persons()
        if selected_person_val == "OTHERS":
            person_ids = tuple(p["id"] for p in all_persons if p.get("id") != 1 and p.get("name") != "个人" and p.get("enabled", True))
            if not person_ids:
                person_ids = (2, 3, 4, 5, 6, 7, 8, 9)
        elif selected_person_val is not None:
            person_ids = (selected_person_val,)
        else:
            person_ids = None
        source_path = self._resolve_source_path()

        raw_rows = []
        try:
            cur = conn.cursor()
            if source_path:
                target_keys = self._history_keys(source_path, all_year_trading_days, person_ids)
                if target_keys:
                    placeholders = ",".join("?" for _ in target_keys)
                    query = f"""
                        SELECT day, stamp, session, payload_json
                        FROM historical_pnl_points
                        WHERE scope_key IN ({placeholders}) AND day LIKE ?
                        ORDER BY stamp;
                    """
                    cur.execute(query, (*target_keys, year_pattern))
                    raw_rows = cur.fetchall()
        except Exception as e:
            self.error.emit(f"查询 {year_str} 年度历史行情失败：{e}")
        finally:
            conn.close()

        by_day = defaultdict(list)
        channels = set()
        tickers = set()
        for day, stamp_str, session, payload_json in raw_rows:
            try:
                dt = datetime.fromisoformat(stamp_str)
                items = json.loads(payload_json)
                for item in items:
                    ch = item.get("channel")
                    if ch:
                        channels.add(ch)
                    tk = item.get("ticker")
                    if tk:
                        tickers.add(tk.upper())
                by_day[day].append((dt, items))
            except Exception:
                continue

        self._cached_year = year_str
        self._cached_year_by_day = dict(by_day)
        self._cached_year_all_trading_days = all_year_trading_days
        self._cached_year_channels = sorted(channels)
        self._cached_year_tickers = sorted(tickers)

        self.channel_filter.populate(self._cached_year_channels)
        self.stock_combo.populate(self._cached_year_tickers)

        self.update_chart()

    def _on_filter_changed(self, *_):
        self.update_chart()

    def update_chart(self):
        if self.tab_bar.currentIndex() == 1:
            self._render_year_chart()
        else:
            self._render_month_chart()

    def _render_month_chart(self):
        all_trading_days = self._cached_all_trading_days
        if not all_trading_days and not self._cached_by_day:
            self.canvas.chart = None
            self.canvas.update()
            self.summary_badge.setText("")
            self.status_label.setText(f"{self._cached_month} 暂无可用交易日或高频行情。")
            return

        if not all_trading_days:
            all_trading_days = sorted(self._cached_by_day.keys())

        mode = self.mode_combo.currentData() or "cumulative"
        selected_person_name = self.person_combo.currentText()
        selected_channels = self.channel_filter.selected_channels()
        selected_ticker = self.stock_combo.selected_ticker()

        if not self._cached_by_day:
            self.canvas.chart = None
            self.canvas.update()
            self.summary_badge.setText("")
            self.status_label.setText(f"{self._cached_month} 期间【{selected_person_name}】暂无高频持仓与交易记录。")
            return

        # 提取官方权威已核算日收益（来自资产总览每日持仓收益，确保日结数据100%严密对齐）
        official_pnl_by_day = {}
        if self.analytics:
            report = self.analytics.reports.get("每日持仓收益")
            if report and report.rows:
                ch_map = {
                    "LONGBRIDGE": "长桥", "长桥": "长桥",
                    "FIRSTRADE": "第一证券", "第一证券": "第一证券",
                    "SCHWAB": "嘉信", "嘉信": "嘉信",
                    "BBAE": "BBAE",
                }
                for r in report.rows:
                    d = r.values[0]
                    ch = ch_map.get(r.values[1], r.values[1])
                    tk = (r.values[3] or "").strip().upper()
                    if (selected_channels is None or ch in selected_channels) and (selected_ticker is None or tk == selected_ticker):
                        pnl = r.values[19]
                        if pnl is not None:
                            official_pnl_by_day[d] = official_pnl_by_day.get(d, Decimal(0)) + pnl

        zone = ZoneInfo("Asia/Shanghai")
        points = []
        vertical_lines = []
        ticks = []
        labels = {}
        day_start_stamps = []
        running_cum = Decimal(0)
        past_days_count = 0
        total_month_days = len(all_trading_days)

        # 遍历当月整月的全部交易日，构建完整连续时间轴（30分钟采样）
        for day in all_trading_days:
            target = date.fromisoformat(day)
            t_night = datetime.combine(target - timedelta(days=1), time(20, 0), NY).astimezone(UTC)
            t_pre = datetime.combine(target, time(4, 0), NY).astimezone(UTC)
            t_intra = datetime.combine(target, time(9, 30), NY).astimezone(UTC)
            t_post = datetime.combine(target, time(16, 0), NY).astimezone(UTC)
            t_end = datetime.combine(target, time(20, 0), NY).astimezone(UTC)
            day_start_stamps.append(t_night.isoformat())

            # 分割线：每日夜盘、盘前、盘中、盘后
            vertical_lines.append((t_night.isoformat(), "", "dash"))
            vertical_lines.append((t_pre.isoformat(), "", "dot"))
            vertical_lines.append((t_intra.isoformat(), "", "dot"))
            vertical_lines.append((t_post.isoformat(), "", "dot"))

            ticks.append(t_night.isoformat())
            labels[t_night.isoformat()] = t_night.astimezone(zone).strftime("%m-%d\n%H:%M")
            ticks.append(t_pre.isoformat())
            labels[t_pre.isoformat()] = ""
            ticks.append(t_intra.isoformat())
            labels[t_intra.isoformat()] = ""
            ticks.append(t_post.isoformat())
            labels[t_post.isoformat()] = ""

            day_entries = self._cached_by_day.get(day)
            day_baseline = running_cum if mode == "cumulative" else Decimal(0)
            is_last_day = (day == all_trading_days[-1])

            if not day_entries:
                cur_stamp = t_night
                while (cur_stamp <= t_end if is_last_day else cur_stamp < t_end):
                    points.append((cur_stamp.isoformat(), None))
                    cur_stamp += timedelta(minutes=30)
                continue

            past_days_count += 1
            max_observed_stamp = max(dt for dt, _ in day_entries)
            last_known_val = Decimal(0)
            close_known_val = None
            day_official_gain = official_pnl_by_day.get(day)

            # 预先获取当天 16:00 收盘点的高频收益
            past_at_close = [e for e in day_entries if e[0] <= t_post]
            if past_at_close:
                _, c_items = past_at_close[-1]
                c_matched = [
                    r for r in c_items
                    if (selected_channels is None or r.get("channel") in selected_channels)
                    and (selected_ticker is None or r.get("ticker", "").upper() == selected_ticker)
                ]
                with localcontext() as ctx:
                    ctx.prec = 50
                    if c_matched and any(r.get("pnl") is not None for r in c_matched):
                        close_known_val = sum((Decimal(r["pnl"]) for r in c_matched if r.get("pnl") is not None), Decimal(0))

            cur_stamp = t_night
            while (cur_stamp <= t_end if is_last_day else cur_stamp < t_end):
                k = cur_stamp.isoformat()
                if cur_stamp > max_observed_stamp:
                    points.append((k, None))
                else:
                    past_samples = [e for e in day_entries if e[0] <= cur_stamp]
                    if past_samples:
                        _, items = past_samples[-1]
                        matched = [
                            r for r in items
                            if (selected_channels is None or r.get("channel") in selected_channels)
                            and (selected_ticker is None or r.get("ticker", "").upper() == selected_ticker)
                        ]
                        with localcontext() as ctx:
                            ctx.prec = 50
                            if matched and any(r.get("pnl") is not None for r in matched):
                                val = sum((Decimal(r["pnl"]) for r in matched if r.get("pnl") is not None), Decimal(0))
                            else:
                                val = Decimal(0)
                        last_known_val = val if val is not None else last_known_val

                        # 官方权威基线锚定：采用纯加法线性渐进平滑模型（杜绝乘除比率导致的除零爆炸与波幅压缩）
                        # 1. 盘中阶段（夜盘 08:00 至收盘 16:00）：日内真实波幅 1:1 展现，日结微差均匀平滑分摊，收盘 100% 严密对齐官方日结；
                        # 2. 盘后阶段（16:00 之后）：以官方日结收益为基底，叠加盘后真实最新变动。
                        if day_official_gain is not None:
                            if cur_stamp > t_post:
                                adj_val = day_official_gain + (val - (close_known_val if close_known_val is not None else val))
                            else:
                                c_val = close_known_val if close_known_val is not None else last_known_val
                                total_sec = (t_post - t_night).total_seconds()
                                progress = min(max(Decimal(str((cur_stamp - t_night).total_seconds() / total_sec)), Decimal(0)), Decimal(1)) if total_sec > 0 else Decimal(1)
                                adj_val = val + progress * (day_official_gain - c_val)
                        else:
                            adj_val = val

                        points.append((k, (day_baseline + adj_val) if adj_val is not None else None))
                    elif cur_stamp == t_night:
                        val = Decimal(0)
                        points.append((k, day_baseline + val))
                    else:
                        points.append((k, None))
                cur_stamp += timedelta(minutes=30)

            # 跨日基准衔接：优先使用官方已确认收盘盈亏，确保与资产总览财务数据100%一致
            settled_gain = day_official_gain if day_official_gain is not None else (close_known_val if (max_observed_stamp >= t_post and close_known_val is not None) else last_known_val)
            running_cum = day_baseline + (settled_gain or Decimal(0))

        valid_points = [(k, v) for k, v in points if v is not None]
        if not valid_points:
            self.canvas.chart = None
            self.canvas.update()
            self.summary_badge.setText("")
            self.status_label.setText("当前筛选条件（人员/渠道/特定股票）下未匹配到有效收益点。")
            return

        min_pt = min(valid_points, key=lambda p: p[1])
        max_pt = max(valid_points, key=lambda p: p[1])
        last_pt = valid_points[-1]

        mode_name = "当月累计盈亏走势" if mode == "cumulative" else "每日日内盈亏走势"
        channel_desc = self.channel_filter.text()
        ticker_desc = f" · 标的：{selected_ticker}" if selected_ticker else " · 全部股票"
        person_desc = f" · {selected_person_name}"
        chart_title = f"{self._cached_month} {mode_name}（USD）{person_desc} · {channel_desc}{ticker_desc}"

        now_ny = datetime.now(NY)
        reached_day = (now_ny + timedelta(days=1) if now_ny.hour >= 20 else now_ny).date()
        reached_days = sum(date.fromisoformat(day) <= reached_day for day in all_trading_days)
        missing_days = max(0, reached_days - past_days_count)
        future_days = total_month_days - reached_days
        chart_note = (
            f"北京时间 · 整月全量 {total_month_days} 个美股交易日时间轴（半小时采样，已有曲线 {past_days_count} 个、已过日期缺点 {missing_days} 个、未来 {future_days} 个） · "
            f"细虚线按每日夜盘、盘前、盘中、盘后精确划分时段；高亮显示全月最高与最低收益。"
        )

        series_name = "月度累计盈亏" if mode == "cumulative" else "当日盈亏"
        self.canvas.chart = Chart(
            id=-101,
            title=chart_title,
            kind="line",
            series=(Series(series_name, tuple(points)),),
            note=chart_note,
            x_labels=labels,
            x_ticks=tuple(ticks),
            show_points=False,
            vertical_lines=tuple(vertical_lines),
            highlight_extremes=True,
            break_keys=tuple(day_start_stamps) if mode == "intraday" else (),
        )
        self.canvas.update()

        currency = "USD"
        last_val_str = f"{last_pt[1]:+,.2f} {currency}"
        self.summary_badge.setText(
            f"全月 {total_month_days} 个交易日（已有曲线 {past_days_count} 个，已过日期缺点 {missing_days} 个，未来 {future_days} 个） · "
            f"最新盈亏：{last_val_str}"
        )

        dt_max_bj = datetime.fromisoformat(max_pt[0]).astimezone(zone).strftime("%m-%d %H:%M")
        dt_min_bj = datetime.fromisoformat(min_pt[0]).astimezone(zone).strftime("%m-%d %H:%M")
        max_val_str = f"{max_pt[1]:+,.2f} {currency} ({dt_max_bj})"
        min_val_str = f"{min_pt[1]:+,.2f} {currency} ({dt_min_bj})"
        self.status_label.setText(
            f"📈 {mode_name}（{selected_person_name} · {channel_desc}{ticker_desc}）：当前最新 {last_val_str} · "
            f"最高点：{max_val_str} · "
            f"最低点：{min_val_str} · "
            f"波动跨度：{max_pt[1] - min_pt[1]:,.2f} {currency}"
        )
        self.notes_label.setText(
            "数据说明：展示所选整月全部美股交易日时间轴（夏令时北京时间每日夜盘 08:00 至次日 08:00 盘后）；"
            "实盘曲线绘制截至当前时点，未来时段或缺少历史点的日期均留白；"
            "垂直虚线按每日夜盘（08:00）、盘前（16:00）、盘中（21:30）、盘后（04:00）精确划分各时段；"
            "横坐标清晰标注各交易日日期与开盘时间；两端高亮圆点与防遮挡金额胶囊标注全月最高与最低收益极值。"
        )

    def _render_year_chart(self):
        all_trading_days = self._cached_year_all_trading_days
        if not all_trading_days and not self._cached_year_by_day:
            self.canvas.chart = None
            self.canvas.update()
            self.summary_badge.setText("")
            self.status_label.setText(f"{self._cached_year} 暂无可用交易日或高频行情。")
            return

        if not all_trading_days:
            all_trading_days = sorted(self._cached_year_by_day.keys())

        mode = self.mode_combo.currentData() or "cumulative"
        selected_person_name = self.person_combo.currentText()
        selected_channels = self.channel_filter.selected_channels()
        selected_ticker = self.stock_combo.selected_ticker()

        if not self._cached_year_by_day:
            self.canvas.chart = None
            self.canvas.update()
            self.summary_badge.setText("")
            self.status_label.setText(f"{self._cached_year} 年期间【{selected_person_name}】暂无高频持仓与交易记录。")
            return

        # 提取官方权威已核算日收益（来自资产总览每日持仓收益，确保日结数据100%严密对齐）
        official_pnl_by_day = {}
        if self.analytics:
            report = self.analytics.reports.get("每日持仓收益")
            if report and report.rows:
                ch_map = {
                    "LONGBRIDGE": "长桥", "长桥": "长桥",
                    "FIRSTRADE": "第一证券", "第一证券": "第一证券",
                    "SCHWAB": "嘉信", "嘉信": "嘉信",
                    "BBAE": "BBAE",
                }
                for r in report.rows:
                    d = r.values[0]
                    ch = ch_map.get(r.values[1], r.values[1])
                    tk = (r.values[3] or "").strip().upper()
                    if (selected_channels is None or ch in selected_channels) and (selected_ticker is None or tk == selected_ticker):
                        pnl = r.values[19]
                        if pnl is not None:
                            official_pnl_by_day[d] = official_pnl_by_day.get(d, Decimal(0)) + pnl

        zone = ZoneInfo("Asia/Shanghai")
        points = []
        vertical_lines = []
        ticks = []
        labels = {}
        day_start_stamps = []
        running_cum = Decimal(0)
        past_days_count = 0
        total_year_days = len(all_trading_days)
        seen_months = set()

        # 遍历整年交易日，按【每 2 个小时一个点】的密度取样
        for day in all_trading_days:
            target = date.fromisoformat(day)
            m_str = day[:7]
            t_night = datetime.combine(target - timedelta(days=1), time(20, 0), NY).astimezone(UTC)
            t_post = datetime.combine(target, time(16, 0), NY).astimezone(UTC)
            t_end = datetime.combine(target, time(20, 0), NY).astimezone(UTC)
            day_start_stamps.append(t_night.isoformat())

            # 每个月首个交易日起始点绘制主虚线与月度标签
            if m_str not in seen_months:
                seen_months.add(m_str)
                vertical_lines.append((t_night.isoformat(), "", "dash"))
                ticks.append(t_night.isoformat())
                month_num = int(m_str.split("-")[1])
                labels[t_night.isoformat()] = f"{month_num:02d}月"

            day_entries = self._cached_year_by_day.get(day)
            day_baseline = running_cum if mode == "cumulative" else Decimal(0)
            is_last_day = (day == all_trading_days[-1])

            if not day_entries:
                # 当年未来的交易日：整日留空，数值设为 None
                cur_stamp = t_night
                while (cur_stamp <= t_end if is_last_day else cur_stamp < t_end):
                    points.append((cur_stamp.isoformat(), None))
                    cur_stamp += timedelta(hours=2)
                continue

            past_days_count += 1
            max_observed_stamp = max(dt for dt, _ in day_entries)
            last_known_val = Decimal(0)
            close_known_val = None
            day_official_gain = official_pnl_by_day.get(day)

            # 预先获取当天 16:00 收盘点的高频收益
            past_at_close = [e for e in day_entries if e[0] <= t_post]
            if past_at_close:
                _, c_items = past_at_close[-1]
                c_matched = [
                    r for r in c_items
                    if (selected_channels is None or r.get("channel") in selected_channels)
                    and (selected_ticker is None or r.get("ticker", "").upper() == selected_ticker)
                ]
                with localcontext() as ctx:
                    ctx.prec = 50
                    if c_matched and any(r.get("pnl") is not None for r in c_matched):
                        close_known_val = sum((Decimal(r["pnl"]) for r in c_matched if r.get("pnl") is not None), Decimal(0))

            cur_stamp = t_night
            while (cur_stamp <= t_end if is_last_day else cur_stamp < t_end):
                k = cur_stamp.isoformat()
                if cur_stamp > max_observed_stamp:
                    points.append((k, None))
                else:
                    past_samples = [e for e in day_entries if e[0] <= cur_stamp]
                    if past_samples:
                        _, items = past_samples[-1]
                        matched = [
                            r for r in items
                            if (selected_channels is None or r.get("channel") in selected_channels)
                            and (selected_ticker is None or r.get("ticker", "").upper() == selected_ticker)
                        ]
                        with localcontext() as ctx:
                            ctx.prec = 50
                            if matched and any(r.get("pnl") is not None for r in matched):
                                val = sum((Decimal(r["pnl"]) for r in matched if r.get("pnl") is not None), Decimal(0))
                            else:
                                val = Decimal(0)
                        last_known_val = val if val is not None else last_known_val

                        # 官方权威基线锚定：采用纯加法线性渐进平滑模型（杜绝乘除比率导致的除零爆炸与波幅压缩）
                        # 1. 盘中阶段（夜盘 08:00 至收盘 16:00）：日内真实波幅 1:1 展现，日结微差均匀平滑分摊，收盘 100% 严密对齐官方日结；
                        # 2. 盘后阶段（16:00 之后）：以官方日结收益为基底，叠加盘后真实最新变动。
                        if day_official_gain is not None:
                            if cur_stamp > t_post:
                                adj_val = day_official_gain + (val - (close_known_val if close_known_val is not None else val))
                            else:
                                c_val = close_known_val if close_known_val is not None else last_known_val
                                total_sec = (t_post - t_night).total_seconds()
                                progress = min(max(Decimal(str((cur_stamp - t_night).total_seconds() / total_sec)), Decimal(0)), Decimal(1)) if total_sec > 0 else Decimal(1)
                                adj_val = val + progress * (day_official_gain - c_val)
                        else:
                            adj_val = val

                        points.append((k, (day_baseline + adj_val) if adj_val is not None else None))
                    elif cur_stamp == t_night:
                        val = Decimal(0)
                        points.append((k, day_baseline + val))
                    else:
                        points.append((k, None))
                cur_stamp += timedelta(hours=2)

            # 跨日基准衔接：优先使用官方已确认收盘盈亏，确保与资产总览财务数据100%一致
            settled_gain = day_official_gain if day_official_gain is not None else (close_known_val if (max_observed_stamp >= t_post and close_known_val is not None) else last_known_val)
            running_cum = day_baseline + (settled_gain or Decimal(0))

        valid_points = [(k, v) for k, v in points if v is not None]
        if not valid_points:
            self.canvas.chart = None
            self.canvas.update()
            self.summary_badge.setText("")
            self.status_label.setText("当前筛选条件（人员/渠道/特定股票）下未匹配到有效收益点。")
            return

        min_pt = min(valid_points, key=lambda p: p[1])
        max_pt = max(valid_points, key=lambda p: p[1])
        last_pt = valid_points[-1]

        mode_name = "当年累计盈亏走势" if mode == "cumulative" else "每日日内盈亏走势"
        channel_desc = self.channel_filter.text()
        ticker_desc = f" · 标的：{selected_ticker}" if selected_ticker else " · 全部股票"
        person_desc = f" · {selected_person_name}"
        chart_title = f"{self._cached_year}年 {mode_name}（USD）{person_desc} · {channel_desc}{ticker_desc}"

        now_ny = datetime.now(NY)
        reached_day = (now_ny + timedelta(days=1) if now_ny.hour >= 20 else now_ny).date()
        reached_days = sum(date.fromisoformat(day) <= reached_day for day in all_trading_days)
        missing_days = max(0, reached_days - past_days_count)
        future_days = total_year_days - reached_days
        chart_note = (
            f"北京时间 · 整年全量 {total_year_days} 个美股交易日时间轴（已有曲线 {past_days_count} 个、已过日期缺点 {missing_days} 个、未来 {future_days} 个） · "
            f"细虚线按月份（每月初首个交易日）划分；高亮显示全年度最高与最低收益。"
        )

        series_name = "年度累计盈亏" if mode == "cumulative" else "当日盈亏"
        self.canvas.chart = Chart(
            id=-102,
            title=chart_title,
            kind="line",
            series=(Series(series_name, tuple(points)),),
            note=chart_note,
            x_labels=labels,
            x_ticks=tuple(ticks),
            show_points=False,
            vertical_lines=tuple(vertical_lines),
            highlight_extremes=True,
            break_keys=tuple(day_start_stamps) if mode == "intraday" else (),
        )
        self.canvas.update()

        currency = "USD"
        last_val_str = f"{last_pt[1]:+,.2f} {currency}"
        self.summary_badge.setText(
            f"全年 {total_year_days} 个交易日（已有曲线 {past_days_count} 个，已过日期缺点 {missing_days} 个，未来 {future_days} 个） · "
            f"最新盈亏：{last_val_str}"
        )

        dt_max_bj = datetime.fromisoformat(max_pt[0]).astimezone(zone).strftime("%m-%d %H:%M")
        dt_min_bj = datetime.fromisoformat(min_pt[0]).astimezone(zone).strftime("%m-%d %H:%M")
        max_val_str = f"{max_pt[1]:+,.2f} {currency} ({dt_max_bj})"
        min_val_str = f"{min_pt[1]:+,.2f} {currency} ({dt_min_bj})"
        self.status_label.setText(
            f"📈 {mode_name}（{selected_person_name} · {channel_desc}{ticker_desc}）：当前最新 {last_val_str} · "
            f"最高点：{max_val_str} · "
            f"最低点：{min_val_str} · "
            f"波动跨度：{max_pt[1] - min_pt[1]:,.2f} {currency}"
        )
        self.notes_label.setText(
            "数据说明：展示所选整年全部美股交易日时间轴（按每 2 个小时一个点的密度精细采样）；"
            "实盘曲线绘制截至当前时点，未来时段或缺少历史点的日期均留白；"
            "垂直虚线按月份（每月初首个交易日）精确划分；"
            "横坐标清晰标注各月份刻度；两端高亮圆点与防遮挡金额胶囊标注全年度最高与最低收益极值。"
        )

    def export(self):
        """Export the realtime chart to high-resolution PNG."""
        is_year = (self.tab_bar.currentIndex() == 1)
        period = self._cached_year or "当年" if is_year else (self._cached_month or "当月")
        mode = "累计走势" if self.mode_combo.currentData() == "cumulative" else "日内走势"
        ch = self.channel_filter.text().replace(" ", "_")
        ticker = self.stock_combo.selected_ticker() or "全股票"
        person = self.person_combo.currentText()
        sampling = "两小时采样" if is_year else "半小时采样"
        chart_type = "整年实时" if is_year else "整月实时"
        title = f"{period}_{chart_type}{mode}_{person}_{ch}_{ticker}_{sampling}"
        dlg_title = "导出整年实时走势图" if is_year else "导出当月实时走势图"
        path, _ = QFileDialog.getSaveFileName(self, dlg_title, f"{title}.png", "PNG (*.png)")
        if path:
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not self.container.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except Exception:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
