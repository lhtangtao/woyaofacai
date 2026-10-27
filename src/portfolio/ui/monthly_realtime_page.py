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

    def _resolve_db_path(self):
        """Locate live_quotes.sqlite3 safely from window quote store or local application data."""
        if hasattr(self.window, "quote_store") and self.window.quote_store and self.window.quote_store.path.exists():
            return self.window.quote_store.path
        from portfolio.paths import quote_cache_path
        return quote_cache_path()

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
        self._shared_request=None
        if hasattr(self,'_shared_analytics'):del self._shared_analytics
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
            cur.execute("SELECT DISTINCT substr(day, 1, 7) FROM us_calendar ORDER BY 1 DESC")
            months = [r[0] for r in cur.fetchall() if r[0]]
        except Exception as e:
            self.error.emit(f"读取行情月份列表失败：{e}")
        finally:
            conn.close()

        if not months:
            months = [date.today().strftime("%Y-%m")]

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
            cur.execute("SELECT DISTINCT substr(day, 1, 4) FROM us_calendar ORDER BY 1 DESC")
            years = [r[0] for r in cur.fetchall() if r[0]]
        except Exception as e:
            self.error.emit(f"读取行情年份列表失败：{e}")
        finally:
            conn.close()

        if not years:
            years = [str(date.today().year)]

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

    def _shared_person_ids(self):
        selected=self.person_combo.currentData()
        if selected == "OTHERS":
            return tuple(p['id'] for p in self._get_persons() if p.get('name') != '个人' and p.get('enabled',True))
        return (selected,) if selected is not None else None

    def _load_shared_period(self, period, year):
        if not period or not self.analytics or not self.window:
            return
        from portfolio.services.derived_queries import load_history
        source=self._resolve_source_path()
        if not source:return
        if year:
            first,last=date(int(period),1,1),date(int(period),12,31)
        else:
            y,m=map(int,period.split('-'));first,last=date(y,m,1),date(y,m,calendar.monthrange(y,m)[1])
        scope=replace(self.analytics.scope,person_ids=self._shared_person_ids(),start_date=None,
                      account_ids=None,instrument_ids=None,category=None,search='',as_of=min(last,date.today()))
        if scope.as_of < first:scope=replace(scope,as_of=last)
        self._shared_request=(period,year,scope)
        self.summary_badge.setText('正在读取共享历史派生数据…')
        def task(cancel):
            result=self.window.analytics_service.calculate(scope,cancel=cancel)
            rows,state=load_history(source,self.window.quote_store,scope,first,last)
            return result,rows,state
        def ready(value):
            if self._shared_request != (period,year,scope):return
            self._shared_analytics,self._shared_rows,self._shared_state=value
            self._shared_period,self._shared_year=period,year
            if year:self._cached_year=period
            else:self._cached_month=period
            channels={r.values[1] for r in value[0].reports['每日持仓收益'].rows}
            tickers={r.values[3] for r in value[0].reports['每日持仓收益'].rows}
            self.channel_filter.populate(sorted(channels));self.stock_combo.populate(sorted(tickers))
            self.update_chart()
        self.window._submit('shared-trend',task,ready)

    def _render_shared_chart(self, year):
        if not hasattr(self,'_shared_analytics') or self._shared_year != year:return
        from portfolio.services.derived_queries import trend
        source=self._resolve_source_path()
        chart,state=trend(source,self.window.quote_store,self._shared_analytics,self._shared_period,year=year,
                          channels=self.channel_filter.selected_channels(),ticker=self.stock_combo.selected_ticker(),
                          mode=self.mode_combo.currentData() or 'cumulative',
                          history_data=(self._shared_rows,self._shared_state))
        self.canvas.chart=chart;self.canvas.update()
        self.summary_badge.setText(state.get('message',''))
        self.notes_label.setText(chart.note)
        valid=[(k,v) for series in chart.series for k,v in series.points if v is not None]
        self.status_label.setText(f'有效采样 {len(valid)} · 最新 {valid[-1][1]:+,.2f} USD' if valid else '当前范围没有有效历史采样；请检查初始化、价格覆盖与缓存状态。')

    def _load_month_data(self, month_str):
        return self._load_shared_period(month_str, False)

    def _load_year_data(self, year_str):
        return self._load_shared_period(year_str, True)

    def _on_filter_changed(self, *_):
        self.update_chart()

    def update_chart(self):
        if self.tab_bar.currentIndex() == 1:
            self._render_year_chart()
        else:
            self._render_month_chart()

    def _render_month_chart(self):
        return self._render_shared_chart(False)

    def _render_year_chart(self):
        return self._render_shared_chart(True)

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
