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
from portfolio.ui.filters import PersonFilter


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
        self.top_bar_widget = QWidget(self)
        top_bar = QHBoxLayout(self.top_bar_widget)
        top_bar.setContentsMargins(0, 0, 0, 0)
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

        # 人员筛选下拉框（支持多选，风格与主界面一致）
        self.person_combo = PersonFilter(self)
        self.person_combo.setMinimumWidth(125)
        self.person_combo.setToolTip("归属人员筛选（点击可单选或多选具体持有者叠加对比走势）")
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
        layout.addWidget(self.top_bar_widget)

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
        self._derived_generation=None
        self.maintenance_timer=QTimer(self)
        self.maintenance_timer.setInterval(2000)
        self.maintenance_timer.timeout.connect(self._poll_derived_progress)
        self.maintenance_timer.start()

    def _poll_derived_progress(self):
        if not self.isVisible():return
        source=self._resolve_source_path()
        if not source:return
        from portfolio.services.derived_maintenance import maintenance_status
        status=maintenance_status(source);progress=status['progress']
        was_running=getattr(self,'_history_running',False)
        self._history_running=status['running']
        if was_running and not status['running']:self._derived_generation=None
        self._sync_loading(status)
        if status['running'] or not status['ready']:
            text=progress.get('message','正在后台计算全部历史人员。')
            if progress.get('total_units'):
                text+=f" {progress.get('completed_units',0)}/{progress['total_units']}（{progress.get('percent',0)}%）"
            self.summary_badge.setText(text)
        if getattr(self,'_shared_busy',None):return
        if status['ready'] and status.get('generation')!=self._derived_generation:
            self._derived_generation=status['generation']
            if not self.month_combo.count():self._init_available_months()
            if not self.year_combo.count():self._init_available_years()
            if self.tab_bar.currentIndex()==1:self._load_year_data(self.year_combo.currentText())
            else:self._load_month_data(self.month_combo.currentText())

    def _refresh_active_period(self):
        if not self.analytics or not self.isVisible():
            return
        from portfolio.domain.live_quotes import market_trade_day
        _,days,halves,_=self.window.quote_store.load(datetime.now(UTC))
        today=market_trade_day(datetime.now(UTC),days,halves)
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
        conn=self._get_connection();months=[]
        if conn:
            try:months=[r[0] for r in conn.execute("SELECT DISTINCT substr(day,1,7) FROM us_calendar ORDER BY 1 DESC") if r[0]]
            finally:conn.close()
        if not months:
            today=date.today()
            months=[f"{year}-{month:02d}" for year in range(2026,today.year+1) for month in range(1,(today.month if year==today.year else 12)+1)][::-1]
        from portfolio.domain.live_quotes import market_trade_day
        _,calendar,halves,_=self.window.quote_store.load(datetime.now(UTC))
        preferred=market_trade_day(datetime.now(UTC),calendar,halves).strftime('%Y-%m')
        if preferred not in months:months.append(preferred);months.sort(reverse=True)
        current=self.month_combo.currentText() or preferred
        self.month_combo.blockSignals(True);self.month_combo.clear();self.month_combo.addItems(months)
        if current in months:self.month_combo.setCurrentText(current)
        self.month_combo.blockSignals(False)
        if self.tab_bar.currentIndex()==0:self._load_month_data(self.month_combo.currentText())

    def _init_available_years(self):
        conn=self._get_connection();years=[]
        if conn:
            try:years=[r[0] for r in conn.execute("SELECT DISTINCT substr(day,1,4) FROM us_calendar ORDER BY 1 DESC") if r[0]]
            finally:conn.close()
        if not years:years=[str(year) for year in range(date.today().year,2025,-1)]
        from portfolio.domain.live_quotes import market_trade_day
        _,calendar,halves,_=self.window.quote_store.load(datetime.now(UTC))
        preferred=str(market_trade_day(datetime.now(UTC),calendar,halves).year)
        if preferred not in years:years.append(preferred);years.sort(reverse=True)
        current=self.year_combo.currentText() or preferred
        self.year_combo.blockSignals(True);self.year_combo.clear();self.year_combo.addItems(years)
        if current in years:self.year_combo.setCurrentText(current)
        self.year_combo.blockSignals(False)
        if self.tab_bar.currentIndex()==1:self._load_year_data(self.year_combo.currentText())

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
        if isinstance(selected, (tuple, list)):
            return tuple(int(pid) for pid in selected) or None
        return (selected,) if selected is not None else None

    def _load_shared_period(self, period, year):
        if not self.analytics or not self.window:
            return
        inherited=getattr(self,'_parent_filters',None) is not None
        if inherited and getattr(self.analytics,'scope',None) and getattr(self.analytics.scope,'as_of',None):
            as_of=self.analytics.scope.as_of
            period=str(as_of.year) if year else as_of.strftime('%Y-%m')
        if not period:
            return
        from portfolio.services.derived_queries import load_history
        source=self._resolve_source_path()
        if not source:return
        if year:
            first,last=date(int(period),1,1),date(int(period),12,31)
        else:
            y,m=map(int,period.split('-'));first,last=date(y,m,1),date(y,m,calendar.monthrange(y,m)[1])
        people=self.analytics.scope.person_ids if inherited else self._shared_person_ids()
        others_mode = (getattr(self.window.person, 'currentData', lambda: None)() == 'OTHERS') if inherited else (self.person_combo.currentData() == 'OTHERS')
        scope=replace(self.analytics.scope,person_ids=people,start_date=None,as_of=min(last,date.today()))
        if getattr(self,'_parent_filters',None) is None:
            scope=replace(scope,account_ids=None,instrument_ids=None,category=None,search='')
        if scope.as_of < first:scope=replace(scope,as_of=last)
        inherited_filters=getattr(self,'_parent_filters',None)
        channels=([inherited_filters[0]] if inherited_filters[0] else None) if inherited_filters is not None else self.channel_filter.selected_channels()
        ticker=inherited_filters[1] if inherited_filters is not None else self.stock_combo.selected_ticker()
        mode=self.mode_combo.currentData() or 'cumulative'
        request=(period,year,scope,others_mode,tuple(sorted(channels or ())),ticker,mode)
        if getattr(self,'_shared_busy',None)==request:return
        self._shared_request=request;self._shared_busy=request
        self.summary_badge.setText('正在读取共享历史派生数据…')
        self.canvas.chart=None
        self.canvas.set_loading('正在读取并整理月度/年度历史曲线…')
        def task(cancel):
            from portfolio.services.derived_store import DerivedStore
            if not DerivedStore(source).readiness()['ready']:
                rows,state=load_history(source,self.window.quote_store,scope,first,last)
                return None,rows,state,None
            from portfolio.services.derived_queries import historical_analytics, trend
            result=historical_analytics(source,scope,first,last)
            rows,state=load_history(source,self.window.quote_store,scope,first,last,aggregate=True,channels=channels,ticker=ticker)
            chart,state=trend(source,self.window.quote_store,result,period,year=year,channels=channels,ticker=ticker,mode=mode,history_data=(rows,state))
            if scope.person_ids and len(scope.person_ids) > 1 and not others_mode:
                persons_map = {p['id']: p['name'] for p in self._get_persons()}
                person_series = []
                for pid in scope.person_ids:
                    pname = persons_map.get(pid, f"人员{pid}")
                    sub_scope = replace(scope, person_ids=(pid,))
                    sub_res = historical_analytics(source, sub_scope, first, last)
                    sub_rows, sub_st = load_history(source, self.window.quote_store, sub_scope, first, last, aggregate=True, channels=channels, ticker=ticker)
                    sub_chart, _ = trend(source, self.window.quote_store, sub_res, period, year=year, channels=channels, ticker=ticker, mode=mode, history_data=(sub_rows, sub_st))
                    if sub_chart.series:
                        person_series.append(Series(pname, sub_chart.series[0].points))
                if person_series:
                    chart = replace(chart, series=tuple(person_series))
            if state.get('baseline_state')=='REPORT_PENDING' or (not any(v is not None for series in chart.series for _,v in series.points) and (state.get('invalid_partitions') or state.get('not_generated_days') or getattr(result,'incomplete_people',False) or state.get('baseline_break_days'))):
                from portfolio.services.derived_maintenance import schedule_changed
                schedule_changed(source)
            return result,rows,state,chart
        def ready(value):
            if self._shared_request != request:return
            self._shared_busy=None
            self.canvas.set_loading()
            self._shared_analytics,self._shared_rows,self._shared_state,chart=value
            self._shared_period,self._shared_year=period,year
            if year:self._cached_year=period
            else:self._cached_month=period
            if value[0] is None:
                self.canvas.chart=None;self.canvas.update()
                self.status_label.setText(value[2].get('message','尚无已发布历史数据。'))
                self._sync_loading()
                return
            channels={r.values[1] for r in value[0].reports['每日持仓收益'].rows}
            tickers={r.values[3] for r in value[0].reports['每日持仓收益'].rows}
            self.channel_filter.populate(sorted(channels));self.stock_combo.populate(sorted(tickers))
            self._display_shared_chart(chart,value[2],year)
        lane=f'shared-trend-{id(self)}'
        self.window._submit(lane,task,ready)
        def failed(message):
            if self._shared_request==request:
                self._shared_busy=None
                self.summary_badge.setText(message)
                self.canvas.set_loading()
                self.canvas.empty_message='历史曲线查询失败：'+message
                self.canvas.update()
        self.window.jobs[lane].signals.error.connect(failed,Qt.ConnectionType.QueuedConnection)

    def set_parent_filters(self,channel,ticker):
        """Embedded Today charts share the parent channel/Ticker selection and hide redundant filter controls."""
        if hasattr(self,'top_bar_widget'):
            self.top_bar_widget.hide()
        if self.layout():
            self.layout().setContentsMargins(0, 4, 0, 0)
        filters=(channel,(ticker or '').strip())
        if getattr(self,'_parent_filters',None)==filters:return
        self._parent_filters=filters
        for control in (self.person_combo,self.channel_filter,self.stock_combo):
            control.setEnabled(False)
            control.setToolTip('随今日盈亏主页面的人员、渠道和股票筛选同步更新。')
        self.update_chart()

    def _render_shared_chart(self, year):
        if getattr(self,'_parent_filters',None) is not None and self.analytics and getattr(self.analytics,'scope',None) and getattr(self.analytics.scope,'as_of',None):
            as_of=self.analytics.scope.as_of
            period=str(as_of.year) if year else as_of.strftime('%Y-%m')
        else:
            period=self.year_combo.currentText() if year else self.month_combo.currentText()
        self._load_shared_period(period,year)

    def _sync_loading(self,status=None):
        if getattr(self,'_shared_busy',None):return
        from portfolio.services.derived_maintenance import maintenance_status
        source=self._resolve_source_path()
        if not source:return
        status=status or maintenance_status(source)
        progress=status['progress']
        chart=self.canvas.chart
        valid=bool(chart and any(v is not None for series in chart.series for _,v in series.points))
        if status['running']:
            text='正在后台生成历史盈亏，完成后自动刷新。\n'+progress.get('message','正在处理历史分区。')
            self.status_label.setText(text)
            if not valid:self.canvas.set_loading(text,progress.get('completed_units'),progress.get('total_units'))
        else:
            self.canvas.set_loading()
            if not valid:
                state=getattr(self,'_shared_state',{})
                message=progress.get('message','') if progress.get('state') in ('FAILED','INTERRUPTED') else state.get('message','')
                self.canvas.empty_message='历史曲线尚不可用（当前没有后台生成任务）。\n'+(message or '请检查行情覆盖、累计基准或任务失败原因。')
                self.status_label.setText(self.canvas.empty_message)
                self.canvas.update()

    def _display_shared_chart(self,chart,state,year):
        self.canvas.chart=chart;self.canvas.update()
        self.summary_badge.setText(state.get('message',''))
        self.notes_label.setText(chart.note)
        if len(chart.series) > 1:
            parts = []
            total_latest = Decimal(0)
            total_pts = 0
            for s in chart.series:
                s_valid = [v for _, v in s.points if v is not None]
                if s_valid:
                    total_pts = max(total_pts, len(s_valid))
                    total_latest += s_valid[-1]
                    parts.append(f"{s.name} {s_valid[-1]:+,.2f} USD")
            self.status_label.setText(
                f"有效采样 {total_pts} · 合计 {total_latest:+,.2f} USD（" + " · ".join(parts) + "）"
                if parts else '当前范围没有有效历史采样；请检查初始化、价格覆盖与缓存状态。'
            )
        else:
            valid=[(k,v) for series in chart.series for k,v in series.points if v is not None]
            latest=datetime.fromisoformat(valid[-1][0]).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M') if valid else ''
            self.status_label.setText(f'有效采样 {len(valid)} · 最近有效点 {latest} 北京时间：{valid[-1][1]:+,.2f} USD' if valid else '当前范围没有有效历史采样；请检查初始化、价格覆盖与缓存状态。')
        if year:
            months=state.get('months',{})
            coverage='；'.join(f"{month[5:]}月 {info['saved_days']}日/{info['complete_points']}个完整点" for month,info in sorted(months.items()) if month[5:]<='06')
            if coverage:self.status_label.setText(self.status_label.text()+' · 上半年已存明细：'+coverage+'；缺失/未确认部分不连线。')
        self._sync_loading()
        # The parent owns its cards and table. Re-entering show_page here can
        # call the wrong page after navigation/refactoring and abort rendering.

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
