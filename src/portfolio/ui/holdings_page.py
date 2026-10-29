"""Unified Holdings Management Page for Mac Desktop (PySide6).

Parity with Web:
1. Dashboard mode: Top 4 cards, person filter tabs, dimension quick buttons,
   person/broker/stock summary tables, donut ring asset distribution.
2. Unified Detail mode: Person, broker, and stock viewing dimensions with state preservation.
3. Stock Right Drawer:
   - Header with 6 summary metrics
   - 4 Tabs: 11-column trade history with cost changes, holding analysis, pnl analysis, monthly performance.
"""
from decimal import Decimal
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
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

from portfolio.domain.analytics import Report, ReportRow, ratio
from portfolio.domain.models import exact_text
from portfolio.paths import DATABASE_PATH
from portfolio.services.charts_service import Chart, Series
from portfolio.services.holdings_management_service import HoldingsManagementService
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.finance_table import FinanceModel, FinanceTable
from portfolio.ui.icons import icon
from portfolio.ui.theme import numeric_font, tokens


class ModernMetricCard(QFrame):
    """Modern financial card matching mockup styling."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet("""
            QFrame#card {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #64748B; font-size: 12px; font-weight: 500;")

        self.val_lbl = QLabel("—")
        self.val_lbl.setFont(numeric_font(22, bold=True))
        self.val_lbl.setStyleSheet("color: #0F172A;")

        self.sub_lbl = QLabel("—")
        self.sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.val_lbl)
        layout.addWidget(self.sub_lbl)

    def set_content(self, main_val: str, sub_val: str, sub_color: str | None = None):
        self.val_lbl.setText(main_val)
        self.sub_lbl.setText(sub_val)
        if sub_color:
            self.sub_lbl.setStyleSheet(f"color: {sub_color}; font-size: 11px; font-weight: 600;")
        else:
            self.sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")


