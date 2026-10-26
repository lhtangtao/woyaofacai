"""Modern Financial Market Monitoring Center (PRD v2.0).
Independent observation cache, technical analytics, rule evaluation, and desktop notifications.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.models import Ledger, QueryScope
from portfolio.domain.monitor_rules import (
    AlertEvent,
    MonitorRule,
    RuntimeState,
    describe_rule,
)
from portfolio.domain.technical_indicators import PERIODS
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.monitor_mapping import MAPPING_VERSION, Mapping, normalize
from portfolio.services.monitor_service import (
    LongbridgeMonitorProvider,
    MonitorService,
    holding_sources,
    key_for,
)
from portfolio.services.monitor_store import MonitorStore
from portfolio.services.notification_service import notification_service
from portfolio.ui.filters import AccountFilter
from portfolio.ui.monitor_chart import MonitorChart
from portfolio.ui.rule_drawer import RuleDrawerDialog
from portfolio.ui.theme import tokens
from portfolio.ui.workers import Worker


def fmt(value: Any, places: int = 2, suffix: str = "") -> str:
    return "—" if value is None else f"{value:,.{places}f}" + suffix


def label(text: str, name: str = "secondary") -> QLabel:
    item = QLabel(text)
    item.setObjectName(name)
    item.setWordWrap(True)
    return item


def card() -> tuple[QFrame, QVBoxLayout]:
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)
    return widget, layout


class AddStockDialog(QDialog):
    """Dialog to manually add an unheld stock to technical watch targets."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("添加自选股票")
        self.resize(380, 200)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        form = QFormLayout()
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("例如 TSLA, NVDA, AAPL")
        form.addRow("股票代码:", self.symbol_input)

        self.market_combo = QComboBox()
        self.market_combo.addItem("美股 (US)", "US")
        form.addRow("交易市场:", self.market_combo)
        layout.addLayout(form)

        hint = QLabel("提示：添加后将纳入技术指标计算与监控观察列表，默认不创建监控规则。")
        hint.setStyleSheet("color: #64748B; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.symbol = ""
        self.market = "US"

    def _accept(self):
        sym = self.symbol_input.text().strip().upper()
        if not sym:
            QMessageBox.warning(self, "输入错误", "请输入有效的股票代码！")
            return
        if "." not in sym:
            sym = f"{sym}.US"
        self.symbol = sym
        self.market = self.market_combo.currentData()
        self.accept()


class AlertDetailDialog(QDialog):
    """Dialog displaying full diagnostic snapshot of a triggered alert event."""

    def __init__(self, event: AlertEvent, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"提醒快照详情 · {event.analysis_symbol}")
        self.resize(600, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"【{event.analysis_symbol}】{event.rule_name}")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        time_str = event.detected_at
        try:
            time_str = (
                datetime.fromisoformat(event.detected_at)
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .strftime("%Y-%m-%d %H:%M:%S 北京时间")
            )
        except Exception:
            pass

        info_box = QGroupBox("触发基本信息")
        info_layout = QFormLayout(info_box)
        info_layout.addRow("触发时间:", QLabel(time_str))
        info_layout.addRow("监控标的:", QLabel(f"{event.analysis_symbol} (监控代码: {event.monitored_symbol})"))
        info_layout.addRow("触发当时价格:", QLabel(f"${event.observed_price}"))
        info_layout.addRow("设定的阈值:", QLabel(event.threshold_value or "—"))
        info_layout.addRow("判定原因:", QLabel(event.trigger_reason))
        info_layout.addRow("所属交易时段:", QLabel(event.quote_session or "盘中"))
        layout.addWidget(info_box)

        ind_box = QGroupBox("触发当时技术指标快照 (长桥前复权)")
        ind_layout = QGridLayout(ind_box)
        row = 0
        snap = event.indicator_snapshot or {}
        for k, v in sorted(snap.items()):
            ind_layout.addWidget(QLabel(f"<b>{k}:</b> {v}"), row // 3, row % 3)
            row += 1
        if not snap:
            ind_layout.addWidget(QLabel("无指标快照数据"), 0, 0)
        layout.addWidget(ind_box)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.accept)
        layout.addWidget(btn_box)


class StockCellWidget(QWidget):
    """
    Custom cell widget for Watchlist Table Column 0.
    Displays stock symbol as a clickable hyperlink to AI Stock Analysis page,
    while clicks on any other non-hyperlink part select the table row as normal.
    """

    def __init__(
        self,
        symbol: str,
        source_label: str,
        row_idx: int,
        on_link_clicked,
        on_select_row,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.symbol = symbol
        self.row_idx = row_idx
        self.on_link_clicked = on_link_clicked
        self.on_select_row = on_select_row

        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 3, 2, 3)
        layout.setSpacing(1)

        # 1. Hyperlink Row
        link_row = QHBoxLayout()
        link_row.setContentsMargins(0, 0, 0, 0)
        link_row.setSpacing(4)

        self.link_lbl = QLabel()
        self.link_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.link_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.link_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        clean_sym = symbol.strip().upper().removesuffix(".US")
        html_link = (
            f'<a href="{clean_sym}" style="color: #0284C7; text-decoration: underline; '
            f'font-weight: 700; font-size: 13px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">'
            f'{clean_sym}</a>'
        )
        self.link_lbl.setText(html_link)
        self.link_lbl.setToolTip(f"点击直接进入【{clean_sym}】AI 股票深度研报分析页面")
        self.link_lbl.linkActivated.connect(self._on_link_activated)
        link_row.addWidget(self.link_lbl)
        link_row.addStretch()
        layout.addLayout(link_row)

        # 2. Source Subtitle Row
        self.sub_lbl = QLabel(f"来自 {source_label}")
        self.sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        self.sub_lbl.setToolTip(f"标的持仓来源: {source_label}")
        # Pass clicks through to container widget so clicking subtitle selects row
        self.sub_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.sub_lbl)

    def _on_link_activated(self, link: str):
        if self.on_select_row:
            self.on_select_row(self.row_idx, self.symbol)
        if self.on_link_clicked:
            self.on_link_clicked(link)

    def mousePressEvent(self, event):
        # Click on non-hyperlink area of the cell selects the table row normally
        if self.on_select_row:
            self.on_select_row(self.row_idx, self.symbol)
        super().mousePressEvent(event)


