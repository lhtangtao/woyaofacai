"""Unified Holdings Management Page for Mac Desktop (PySide6).

Parity with Web:
1. Dashboard mode: Top 4 cards, person/broker/stock summary tables, donut asset distribution.
2. Unified Detail mode: Person, broker, and stock viewing dimensions with state preservation.
3. Stock Right Drawer:
   - Header with 6 summary metrics
   - 4 Tabs: 11-column trade history with cost changes, holding analysis, pnl analysis, monthly performance.
"""
from decimal import Decimal
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import exact_text
from portfolio.paths import DATABASE_PATH
from portfolio.services.charts_service import Chart, Series
from portfolio.services.holdings_management_service import HoldingsManagementService
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.finance_table import FinanceModel, FinanceTable
from portfolio.ui.icons import icon
from portfolio.ui.components import MetricCard
from portfolio.ui.theme import numeric_font, tokens


class HoldingsTableModel(FinanceModel):
    """Custom finance table model for holding summaries and details."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns = ()

    def columnCount(self, parent=None):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                return self.columns[section]
            return None
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str(section + 1)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()
        val = row.values[col] if col < len(row.values) else None
        col_name = self.columns[col] if col < len(self.columns) else ""

        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(val, (int, float, Decimal)):
                if "数量" in col_name or "股数" in col_name or "股票" in col_name or "持仓" in col_name and not "市值" in col_name and not "盈亏" in col_name and not "成本" in col_name and not "状态" in col_name:
                    return f"{val:,.0f}" if isinstance(val, (int, Decimal)) and val % 1 == 0 else f"{val:,.2f}"
                if any(w in col_name for w in ("收益率", "比例", "占比", "涨幅")):
                    sign = "+" if val > 0 else ""
                    return f"{sign}{val * 100:.2f}%"
                if any(w in col_name for w in ("市值", "盈亏", "投入", "成本", "价格", "现金", "金额")):
                    sign = "+" if "盈亏" in col_name and val > 0 else ""
                    return f"{sign}${val:,.2f}"
                return f"{val:,.2f}"
            return exact_text(val)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if isinstance(val, (int, float, Decimal)):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.FontRole:
            if isinstance(val, (int, float, Decimal)) or "时间" in col_name or "Ticker" in col_name:
                return numeric_font(12)

        if role == Qt.ItemDataRole.ForegroundRole:
            if val is None:
                return QColor(tokens(self.parent()).subtle)
            if isinstance(val, (int, float, Decimal)) and any(w in col_name for w in ("盈亏", "收益", "涨幅", "涨跌")):
                return QColor(tokens(self.parent()).profit if val > 0 else tokens(self.parent()).loss if val < 0 else tokens(self.parent()).muted)
            if isinstance(val, str) and ("降低" in val or "买入" in val):
                return QColor(tokens(self.parent()).profit)
            if isinstance(val, str) and ("提高" in val or "卖出" in val):
                return QColor(tokens(self.parent()).loss)

        return None


class StockDrawerWidget(QFrame):
    """Slide-out drawer on the right side of the screen."""
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("filterCard")
        self.setMinimumWidth(480)
        self.setMaximumWidth(700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)

        # 1. Header (Badge, Ticker, Company Name, Close button)
        header = QHBoxLayout()
        self.badge_label = QLabel("NV")
        self.badge_label.setFixedSize(36, 36)
        self.badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_label.setStyleSheet("background: #1E293B; color: #fff; font-weight: bold; border-radius: 6px; font-size: 14px;")
        header.addWidget(self.badge_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.ticker_title = QLabel("NVDA")
        self.ticker_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.name_title = QLabel("NVIDIA Corporation · 美股 标的")
        self.name_title.setObjectName("secondary")
        self.name_title.setStyleSheet("font-size: 11px;")
        title_box.addWidget(self.ticker_title)
        title_box.addWidget(self.name_title)
        header.addLayout(title_box, 1)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("border: none; font-size: 14px; font-weight: bold; border-radius: 14px;")
        close_btn.clicked.connect(self.closed.emit)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # 2. Top Summary Metrics (6 Items)
        metrics_frame = QFrame()
        metrics_frame.setObjectName("statCard")
        metrics_frame.setStyleSheet("background: #fff; border: 1px solid var(--border-color); border-radius: 8px; padding: 6px;")
        m_layout = QHBoxLayout(metrics_frame)
        m_layout.setContentsMargins(6, 6, 6, 6)
        m_layout.setSpacing(8)

        self.m_qty = MetricCard("当前持仓", "当前剩余股数")
        self.m_cost = MetricCard("持仓成本", "净投入每股成本")
        self.m_curr = MetricCard("最新价", "最新有效行情")
        self.m_mv = MetricCard("持仓市值", "当前持有市值")
        self.m_fpnl = MetricCard("浮动盈亏", "持仓未实现损益")
        self.m_cpnl = MetricCard("累计盈亏", "含已清仓全时期收益")
        for m in (self.m_qty, self.m_cost, self.m_curr, self.m_mv, self.m_fpnl, self.m_cpnl):
            m_layout.addWidget(m, 1)
        layout.addWidget(metrics_frame)

        # 3. Tab Bar (4 Tabs)
        self.tabs = QTabBar(self)
        self.tabs.setDrawBase(False)
        self.tabs.addTab("交易记录")
        self.tabs.addTab("持仓分析")
        self.tabs.addTab("收益分析")
        self.tabs.addTab("月度表现")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # 4. Tab Stack
        self.stack = QStackedWidget(self)

        # Tab 1: 交易记录 (11 Columns)
        tab_trades = QWidget()
        tt_layout = QVBoxLayout(tab_trades)
        tt_layout.setContentsMargins(0, 4, 0, 0)
        tt_layout.setSpacing(6)

        t_bar = QHBoxLayout()
        self.trade_info_label = QLabel("共 0 笔成交记录")
        self.trade_info_label.setObjectName("secondary")
        t_bar.addWidget(self.trade_info_label, 1)

        self.export_csv_btn = QPushButton("导出 CSV…")
        self.export_csv_btn.setIcon(icon("download", self))
        self.export_csv_btn.clicked.connect(self._export_trades)
        t_bar.addWidget(self.export_csv_btn)
        tt_layout.addLayout(t_bar)

        self.trade_table = FinanceTable(freeze=False)
        self.trade_model = HoldingsTableModel(self.trade_table)
        self.trade_model.columns = (
            "交易时间", "归属人员", "券商渠道", "交易方向", "成交数量",
            "当前价格", "成交价格", "成交金额", "手续费", "成交后持仓", "成本变化"
        )
        self.trade_table.setModel(self.trade_model)
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.trade_table.horizontalHeader().sectionClicked.connect(self._on_trade_header_clicked)
        tt_layout.addWidget(self.trade_table, 1)
        self.stack.addWidget(tab_trades)

        # Tab 2: 持仓分析
        tab_holdings = QWidget()
        th_layout = QVBoxLayout(tab_holdings)
        th_layout.setContentsMargins(0, 4, 0, 0)
        th_layout.addWidget(QLabel("持仓数量与成本价变化走势"))
        self.holding_canvas = ChartCanvas()
        self.holding_canvas.setMinimumHeight(240)
        th_layout.addWidget(self.holding_canvas, 1)
        self.stack.addWidget(tab_holdings)

        # Tab 3: 收益分析
        tab_pnl = QWidget()
        tp_layout = QVBoxLayout(tab_pnl)
        tp_layout.setContentsMargins(0, 4, 0, 0)
        tp_layout.addWidget(QLabel("买卖点与收益走势分析"))
        self.pnl_canvas = ChartCanvas()
        self.pnl_canvas.setMinimumHeight(240)
        tp_layout.addWidget(self.pnl_canvas, 1)
        self.stack.addWidget(tab_pnl)

        # Tab 4: 月度表现
        tab_monthly = QWidget()
        tm_layout = QVBoxLayout(tab_monthly)
        tm_layout.setContentsMargins(0, 4, 0, 0)
        tm_layout.addWidget(QLabel("按月展示持仓市值、月度盈亏和收益率"))
        self.monthly_table = FinanceTable(freeze=False)
        self.monthly_model = HoldingsTableModel(self.monthly_table)
        self.monthly_model.columns = ("月份", "月末市值(USD)", "月度盈亏(USD)", "收益率")
        self.monthly_table.setModel(self.monthly_model)
        self.monthly_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        tm_layout.addWidget(self.monthly_table, 1)
        self.stack.addWidget(tab_monthly)

        layout.addWidget(self.stack, 1)

        self.current_ticker = None
        self.raw_trades_data = []
        self.sort_asc = False

    def _on_tab_changed(self, idx):
        self.stack.setCurrentIndex(idx)

    def _on_trade_header_clicked(self, col):
        if col == 0:  # 交易时间
            self.sort_asc = not self.sort_asc
            self._render_trades_table()

    def set_data(self, drawer_data: dict[str, Any]):
        summary = drawer_data.get("summary", {})
        ticker = summary.get("ticker", "NVDA")
        self.current_ticker = ticker
        self.badge_label.setText(ticker[:2])
        self.ticker_title.setText(ticker)
        self.name_title.setText(f"{summary.get('company_name', ticker)} · {' '.join(summary.get('tags', []))}")

        # Metrics
        self.m_qty.set_value(f"{summary.get('current_quantity') or 0:,.0f} 股")
        self.m_cost.set_value(f"${summary.get('cost_price') or 0:,.2f}" if summary.get("cost_price") else "—")
        self.m_curr.set_value(f"${summary.get('current_price') or 0:,.2f}" if summary.get("current_price") else "—")
        self.m_mv.set_value(f"${summary.get('market_value') or 0:,.2f}")
        fpnl = summary.get("floating_pnl")
        self.m_fpnl.set_value(f"${fpnl:,.2f}" if fpnl is not None else "—")
        cpnl = summary.get("cumulative_pnl")
        self.m_cpnl.set_value(f"${cpnl:,.2f}" if cpnl is not None else "—")

        # Tab 1: 11-column trades
        self.raw_trades_data = drawer_data.get("trades", [])
        self._render_trades_table()

        # Tab 2: Holding analysis chart
        ha = drawer_data.get("holding_analysis", {})
        dates = ha.get("dates", [])
        qtys = ha.get("quantities", [])
        costs = ha.get("costs", [])
        if dates:
            pts_qty = tuple((d, Decimal(str(q))) for d, q in zip(dates, qtys))
            pts_cost = tuple((d, Decimal(str(c))) for d, c in zip(dates, costs))
            chart_ha = Chart(91, f"{ticker} · 持仓与成本轨迹", "line", (
                Series("持仓数量", pts_qty),
                Series("持仓成本价", pts_cost),
            ), "持仓股数与每股净投入成本随成交的历史变化轨迹")
            self.holding_canvas.chart = chart_ha
            self.holding_canvas.update()

        # Tab 3: PnL analysis chart
        pa = drawer_data.get("pnl_analysis", {})
        markers = pa.get("markers", [])
        if markers:
            pts_m = tuple((m["date"], Decimal(str(m["price"] or 0))) for m in markers)
            chart_pa = Chart(92, f"{ticker} · 买卖点走势", "line", (
                Series("成交点", pts_m),
            ), "历史买入与卖出价格分布")
            self.pnl_canvas.chart = chart_pa
            self.pnl_canvas.update()

        # Tab 4: Monthly performance
        monthly_recs = drawer_data.get("monthly_performance", [])
        m_rows = []
        for mr in monthly_recs:
            m_rows.append(ReportRow((
                mr.get("month"),
                Decimal(str(mr.get("market_value") or 0)),
                Decimal(str(mr.get("monthly_pnl") or 0)),
                Decimal(str(mr.get("return_rate") or 0)) if mr.get("return_rate") is not None else None,
            ), {}))
        self.monthly_model.replace(m_rows)

    def _render_trades_table(self):
        sorted_trades = sorted(
            self.raw_trades_data,
            key=lambda x: str(x.get("trade_time") or ""),
            reverse=not self.sort_asc,
        )
        self.trade_info_label.setText(f"共 {len(sorted_trades)} 笔成交记录（点击表头切换时间排序）")
        rows = []
        for t in sorted_trades:
            rows.append(ReportRow((
                t.get("trade_time"),
                t.get("person_name"),
                t.get("channel"),
                t.get("side"),
                Decimal(str(t.get("quantity") or 0)),
                Decimal(str(t.get("current_price"))) if t.get("current_price") is not None else None,
                Decimal(str(t.get("price") or 0)),
                Decimal(str(t.get("amount") or 0)),
                Decimal(str(t.get("fee") or 0)),
                Decimal(str(t.get("post_quantity") or 0)) if t.get("post_quantity") is not None else None,
                t.get("cost_change"),
            ), {}))
        self.trade_model.replace(rows)

    def _export_trades(self):
        if not self.raw_trades_data:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出标的交易记录", f"{self.current_ticker}_交易记录.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        headers = self.trade_model.columns
        lines = [",".join(headers)]
        for r in self.trade_model.rows:
            lines.append(",".join(f'"{v}"' if v is not None else '""' for v in r.values))
        Path(path).write_text("\ufeff" + "\n".join(lines), encoding="utf-8")


class HoldingsPage(QWidget):
    drill = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.analytics = None
        self.dimension = "person"
        self.view_mode = "dashboard"

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 12, 16, 16)
        root_layout.setSpacing(10)

        # 1. Top Control Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        # Mode buttons (Dashboard vs Detail)
        self.mode_dash_btn = QPushButton("仪表板总览")
        self.mode_dash_btn.setCheckable(True)
        self.mode_dash_btn.setChecked(True)
        self.mode_dash_btn.clicked.connect(lambda: self.switch_view_mode("dashboard"))
        top_bar.addWidget(self.mode_dash_btn)

        self.mode_detail_btn = QPushButton("持仓明细页")
        self.mode_detail_btn.setCheckable(True)
        self.mode_detail_btn.clicked.connect(lambda: self.switch_view_mode("detail"))
        top_bar.addWidget(self.mode_detail_btn)

        # Dimension selector (Person, Broker, Stock)
        dim_lbl = QLabel("维度：")
        top_bar.addWidget(dim_lbl)
        self.dim_combo = QComboBox()
        self.dim_combo.addItem("按人员查看", "person")
        self.dim_combo.addItem("按券商查看", "broker")
        self.dim_combo.addItem("按股票查看", "stock")
        self.dim_combo.currentIndexChanged.connect(self._on_dim_changed)
        top_bar.addWidget(self.dim_combo)

        # Status filter
        status_lbl = QLabel("状态：")
        top_bar.addWidget(status_lbl)
        self.status_combo = QComboBox()
        self.status_combo.addItem("全部状态", "全部")
        self.status_combo.addItem("当前持仓 (持有中)", "持有中")
        self.status_combo.addItem("已清仓记录", "已清仓")
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.status_combo)

        # Broker filter
        self.broker_combo = QComboBox()
        self.broker_combo.addItem("全部渠道", "")
        self.broker_combo.addItem("长桥", "长桥")
        self.broker_combo.addItem("BBAE", "BBAE")
        self.broker_combo.addItem("第一证券", "第一证券")
        self.broker_combo.addItem("嘉信", "嘉信")
        self.broker_combo.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.broker_combo)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索人员、券商或股票代码…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.search_input)

        top_bar.addStretch()

        self.export_btn = QPushButton("导出明细…")
        self.export_btn.setIcon(icon("download", self))
        self.export_btn.clicked.connect(self._export_detail)
        top_bar.addWidget(self.export_btn)

        root_layout.addLayout(top_bar)

        # 2. Main Stack (Dashboard vs Detail)
        self.stack = QStackedWidget(self)

        # --- Stack Page 0: Dashboard (仪表板总览) ---
        dash_scroll = QScrollArea()
        dash_scroll.setWidgetResizable(True)
        dash_scroll.setFrameShape(QFrame.Shape.NoFrame)

        dash_widget = QWidget()
        dash_layout = QVBoxLayout(dash_widget)
        dash_layout.setContentsMargins(0, 4, 0, 8)
        dash_layout.setSpacing(14)

        # Top 4 Cards
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(12)
        self.card_mv = MetricCard("持仓总市值 (USD)", "当前持仓标的市场价值总和")
        self.card_count = MetricCard("持仓股票数量", "当前持有中股票数与已清仓标的数")
        self.card_pnl = MetricCard("累计盈亏 (含已清仓)", "持仓浮动盈亏与历史已清仓收益合计")
        self.card_invest = MetricCard("总投入 (USD)", "累计净投入金额与已实现/浮动构成")
        for c in (self.card_mv, self.card_count, self.card_pnl, self.card_invest):
            cards_layout.addWidget(c, 1)
        dash_layout.addLayout(cards_layout)

        # Lower grid: Left tables + Right donut
        grid_layout = QHBoxLayout()
        grid_layout.setSpacing(14)

        # Left column: Person, Broker, Stock tables
        left_box = QVBoxLayout()
        left_box.setSpacing(12)

        # 人员持仓汇总
        p_card = QFrame()
        p_card.setObjectName("filterCard")
        p_layout = QVBoxLayout(p_card)
        p_layout.setContentsMargins(12, 10, 12, 10)
        p_lbl = QLabel("人员持仓明细（点击人员查看专属持仓）")
        p_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        p_layout.addWidget(p_lbl)
        self.p_table = FinanceTable(freeze=False)
        self.p_model = HoldingsTableModel(self.p_table)
        self.p_model.columns = ("人员", "持仓市值", "累计盈亏", "收益率", "持仓股票", "当前持仓", "已清仓")
        self.p_table.setModel(self.p_model)
        self.p_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.p_table.clicked.connect(self._on_person_table_clicked)
        p_layout.addWidget(self.p_table)
        left_box.addWidget(p_card)

        # Broker & Stock in horizontal row
        mid_row = QHBoxLayout()
        mid_row.setSpacing(12)

        # 券商持仓汇总
        b_card = QFrame()
        b_card.setObjectName("filterCard")
        b_layout = QVBoxLayout(b_card)
        b_layout.setContentsMargins(10, 8, 10, 8)
        b_lbl = QLabel("券商持仓汇总")
        b_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        b_layout.addWidget(b_lbl)
        self.b_table = FinanceTable(freeze=False)
        self.b_model = HoldingsTableModel(self.b_table)
        self.b_model.columns = ("券商", "持仓市值", "累计盈亏", "收益率")
        self.b_table.setModel(self.b_model)
        self.b_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.b_table.clicked.connect(self._on_broker_table_clicked)
        b_layout.addWidget(self.b_table)
        mid_row.addWidget(b_card, 1)

        # 股票持仓汇总
        s_card = QFrame()
        s_card.setObjectName("filterCard")
        s_layout = QVBoxLayout(s_card)
        s_layout.setContentsMargins(10, 8, 10, 8)
        s_lbl = QLabel("股票持仓汇总（点击展开抽屉）")
        s_lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        s_layout.addWidget(s_lbl)
        self.s_table = FinanceTable(freeze=False)
        self.s_model = HoldingsTableModel(self.s_table)
        self.s_model.columns = ("Ticker", "持仓市值", "累计盈亏", "收益率")
        self.s_table.setModel(self.s_model)
        self.s_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.s_table.clicked.connect(self._on_stock_table_clicked)
        s_layout.addWidget(self.s_table)
        mid_row.addWidget(s_card, 1)

        left_box.addLayout(mid_row)
        grid_layout.addLayout(left_box, 6)

        # Right column: Asset Distribution Donut Chart
        right_card = QFrame()
        right_card.setObjectName("filterCard")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(12, 10, 12, 10)
        right_lbl = QLabel("资产分布（按人员 / 券商 / 股票切换）")
        right_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        right_layout.addWidget(right_lbl)

        self.dist_tabs = QTabBar()
        self.dist_tabs.addTab("按人员")
        self.dist_tabs.addTab("按券商")
        self.dist_tabs.addTab("按股票")
        self.dist_tabs.currentChanged.connect(self._on_dist_tab_changed)
        right_layout.addWidget(self.dist_tabs)

        self.dist_canvas = ChartCanvas()
        self.dist_canvas.setMinimumHeight(280)
        right_layout.addWidget(self.dist_canvas, 1)
        grid_layout.addWidget(right_card, 4)

        dash_layout.addLayout(grid_layout)
        dash_scroll.setWidget(dash_widget)
        self.stack.addWidget(dash_scroll)

        # --- Stack Page 1: Unified Detail + Right Drawer ---
        detail_container = QWidget()
        dc_layout = QVBoxLayout(detail_container)
        dc_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        # Left: Unified Detail Table
        left_table_widget = QWidget()
        lt_layout = QVBoxLayout(left_table_widget)
        lt_layout.setContentsMargins(0, 0, 0, 0)
        lt_layout.setSpacing(6)

        self.detail_info = QLabel("统一持仓明细 · 点击任意标的行在右侧抽屉查看交易流水与收益分析")
        self.detail_info.setObjectName("secondary")
        lt_layout.addWidget(self.detail_info)

        self.detail_table = FinanceTable(freeze=False)
        self.detail_model = HoldingsTableModel(self.detail_table)
        self.detail_model.columns = (
            "归属人员", "券商渠道", "Ticker", "持仓状态", "持仓数量",
            "持仓成本价", "当前最新价", "持仓市值", "浮动盈亏", "累计盈亏", "收益率", "持仓占比"
        )
        self.detail_table.setModel(self.detail_model)
        self.detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.detail_table.clicked.connect(self._on_detail_row_clicked)
        lt_layout.addWidget(self.detail_table, 1)

        self.splitter.addWidget(left_table_widget)

        # Right: Stock Drawer
        self.drawer = StockDrawerWidget(self)
        self.drawer.closed.connect(self._close_drawer)
        self.drawer.hide()
        self.splitter.addWidget(self.drawer)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        dc_layout.addWidget(self.splitter, 1)
        self.stack.addWidget(detail_container)

        root_layout.addWidget(self.stack, 1)

        # Internal state
        self._cached_dash_data = None
        self._selected_person_filter = None
        self._selected_broker_filter = None
        self._selected_ticker_filter = None

    def switch_view_mode(self, mode: str):
        self.view_mode = mode
        self.mode_dash_btn.setChecked(mode == "dashboard")
        self.mode_detail_btn.setChecked(mode == "detail")
        self.stack.setCurrentIndex(0 if mode == "dashboard" else 1)
        self.export_btn.setVisible(mode == "detail")
        if mode == "detail":
            self.refresh_detail()

    def set_view(self, mode: str, dimension: str = "person", status: str = "全部", person_id: int | None = None, broker: str | None = None, ticker: str | None = None):
        self.dimension = dimension
        idx = self.dim_combo.findData(dimension)
        if idx >= 0:
            self.dim_combo.setCurrentIndex(idx)
        s_idx = self.status_combo.findData(status)
        if s_idx >= 0:
            self.status_combo.setCurrentIndex(s_idx)
        if broker:
            b_idx = self.broker_combo.findText(broker)
            if b_idx >= 0:
                self.broker_combo.setCurrentIndex(b_idx)
        if ticker:
            self.search_input.setText(ticker)
        self._selected_person_filter = person_id
        self.switch_view_mode(mode)

    def _on_dim_changed(self):
        self.dimension = self.dim_combo.currentData() or "person"
        self.refresh_detail()

    def _on_filter_changed(self):
        self.refresh_detail()

    def _on_dist_tab_changed(self, idx):
        if not self._cached_dash_data:
            return
        keys = ["by_person", "by_broker", "by_stock"]
        k = keys[idx % 3]
        dist = self._cached_dash_data.get("distribution", {}).get(k, [])
        pts = tuple((d["name"], Decimal(str(d["value"]))) for d in dist)
        chart = Chart(88, "资产分布占比", "pie", (Series("资产市值", pts),), "持仓市值分布")
        self.dist_canvas.chart = chart
        self.dist_canvas.update()

    def set_analytics(self, analytics):
        self.analytics = analytics
        self.refresh_dashboard()
        if self.view_mode == "detail":
            self.refresh_detail()

    def refresh_dashboard(self):
        if not self.analytics:
            return
        dash = HoldingsManagementService.get_dashboard_data(self.analytics)
        self._cached_dash_data = dash

        # Cards
        cards = dash.get("cards", {})
        mv = cards.get("total_market_value") or 0
        fpnl = cards.get("floating_pnl") or 0
        frate = cards.get("floating_pnl_rate") or 0
        self.card_mv.set_value(f"${mv:,.2f} ({'+' if frate >= 0 else ''}{frate*100:.2f}%)")

        tot_s = cards.get("total_stock_count") or 0
        op_s = cards.get("open_stock_count") or 0
        cl_s = cards.get("closed_stock_count") or 0
        self.card_count.set_value(f"{tot_s} (当前 {op_s} | 清仓 {cl_s})")

        cpnl = cards.get("cumulative_pnl") or 0
        crate = cards.get("cumulative_pnl_rate") or 0
        self.card_pnl.set_value(f"${cpnl:,.2f} ({'+' if crate >= 0 else ''}{crate*100:.2f}%)")

        inv = cards.get("total_invested") or 0
        rpnl = cards.get("realized_pnl") or 0
        self.card_invest.set_value(f"${inv:,.2f} (已实现 ${rpnl:,.2f})")

        # Tables
        p_recs = dash.get("person_summary", [])
        p_rows = []
        for pr in p_recs:
            p_rows.append(ReportRow((
                pr.get("name"),
                Decimal(str(pr.get("market_value") or 0)),
                Decimal(str(pr.get("total_pnl") or 0)),
                Decimal(str(pr.get("pnl_rate") or 0)) if pr.get("pnl_rate") is not None else None,
                Decimal(str(pr.get("total_stocks") or 0)),
                Decimal(str(pr.get("open_stocks") or 0)),
                Decimal(str(pr.get("closed_stocks") or 0)),
            ), {"person_id": pr.get("person_id")}))
        self.p_model.replace(p_rows)

        b_recs = dash.get("broker_summary", [])
        b_rows = []
        for br in b_recs:
            b_rows.append(ReportRow((
                br.get("channel"),
                Decimal(str(br.get("market_value") or 0)),
                Decimal(str(br.get("total_pnl") or 0)),
                Decimal(str(br.get("pnl_rate") or 0)) if br.get("pnl_rate") is not None else None,
            ), {}))
        self.b_model.replace(b_rows)

        s_recs = dash.get("stock_summary", [])
        s_rows = []
        for sr in s_recs[:10]:
            s_rows.append(ReportRow((
                sr.get("ticker"),
                Decimal(str(sr.get("market_value") or 0)),
                Decimal(str(sr.get("total_pnl") or 0)),
                Decimal(str(sr.get("pnl_rate") or 0)) if sr.get("pnl_rate") is not None else None,
            ), {}))
        self.s_model.replace(s_rows)

        # Donut Chart
        self._on_dist_tab_changed(self.dist_tabs.currentIndex())

    def refresh_detail(self):
        if not self.analytics:
            return
        status = self.status_combo.currentData() or "全部"
        broker = self.broker_combo.currentData() or None
        search = self.search_input.text().strip() or None

        rows_data = HoldingsManagementService.get_holdings_detail_rows(
            self.analytics,
            dimension=self.dimension,
            person_id=self._selected_person_filter,
            broker=broker,
            status=status,
            search=search,
        )
        self.detail_info.setText(f"统一持仓明细 · 共 {len(rows_data)} 条记录（点击任意行从右侧抽屉查看）")

        table_rows = []
        for r in rows_data:
            table_rows.append(ReportRow((
                r.get("person_name"),
                r.get("channel"),
                r.get("ticker"),
                r.get("status"),
                Decimal(str(r.get("quantity") or 0)),
                Decimal(str(r.get("cost_price") or 0)) if r.get("cost_price") is not None else None,
                Decimal(str(r.get("current_price") or 0)) if r.get("current_price") is not None else None,
                Decimal(str(r.get("market_value") or 0)),
                Decimal(str(r.get("pnl") or 0)),
                Decimal(str(r.get("pnl") or 0)),
                Decimal(str(r.get("pnl_rate") or 0)) if r.get("pnl_rate") is not None else None,
                Decimal(str(r.get("weight") or 0)) if r.get("weight") is not None else None,
            ), r))
        self.detail_model.replace(table_rows)

    def _on_person_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.p_model.rows[index.row()]
        p_id = row.metadata.get("person_id")
        self._selected_person_filter = p_id
        self.switch_view_mode("detail")

    def _on_broker_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.b_model.rows[index.row()]
        channel = str(row.values[0])
        b_idx = self.broker_combo.findText(channel)
        if b_idx >= 0:
            self.broker_combo.setCurrentIndex(b_idx)
        self.switch_view_mode("detail")

    def _on_stock_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.s_model.rows[index.row()]
        ticker = str(row.values[0])
        self.open_stock_drawer(ticker)

    def _on_detail_row_clicked(self, index):
        if not index.isValid():
            return
        row = self.detail_model.rows[index.row()]
        ticker = str(row.values[2])
        self.open_stock_drawer(ticker)

    def open_stock_drawer(self, ticker: str):
        if not ticker:
            return
        src_path = str(DATABASE_PATH)
        if self.main_window and hasattr(self.main_window, "service") and self.main_window.service:
            src_path = str(self.main_window.service.path)
        drawer_data = HoldingsManagementService.get_stock_drawer_data(
            src_path,
            self.analytics,
            ticker,
            person_id=self._selected_person_filter,
            broker=self.broker_combo.currentData() or None,
        )
        self.drawer.set_data(drawer_data)
        self.drawer.show()
        # Ensure optimal splitter width for drawer
        self.splitter.setSizes([self.width() - 520, 520])

    def _close_drawer(self):
        self.drawer.hide()

    def _export_detail(self):
        if not self.detail_model.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出持仓明细", "持仓明细.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        headers = self.detail_model.columns
        lines = [",".join(headers)]
        for r in self.detail_model.rows:
            lines.append(",".join(f'"{v}"' if v is not None else '""' for v in r.values))
        Path(path).write_text("\ufeff" + "\n".join(lines), encoding="utf-8")