class DonutChartWidget(QWidget):
    """Custom hollow donut ring chart with center text and clean legend."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 260)
        self.total_val = 0.0
        self.slices: list[tuple[str, float, float, str]] = []  # (name, val, percent, color)
        self.palette = [
            "#2563EB", "#0D9488", "#D97706", "#8B5CF6", "#0284C7", "#EC4899", "#EA580C", "#94A3B8"
        ]

    def set_data(self, total: float, items: list[dict[str, Any]]):
        self.total_val = float(total or 0)
        self.slices = []
        if self.total_val <= 0 or not items:
            self.update()
            return

        sorted_items = sorted(items, key=lambda x: -float(x.get("value") or 0))
        top_items = sorted_items[:5]
        other_items = sorted_items[5:]

        for idx, it in enumerate(top_items):
            v = float(it.get("value") or 0)
            if v <= 0:
                continue
            pct = (v / self.total_val) * 100
            color = self.palette[idx % len(self.palette)]
            self.slices.append((it.get("name", ""), v, pct, color))

        if other_items:
            other_v = sum((float(it.get("value") or 0) for it in other_items), 0.0)
            if other_v > 0:
                other_pct = (other_v / self.total_val) * 100
                self.slices.append(("其他", other_v, other_pct, "#94A3B8"))

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens(self)

        if not self.slices or self.total_val <= 0:
            painter.setPen(QColor(t.muted))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待计算结果")
            return

        w, h = self.width(), self.height()
        donut_size = min(h - 30, int(w * 0.44))
        donut_size = max(donut_size, 140)
        donut_rect = QRectF(14, (h - donut_size) / 2, donut_size, donut_size)
        cx, cy = donut_rect.center().x(), donut_rect.center().y()
        outer_r = donut_size / 2.0
        inner_r = outer_r * 0.65

        # 1. Ring Slices
        start_angle = 90.0
        for name, v, pct, color in self.slices:
            span_angle = (pct / 100.0) * 360.0
            path = QPainterPath()
            path.arcMoveTo(QRectF(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2), start_angle)
            path.arcTo(QRectF(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2), start_angle, -span_angle)
            path.arcTo(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2), start_angle - span_angle, span_angle)
            path.closeSubpath()
            painter.fillPath(path, QColor(color))
            start_angle -= span_angle

        # 2. Center Text
        painter.setPen(QColor(t.heading))
        painter.setFont(QFont(".AppleSystemUIFont", 14, QFont.Weight.Bold))
        text_mv = f"${self.total_val:,.0f}" if self.total_val >= 10000 else f"${self.total_val:,.2f}"
        painter.drawText(QRectF(cx - inner_r, cy - 14, inner_r * 2, 20), Qt.AlignmentFlag.AlignCenter, text_mv)

        painter.setPen(QColor(t.muted))
        painter.setFont(QFont(".AppleSystemUIFont", 10))
        painter.drawText(QRectF(cx - inner_r, cy + 8, inner_r * 2, 16), Qt.AlignmentFlag.AlignCenter, "USD")

        # 3. Legend on the Right
        legend_x = donut_rect.right() + 18
        legend_w = w - legend_x - 10
        if legend_w > 70:
            item_h = 24
            total_items_h = len(self.slices) * item_h
            start_y = max(12.0, (h - total_items_h) / 2.0)

            for idx, (name, v, pct, color) in enumerate(self.slices):
                row_y = start_y + idx * item_h

                # Color dot
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(color))
                painter.drawEllipse(QRectF(legend_x, row_y + 7, 9, 9))

                # Name
                painter.setPen(QColor(t.heading))
                painter.setFont(QFont(".AppleSystemUIFont", 11))
                elided_name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, int(legend_w * 0.38))
                painter.drawText(QRectF(legend_x + 15, row_y, legend_w * 0.38, item_h),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided_name)

                # Value
                painter.setFont(numeric_font(11))
                val_box = QRectF(legend_x + 15 + legend_w * 0.38, row_y, legend_w * 0.35, item_h)
                painter.drawText(val_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{v:,.2f}")

                # Percent
                pct_box = QRectF(legend_x + legend_w * 0.73, row_y, legend_w * 0.27, item_h)
                painter.setPen(QColor(t.muted))
                painter.drawText(pct_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{pct:.1f}%")


class HoldingsTableModel(FinanceModel):
    """Custom finance table model for holding summaries and details."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns = ()
        self.sort_column = -1
        self.sort_order = Qt.SortOrder.AscendingOrder
        self._raw_rows = []

    def columnCount(self, parent=None):
        return len(self.columns)

    def replace(self, rows):
        self._raw_rows = list(rows)
        if self.sort_column >= 0:
            self._apply_sort()
        else:
            self.beginResetModel()
            self.rows = tuple(self._raw_rows)
            self.endResetModel()

    def toggle_sort(self, column: int):
        if column < 0 or column >= len(self.columns):
            return
        if self.columns[column] == "操作":
            return
        if self.sort_column == column:
            if self.sort_order == Qt.SortOrder.AscendingOrder:
                self.sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.sort_order = Qt.SortOrder.AscendingOrder
        else:
            self.sort_column = column
            self.sort_order = Qt.SortOrder.AscendingOrder
        self._apply_sort()

    def _apply_sort(self):
        if self.sort_column < 0 or not self._raw_rows:
            self.beginResetModel()
            self.rows = tuple(self._raw_rows)
            self.endResetModel()
            return

        col = self.sort_column
        normal_rows = []
        total_rows = []
        for r in self._raw_rows:
            if (len(r.values) > 0 and r.values[0] == "合计") or r.metadata.get("is_total", False):
                total_rows.append(r)
            else:
                normal_rows.append(r)

        def sort_key(row):
            if col >= len(row.values):
                return (0, 0, "")
            val = row.values[col]
            if val is None or val == "—":
                return (0, 0, "")
            if isinstance(val, (int, float, Decimal)):
                return (1, float(val), "")
            return (2, 0, str(val).strip().lower())

        reverse = (self.sort_order == Qt.SortOrder.DescendingOrder)
        normal_rows.sort(key=sort_key, reverse=reverse)
        self.beginResetModel()
        self.rows = tuple(normal_rows + total_rows)
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                col_name = self.columns[section]
                if self.sort_column == section and col_name != "操作":
                    arrow = " ▲" if self.sort_order == Qt.SortOrder.AscendingOrder else " ▼"
                    return f"{col_name}{arrow}"
                return col_name
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
        is_total = (row.values[0] == "合计") or row.metadata.get("is_total", False)

        if role == Qt.ItemDataRole.DisplayRole:
            if col_name == "操作" and not is_total:
                return "🔍 查看明细"
            if isinstance(val, (int, float, Decimal)):
                # 股票只数/数量/买卖次数等离散计数，一律展示为纯整数，不带小数位 (解决如已清仓显示为 35.00 的问题)
                if col_name in ("买入次数", "卖出次数", "已清仓", "当前持仓", "持仓股票", "股票数量", "持股只数") or (
                    ("股票" in col_name or "清仓" in col_name or "次数" in col_name) and not any(k in col_name for k in ("市值", "盈亏", "成本", "收益", "比例", "率", "金", "额"))
                ):
                    return f"{int(round(float(val))):,d}"
                if "数量" in col_name or "股数" in col_name or ("持仓" in col_name and not any(k in col_name for k in ("市值", "盈亏", "成本", "状态"))):
                    return f"{val:,.0f}" if isinstance(val, (int, Decimal)) and val % 1 == 0 else f"{val:,.2f}"
                if col_name == "持仓占比":
                    return f"{val * 100:.2f}%"
                if any(w in col_name for w in ("收益率", "比例", "涨幅")):
                    sign = "+" if val > 0 else ""
                    return f"{sign}{val * 100:.2f}%"
                if any(w in col_name for w in ("市值", "盈亏", "投入", "成本", "价格", "现金", "金额", "总额")):
                    sign = "+" if "盈亏" in col_name and val > 0 else ""
                    return f"{sign}${val:,.2f}"
                return f"{val:,.2f}"
            return exact_text(val)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col_name == "操作":
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if isinstance(val, (int, float, Decimal)):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.FontRole:
            if is_total:
                return numeric_font(12, bold=True)
            if isinstance(val, (int, float, Decimal)) or "时间" in col_name or "Ticker" in col_name or col_name == "操作":
                return numeric_font(12)

        if role == Qt.ItemDataRole.BackgroundRole:
            if is_total:
                return QColor("#F8FAFC")

        if role == Qt.ItemDataRole.ForegroundRole:
            if col_name == "操作" and not is_total:
                return QColor("#2563EB")
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

        # 2. Top 6 Metrics
        metrics_frame = QFrame()
        metrics_frame.setStyleSheet("background: #fff; border: 1px solid var(--border-color); border-radius: 8px; padding: 4px;")
        m_layout = QHBoxLayout(metrics_frame)
        m_layout.setContentsMargins(6, 6, 6, 6)
        m_layout.setSpacing(8)

        self.m_qty = ModernMetricCard("当前持仓")
        self.m_cost = ModernMetricCard("持仓成本")
        self.m_curr = ModernMetricCard("最新价")
        self.m_mv = ModernMetricCard("持仓市值")
        self.m_fpnl = ModernMetricCard("浮动盈亏")
        self.m_cpnl = ModernMetricCard("累计盈亏")
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

        # Tab 1: 交易记录 (11 列字段)
        tab_trades = QWidget()
        tt_layout = QVBoxLayout(tab_trades)
        tt_layout.setContentsMargins(0, 4, 0, 0)
        tt_layout.setSpacing(6)

        t_bar = QHBoxLayout()
        self.trade_count_lbl = QLabel("共 0 笔交易明细")
        self.trade_count_lbl.setObjectName("secondary")
        t_bar.addWidget(self.trade_count_lbl)
        t_bar.addStretch()
        self.export_csv_btn = QPushButton("导出 CSV")
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
        th_layout.addWidget(QLabel("持仓数量与成本价变化趋势"))
        self.holdings_canvas = ChartCanvas()
        self.holdings_canvas.setMinimumHeight(240)
        th_layout.addWidget(self.holdings_canvas, 1)
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
        self.monthly_table.horizontalHeader().sectionClicked.connect(self.monthly_model.toggle_sort)
        tm_layout.addWidget(self.monthly_table, 1)
        self.stack.addWidget(tab_monthly)

        layout.addWidget(self.stack, 1)

        self.current_ticker = None
        self.raw_trades_data = []
        self.sort_asc = False

    def _on_tab_changed(self, idx):
        self.stack.setCurrentIndex(idx)

    def _on_trade_header_clicked(self, col):
        self.trade_model.toggle_sort(col)

    def set_data(self, drawer_data: dict[str, Any]):
        summary = drawer_data.get("summary", {})
        ticker = summary.get("ticker", "NVDA")
        self.current_ticker = ticker
        self.badge_label.setText(ticker[:2])
        self.ticker_title.setText(ticker)
        self.name_title.setText(f"{summary.get('company_name', ticker)} · {' '.join(summary.get('tags', []))}")

        # Metrics
        qty = summary.get("current_quantity") or 0
        self.m_qty.set_content(f"{qty:,.0f} 股", "当前剩余持仓")

        cost = summary.get("cost_price")
        self.m_cost.set_content(f"${cost:,.2f}" if cost else "—", "净投入每股成本")

        curr = summary.get("current_price")
        self.m_curr.set_content(f"${curr:,.2f}" if curr else "—", "最新有效行情")

        mv = summary.get("market_value") or 0
        self.m_mv.set_content(f"${mv:,.2f}", "当前持仓市值")

        fpnl = summary.get("floating_pnl")
        frate = summary.get("floating_pnl_rate")
        fpnl_color = "#16A34A" if (fpnl or 0) > 0 else "#DC2626" if (fpnl or 0) < 0 else "#64748B"
        sub_f = f"{frate*100:+.2f}%" if frate is not None else "—"
        self.m_fpnl.set_content(f"${fpnl:,.2f}" if fpnl is not None else "—", sub_f, fpnl_color)

        cpnl = summary.get("cumulative_pnl")
        crate = summary.get("cumulative_pnl_rate")
        cpnl_color = "#16A34A" if (cpnl or 0) > 0 else "#DC2626" if (cpnl or 0) < 0 else "#64748B"
        sub_c = f"{crate*100:+.2f}%" if crate is not None else "—"
        self.m_cpnl.set_content(f"${cpnl:,.2f}" if cpnl is not None else "—", sub_c, cpnl_color)

        # Tab 1: 11-column trades
        self.raw_trades_data = drawer_data.get("trades", [])
        self._render_trades_table()

        # Tab 2: Holding analysis
        h_data = drawer_data.get("holding_analysis", {})
        h_series = []
        if h_data.get("quantities"):
            pts = tuple((p["date"], Decimal(str(p["quantity"]))) for p in h_data["quantities"])
            h_series.append(Series("持仓股数", pts))
        if h_data.get("costs"):
            pts = tuple((p["date"], Decimal(str(p["cost"]))) for p in h_data["costs"])
            h_series.append(Series("持仓成本价", pts))
        if h_series:
            self.holdings_canvas.chart = Chart(89, "持仓与成本轨迹", "trend", tuple(h_series), "数量与成本历史变化")
            self.holdings_canvas.update()

        # Tab 3: PnL analysis
        pnl_data = drawer_data.get("pnl_analysis", {})
        curve = pnl_data.get("curve", [])
        if curve:
            pts = tuple((p["date"], Decimal(str(p["cumulative_return"]))) for p in curve)
            p_series = [Series("累计收益率", pts)]
            self.pnl_canvas.chart = Chart(90, "收益率走势图", "trend", tuple(p_series), "包含买卖成交点位标记")
            self.pnl_canvas.update()

        # Tab 4: Monthly performance
        m_recs = drawer_data.get("monthly_performance", [])
        m_rows = []
        for mr in m_recs:
            m_rows.append(ReportRow((
                mr.get("month"),
                Decimal(str(mr.get("market_value") or 0)),
                Decimal(str(mr.get("pnl") or 0)),
                Decimal(str(mr.get("pnl_rate") or 0)) if mr.get("pnl_rate") is not None else None,
            ), {}))
        self.monthly_model.replace(m_rows)

    def _render_trades_table(self):
        sorted_trades = sorted(
            self.raw_trades_data,
            key=lambda x: str(x.get("trade_time") or ""),
            reverse=not self.sort_asc,
        )
        self.trade_count_lbl.setText(f"共 {len(sorted_trades)} 笔交易明细 ({'升序' if self.sort_asc else '降序'})")
        rows = []
        for t in sorted_trades:
            rows.append(ReportRow((
                t.get("trade_time"),
                t.get("person_name"),
                t.get("broker_channel"),
                t.get("direction"),
                Decimal(str(t.get("quantity") or 0)),
                Decimal(str(t.get("current_price") or 0)) if t.get("current_price") is not None else None,
                Decimal(str(t.get("price") or 0)),
                Decimal(str(t.get("amount") or 0)),
                Decimal(str(t.get("fee") or 0)),
                Decimal(str(t.get("post_holding") or 0)),
                t.get("cost_change"),
            ), {}))
        self.trade_model.replace(rows)

    def _export_trades(self):
        if not self.raw_trades_data:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出交易记录", f"{self.current_ticker}_交易明细.csv", "CSV 文件 (*.csv)")
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
        self._selected_person_filter = None
        self._cached_dash_data = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 12, 16, 16)
        root_layout.setSpacing(10)

        # 1. Top Control Bar (Person Filter Tabs on Left, Mode Switcher on Right)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)

        # Left: Person Filter Tabs (Horizontal Pills)
        self.person_tab_bar = QTabBar(self)
        self.person_tab_bar.setDrawBase(False)
        self.person_tab_bar.setStyleSheet("""
            QTabBar::tab {
                background: #F1F5F9;
                color: #475569;
                padding: 6px 14px;
                margin-right: 6px;
                border-radius: 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #2563EB;
                color: #FFFFFF;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background: #E2E8F0;
                color: #1E293B;
            }
        """)
        self.person_tab_bar.currentChanged.connect(self._on_person_tab_changed)
        top_bar.addWidget(self.person_tab_bar, 1)

        # Right: Mode Switcher (仪表板总览 vs 持仓明细页)
        mode_box = QFrame()
        mode_box.setStyleSheet("background: #F1F5F9; border-radius: 8px; padding: 2px;")
        mb_layout = QHBoxLayout(mode_box)
        mb_layout.setContentsMargins(2, 2, 2, 2)
        mb_layout.setSpacing(4)

        self.mode_dash_btn = QPushButton("仪表板总览")
        self.mode_dash_btn.setCheckable(True)
        self.mode_dash_btn.setChecked(True)
        self.mode_dash_btn.setStyleSheet("""
            QPushButton { border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 500; color: #64748B; background: transparent; }
            QPushButton:checked { background: #2563EB; color: #FFFFFF; font-weight: bold; }
        """)
        self.mode_dash_btn.clicked.connect(lambda: self.switch_view_mode("dashboard"))
        mb_layout.addWidget(self.mode_dash_btn)

        self.mode_detail_btn = QPushButton("持仓明细页")
        self.mode_detail_btn.setCheckable(True)
        self.mode_detail_btn.setStyleSheet("""
            QPushButton { border: none; padding: 6px 14px; border-radius: 6px; font-size: 12px; font-weight: 500; color: #64748B; background: transparent; }
            QPushButton:checked { background: #2563EB; color: #FFFFFF; font-weight: bold; }
        """)
        self.mode_detail_btn.clicked.connect(lambda: self.switch_view_mode("detail"))
        mb_layout.addWidget(self.mode_detail_btn)

        top_bar.addWidget(mode_box)
        root_layout.addLayout(top_bar)

        # 2. Main Stack (Dashboard vs Detail)
        self.stack = QStackedWidget(self)

        # =====================================================================
        # Stack Page 0: Dashboard (仪表板总览)
        # =====================================================================
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
        self.card_mv = ModernMetricCard("持仓总市值 (USD)")
        self.card_count = ModernMetricCard("持仓股票")
        self.card_pnl = ModernMetricCard("累计盈亏 (含已清仓)")
        self.card_invest = ModernMetricCard("总投入 (USD)")
        for c in (self.card_mv, self.card_count, self.card_pnl, self.card_invest):
            cards_layout.addWidget(c, 1)
        dash_layout.addLayout(cards_layout)

        # =====================================================================
        # 上下两层结构：
        # 上层：人员持仓明细 (Full Width)
        # 下层：券商持仓汇总 + 股票持仓汇总 + 资产分布 (等高三列并排，表头严格水平对齐)
        # =====================================================================

        # 上层：人员持仓明细
        p_card = QFrame()
        p_card.setObjectName("filterCard")
        p_layout = QVBoxLayout(p_card)
        p_layout.setContentsMargins(12, 10, 12, 10)
        p_layout.setSpacing(8)
        p_hdr = QHBoxLayout()
        p_lbl = QLabel("人员持仓明细")
        p_lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #0F172A;")
        p_hdr.addWidget(p_lbl)
        p_hdr.addStretch()
        p_sub = QLabel("点击表头可正倒序切换")
        p_sub.setStyleSheet("font-size: 11px; color: #64748B;")
        p_hdr.addWidget(p_sub)
        p_layout.addLayout(p_hdr)

        self.p_table = FinanceTable(freeze=False)
        self.p_model = HoldingsTableModel(self.p_table)
        self.p_model.columns = ("人员", "持仓市值(USD)", "累计盈亏(USD)", "收益率", "持仓股票", "当前持仓", "已清仓")
        self.p_table.setModel(self.p_model)
        self.p_table.horizontalHeader().setFixedHeight(36)
        self.p_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.p_table.horizontalHeader().setStretchLastSection(True)
        self.p_table.horizontalHeader().setStyleSheet("background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0;")
        self.p_table.setStyleSheet("QHeaderView, QHeaderView::section { background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0; }")
        self.p_table.horizontalHeader().sectionClicked.connect(self.p_model.toggle_sort)
        p_layout.addWidget(self.p_table)
        dash_layout.addWidget(p_card)

        # 下层：券商持仓汇总 + 股票持仓汇总 + 资产分布 (等高并列)
        lower_row = QHBoxLayout()
        lower_row.setSpacing(12)

        # 1. 券商持仓汇总
        b_card = QFrame()
        b_card.setObjectName("filterCard")
        b_card.setMinimumHeight(290)
        b_layout = QVBoxLayout(b_card)
        b_layout.setContentsMargins(12, 10, 12, 10)
        b_layout.setSpacing(8)

        b_hdr_widget = QWidget()
        b_hdr_widget.setFixedHeight(24)
        b_hdr = QHBoxLayout(b_hdr_widget)
        b_hdr.setContentsMargins(0, 0, 0, 0)
        b_lbl = QLabel("券商持仓汇总")
        b_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #0F172A;")
        b_hdr.addWidget(b_lbl)
        b_hdr.addStretch()
        b_layout.addWidget(b_hdr_widget)

        self.b_table = FinanceTable(freeze=False)
        self.b_model = HoldingsTableModel(self.b_table)
        self.b_model.columns = ("券商", "持仓市值(USD)", "累计盈亏", "收益率")
        self.b_table.setModel(self.b_model)
        self.b_table.horizontalHeader().setFixedHeight(36)
        self.b_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.b_table.horizontalHeader().setStretchLastSection(True)
        self.b_table.horizontalHeader().setStyleSheet("background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0;")
        self.b_table.setStyleSheet("QHeaderView, QHeaderView::section { background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0; }")
        self.b_table.horizontalHeader().sectionClicked.connect(self.b_model.toggle_sort)
        b_layout.addWidget(self.b_table)
        lower_row.addWidget(b_card, 1)

        # 2. 股票持仓汇总
        s_card = QFrame()
        s_card.setObjectName("filterCard")
        s_card.setMinimumHeight(290)
        s_layout = QVBoxLayout(s_card)
        s_layout.setContentsMargins(12, 10, 12, 10)
        s_layout.setSpacing(8)

        s_hdr_widget = QWidget()
        s_hdr_widget.setFixedHeight(24)
        s_hdr = QHBoxLayout(s_hdr_widget)
        s_hdr.setContentsMargins(0, 0, 0, 0)
        s_lbl = QLabel("股票持仓汇总")
        s_lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #0F172A;")
        s_hdr.addWidget(s_lbl)
        s_hdr.addStretch()
        s_layout.addWidget(s_hdr_widget)

        self.s_table = FinanceTable(freeze=False)
        self.s_model = HoldingsTableModel(self.s_table)
        self.s_model.columns = ("Ticker", "持仓市值(USD)", "累计盈亏", "收益率")
        self.s_table.setModel(self.s_model)
        self.s_table.horizontalHeader().setFixedHeight(36)
        self.s_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.s_table.horizontalHeader().setStretchLastSection(True)
        self.s_table.horizontalHeader().setStyleSheet("background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0;")
        self.s_table.setStyleSheet("QHeaderView, QHeaderView::section { background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0; }")
        self.s_table.clicked.connect(self._on_stock_table_clicked)
        self.s_table.horizontalHeader().sectionClicked.connect(self.s_model.toggle_sort)
        s_layout.addWidget(self.s_table)
        lower_row.addWidget(s_card, 1)

        # 3. 资产分布 (Donut Ring Chart)
        right_card = QFrame()
        right_card.setObjectName("filterCard")
        right_card.setMinimumHeight(290)
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(12, 10, 12, 10)

        right_hdr = QHBoxLayout()
        right_lbl = QLabel("资产分布")
        right_lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #0F172A;")
        right_hdr.addWidget(right_lbl)
        right_hdr.addStretch()

        self.dist_tabs = QTabBar()
        self.dist_tabs.setDrawBase(False)
        self.dist_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.dist_tabs.setExpanding(False)
        self.dist_tabs.setStyleSheet("""
            QTabBar::tab {
                background: #F1F5F9;
                color: #64748B;
                padding: 4px 12px;
                min-width: 52px;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                margin-left: 3px;
            }
            QTabBar::tab:selected {
                background: #2563EB;
                color: #FFFFFF;
                font-weight: bold;
            }
            QTabBar::tab:hover:!selected {
                background: #E2E8F0;
                color: #1E293B;
            }
        """)
        self.dist_tabs.addTab("按人员")
        self.dist_tabs.addTab("按券商")
        self.dist_tabs.addTab("按股票")
        self.dist_tabs.currentChanged.connect(self._on_dist_tab_changed)
        right_hdr.addWidget(self.dist_tabs)
        right_layout.addLayout(right_hdr)

        dist_sub = QLabel("持仓市值分布占比")
        dist_sub.setStyleSheet("font-size: 11px; color: #64748B; margin-top: -4px;")
        right_layout.addWidget(dist_sub)

        # Custom Donut Chart Ring Widget
        self.dist_donut = DonutChartWidget(self)
        right_layout.addWidget(self.dist_donut, 1)
        lower_row.addWidget(right_card, 1)

        dash_layout.addLayout(lower_row)
        dash_scroll.setWidget(dash_widget)
        self.stack.addWidget(dash_scroll)

        # =====================================================================
        # Stack Page 1: Unified Detail + Right Drawer
        # =====================================================================
        detail_container = QWidget()
        dc_layout = QVBoxLayout(detail_container)
        dc_layout.setContentsMargins(0, 0, 0, 0)
        dc_layout.setSpacing(8)

        # Detail toolbar (Placed exclusively in Detail View!)
        detail_bar = QHBoxLayout()
        detail_bar.setSpacing(10)

        # Status filter (仅持仓与已清仓两种状态，默认持仓)
        status_lbl = QLabel("状态：")
        detail_bar.addWidget(status_lbl)
        self.status_combo = QComboBox()
        self.status_combo.addItem("当前持仓", "持有中")
        self.status_combo.addItem("已清仓", "已清仓")
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        detail_bar.addWidget(self.status_combo)

        # Broker filter
        self.broker_combo = QComboBox()
        self.broker_combo.addItem("全部渠道", "")
        self.broker_combo.addItem("长桥", "长桥")
        self.broker_combo.addItem("BBAE", "BBAE")
        self.broker_combo.addItem("第一证券", "第一证券")
        self.broker_combo.addItem("嘉信", "嘉信")
        self.broker_combo.currentIndexChanged.connect(self._on_filter_changed)
        detail_bar.addWidget(self.broker_combo)

        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索人员、券商或股票代码…")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_filter_changed)
        detail_bar.addWidget(self.search_input)

        detail_bar.addStretch()

        self.export_btn = QPushButton("导出明细…")
        self.export_btn.setIcon(icon("download", self))
        self.export_btn.clicked.connect(self._export_detail)
        detail_bar.addWidget(self.export_btn)

        dc_layout.addLayout(detail_bar)

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
            "归属人员", "券商渠道", "Ticker", "数量", "成本价", "成本总额",
            "当前最新价", "市值", "收益率", "累计盈亏", "买入次数", "卖出次数", "持仓占比"
        )
        self.detail_table.setModel(self.detail_model)
        self.detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.detail_table.horizontalHeader().sectionClicked.connect(self.detail_model.toggle_sort)
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

    def _get_persons(self) -> list[dict[str, Any]]:
        if self.main_window and hasattr(self.main_window, "info") and self.main_window.info:
            persons = getattr(self.main_window.info, "persons", None)
            if persons:
                return persons
        import sqlite3
        try:
            with sqlite3.connect(DATABASE_PATH) as conn:
                rows = conn.execute("SELECT id, name, enabled FROM persons ORDER BY id").fetchall()
                return [{"id": r[0], "name": r[1], "enabled": bool(r[2])} for r in rows]
        except Exception:
            return [{"id": 1, "name": "个人", "enabled": True}]

    def _populate_persons(self):
        persons = self._get_persons()
        self.person_tab_bar.blockSignals(True)
        while self.person_tab_bar.count() > 0:
            self.person_tab_bar.removeTab(0)

        idx = self.person_tab_bar.addTab("全部启用人员")
        self.person_tab_bar.setTabData(idx, None)

        for p in persons:
            if p.get("enabled", True):
                idx = self.person_tab_bar.addTab(p["name"])
                self.person_tab_bar.setTabData(idx, p["id"])

        self.person_tab_bar.blockSignals(False)

    def _on_person_tab_changed(self, idx):
        p_id = self.person_tab_bar.tabData(idx)
        self._selected_person_filter = p_id
        if self.view_mode == "dashboard":
            self.refresh_dashboard()
        else:
            self.refresh_detail()

    def switch_view_mode(self, mode: str):
        self.view_mode = mode
        self.mode_dash_btn.setChecked(mode == "dashboard")
        self.mode_detail_btn.setChecked(mode == "detail")
        self.stack.setCurrentIndex(0 if mode == "dashboard" else 1)
        if mode == "dashboard":
            self.refresh_dashboard()
        else:
            self.refresh_detail()

    def set_view(self, mode: str, dimension: str = "person", status: str = "持有中", person_id: int | None = None, broker: str | None = None, ticker: str | None = None):
        self.dimension = dimension
        s_idx = self.status_combo.findData(status)
        if s_idx >= 0:
            self.status_combo.setCurrentIndex(s_idx)
        else:
            self.status_combo.setCurrentIndex(0)
        if broker:
            b_idx = self.broker_combo.findText(broker)
            if b_idx >= 0:
                self.broker_combo.setCurrentIndex(b_idx)
        if ticker:
            self.search_input.setText(ticker)
        if person_id is not None:
            self._selected_person_filter = person_id
            for i in range(self.person_tab_bar.count()):
                if self.person_tab_bar.tabData(i) == person_id:
                    self.person_tab_bar.blockSignals(True)
                    self.person_tab_bar.setCurrentIndex(i)
                    self.person_tab_bar.blockSignals(False)
                    break
        self.switch_view_mode(mode)

    def _on_filter_changed(self):
        self.refresh_detail()

    def _on_dist_tab_changed(self, idx):
        if not hasattr(self, "_cached_dash_data") or not self._cached_dash_data:
            return
        keys = ("by_person", "by_broker", "by_stock")
        k = keys[idx] if idx < len(keys) else "by_person"
        dist = self._cached_dash_data.get("distribution", {}).get(k, [])
        cards = self._cached_dash_data.get("cards", {})
        total_mv = float(cards.get("total_market_value") or 0)
        self.dist_donut.set_data(total_mv, dist)

    def set_analytics(self, analytics):
        self.analytics = analytics
        if self.person_tab_bar.count() <= 1:
            self._populate_persons()
        if self.view_mode == "dashboard":
            self.refresh_dashboard()
        else:
            self.refresh_detail()

    def refresh_dashboard(self):
        if not self.analytics:
            return
        dash = HoldingsManagementService.get_dashboard_data(self.analytics, person_id=self._selected_person_filter)
        self._cached_dash_data = dash

        # 1. Cards
        cards = dash.get("cards", {})
        mv = float(cards.get("total_market_value") or 0)
        fpnl = float(cards.get("floating_pnl") or 0)
        frate = float(cards.get("floating_pnl_rate") or 0)
        fpnl_color = "#16A34A" if fpnl > 0 else "#DC2626" if fpnl < 0 else "#64748B"
        sub_mv = f"{fpnl:+,.2f} ({frate*100:+.2f}%)" if fpnl != 0 else "—"
        self.card_mv.set_content(f"${mv:,.2f}", sub_mv, fpnl_color)

        tot_s = cards.get("total_stock_count") or 0
        op_s = cards.get("open_stock_count") or 0
        cl_s = cards.get("closed_stock_count") or 0
        self.card_count.set_content(f"{tot_s}", f"当前持仓 {op_s} | 已清仓 {cl_s}")

        cpnl = float(cards.get("cumulative_pnl") or 0)
        crate = float(cards.get("cumulative_pnl_rate") or 0)
        cpnl_color = "#16A34A" if cpnl > 0 else "#DC2626" if cpnl < 0 else "#64748B"
        self.card_pnl.set_content(f"${cpnl:+,.2f}", f"收益率 {crate*100:+.2f}%", cpnl_color)

        inv = float(cards.get("total_invested") or 0)
        rpnl = float(cards.get("realized_pnl") or 0)
        self.card_invest.set_content(f"${inv:,.2f}", f"已实现 ${rpnl:,.2f} | 浮动 ${fpnl:,.2f}")

        # 2. Tables
        # 人员持仓明细
        p_recs = dash.get("person_summary", [])
        p_rows = []
        tot_p_mv = Decimal(0)
        tot_p_pnl = Decimal(0)
        tot_p_stocks = 0
        tot_p_open = 0
        tot_p_closed = 0
        for pr in p_recs:
            p_mv = Decimal(str(pr.get("market_value") or 0))
            p_pnl = Decimal(str(pr.get("total_pnl") or 0))
            tot_p_mv += p_mv
            tot_p_pnl += p_pnl
            ts = int(pr.get("total_stocks") or 0)
            os = int(pr.get("open_stocks") or 0)
            cs = int(pr.get("closed_stocks") or 0)
            tot_p_stocks += ts
            tot_p_open += os
            tot_p_closed += cs
            p_rows.append(ReportRow((
                pr.get("name"),
                p_mv,
                p_pnl,
                Decimal(str(pr.get("pnl_rate") or 0)) if pr.get("pnl_rate") is not None else None,
                ts,
                os,
                cs,
            ), {"person_id": pr.get("person_id")}))

        if p_rows:
            p_base = (tot_p_mv - tot_p_pnl)
            p_tot_rate = ratio(tot_p_pnl, p_base) if p_base > 0 else None
            p_rows.append(ReportRow((
                "合计",
                tot_p_mv,
                tot_p_pnl,
                p_tot_rate,
                tot_p_stocks,
                tot_p_open,
                tot_p_closed,
            ), {"is_total": True}))
        self.p_model.replace(p_rows)

        # 券商持仓汇总
        b_recs = dash.get("broker_summary", [])
        b_rows = []
        tot_b_mv = Decimal(0)
        tot_b_pnl = Decimal(0)
        for br in b_recs:
            b_mv = Decimal(str(br.get("market_value") or 0))
            b_pnl = Decimal(str(br.get("total_pnl") or 0))
            tot_b_mv += b_mv
            tot_b_pnl += b_pnl
            b_rows.append(ReportRow((
                br.get("channel"),
                b_mv,
                b_pnl,
                Decimal(str(br.get("pnl_rate") or 0)) if br.get("pnl_rate") is not None else None,
            ), {}))
        if b_rows:
            b_base = (tot_b_mv - tot_b_pnl)
            b_tot_rate = ratio(tot_b_pnl, b_base) if b_base > 0 else None
            b_rows.append(ReportRow((
                "合计",
                tot_b_mv,
                tot_b_pnl,
                b_tot_rate,
            ), {"is_total": True}))
        self.b_model.replace(b_rows)

        # 股票持仓汇总
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

        # 3. Donut Ring Chart
        self._on_dist_tab_changed(self.dist_tabs.currentIndex())

    def refresh_detail(self):
        if not self.analytics:
            return
        status = self.status_combo.currentData() or "持有中"
        status_filter = status
        broker_filter = self.broker_combo.currentData() or None
        search_filter = self.search_input.text().strip() or None

        raw_rows = HoldingsManagementService.get_holdings_detail_rows(
            self.analytics,
            dimension=self.dimension,
            person_id=self._selected_person_filter,
            broker=broker_filter,
            status=status_filter,
            search=search_filter,
        )

        rows = []
        for item in raw_rows:
            qty = Decimal(str(item.get("quantity") or 0)) if item.get("quantity") is not None else None
            cost_p = Decimal(str(item.get("cost_price") or 0)) if item.get("cost_price") is not None else None
            cost_amt = Decimal(str(item.get("cost_amount") or 0)) if item.get("cost_amount") is not None else None
            curr_p = Decimal(str(item.get("current_price") or 0)) if item.get("current_price") is not None else None
            mv = Decimal(str(item.get("market_value") or 0)) if item.get("market_value") is not None else None
            pnl_r = Decimal(str(item.get("pnl_rate") or 0)) if item.get("pnl_rate") is not None else None
            tot_pnl = Decimal(str(item.get("total_pnl") or 0)) if item.get("total_pnl") is not None else None
            buy_cnt = item.get("buy_count")
            sell_cnt = item.get("sell_count")
            weight = Decimal(str(item.get("weight") or 0)) if item.get("weight") is not None else None

            rows.append(ReportRow((
                item.get("person_name"),
                item.get("channel"),
                item.get("ticker"),
                qty,
                cost_p,
                cost_amt,
                curr_p,
                mv,
                pnl_r,
                tot_pnl,
                buy_cnt,
                sell_cnt,
                weight,
            ), {
                "person_id": item.get("person_id"),
                "account_id": item.get("account_id"),
                "ticker": item.get("ticker"),
            }))

        self.detail_model.replace(rows)
        self.detail_info.setText(f"统一持仓明细 · 共 {len(rows)} 条标的记录 · 点击任意行从右侧滑出抽屉")

    def _on_person_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.p_model.rows[index.row()]
        if row.values[0] == "合计":
            return
        p_id = row.metadata.get("person_id")
        self.set_view("detail", dimension="person", person_id=p_id)

    def _on_broker_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.b_model.rows[index.row()]
        if row.values[0] == "合计":
            return
        broker_name = str(row.values[0])
        self.set_view("detail", dimension="broker", broker=broker_name)

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