class MonitoringPage(QWidget):
    """
    Complete Market Monitoring Center (PRD v2.0):
    - Multi-tab navigation: Technical Watchlist, Rule Management, Alert History
    - Real-time quote sharing & local indicator calculation
    - Independent rules & notification delivery
    - Process-only monitoring boundary
    """

    def __init__(self, window, *, store=None):
        super().__init__()
        self.host = window
        self.store = store
        self.service: Optional[MonitorService] = None
        self.holdings_service: Optional[AnalyticsService] = None
        self.namespace = self.scope_key = self.signature = None
        self.targets: List[Dict[str, Any]] = []
        self.market: Dict[str, Any] = {}
        self.rules: List[MonitorRule] = []
        self.alert_events: List[AlertEvent] = []
        self.selected: str = ""
        self.busy: bool = False
        self.worker: Optional[Worker] = None
        self.generation: int = 0
        self.account_source: Optional[str] = None

        self._init_ui()

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # 1. Top Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.owner = QComboBox()
        self.owner.addItems(["仅本人持仓", "全部账户持仓（所有人）"])
        toolbar.addWidget(self.owner)

        self.accounts = AccountFilter()
        toolbar.addWidget(self.accounts)

        self.sync_button = QPushButton("同步持仓")
        self.sync_button.setStyleSheet("font-weight: 500;")
        toolbar.addWidget(self.sync_button)

        self.add_stock_btn = QPushButton("＋ 添加自选股票")
        toolbar.addWidget(self.add_stock_btn)

        self.new_rule_btn = QPushButton("＋ 新建监控规则")
        self.new_rule_btn.setStyleSheet("background-color: #2563EB; color: white; font-weight: bold;")
        toolbar.addWidget(self.new_rule_btn)

        self.toggle_all_btn = QPushButton("全部暂停")
        toolbar.addWidget(self.toggle_all_btn)

        self.refresh_button = QPushButton("刷新行情")
        toolbar.addWidget(self.refresh_button)

        toolbar.addStretch()

        self.status_badge = label("🟢 监控待命中", "secondary")
        self.status_badge.setStyleSheet(
            "padding: 4px 10px; border-radius: 6px; background-color: #F1F5F9; font-weight: 600;"
        )
        toolbar.addWidget(self.status_badge)

        root.addLayout(toolbar)

        # 2. Top Metric Cards (4 stats)
        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.stat_widgets = []
        card_titles = ["观察标的 (持仓/自选)", "监控规则 (启用/总数)", "今日触发提醒", "监控运行状态"]
        for title in card_titles:
            panel, col = card()
            col.addWidget(label(title, "secondary"))
            val = label("0", "metricValue")
            val.setStyleSheet("font-size: 20px; font-weight: bold;")
            col.addWidget(val)
            self.stat_widgets.append(val)
            stats.addWidget(panel)
        root.addLayout(stats)

        self.notice = label("从当前持仓自动导入；新规则默认暂停，仅在程序打开时监控。")
        self.notice.setStyleSheet("color: #64748B; font-size: 12px;")
        root.addWidget(self.notice)

        # 3. Main Tabs
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # Tab 1: Watchlist and Technical Analysis
        tab1 = QWidget()
        self._build_watchlist_tab(tab1)
        self.tabs.addTab(tab1, "📊 观察列表与行情分析")

        # Tab 2: Rule Management
        tab2 = QWidget()
        self._build_rules_tab(tab2)
        self.tabs.addTab(tab2, "⚙️ 监控规则管理")

        # Tab 3: Alert History
        tab3 = QWidget()
        self._build_history_tab(tab3)
        self.tabs.addTab(tab3, "🔔 提醒历史记录")

        # Connect signals
        self.owner.currentIndexChanged.connect(lambda: self.activate(force=True))
        self.accounts.changed.connect(lambda: self.activate(force=True))
        self.sync_button.clicked.connect(lambda: self.activate(force=True))
        self.add_stock_btn.clicked.connect(self._on_add_stock)
        self.new_rule_btn.clicked.connect(lambda: self._open_rule_dialog())
        self.toggle_all_btn.clicked.connect(self._on_toggle_all_rules)
        self.refresh_button.clicked.connect(self.refresh_market)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        # Periodic Monitoring Timer (10 seconds)
        self.timer = QTimer(self)
        self.timer.setInterval(10000)
        self.timer.timeout.connect(self.tick)

    # --- TAB 1: WATCHLIST & TECHNICAL ANALYSIS ---
    def _build_watchlist_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(6, 10, 6, 6)

        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split)

        # Left Column: Watchlist Table
        left, col = card()
        split.addWidget(left)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索分析标的 / 持仓代码")
        filters.addWidget(self.search, 1)

        self.kind = QComboBox()
        self.kind.addItems(["全部类型", "直接持有", "正股映射", "自选股票", "待确认", "已不再持有"])
        filters.addWidget(self.kind)
        col.addLayout(filters)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "标的 / 来源",
            "现价 / 涨跌",
            "MA20",
            "MA50",
            "MA200",
            "MA250",
            "距MA200",
            "短/中/长",
            "规则",
            "操作",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setMinimumSectionSize(30)
        # 优化各列宽度：大幅缩小名字列与规则/操作列，释放充足空间确保全部均线价格 100% 完整显示无省略号
        col_widths = (82, 76, 74, 74, 76, 76, 76, 70, 42, 36)
        for col_idx, width in enumerate(col_widths):
            self.table.setColumnWidth(col_idx, width)
        # 禁止末尾操作列自动拉伸，防止操作列变成巨大空白并压缩前列
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setMinimumWidth(680)
        self.table.setStyleSheet(
            "QTableWidget { font-size: 12px; }"
            "QTableWidget::item { padding-left: 2px; padding-right: 2px; }"
            "QHeaderView::section { font-size: 12px; font-weight: bold; padding: 2px 2px; }"
        )
        col.addWidget(self.table)

        table_actions = QHBoxLayout()
        self.mapping_button = QPushButton("查看 / 管理映射")
        self.mapping_button.clicked.connect(self.edit_mapping)
        table_actions.addWidget(self.mapping_button)

        self.remove_stock_btn = QPushButton("移除所选自选股票")
        self.remove_stock_btn.clicked.connect(self._on_remove_stock)
        table_actions.addWidget(self.remove_stock_btn)
        table_actions.addStretch()
        col.addLayout(table_actions)

        # Right Column: Chart & Indicator Cards
        self.right_panel, r_col = card()
        split.addWidget(self.right_panel)
        split.setSizes([780, 620])
        split.setChildrenCollapsible(False)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        r_col.addWidget(right_scroll)

        right_body = QWidget()
        right_scroll.setWidget(right_body)
        detail_layout = QVBoxLayout(right_body)
        detail_layout.setContentsMargins(0, 0, 8, 0)
        detail_layout.setSpacing(12)

        # Header info
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        self.title = label("选择一个分析标的", "sectionTitle")
        self.title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_row.addWidget(self.title)

        self.price = label("—", "metricValue")
        self.price.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_row.addWidget(self.price)
        header_row.addStretch()

        self.ai_analyze_btn = QPushButton("🧠 AI 股票研报")
        self.ai_analyze_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_analyze_btn.setStyleSheet(
            "QPushButton { background: #F0FDF4; border: 1px solid #86EFAC; border-radius: 6px; padding: 4px 10px; font-size: 12px; color: #166534; font-weight: 600; }"
            "QPushButton:hover { background: #DCFCE7; border-color: #22C55E; color: #15803D; }"
        )
        self.ai_analyze_btn.clicked.connect(lambda: self.jump_to_stock_analysis(self.selected))
        header_row.addWidget(self.ai_analyze_btn)

        self.add_rule_for_stock_btn = QPushButton("＋ 新建规则")
        self.add_rule_for_stock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_rule_for_stock_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; padding: 4px 10px; font-size: 12px; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self.add_rule_for_stock_btn.clicked.connect(lambda: self._open_rule_dialog(default_symbol=self.selected))
        header_row.addWidget(self.add_rule_for_stock_btn)
        detail_layout.addLayout(header_row)

        self.detail = label("")
        detail_layout.addWidget(self.detail)

        # Chart controls
        options = QHBoxLayout()
        self.range = QComboBox()
        for t, c in (("1个月", 21), ("3个月", 63), ("6个月", 126), ("1年", 252), ("全部缓存", 0)):
            self.range.addItem(t, c)
        self.range.setCurrentIndex(2)
        options.addWidget(self.range)

        self.panel = QComboBox()
        self.panel.addItems(["成交量", "RSI14", "MACD"])
        options.addWidget(self.panel)

        self.boll_check = QCheckBox("BOLL布林带")
        self.boll_check.setChecked(False)
        self.boll_check.toggled.connect(self._on_boll_toggled)
        options.addWidget(self.boll_check)

        options.addStretch()
        detail_layout.addLayout(options)

        mas = QGridLayout()
        self.ma_checks = {}
        for i, n in enumerate(PERIODS):
            check = QCheckBox(f"MA{n}")
            check.setChecked(n in (20, 50, 200))
            mas.addWidget(check, i // 4, i % 4)
            self.ma_checks[n] = check
            check.toggled.connect(self.draw_chart)
        detail_layout.addLayout(mas)

        self.chart = MonitorChart()
        detail_layout.addWidget(self.chart)

        # Technical Indicators Grid with Quick Add/Edit Rule Icon Buttons
        ind_group = QGroupBox("技术指标详情与监控预警")
        ind_box = QVBoxLayout(ind_group)
        ind_box.setContentsMargins(8, 6, 8, 6)
        ind_box.setSpacing(4)

        # 1. Full MA Moving Averages (All periods)
        c1, self.lbl_ma_summary = self._create_indicator_card(
            "📈 均线组",
            lambda: self._open_rule_dialog(default_symbol=self.selected, default_type="PRICE_MA_CROSS"),
            tooltip="新建/编辑【均线交叉/偏离】监控预警规则",
        )
        ind_box.addWidget(c1)

        # 2. RSI
        c2, self.lbl_rsi_summary = self._create_indicator_card(
            "🎯 RSI14",
            lambda: self._open_rule_dialog(default_symbol=self.selected, default_type="RSI"),
            tooltip="新建/编辑【RSI超买超卖】监控预警规则",
        )
        ind_box.addWidget(c2)

        # 3. MACD
        c3, self.lbl_macd_summary = self._create_indicator_card(
            "⚡ MACD",
            lambda: self._open_rule_dialog(default_symbol=self.selected, default_type="MACD"),
            tooltip="新建/编辑【MACD金叉死叉】监控预警规则",
        )
        ind_box.addWidget(c3)

        # 4. BOLL
        c4, self.lbl_boll_summary = self._create_indicator_card(
            "📊 布林带",
            lambda: self._open_rule_dialog(default_symbol=self.selected, default_type="PRICE"),
            tooltip="新建/编辑【布林通道突破】监控预警规则",
        )
        ind_box.addWidget(c4)

        # 5. Volume
        c5, self.lbl_vol_summary = self._create_indicator_card(
            "📦 成交量",
            lambda: self._open_rule_dialog(default_symbol=self.selected, default_type="VOLUME"),
            tooltip="新建/编辑【成交量放量突破】监控预警规则",
        )
        ind_box.addWidget(c5)

        # 6. ATH & 52W
        c6, self.lbl_ath_summary = self._create_indicator_card(
            "🏆 极值/回撤",
            lambda: self._open_rule_dialog(default_symbol=self.selected, default_type="ATH_DRAWDOWN"),
            tooltip="新建/编辑【52周新高/回撤】监控预警规则",
        )
        ind_box.addWidget(c6)

        detail_layout.addWidget(ind_group)

        self.basis = label("MA基于已完成交易日日K；前复权，与当前价同尺度。")
        self.basis.setStyleSheet("color: #64748B; font-size: 11px;")
        detail_layout.addWidget(self.basis)

        # Connect watchlist filters & selections
        self.search.textChanged.connect(self.draw_table)
        self.kind.currentIndexChanged.connect(self.draw_table)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.range.currentIndexChanged.connect(self.draw_chart)
        self.panel.currentIndexChanged.connect(self.draw_chart)

    def _create_indicator_card(self, title: str, on_btn_click, tooltip: str = ""):
        card_frame = QFrame()
        card_frame.setStyleSheet(
            "QFrame { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; }"
        )
        row = QHBoxLayout(card_frame)
        row.setContentsMargins(8, 3, 6, 3)
        row.setSpacing(8)

        lbl_title = QLabel(f"<b>{title}</b>")
        lbl_title.setStyleSheet("color: #1E293B; font-size: 11px; min-width: 62px;")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(lbl_title)

        lbl_values = QLabel("—")
        lbl_values.setWordWrap(True)
        lbl_values.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl_values.setStyleSheet(
            "color: #334155; font-size: 11px; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif;"
        )
        row.addWidget(lbl_values, 1)

        btn = QPushButton("➕")
        btn.setFixedSize(22, 22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip or f"新建/编辑【{title}】监控预警规则")
        btn.setStyleSheet(
            "QPushButton { background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 4px; font-size: 11px; color: #1D4ED8; font-weight: bold; padding: 0px; }"
            "QPushButton:hover { background: #DBEAFE; border-color: #3B82F6; color: #1E40AF; }"
        )
        btn.clicked.connect(on_btn_click)
        row.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

        return card_frame, lbl_values

    # --- TAB 2: RULES MANAGEMENT ---
    def _build_rules_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Rules Toolbar
        r_toolbar = QHBoxLayout()
        r_toolbar.setSpacing(8)

        self.rule_sym_filter = QComboBox()
        self.rule_sym_filter.addItem("全部标的", "")
        self.rule_sym_filter.currentIndexChanged.connect(self._load_rules_table)
        r_toolbar.addWidget(self.rule_sym_filter)

        self.rule_state_filter = QComboBox()
        self.rule_state_filter.addItems(["全部状态", "仅已启用", "仅已暂停"])
        self.rule_state_filter.currentIndexChanged.connect(self._load_rules_table)
        r_toolbar.addWidget(self.rule_state_filter)

        self.rule_search = QLineEdit()
        self.rule_search.setPlaceholderText("搜索规则名称 / 备注")
        self.rule_search.textChanged.connect(self._load_rules_table)
        r_toolbar.addWidget(self.rule_search, 1)

        btn_batch_enable = QPushButton("批量启用所选")
        btn_batch_enable.clicked.connect(lambda: self._batch_set_rule_state(True))
        r_toolbar.addWidget(btn_batch_enable)

        btn_batch_pause = QPushButton("批量暂停所选")
        btn_batch_pause.clicked.connect(lambda: self._batch_set_rule_state(False))
        r_toolbar.addWidget(btn_batch_pause)

        btn_batch_del = QPushButton("批量删除")
        btn_batch_del.clicked.connect(self._batch_delete_rules)
        r_toolbar.addWidget(btn_batch_del)

        layout.addLayout(r_toolbar)

        # Rules Table
        self.rules_table = QTableWidget(0, 9)
        self.rules_table.setHorizontalHeaderLabels([
            "规则名称",
            "监控标的",
            "类别",
            "条件描述",
            "时段/时机",
            "重复策略",
            "触发统计",
            "启用状态",
            "操作",
        ])
        self.rules_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rules_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rules_table.verticalHeader().hide()
        self.rules_table.setAlternatingRowColors(True)
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.rules_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.rules_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.rules_table.doubleClicked.connect(self._on_rule_double_clicked)
        layout.addWidget(self.rules_table)

    # --- TAB 3: ALERT HISTORY ---
    def _build_history_tab(self, parent: QWidget):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        h_toolbar = QHBoxLayout()
        h_toolbar.setSpacing(10)

        self.unread_only_check = QCheckBox("仅显示未读提醒")
        self.unread_only_check.toggled.connect(self._load_history_table)
        h_toolbar.addWidget(self.unread_only_check)

        h_toolbar.addStretch()

        btn_mark_all_read = QPushButton("全部标为已读")
        btn_mark_all_read.clicked.connect(self._mark_all_alerts_read)
        h_toolbar.addWidget(btn_mark_all_read)

        btn_clear_history = QPushButton("清空提醒记录")
        btn_clear_history.clicked.connect(self._clear_alert_history)
        h_toolbar.addWidget(btn_clear_history)

        btn_refresh_hist = QPushButton("刷新记录")
        btn_refresh_hist.clicked.connect(self._load_history_table)
        h_toolbar.addWidget(btn_refresh_hist)

        layout.addLayout(h_toolbar)

        # History Table
        self.history_table = QTableWidget(0, 8)
        self.history_table.setHorizontalHeaderLabels([
            "触发时间 (北京时间)",
            "标的代码",
            "规则名称",
            "触发详情 / 条件",
            "现价 / 阈值",
            "交易时段",
            "状态",
            "操作",
        ])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().hide()
        self.history_table.setAlternatingRowColors(True)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.history_table)

    # --- PAGE CONTROLS & ACTIVATION ---
    def clear(self):
        self.generation += 1
        if self.worker:
            self.worker.cancel.set()
        self.worker = None
        self.busy = False
        self.signature = None
        self.targets = []
        self.market = {}
        self.selected = ""
        self.timer.stop()
        self.draw_table()
        self.draw_detail()
        self.notice.setText("等待当前数据源连接；正式界面不会自动填入演示行情。")

    def submit(self, task, ready):
        if self.worker:
            self.worker.cancel.set()
        worker = Worker(task)
        self.worker = worker
        self.busy = True
        generation = self.generation

        def done(result):
            if self.worker is worker and generation == self.generation:
                self.busy = False
                ready(result)

        def error(message):
            if self.worker is worker and generation == self.generation:
                self.busy = False
                self.notice.setText("监控更新失败；请检查数据源、凭证及行情权限后重试。缓存保留。")

        worker.signals.done.connect(done, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(error, Qt.ConnectionType.QueuedConnection)
        self.host.pool.start(worker)

    def activate(self, force: bool = False):
        info = getattr(self.host, "info", None)
        if info is None:
            return
        path = str(info.path)
        if self.account_source != path:
            self.accounts.blockSignals(True)
            self.accounts.populate(info.accounts)
            for identifier, action in self.accounts.actions_by_id.items():
                action.setChecked(identifier in (self.host.accounts.selected_ids() or ()))
            self.accounts.blockSignals(False)
            self.account_source = path

        own = tuple(p["id"] for p in info.persons if p["name"] == "个人" and p["enabled"])
        if self.owner.currentIndex() == 0 and not own:
            self.clear()
            self.notice.setText("未找到启用的“个人”归属，无法确定本人持仓；请核对人员配置。")
            return

        scope = QueryScope(
            as_of=date.today(),
            ledger=Ledger.PERSON,
            person_ids=own if self.owner.currentIndex() == 0 else None,
            account_ids=self.accounts.selected_ids(),
        )

        source = Path(path)
        wal = Path(path + "-wal")
        signature = (
            path,
            scope,
            source.stat().st_mtime_ns if source.exists() else 0,
            wal.stat().st_mtime_ns if wal.exists() else 0,
            MAPPING_VERSION,
        )

        self.timer.start()
        if signature == self.signature and not force:
            if not self.busy:
                self.refresh_market()
            return

        self.generation += 1
        self.signature = signature
        self.namespace = hashlib.sha256(path.encode()).hexdigest()
        self.scope_key = key_for(path, scope)
        instruments = {i["id"]: i for i in info.instruments}

        if self.holdings_service is None or self.holdings_service.path != info.path:
            self.holdings_service = AnalyticsService(info.path)
        analytics_service = self.holdings_service
        namespace, key = self.namespace, self.scope_key
        credential = self.host.live_panel.path

        if self.service is None or self.service.provider.path != credential:
            cache = self.store or MonitorStore(
                Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
                / "monitoring.sqlite3"
            )
            self.store = cache
            self.service = MonitorService(cache, LongbridgeMonitorProvider(credential))

        service = self.service
        self.notice.setText("正在只读计算当前持仓并同步技术观察列表…")

        def task(cancel):
            analytics = analytics_service.calculate(scope, cancel=cancel)
            if cancel.is_set():
                return None
            sources = holding_sources(analytics, instruments, service.store.overrides(namespace))
            targets = service.store.sync(key, sources, datetime.now(ZoneInfo("Asia/Shanghai")))
            market = service.load(targets, cancel, network=False)
            return targets, market

        def ready(payload):
            if not payload:
                return
            self.targets, self.market = payload
            self.draw_table()
            self._update_top_stats()
            self._update_rule_symbol_filter()
            self.refresh_market()

        self.submit(task, ready)

    def tick(self):
        """Periodic background monitoring tick: executes as long as Money Portfolio is open."""
        if not self.busy and self.service and self.scope_key:
            self.refresh_market()

    def refresh_market(self):
        if self.busy or not self.service or not self.scope_key:
            return
        targets = self.targets
        service = self.service
        feed = self.host.live_panel.feed
        active = {
            t["analysis_symbol"] for t in targets if t["archive_state"] == "HELD" and t["market"] == "US"
        }
        self.host.live_panel.observation_symbols = active
        if feed:
            with feed.lock:
                symbols = set(feed.symbols) | active
            feed.set_symbols(symbols)
        quotes = feed.snapshot()[2] if feed else {}
        network = not getattr(self.host.info, "is_demo", True)

        def task(cancel):
            market = service.load(targets, cancel, quotes, network)
            # Evaluate monitoring rules and dispatch notifications if triggered
            triggered = service.evaluate_rules(self.scope_key, market)
            return market, triggered

        def ready(result):
            if not result:
                return
            market, triggered = result
            self.market = market
            self.draw_table()
            self._update_top_stats()
            if self.tabs.currentIndex() == 1:
                self._load_rules_table()
            elif self.tabs.currentIndex() == 2 or triggered:
                self._load_history_table()

            errors = [symbol + "：" + d["error"] for symbol, d in market.items() if d["error"]]
            if errors:
                self.notice.setText("；".join(errors))
            elif triggered:
                self.notice.setText(f"已触发 {len(triggered)} 条预警提醒！")
            else:
                self.notice.setText("行情实时监控中 · 规则判断正常")

        self.submit(task, ready)

    def _update_top_stats(self):
        if not self.store or not self.scope_key:
            return
        stats = self.store.stats(self.scope_key)
        targets_count = stats.get("targets_count", 0)
        manual_count = stats.get("manual_count", 0)
        enabled_rules = stats.get("enabled_rules", 0)
        total_rules = stats.get("total_rules", 0)
        today_alerts = stats.get("today_alerts", 0)

        self.stat_widgets[0].setText(f"{targets_count} (自选 {manual_count})")
        self.stat_widgets[1].setText(f"{enabled_rules} / {total_rules}")
        self.stat_widgets[2].setText(str(today_alerts))

        status_info = self.service.get_status(self.scope_key) if self.service else {}
        status_desc = status_info.get("status_desc", "待命中")
        level = status_info.get("status_level", "IDLE")

        self.stat_widgets[3].setText(status_desc)
        if level == "RUNNING":
            self.status_badge.setText("🟢 监控中")
            self.status_badge.setStyleSheet("padding: 4px 10px; border-radius: 6px; background-color: #DCFCE7; color: #166534; font-weight: 600;")
            self.toggle_all_btn.setText("全部暂停")
        elif level == "WARNING":
            self.status_badge.setText("🟡 行情受限")
            self.status_badge.setStyleSheet("padding: 4px 10px; border-radius: 6px; background-color: #FEF9C3; color: #854D0E; font-weight: 600;")
        else:
            self.status_badge.setText("⚪ 待命中")
            self.status_badge.setStyleSheet("padding: 4px 10px; border-radius: 6px; background-color: #F1F5F9; color: #475569; font-weight: 600;")
            self.toggle_all_btn.setText("全部启用")

    def _update_rule_symbol_filter(self):
        curr = self.rule_sym_filter.currentData()
        self.rule_sym_filter.blockSignals(True)
        self.rule_sym_filter.clear()
        self.rule_sym_filter.addItem("全部标的", "")
        for t in self.targets:
            sym = t["analysis_symbol"]
            self.rule_sym_filter.addItem(sym.removesuffix(".US"), sym)
        idx = self.rule_sym_filter.findData(curr)
        if idx >= 0:
            self.rule_sym_filter.setCurrentIndex(idx)
        self.rule_sym_filter.blockSignals(False)

    # --- WATCHLIST TABLE RENDERING ---
    def draw_table(self, *_):
        text = self.search.text().strip().upper()
        kind = self.kind.currentIndex()
        rows = []
        rule_counts = self.store.active_rule_count_by_symbol(self.scope_key) if self.store and self.scope_key else {}

        for target in self.targets:
            sources = target.get("sources", [])
            types = {s.get("relation_type") for s in sources}
            is_manual = (target.get("source_type") == "MANUAL")
            is_held = (target.get("archive_state") == "HELD")

            if (
                text
                and text not in target["analysis_symbol"]
                and not any(text in s.get("held_symbol", "") for s in sources)
            ):
                continue

            if kind == 1 and ("DIRECT" not in types or is_manual):
                continue
            if kind == 2 and not types.intersection(("LEVERAGED_LONG", "LEVERAGED_SHORT")):
                continue
            if kind == 3 and "INDEX_PROXY" not in types:
                continue
            if kind == 4 and not is_manual:
                continue
            if kind == 5 and not any(s.get("status") == "PENDING" for s in sources):
                continue
            if kind == 6 and is_held:
                continue
            if kind != 6 and not is_held:
                continue
            rows.append(target)

        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        selected_row = 0

        for row, target in enumerate(rows):
            symbol = target["analysis_symbol"]
            data = self.market.get(symbol, {})
            m = data.get("metrics", {})
            sources = target.get("sources", [])
            is_manual = (target.get("source_type") == "MANUAL")

            if is_manual:
                source_label = "自选添加"
            else:
                source_label = "、".join(
                    dict.fromkeys(
                        s["held_symbol"].removesuffix(".US")
                        + (" 待确认" if s.get("status") == "PENDING" else " 代理" if s.get("relation_type") == "INDEX_PROXY" else "")
                        for s in sources
                    )
                ) or "持仓标的"

            if target.get("archive_state") != "HELD":
                source_label = "已不再持有（保留配置）"

            act_rules = rule_counts.get(symbol, 0)
            rules_badge = f"{act_rules}条" if act_rules > 0 else "无"
            action_icon = "✏️" if act_rules > 0 else "➕"

            values = [
                symbol,
                f"{fmt(data.get('price'))}\n{fmt(data.get('change'), suffix='%')}",
                *(fmt(m.get("MA" + str(n))) for n in (20, 50, 200, 250)),
                fmt(m.get("DEV200"), suffix="%"),
                "/".join(
                    m.get(k, "—").replace("偏", "").replace("数据不足", "—")
                    for k in ("SHORT", "MEDIUM", "LONG")
                ),
                rules_badge,
                action_icon,
            ]

            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, symbol)
                item.setToolTip(value + "\n" + data.get("error", ""))
                if col in range(1, 7):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                elif col in (7, 8, 9):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                clean_sym = symbol.removesuffix(".US")
                if col in (1, 6):
                    number = data.get("change") if col == 1 else m.get("DEV200")
                    if number is not None:
                        item.setForeground(QColor(tokens(self).profit if number >= 0 else tokens(self).loss))
                elif col == 8:
                    if act_rules > 0:
                        item.setForeground(QColor("#16A34A"))
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                        item.setToolTip(f"【{clean_sym}】已启用 {act_rules} 条监控预警规则，点击管理")
                    else:
                        item.setForeground(QColor("#94A3B8"))
                        item.setToolTip(f"【{clean_sym}】暂未启用监控规则，点击新建")
                elif col == 9:
                    item.setToolTip(f"点击为【{clean_sym}】{'管理/编辑' if act_rules > 0 else '新建'}监控预警规则")
                    item.setForeground(QColor("#2563EB"))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

                self.table.setItem(row, col, item)

            # Column 0: clickable hyperlink widget for AI analysis navigation
            stock_cell = StockCellWidget(
                symbol=symbol,
                source_label=source_label,
                row_idx=row,
                on_link_clicked=self.jump_to_stock_analysis,
                on_select_row=self._select_table_row,
            )
            self.table.setCellWidget(row, 0, stock_cell)
            self.table.setRowHeight(row, 56)
            if symbol == self.selected:
                selected_row = row

        self.table.blockSignals(False)
        if rows:
            self.table.selectRow(selected_row)
            self.selected = rows[selected_row]["analysis_symbol"]
        else:
            self.selected = ""
        self.draw_detail()

    def selection_changed(self):
        item = self.table.item(self.table.currentRow(), 0)
        self.selected = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self.draw_detail()

    def _select_table_row(self, row_idx: int, symbol: str):
        self.table.blockSignals(True)
        self.table.selectRow(row_idx)
        self.table.blockSignals(False)
        self.selected = symbol
        self.draw_detail()

    def jump_to_stock_analysis(self, symbol: str):
        """Navigate to AI Stock Analysis page (Page 20) with target symbol loaded."""
        if not symbol or not self.host:
            return
        clean_symbol = symbol.strip().upper()
        # 1. Update selection in current watchlist table first
        target_row = -1
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == clean_symbol:
                target_row = r
                break
        if target_row >= 0:
            self._select_table_row(target_row, clean_symbol)

        # 2. Select stock in AI Stock Analysis Page
        if hasattr(self.host, "stock_analysis_page") and self.host.stock_analysis_page:
            self.host.stock_analysis_page.select_stock(clean_symbol)

        # 3. Navigate sidebar route to Page 20 ("AI 股票分析")
        if hasattr(self.host, "navigation") and self.host.navigation:
            self.host.navigation.select(20)
        elif hasattr(self.host, "pages") and self.host.pages:
            self.host.pages.setCurrentIndex(20)

    def _on_cell_clicked(self, row: int, col: int):
        if col in (8, 9):
            item = self.table.item(row, 0)
            sym = item.data(Qt.ItemDataRole.UserRole) if item else ""
            if sym:
                self._open_rule_dialog(default_symbol=sym)

    def draw_detail(self):
        data = self.market.get(self.selected, {})
        clean_selected = self.selected.removesuffix(".US") if self.selected else ""
        self.title.setText(clean_selected or "选择一个分析标的")
        if hasattr(self, "ai_analyze_btn"):
            if self.selected:
                self.ai_analyze_btn.setText(f"🧠 进入【{clean_selected}】AI 股票研报分析")
                self.ai_analyze_btn.setEnabled(True)
            else:
                self.ai_analyze_btn.setText("🧠 进入 AI 股票研报分析")
                self.ai_analyze_btn.setEnabled(False)
        self.price.setText(fmt(data.get("price")) + "   " + fmt(data.get("change"), suffix="%"))
        if data.get("change") is not None:
            self.price.setStyleSheet(
                "font-size: 18px; font-weight: bold; color:"
                + (tokens(self).profit if data["change"] >= 0 else tokens(self).loss)
            )

        target = next((t for t in self.targets if t["analysis_symbol"] == self.selected), {})
        sources = target.get("sources", [])
        is_manual = (target.get("source_type") == "MANUAL")

        if is_manual:
            source_desc = "手动添加自选股票"
        else:
            source_desc = "；".join(
                dict.fromkeys(
                    s["held_symbol"].removesuffix(".US")
                    + " → "
                    + s.get("analysis_symbol", target.get("analysis_symbol", "")).removesuffix(".US")
                    + (
                        "（" + s["leverage_multiple"] + "×做" + s["direction"] + "）"
                        if s.get("leverage_multiple") and s.get("relation_type") != "DIRECT"
                        else ""
                    )
                    + (" · " + s["benchmark"] if s.get("benchmark") else "")
                    for s in sources
                )
            ) or "持仓标的"

        quote_time = data.get("quote_time", "")
        if quote_time:
            try:
                quote_time = (
                    datetime.fromisoformat(quote_time)
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .strftime("%m-%d %H:%M:%S 北京时间")
                )
            except Exception:
                pass

        self.detail.setText(
            source_desc
            + "\n"
            + data.get("session", "—")
            + " · "
            + data.get("quote_status", "无数据")
            + " · "
            + quote_time
            + ("\n" + data.get("error", "") if data.get("error") else "")
        )

        m = data.get("metrics", {})
        # Update technical indicator sections: display ALL MA lines (5, 10, 20, 50, 60, 120, 200, 250) + dev
        ma_parts = []
        for n in PERIODS:  # (5, 10, 20, 50, 60, 120, 200, 250)
            ma_parts.append(f"MA{n}: {fmt(m.get('MA' + str(n)))}")
        dev200_str = fmt(m.get('DEV200'), suffix='%')
        ma_txt = "   ·   ".join(ma_parts) + f"   ·   距MA200: {dev200_str}"
        self.lbl_ma_summary.setText(ma_txt)

        rsi_val = m.get("RSI14")
        rsi_state = "超买 (>70)" if rsi_val and rsi_val >= 70 else "超卖 (<30)" if rsi_val and rsi_val <= 30 else "常态中性"
        self.lbl_rsi_summary.setText(f"数值: {fmt(rsi_val)}   ({rsi_state})")

        macd_txt = f"DIF: {fmt(m.get('DIF'))}   ·   DEA: {fmt(m.get('DEA'))}   ·   MACD柱: {fmt(m.get('MACD'))}"
        self.lbl_macd_summary.setText(macd_txt)

        boll_txt = f"上轨: {fmt(m.get('BOLL_UP'))}   ·   中轨: {fmt(m.get('BOLL_MID'))}   ·   下轨: {fmt(m.get('BOLL_LOW'))}"
        self.lbl_boll_summary.setText(boll_txt)

        vol_txt = f"5日均量: {fmt(m.get('VMA5'))}   ·   20日均量: {fmt(m.get('VMA20'))}"
        self.lbl_vol_summary.setText(vol_txt)

        ath_txt = f"52周高/低: {fmt(m.get('HIGH52'))} / {fmt(m.get('LOW52'))}   ·   最高: {fmt(m.get('AVAILABLE_HIGH'))} (距最高: {fmt(m.get('DRAWDOWN'), suffix='%')})"
        self.lbl_ath_summary.setText(ath_txt)

        bars = data.get("bars", [])
        self.basis.setText(
            (
                "已完成日K " + str(bars[0].day) + " 至 " + str(bars[-1].day) + " · " + str(len(bars)) + " 根"
                if bars
                else "暂无已完成日K"
            )
            + "\n长桥前复权（含拆股/分红调整）；MA基于已完成日K。52周需252根日K；技术分析仅作辅助参考。"
        )

        self.draw_chart()

    def draw_chart(self, *_):
        if not hasattr(self, "chart"):
            return
        self.chart.data = self.market.get(self.selected)
        self.chart.count = self.range.currentData()
        self.chart.panel = self.panel.currentText()
        self.chart.mas = {n for n, c in self.ma_checks.items() if c.isChecked()}
        self.chart.show_boll = self.boll_check.isChecked()
        self.chart.update()

    def _on_boll_toggled(self, checked: bool):
        self.chart.show_boll = checked
        self.chart.update()

    # --- TAB 2: RULES TABLE LOADING & ACTIONS ---
    def _load_rules_table(self):
        if not self.store or not self.scope_key:
            return
        sym_filter = self.rule_sym_filter.currentData()
        state_filter = self.rule_state_filter.currentIndex()
        search_txt = self.rule_search.text().strip().upper()

        all_rules = self.store.get_rules(scope_key=self.scope_key)
        filtered = []
        for r in all_rules:
            if sym_filter and r.analysis_symbol != sym_filter:
                continue
            if state_filter == 1 and not r.enabled:
                continue
            if state_filter == 2 and r.enabled:
                continue
            if search_txt and (search_txt not in r.name.upper() and search_txt not in r.note.upper()):
                continue
            filtered.append(r)

        self.rules = filtered
        self.rules_table.blockSignals(True)
        self.rules_table.setRowCount(len(filtered))

        for row, rule in enumerate(filtered):
            # 0: Name
            item0 = QTableWidgetItem(rule.name)
            item0.setData(Qt.ItemDataRole.UserRole, rule.rule_id)
            self.rules_table.setItem(row, 0, item0)

            # 1: Symbol
            sym_desc = rule.analysis_symbol.removesuffix(".US")
            if rule.monitored_symbol and rule.monitored_symbol != rule.analysis_symbol:
                sym_desc += f"\n(监控: {rule.monitored_symbol.removesuffix('.US')})"
            self.rules_table.setItem(row, 1, QTableWidgetItem(sym_desc))

            # 2: Type
            type_labels = {
                "PRICE": "价格突破",
                "PRICE_MA_CROSS": "股价/均线",
                "MA_CROSS": "均线交叉",
                "MACD": "MACD",
                "RSI": "RSI",
                "VOLUME": "成交量",
                "WEEK52": "52周高低",
                "ATH_DRAWDOWN": "历史高点回撤",
                "COMPOSITE": "多条件组合",
            }
            self.rules_table.setItem(row, 2, QTableWidgetItem(type_labels.get(rule.rule_type, rule.rule_type)))

            # 3: Condition description
            desc = describe_rule(rule)
            self.rules_table.setItem(row, 3, QTableWidgetItem(desc))

            # 4: Timing & Session
            timing = "盘中实时" if rule.check_mode == "INTRADAY" else "日K收盘确认"
            session = "正常时段" if rule.session_scope == "REGULAR" else "含盘前盘后"
            self.rules_table.setItem(row, 4, QTableWidgetItem(f"{timing}\n{session}"))

            # 5: Repeat mode
            repeat_labels = {
                "REARM_CROSSING": "重新穿越触发",
                "ONCE": "仅触发单次",
                "COOLDOWN": f"冷却间隔({rule.cooldown_seconds}s)",
            }
            self.rules_table.setItem(row, 5, QTableWidgetItem(repeat_labels.get(rule.repeat_mode, rule.repeat_mode)))

            # 6: Trigger stats
            trigger_str = f"触发 {rule.trigger_count} 次"
            if rule.last_trigger_at:
                try:
                    last_t = (
                        datetime.fromisoformat(rule.last_trigger_at)
                        .astimezone(ZoneInfo("Asia/Shanghai"))
                        .strftime("%m-%d %H:%M")
                    )
                    trigger_str += f"\n最近: {last_t}"
                except Exception:
                    pass
            self.rules_table.setItem(row, 6, QTableWidgetItem(trigger_str))

            # 7: Enable Status (Widget with toggle button)
            toggle_btn = QPushButton("已启用" if rule.enabled else "已暂停")
            toggle_btn.setCheckable(True)
            toggle_btn.setChecked(rule.enabled)
            if rule.enabled:
                toggle_btn.setStyleSheet("background-color: #DCFCE7; color: #166534; font-weight: bold;")
            else:
                toggle_btn.setStyleSheet("background-color: #F1F5F9; color: #64748B;")
            toggle_btn.clicked.connect(lambda _, r_id=rule.rule_id, cur_en=rule.enabled: self._toggle_rule_state(r_id, not cur_en))
            self.rules_table.setCellWidget(row, 7, toggle_btn)

            # 8: Operations (Edit, Copy, Delete)
            op_widget = QWidget()
            op_layout = QHBoxLayout(op_widget)
            op_layout.setContentsMargins(2, 2, 2, 2)
            op_layout.setSpacing(4)

            btn_edit = QPushButton("编辑")
            btn_edit.clicked.connect(lambda _, r_id=rule.rule_id: self._edit_rule(r_id))
            op_layout.addWidget(btn_edit)

            btn_copy = QPushButton("复制")
            btn_copy.clicked.connect(lambda _, r_id=rule.rule_id: self._copy_rule(r_id))
            op_layout.addWidget(btn_copy)

            btn_del = QPushButton("删除")
            btn_del.setStyleSheet("color: #DC2626;")
            btn_del.clicked.connect(lambda _, r_id=rule.rule_id: self._delete_rule(r_id))
            op_layout.addWidget(btn_del)

            self.rules_table.setCellWidget(row, 8, op_widget)
            self.rules_table.setRowHeight(row, 50)

        self.rules_table.blockSignals(False)

    def _toggle_rule_state(self, rule_id: str, enabled: bool):
        if not self.store:
            return
        self.store.set_rule_enabled(rule_id, enabled)
        self._load_rules_table()
        self._update_top_stats()
        self.draw_table()

    def _batch_set_rule_state(self, enabled: bool):
        selected_indexes = self.rules_table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "批量操作", "请先在规则表格中选择需要操作的行！")
            return
        rule_ids = []
        for idx in selected_indexes:
            item = self.rules_table.item(idx.row(), 0)
            if item:
                rule_ids.append(item.data(Qt.ItemDataRole.UserRole))
        if rule_ids and self.store:
            self.store.batch_set_enabled(rule_ids, enabled)
            self._load_rules_table()
            self._update_top_stats()
            self.draw_table()

    def _batch_delete_rules(self):
        selected_indexes = self.rules_table.selectionModel().selectedRows()
        if not selected_indexes:
            QMessageBox.information(self, "批量删除", "请先选择需要删除的规则！")
            return
        reply = QMessageBox.question(
            self,
            "确认批量删除",
            f"确定要删除所选的 {len(selected_indexes)} 条监控规则吗？此操作无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and self.store:
            for idx in selected_indexes:
                item = self.rules_table.item(idx.row(), 0)
                if item:
                    self.store.delete_rule(item.data(Qt.ItemDataRole.UserRole))
            self._load_rules_table()
            self._update_top_stats()
            self.draw_table()

    def _on_rule_double_clicked(self, index):
        item = self.rules_table.item(index.row(), 0)
        if item:
            self._edit_rule(item.data(Qt.ItemDataRole.UserRole))

    def _edit_rule(self, rule_id: str):
        if not self.store:
            return
        rule = self.store.get_rule(rule_id)
        if not rule:
            return
        dialog = RuleDrawerDialog(
            parent=self,
            rule=rule,
            available_symbols=self.targets,
            scope_key=self.scope_key,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.update_rule(dialog.result_rule)
            self._load_rules_table()
            self._update_top_stats()
            self.draw_table()

    def _copy_rule(self, rule_id: str):
        if not self.store:
            return
        self.store.duplicate_rule(rule_id)
        self._load_rules_table()
        self._update_top_stats()
        self.draw_table()

    def _delete_rule(self, rule_id: str):
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除该监控规则吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and self.store:
            self.store.delete_rule(rule_id)
            self._load_rules_table()
            self._update_top_stats()
            self.draw_table()

    def _on_toggle_all_rules(self):
        if not self.store or not self.scope_key:
            return
        stats = self.store.stats(self.scope_key)
        all_enabled = stats.get("enabled_rules", 0) > 0
        new_state = not all_enabled
        self.store.batch_set_all_enabled(self.scope_key, new_state)
        self._update_top_stats()
        self._load_rules_table()
        self.draw_table()
        self.refresh_market()

    # --- TAB 3: ALERT HISTORY LOADING & ACTIONS ---
    def _load_history_table(self):
        if not self.store:
            return
        unread_only = self.unread_only_check.isChecked()
        events = self.store.get_alert_events(limit=200, unread_only=unread_only)
        self.alert_events = events

        self.history_table.blockSignals(True)
        self.history_table.setRowCount(len(events))

        for row, evt in enumerate(events):
            # Time
            time_str = evt.detected_at
            try:
                time_str = (
                    datetime.fromisoformat(evt.detected_at)
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .strftime("%m-%d %H:%M:%S")
                )
            except Exception:
                pass
            self.history_table.setItem(row, 0, QTableWidgetItem(time_str))

            # Symbol
            self.history_table.setItem(row, 1, QTableWidgetItem(evt.analysis_symbol.removesuffix(".US")))

            # Rule Name
            self.history_table.setItem(row, 2, QTableWidgetItem(evt.rule_name))

            # Reason
            self.history_table.setItem(row, 3, QTableWidgetItem(evt.trigger_reason))

            # Price & Threshold
            price_val = f"${evt.observed_price}"
            if evt.threshold_value:
                price_val += f" / 阈值: {evt.threshold_value}"
            self.history_table.setItem(row, 4, QTableWidgetItem(price_val))

            # Session
            self.history_table.setItem(row, 5, QTableWidgetItem(evt.quote_session or "盘中"))

            # Status
            is_read = bool(evt.read_at)
            status_item = QTableWidgetItem("已读" if is_read else "未读")
            if not is_read:
                status_item.setForeground(QColor("#2563EB"))
                font = status_item.font()
                font.setBold(True)
                status_item.setFont(font)
            self.history_table.setItem(row, 6, status_item)

            # Action button
            detail_btn = QPushButton("快照详情")
            detail_btn.clicked.connect(lambda _, e=evt: self._show_alert_detail(e))
            self.history_table.setCellWidget(row, 7, detail_btn)
            self.history_table.setRowHeight(row, 44)

        self.history_table.blockSignals(False)

    def _show_alert_detail(self, evt: AlertEvent):
        if not evt.read_at and self.store:
            self.store.mark_events_read([evt.event_id])
            self._load_history_table()
        dialog = AlertDetailDialog(evt, self)
        dialog.exec()

    def _mark_all_alerts_read(self):
        if self.store:
            self.store.mark_events_read()
            self._load_history_table()

    def _clear_alert_history(self):
        reply = QMessageBox.question(
            self,
            "清空历史",
            "确定要清空全部提醒历史记录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes and self.store:
            self.store.clear_alert_events()
            self._load_history_table()
            self._update_top_stats()

    # --- TAB SWITCHING & DIALOG LAUNCHERS ---
    def _on_tab_changed(self, idx: int):
        if idx == 1:
            self._load_rules_table()
        elif idx == 2:
            self._load_history_table()

    def _open_rule_dialog(
        self,
        default_symbol: str = "",
        default_type: str = "PRICE",
        default_expr: Optional[Dict[str, Any]] = None,
    ):
        if not self.store or not self.scope_key:
            QMessageBox.warning(self, "未就绪", "请等待持仓同步完成后再创建监控规则！")
            return
        dialog = RuleDrawerDialog(
            parent=self,
            default_symbol=default_symbol or self.selected,
            default_type=default_type,
            default_expr=default_expr,
            available_symbols=self.targets,
            scope_key=self.scope_key,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_rule:
            self.store.add_rule(dialog.result_rule)
            self._load_rules_table()
            self._update_top_stats()
            self.draw_table()
            QMessageBox.information(
                self,
                "规则已创建",
                f"规则【{dialog.result_rule.name}】已成功保存！\n"
                + ("当前处于【已启用】监控状态。" if dialog.result_rule.enabled else "根据规则默认处于【暂停】状态，可在规则管理中主动开启。"),
            )

    def _on_add_stock(self):
        if not self.store or not self.scope_key:
            return
        dialog = AddStockDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.add_manual_target(self.scope_key, dialog.symbol, dialog.market)
            self.activate(force=True)

    def _on_remove_stock(self):
        if not self.store or not self.scope_key or not self.selected:
            return
        target = next((t for t in self.targets if t["analysis_symbol"] == self.selected), None)
        if not target:
            return
        if target.get("source_type") != "MANUAL":
            QMessageBox.information(self, "无法移除", "该标的来自真实持仓自动同步，不可直接手动移除；清仓后将自动归入“已不再持有”。")
            return
        reply = QMessageBox.question(
            self,
            "确认移除",
            f"确定要从观察列表中移除自选标的【{self.selected}】吗？关联规则将自动停用。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.remove_manual_target(self.scope_key, self.selected, target.get("market", "US"))
            self.activate(force=True)

    def edit_mapping(self):
        target = next((t for t in self.targets if t["analysis_symbol"] == self.selected), None)
        if not target or not target["sources"] or not self.store:
            return
        sources = {s["held_symbol"]: s for s in target["sources"]}
        dialog = QDialog(self)
        dialog.setWindowTitle("观察映射（不修改原始证券）")
        dialog.resize(580, 500)
        form = QFormLayout(dialog)
        held = QComboBox()
        held.addItems(sources)
        form.addRow("持仓证券", held)
        information = label("")
        information.setOpenExternalLinks(True)
        information.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        form.addRow(information)
        own = QCheckBox("改为分析 ETF 自身")
        form.addRow(own)
        analysis = QLineEdit()
        form.addRow("分析代码（例如 AAOI.US）", analysis)
        relation = QComboBox()
        relation.addItems(["DIRECT", "LEVERAGED_LONG", "LEVERAGED_SHORT", "INDEX_PROXY", "UNKNOWN"])
        form.addRow("关系类型", relation)
        multiple = QLineEdit()
        form.addRow("杠杆倍数（绝对值）", multiple)
        source_url = QLineEdit()
        form.addRow("验证来源 URL / 依据", source_url)

        def bind():
            own.setChecked(False)
            s = sources[held.currentText()]
            analysis.setText(s["analysis_symbol"])
            multiple.setText(s["leverage_multiple"])
            relation.setCurrentText(s["relation_type"])
            source_url.setText(s["source_url"])
            information.setText(
                s["product_name"]
                + "\n状态 "
                + s["status"]
                + " · 核实 "
                + s["verified_at"]
                + "\n"
                + s["source_url"]
                + "\n来源账户/人员 ID："
                + ", ".join(
                    f"{x['account_id']}/{x['person_id']}：{x['quantity']}股（{x['holding_status']}）"
                    for x in target["sources"]
                    if x["held_symbol"] == held.currentText()
                )
            )

        held.currentIndexChanged.connect(bind)
        bind()
        own.toggled.connect(lambda checked: analysis.setText(held.currentText()) if checked else None)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            try:
                symbol = normalize(analysis.text())
                n = Decimal(multiple.text()) if multiple.text() else None
                if n is not None and (not n.is_finite() or n <= 0):
                    raise ValueError("杠杆倍数必须为正数")
                kind = "DIRECT" if own.isChecked() else relation.currentText()
                if kind in ("LEVERAGED_LONG", "LEVERAGED_SHORT") and (
                    n is None or not source_url.text().strip()
                ):
                    raise ValueError("杠杆映射请填写倍数与核实依据")
                s = sources[held.currentText()]
                mapping = Mapping(
                    s["held_symbol"],
                    symbol,
                    kind,
                    str(n) if n else "",
                    "空" if kind == "LEVERAGED_SHORT" else "多",
                    source_url.text().strip(),
                    datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    "MANUAL",
                    s["product_name"],
                    s["benchmark"],
                    True,
                )
                namespace = self.namespace
                store = self.store
                self.submit(
                    lambda cancel: store.save_override(namespace, mapping),
                    lambda _: self.activate(force=True),
                )
                dialog.accept()
            except (ValueError, ArithmeticError):
                QMessageBox.warning(
                    dialog, "映射未保存", "请核对证券代码、倍数及来源；杠杆映射必须有核实依据。"
                )

        buttons.accepted.connect(save)
        dialog.exec()
