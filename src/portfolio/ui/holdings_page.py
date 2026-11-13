"""Unified Holdings Management Page for Mac Desktop (PySide6).

Parity with Web:
1. Dashboard mode: Top 4 cards, person filter tabs, dimension quick buttons,
   person/broker/stock summary tables, donut ring asset distribution.
2. Unified Detail mode: Person, broker, and stock viewing dimensions with state preservation.
3. Stock Right Drawer:
   - Header with 6 summary metrics
   - 4 Tabs: 11-column trade history with cost changes, holding analysis, pnl analysis, monthly performance.
"""
import csv
from dataclasses import replace
from decimal import Decimal
import logging
from pathlib import Path
import time
from typing import Any

from PySide6.QtCore import QDate, QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QSplitterHandle,
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
from portfolio.services.closed_analytics_service import (
    build_closed_dashboard_analytics,
    filter_closed_rows,
    resolve_close_date_bounds,
)
from portfolio.services.holdings_management_service import (
    HoldingsManagementService,
    channel_label,
    closed_account_rows,
    closed_columns,
)
from portfolio.services.open_holdings_analytics_service import (
    build_open_holdings_dashboard,
)
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.holding_cost_chart import HoldingCostDualAxisChart
from portfolio.ui.pnl_analysis_charts import PnlAnalysisChartsWidget
from portfolio.ui.closed_dashboard import ClosedDashboardWidget, ClosedHoldingsKpiBar
from portfolio.ui.filters import PersonFilter
from portfolio.ui.finance_table import FinanceModel, FinanceTable
from portfolio.ui.icons import icon
from portfolio.ui.open_holdings_dashboard import (
    OpenHoldingsChartsRow,
    OpenHoldingsKpiBar,
    OpenTop10Card,
)
from portfolio.ui.theme import numeric_font, tokens


class HoldingsLocalFilterBar(QFrame):
    """持有中与已清仓两个页面统一共用的局部筛选栏。

    规格与交互规范：
    1. 嵌入顶部操作栏右侧（在 status_tabs 之后、导出明细之前），充分节约纵向空间。
    2. 控件尺寸、字体与边框统一紧凑设计（高度 32px）。
    3. 搜索框位于筛选条件之后，「重置筛选」按钮位于搜索框之后。
    4. 持有中：股票类型 + 搜索框 + 重置筛选。
    5. 已清仓：清仓日期 + 搜索框 + 重置筛选。
    6. 彻底清理已不再需要的「币种」筛选条件。
    7. 两种模式分别记忆自己的局部筛选状态，切回时自动恢复。
    """

    filter_changed = Signal()
    reset_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HoldingsLocalFilterBar")
        self.setStyleSheet("""
            QFrame#HoldingsLocalFilterBar {
                background: transparent;
                border: none;
            }
            QLabel.FilterLabel {
                color: #475569;
                font-size: 12px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
            QComboBox {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 12px;
                color: #0F172A;
                min-height: 24px;
            }
            QComboBox:hover {
                border-color: #93C5FD;
            }
            QComboBox:focus {
                border-color: #2563EB;
            }
            QLineEdit {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 12px;
                color: #0F172A;
                min-height: 24px;
            }
            QLineEdit:hover {
                border-color: #93C5FD;
            }
            QLineEdit:focus {
                border-color: #2563EB;
            }
            QPushButton.ResetBtn {
                background: #F8FAFC;
                color: #475569;
                font-size: 12px;
                font-weight: 600;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 3px 12px;
                min-height: 24px;
            }
            QPushButton.ResetBtn:hover {
                background: #F1F5F9;
                color: #1E293B;
                border-color: #94A3B8;
            }
        """)
        self.setFixedHeight(32)
        self._mode = "open"

        # 模式独立状态缓存
        self._open_type = ""
        self._open_search = ""
        self._closed_date = "all"
        self._closed_search = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 1. 持有中：股票类型
        self.lbl_stock_type = QLabel("股票类型")
        self.lbl_stock_type.setProperty("class", "FilterLabel")
        self.lbl_stock_type.setStyleSheet("color: #475569; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.lbl_stock_type)

        self.open_industry_combo = QComboBox(self)
        self.open_industry_combo.setMinimumWidth(95)
        self.open_industry_combo.addItem("全部", "")
        layout.addWidget(self.open_industry_combo)

        # 2. 已清仓：清仓日期
        self.lbl_close_date = QLabel("清仓日期")
        self.lbl_close_date.setProperty("class", "FilterLabel")
        self.lbl_close_date.setStyleSheet("color: #475569; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.lbl_close_date)

        self.close_date_combo = QComboBox(self)
        self.close_date_combo.setMinimumWidth(110)
        for label, key in (
            ("全部时间", "all"),
            ("近 6 个月", "6m"),
            ("近 12 个月", "12m"),
            ("近 24 个月", "24m"),
            ("今年以来", "ytd"),
            ("去年", "last_year"),
        ):
            self.close_date_combo.addItem(label, key)
        layout.addWidget(self.close_date_combo)

        # 3. 搜索框（紧跟在筛选条件之后）
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("搜索股票名称 / Ticker")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(200)
        self.open_search_input = self.search_input
        layout.addWidget(self.search_input)

        # 4. 重置筛选按钮（固定在最右侧）
        self.reset_btn = QPushButton("重置筛选", self)
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setProperty("class", "ResetBtn")
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background: #F8FAFC; color: #475569; font-size: 12px; font-weight: 600;
                border: 1px solid #CBD5E1; border-radius: 6px; padding: 3px 12px; min-height: 24px;
            }
            QPushButton:hover { background: #F1F5F9; color: #1E293B; border-color: #94A3B8; }
        """)
        layout.addWidget(self.reset_btn)

        self._apply_mode_visibility()

    def set_mode(self, mode: str):
        if mode == self._mode:
            self._apply_mode_visibility()
            return

        # 1. 记忆当前模式状态
        if self._mode == "open":
            self._open_type = str(self.open_industry_combo.currentData() or "")
            self._open_search = self.search_input.text()
        else:
            self._closed_date = str(self.close_date_combo.currentData() or "all")
            self._closed_search = self.search_input.text()

        self._mode = mode
        self._apply_mode_visibility()

        # 2. 恢复切入模式状态（阻断信号避免冗余触发）
        b_search = self.search_input.blockSignals(True)
        if mode == "open":
            self.search_input.setPlaceholderText("搜索股票名称 / Ticker")
            self.search_input.setText(self._open_search)
            b_type = self.open_industry_combo.blockSignals(True)
            self._select_combo_data(self.open_industry_combo, self._open_type)
            self.open_industry_combo.blockSignals(b_type)
        else:
            self.search_input.setPlaceholderText("搜索股票名称、Ticker、人员、券商或账号…")
            self.search_input.setText(self._closed_search)
            b_date = self.close_date_combo.blockSignals(True)
            self._select_combo_data(self.close_date_combo, self._closed_date)
            self.close_date_combo.blockSignals(b_date)
        self.search_input.blockSignals(b_search)

    def _apply_mode_visibility(self):
        is_open = self._mode == "open"
        self.lbl_stock_type.setVisible(is_open)
        self.open_industry_combo.setVisible(is_open)
        self.lbl_close_date.setVisible(not is_open)
        self.close_date_combo.setVisible(not is_open)

    @staticmethod
    def _select_combo_data(combo: QComboBox, target_data: str):
        if not target_data:
            combo.setCurrentIndex(0)
            return
        for i in range(combo.count()):
            if str(combo.itemData(i) or "").casefold() == str(target_data).casefold():
                combo.setCurrentIndex(i)
                return
        combo.setCurrentIndex(0)

    def reset_current_mode(self):
        """仅重置当前模式的局部筛选条件。"""
        b_s = self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(b_s)
        if self._mode == "open":
            self._open_type = ""
            self._open_search = ""
            b_t = self.open_industry_combo.blockSignals(True)
            self.open_industry_combo.setCurrentIndex(0)
            self.open_industry_combo.blockSignals(b_t)
        else:
            self._closed_date = "all"
            self._closed_search = ""
            b_d = self.close_date_combo.blockSignals(True)
            self.close_date_combo.setCurrentIndex(0)
            self.close_date_combo.blockSignals(b_d)

OPEN_HOLDINGS_COLUMNS = (
    "归属人员",
    "券商渠道",
    "Ticker",
    "类型",
    "持仓数量",
    "持仓均价",
    "当前价格",
    "离回本需涨",
    "持仓市值",
    "持仓成本",
    "浮动盈亏",
    "收益率",
    "持仓占比",
    "买入次数",
    "卖出次数",
    "详情",
)

OPEN_PERSON_SUMMARY_COLUMNS = (
    "归属人员",
    "持仓市值",
    "持仓成本",
    "浮动盈亏",
    "持仓收益率",
    "持仓占比",
    "持仓股票数",
    "盈利股票数",
    "亏损股票数",
)

logger = logging.getLogger(__name__)


class ModernMetricCard(QFrame):
    """Modern financial card matching mockup styling."""
    def __init__(self, title: str, parent=None, compact: bool = False):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet("""
            QFrame#card {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QFrame#card QLabel {
                border: none;
                background: transparent;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10 if compact else 14, 8 if compact else 12, 10 if compact else 14, 8 if compact else 12)
        layout.setSpacing(3 if compact else 4)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;" if compact else "color: #64748B; font-size: 12px; font-weight: 500;")

        self.val_lbl = QLabel("—")
        self.val_lbl.setFont(numeric_font(16 if compact else 22, bold=True))
        self.val_lbl.setStyleSheet("color: #0F172A;")

        self.sub_lbl = QLabel("")
        self.sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        self.sub_lbl.hide()

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.val_lbl)
        layout.addWidget(self.sub_lbl)
        if compact:
            layout.addStretch()

    def set_content(self, main_val: str, sub_val: str = "", sub_color: str | None = None):
        self.val_lbl.setText(main_val)
        if sub_val:
            self.sub_lbl.setText(sub_val)
            self.sub_lbl.show()
            if sub_color:
                self.sub_lbl.setStyleSheet(f"color: {sub_color}; font-size: 11px; font-weight: 600;")
            else:
                self.sub_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        else:
            self.sub_lbl.setText("")
            self.sub_lbl.hide()


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
        text_mv = f"{self.total_val:,.0f}" if self.total_val >= 10000 else f"{self.total_val:,.2f}"
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
        self.nulls_last = False

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
        if self.columns[column] in ("操作", "详情"):
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
                return (1, val, "")
            return (2, 0, str(val).strip().lower())

        reverse = (self.sort_order == Qt.SortOrder.DescendingOrder)
        if self.nulls_last:
            missing = [row for row in normal_rows if row.values[col] in (None, "—")]
            normal_rows = [row for row in normal_rows if row.values[col] not in (None, "—")]
        else:
            missing = []
        normal_rows.sort(key=sort_key, reverse=reverse)
        self.beginResetModel()
        self.rows = tuple(normal_rows + missing + total_rows)
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.columns):
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                col_name = self.columns[section]
                if self.sort_column == section and col_name not in ("操作", "详情"):
                    arrow = " ▲" if self.sort_order == Qt.SortOrder.AscendingOrder else " ▼"
                    return f"{col_name}{arrow}"
                return col_name
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
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

        currency = "¥" if row.metadata.get("currency") == "CNY" else ""
        if role == Qt.ItemDataRole.DisplayRole:
            if col_name == "操作" and not is_total:
                return "🔍 查看明细"
            if col_name == "详情" and not is_total:
                return "查看 >"
            if isinstance(val, (int, float, Decimal)):
                if col_name == "持仓占比":
                    return f"{val * 100:.2f}%"
                if col_name == "离回本需涨":
                    if val <= 0:
                        return "已回本"
                    return f"+{val * 100:.2f}%"
                # 股票只数/买卖次数等离散计数，一律展示为纯整数，不带小数位 (解决如已清仓显示为 35.00 的问题)
                if col_name in ("操作笔数", "买入次数", "卖出次数", "已清仓", "当前持仓", "持仓股票", "股票数量", "持股只数", "持仓股票数", "盈利股票数", "亏损股票数") or (
                    ("股票" in col_name or "次数" in col_name) and not any(k in col_name for k in ("市值", "盈亏", "成本", "收益", "比例", "率", "金", "额"))
                ):
                    return f"{int(round(float(val))):,d}"
                if "数量" in col_name or "股数" in col_name or ("持仓" in col_name and not any(k in col_name for k in ("市值", "盈亏", "成本", "状态", "占比", "比例"))):
                    return f"{val:,.0f}" if isinstance(val, (int, Decimal)) and val % 1 == 0 else f"{val:,.2f}"
                if any(w in col_name for w in ("收益率", "比例", "涨幅", "涨跌幅", "涨跌")):
                    sign = "+" if val > 0 else ""
                    return f"{sign}{val * 100:.2f}%"
                if any(w in col_name for w in ("市值", "本金", "盈亏", "投入", "成本", "价格", "现金", "金额", "总额", "均价", "清仓价", "最后清仓价", "当前价", "最新价", "清仓收益")):
                    sign = "+" if ("盈亏" in col_name or col_name == "清仓收益") and val > 0 else ""
                    if col_name in ("清仓价", "最后清仓价") and row.metadata.get("is_split_adjusted"):
                        return f"{currency}{val:,.2f}*"
                    return f"{sign}{currency}{val:,.2f}"
                return f"{val:,.2f}"
            return exact_text(val)

        if role == Qt.ItemDataRole.ToolTipRole:
            if col_name == "当前价" and row.metadata.get("quote_status"):
                return " · ".join(filter(None,(row.metadata["quote_status"],row.metadata.get("quote_time"))))
            if col_name in ("清仓价", "最后清仓价") and val is not None and row.metadata.get("is_split_adjusted"):
                orig = row.metadata.get("orig_close_price")
                notes = row.metadata.get("split_notes") or ""
                orig_str = f"{currency}{orig:,.2f}" if orig is not None else "—"
                return f"折算后清仓价: {currency}{val:,.2f}\n原始清仓价: {orig_str}\n清仓后除权调整: {notes}"
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.FontRole:
            if is_total:
                return numeric_font(12, bold=True)
            if isinstance(val, (int, float, Decimal)) or "时间" in col_name or "日期" in col_name or "Ticker" in col_name or col_name == "操作":
                return numeric_font(12)

        if role == Qt.ItemDataRole.BackgroundRole:
            if is_total:
                return QColor("#F8FAFC")

        if role == Qt.ItemDataRole.ForegroundRole:
            if col_name in ("操作", "详情") and not is_total:
                return QColor("#2563EB")
            if col_name == "Ticker" and not is_total:
                return QColor("#0F172A")
            if val is None:
                return QColor(tokens(self.parent()).subtle)
            if col_name == "离回本需涨" and isinstance(val, (int, float, Decimal)):
                return QColor(tokens(self.parent()).loss if val > 0 else tokens(self.parent()).profit)
            if col_name == "盈利股票数" and isinstance(val, (int, float, Decimal)) and val > 0:
                return QColor(tokens(self.parent()).profit)
            if col_name == "亏损股票数" and isinstance(val, (int, float, Decimal)) and val > 0:
                return QColor(tokens(self.parent()).loss)
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
        self.setMinimumWidth(360)
        self.setMaximumWidth(900)

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
        metrics_frame.setObjectName("stockMetricsContainer")
        metrics_frame.setStyleSheet("#stockMetricsContainer { background: transparent; border: none; padding: 0px; }")
        m_layout = QHBoxLayout(metrics_frame)
        m_layout.setContentsMargins(0, 0, 0, 0)
        m_layout.setSpacing(8)

        self.m_qty = ModernMetricCard("当前持仓", compact=True)
        self.m_cost = ModernMetricCard("持仓成本", compact=True)
        self.m_curr = ModernMetricCard("最新价", compact=True)
        self.m_mv = ModernMetricCard("持仓市值", compact=True)
        self.m_fpnl = ModernMetricCard("浮动盈亏", compact=True)
        self.m_cpnl = ModernMetricCard("累计盈亏", compact=True)
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
        th_layout.setSpacing(6)
        th_lbl = QLabel("持仓与成本轨迹")
        th_lbl.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700;")
        th_layout.addWidget(th_lbl)
        self.holdings_canvas = HoldingCostDualAxisChart(tab_holdings)
        self.holdings_canvas.setMinimumHeight(260)
        th_layout.addWidget(self.holdings_canvas, 1)
        self.stack.addWidget(tab_holdings)

        # Tab 3: 收益分析
        tab_pnl = QScrollArea()
        tab_pnl.setWidgetResizable(True)
        tab_pnl.setFrameShape(QFrame.Shape.NoFrame)
        tab_pnl_inner = QWidget()
        tp_layout = QVBoxLayout(tab_pnl_inner)
        tp_layout.setContentsMargins(0, 4, 4, 4)
        tp_layout.setSpacing(8)
        pnl_cards_row = QHBoxLayout()
        pnl_cards_row.setSpacing(8)
        self.pnl_flt_card = ModernMetricCard("浮动盈亏 (USD)", compact=True)
        self.pnl_rlz_card = ModernMetricCard("已实现盈亏 (USD)", compact=True)
        self.pnl_cum_card = ModernMetricCard("累计盈亏 (USD)", compact=True)
        for c in (self.pnl_flt_card, self.pnl_rlz_card, self.pnl_cum_card):
            pnl_cards_row.addWidget(c, 1)
        tp_layout.addLayout(pnl_cards_row)
        self.pnl_canvas = PnlAnalysisChartsWidget(tab_pnl_inner)
        tp_layout.addWidget(self.pnl_canvas, 1)
        tab_pnl.setWidget(tab_pnl_inner)
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
        self.monthly_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
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
        self.m_qty.set_content(f"{qty:,.0f} 股", "")

        cost = summary.get("cost_price")
        self.m_cost.set_content(f"${cost:,.2f}" if cost else "—", "")

        curr = summary.get("current_price")
        self.m_curr.set_content(f"${curr:,.2f}" if curr else "—", "")

        mv = summary.get("market_value") or 0
        self.m_mv.set_content(f"${mv:,.2f}", "")

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

        # Tab 2: Holding analysis (双 Y 轴交互式持仓与成本轨迹)
        h_data = drawer_data.get("holding_analysis", {})
        self.holdings_canvas.set_data(h_data)

        # Tab 3: PnL analysis
        pnl_data = drawer_data.get("pnl_analysis", {})
        def _fmt_pnl(val):
            if val is None:
                return "—"
            return f"{'+' if val > 0 else ''}${val:,.2f}"
        flt_v = pnl_data.get("floating_pnl")
        rlz_v = pnl_data.get("realized_pnl")
        cum_v = pnl_data.get("cumulative_pnl")
        self.pnl_flt_card.set_content(_fmt_pnl(flt_v), sub_f if frate is not None else "", fpnl_color)
        rlz_color = "#16A34A" if (rlz_v or 0) > 0 else "#DC2626" if (rlz_v or 0) < 0 else "#64748B"
        self.pnl_rlz_card.set_content(_fmt_pnl(rlz_v), "")
        self.pnl_rlz_card.val_lbl.setStyleSheet(f"color: {rlz_color};")
        self.pnl_cum_card.set_content(_fmt_pnl(cum_v), sub_c if crate is not None else "", cpnl_color)
        self.pnl_canvas.set_data(pnl_data)

        # Tab 4: Monthly performance
        m_recs = drawer_data.get("monthly_performance", [])
        m_rows = []
        for mr in m_recs:
            m_rows.append(ReportRow((
                mr.get("month"),
                Decimal(str(mr.get("market_value") or 0)),
                Decimal(str(mr["monthly_pnl"])) if mr.get("monthly_pnl") is not None else None,
                Decimal(str(mr["return_rate"])) if mr.get("return_rate") is not None else None,
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
                t.get("channel"),
                t.get("side"),
                Decimal(str(t.get("quantity") or 0)),
                Decimal(str(t.get("current_price") or 0)) if t.get("current_price") is not None else None,
                Decimal(str(t.get("price") or 0)),
                Decimal(str(t.get("amount") or 0)),
                Decimal(str(t.get("fee") or 0)),
                Decimal(str(t["post_quantity"])) if t.get("post_quantity") is not None else None,
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

    def mousePressEvent(self, event):
        # 阻止抽屉内部标签/空白区域的点击事件向父容器冒泡，防止误触外部点击收起抽屉
        event.accept()


class HoldingsPage(QWidget):
    drill = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.analytics = None
        self.dimension = "person"
        self.view_mode = "detail"
        self._selected_person_filter = None
        self._cached_dash_data = None

        has_host_topbar = getattr(parent, "top_bar_layout", None) is not None
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0 if has_host_topbar else 16, 2 if has_host_topbar else 12, 0 if has_host_topbar else 16, 12)
        root_layout.setSpacing(8)

        # Left: Person Filter Dropdown (与主界面统一风格的下拉选择框)
        self.person_filter = PersonFilter(self)
        self.person_filter.setMinimumWidth(136)
        self.person_filter.changed.connect(self._on_person_filter_changed)
        self.person_filter.hide()

        # Right: Mode Switcher (已移除仪表板总览入口，默认隐藏)
        self.mode_box = QFrame()
        self.mode_box.setObjectName("holdingsModeBox")
        self.mode_box.setStyleSheet("#holdingsModeBox { background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 8px; }")
        mb_layout = QHBoxLayout(self.mode_box)
        mb_layout.setContentsMargins(3, 3, 3, 3)
        mb_layout.setSpacing(4)

        self.mode_dash_btn = QPushButton("仪表板总览")
        self.mode_dash_btn.setCheckable(True)
        self.mode_dash_btn.setChecked(False)
        self.mode_dash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_dash_btn.setStyleSheet("""
            QPushButton { border: none; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; color: #64748B; background: transparent; }
            QPushButton:checked { background: #2563EB; color: #FFFFFF; font-weight: bold; }
        """)
        self.mode_dash_btn.clicked.connect(lambda: self.switch_view_mode("dashboard"))
        mb_layout.addWidget(self.mode_dash_btn)

        self.mode_detail_btn = QPushButton("持仓明细页")
        self.mode_detail_btn.setCheckable(True)
        self.mode_detail_btn.setChecked(True)
        self.mode_detail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_detail_btn.setStyleSheet("""
            QPushButton { border: none; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; color: #64748B; background: transparent; }
            QPushButton:checked { background: #2563EB; color: #FFFFFF; font-weight: bold; }
        """)
        self.mode_detail_btn.clicked.connect(lambda: self.switch_view_mode("detail"))
        mb_layout.addWidget(self.mode_detail_btn)
        self.mode_box.hide()

        if not has_host_topbar:
            top_bar = QHBoxLayout()
            top_bar.setSpacing(12)
            top_bar.addWidget(self.person_filter)
            top_bar.addStretch(1)
            top_bar.addWidget(self.mode_box)
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
        self.p_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.p_model = HoldingsTableModel(self.p_table)
        self.p_model.columns = ("人员", "持仓市值", "本金", "累计盈亏", "收益率", "持仓股票", "当前持仓", "已清仓")
        self.p_table.setModel(self.p_model)
        self.p_table.horizontalHeader().setFixedHeight(36)
        self.p_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
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
        self.b_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.b_model = HoldingsTableModel(self.b_table)
        self.b_model.columns = ("券商", "持仓市值", "本金", "累计盈亏", "收益率")
        self.b_table.setModel(self.b_model)
        b_hdr_view = self.b_table.horizontalHeader()
        b_hdr_view.setFixedHeight(36)
        b_hdr_view.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        b_hdr_view.setMinimumSectionSize(56)
        b_hdr_view.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        b_hdr_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        b_hdr_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        b_hdr_view.setStyleSheet("background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0;")
        self.b_table.setStyleSheet("QHeaderView, QHeaderView::section { background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0; } QTableView::item { padding: 0 3px; }")
        self.b_table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_table.clicked.connect(self._on_broker_table_clicked)
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
        self.s_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.s_model = HoldingsTableModel(self.s_table)
        self.s_model.columns = ("Ticker", "持仓市值", "本金", "累计盈亏", "收益率")
        self.s_table.setModel(self.s_model)
        s_hdr_view = self.s_table.horizontalHeader()
        s_hdr_view.setFixedHeight(36)
        s_hdr_view.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        s_hdr_view.setMinimumSectionSize(52)
        s_hdr_view.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        s_hdr_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        s_hdr_view.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        s_hdr_view.setStyleSheet("background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0;")
        self.s_table.setStyleSheet("QHeaderView, QHeaderView::section { background: #FFFFFF; border: none; border-bottom: 1px solid #E2E8F0; } QTableView::item { padding: 0 3px; }")
        self.s_table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
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
        self.dist_tabs.setCurrentIndex(2)
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

        # Status Tabs (持仓中 / 已清仓 两个 Tab，持仓中放在前面展示)
        status_lbl = QLabel("状态：")
        status_lbl.hide()
        detail_bar.addWidget(status_lbl)
        self.status_combo = QComboBox()
        self.status_combo.addItem("当前持仓", "持有中")
        self.status_combo.addItem("已清仓", "已清仓")
        self.status_combo.hide()
        self.status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        detail_bar.addWidget(self.status_combo)

        self.status_tabs = QTabBar()
        self.status_tabs.setDrawBase(False)
        self.status_tabs.setExpanding(False)
        self.status_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self.status_tabs.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status_tabs.setStyleSheet("""
            QTabBar::tab {
                padding: 7px 18px;
                background: #EFF4FB;
                color: #334155;
                border-radius: 7px;
                font-size: 13px;
                font-weight: 600;
                margin-right: 6px;
            }
            QTabBar::tab:selected {
                background: #2563EB;
                color: #FFFFFF;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected {
                background: #E2E8F0;
                color: #0F172A;
            }
        """)
        self.status_tabs.addTab("持仓中")
        self.status_tabs.addTab("已清仓")
        self.status_tabs.setCurrentIndex(0)
        self.status_tabs.currentChanged.connect(self._on_status_tab_changed)
        detail_bar.addWidget(self.status_tabs)

        self.closed_sep = QFrame()
        self.closed_sep.setFixedWidth(1)
        self.closed_sep.setFixedHeight(20)
        self.closed_sep.setStyleSheet("background: #CBD5E1; border: none;")
        self.closed_sep.hide()
        detail_bar.addWidget(self.closed_sep)

        self.closed_mode = QTabBar()
        self.closed_mode.setDrawBase(False)
        self.closed_mode.setStyleSheet(
            "QTabBar::tab { padding: 7px 10px; background: #EFF4FB; color: #334155; border-radius: 6px; margin-right: 4px; }"
            "QTabBar::tab:selected { background: #2563EB; color: white; }"
        )
        self.closed_mode.addTab("按人员明细")
        self.closed_mode.addTab("按券商账户汇总")
        self.closed_mode.hide()
        self.closed_mode.currentChanged.connect(self._on_filter_changed)
        detail_bar.addWidget(self.closed_mode)
        self._detail_states = {}
        self._detail_mode = None
        self._closed_pnl_filter = ""
        self._closed_ticker_filter = ""
        self._closed_trend_window = "all"
        self._open_stock_type = ""
        self._open_industry = ""
        self._open_currency = ""
        self._open_pnl_filter = "all"
        self._open_ticker_filter = ""
        self._open_person_drill_id: int | None = None
        self._open_person_drill_name: str = ""
        self._open_trend_window = "ytd"
        self._open_page = 1
        self._open_page_size = 50
        self._open_all_rows: list[ReportRow] = []

        # 虚拟保留兼容对象（不加入顶部 detail_bar，避免重复冗余）
        self.closed_broker_lbl = QLabel()
        self.closed_broker_lbl.hide()
        self.broker_combo = QComboBox()
        self.broker_combo.addItem("全部渠道", "")
        self.broker_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.broker_combo.hide()
        self.closed_person_lbl = QLabel()
        self.closed_person_lbl.hide()
        self.closed_person_combo = PersonFilter(self)
        self.closed_person_combo.hide()

        # Active broker filter chip (shown when filtered by a specific broker channel)
        self.active_broker_chip = QPushButton("渠道: — ✕")
        self.active_broker_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.active_broker_chip.setToolTip("点击清除券商渠道筛选，查看全部渠道")
        self.active_broker_chip.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; color: #2563EB; font-weight: 600; font-size: 12px;
                border: 1px solid #BFDBFE; border-radius: 6px; padding: 5px 10px;
            }
            QPushButton:hover { background: #DBEAFE; border-color: #93C5FD; }
        """)
        self.active_broker_chip.clicked.connect(self._on_active_broker_chip_clicked)
        detail_bar.addWidget(self.active_broker_chip)
        self.active_broker_chip.hide()

        # Active type filter chip (shown when filtered by a specific type in open holdings)
        self.active_industry_chip = QPushButton("类型: — ✕")
        self.active_industry_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.active_industry_chip.setToolTip("点击清除类型筛选，查看全部类型")
        self.active_industry_chip.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; color: #2563EB; font-weight: 600; font-size: 12px;
                border: 1px solid #BFDBFE; border-radius: 6px; padding: 5px 10px;
            }
            QPushButton:hover { background: #DBEAFE; border-color: #93C5FD; }
        """)
        self.active_industry_chip.clicked.connect(lambda: self._on_open_industry_donut_clicked(""))
        detail_bar.addWidget(self.active_industry_chip)
        self.active_industry_chip.hide()

        # Active structure filter chip (shown when donut slice/legend is selected)
        self.structure_chip = QPushButton("结构筛选: — ✕")
        self.structure_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.structure_chip.setToolTip("点击清除盈利/亏损结构筛选，恢复全部已清仓记录")
        self.structure_chip.setStyleSheet("""
            QPushButton {
                background: #F0FDF4; color: #15803D; font-weight: 600; font-size: 12px;
                border: 1px solid #BBF7D0; border-radius: 6px; padding: 5px 10px;
            }
            QPushButton:hover { background: #DCFCE7; }
        """)
        self.structure_chip.clicked.connect(lambda: self._on_structure_clicked(""))
        detail_bar.addWidget(self.structure_chip)
        self.structure_chip.hide()

        self.closed_ticker_chip = QPushButton("排行筛选: — ✕")
        self.closed_ticker_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.closed_ticker_chip.setToolTip("点击清除 Top 10 股票筛选，恢复全部已清仓记录")
        self.closed_ticker_chip.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; color: #1D4ED8; font-weight: 600; font-size: 12px;
                border: 1px solid #BFDBFE; border-radius: 6px; padding: 5px 10px;
            }
            QPushButton:hover { background: #DBEAFE; }
        """)
        self.closed_ticker_chip.clicked.connect(lambda: self._on_closed_ticker_clicked(""))
        detail_bar.addWidget(self.closed_ticker_chip)
        self.closed_ticker_chip.hide()

        self.open_ticker_chip = QPushButton("股票筛选: — ✕")
        self.open_ticker_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_ticker_chip.setToolTip("点击清除 Top 股票筛选，恢复全部持仓")
        self.open_ticker_chip.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; color: #1D4ED8; font-weight: 600; font-size: 12px;
                border: 1px solid #BFDBFE; border-radius: 6px; padding: 5px 10px;
            }
            QPushButton:hover { background: #DBEAFE; }
        """)
        self.open_ticker_chip.clicked.connect(lambda: self._on_open_ticker_clicked(""))
        detail_bar.addWidget(self.open_ticker_chip)
        self.open_ticker_chip.hide()

        self.inline_status_lbl = QLabel("")
        self.inline_status_lbl.setStyleSheet("color: #2563EB; font-size: 12px; font-weight: 500; padding: 0 8px; background: transparent; border: none;")
        detail_bar.addWidget(self.inline_status_lbl)

        detail_bar.addStretch()

        # -----------------------------------------------------------------
        # 局部筛选栏：嵌入顶部操作栏右侧，节约纵向空间，紧邻导出明细按钮
        # -----------------------------------------------------------------
        self.local_filter_bar = HoldingsLocalFilterBar(self)
        self.open_filter_bar = self.local_filter_bar  # 兼容旧属性
        self.close_date_lbl = self.local_filter_bar.lbl_close_date
        self.close_date_combo = self.local_filter_bar.close_date_combo
        self.open_industry_combo = self.local_filter_bar.open_industry_combo
        self.search_input = self.local_filter_bar.search_input
        self.open_search_input = self.local_filter_bar.search_input
        self.open_reset_btn = self.local_filter_bar.reset_btn
        self.open_type_combo = QComboBox()
        self.open_type_combo.hide()
        self.open_broker_combo = QComboBox()
        self.open_broker_combo.hide()
        self.open_broker_combo.currentIndexChanged.connect(self._on_open_broker_combo_changed)
        self.open_currency_combo = QComboBox()
        self.open_currency_combo.hide()

        self.local_filter_bar.open_industry_combo.currentIndexChanged.connect(self._on_open_industry_combo_changed)
        self.local_filter_bar.close_date_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.local_filter_bar.search_input.textChanged.connect(self._on_search_input_changed)
        self.local_filter_bar.reset_btn.clicked.connect(self._on_local_reset_clicked)
        detail_bar.addWidget(self.local_filter_bar)

        self.export_btn = QPushButton("导出明细…")
        self.export_btn.setIcon(icon("download", self))
        self.export_btn.clicked.connect(self._export_detail)
        detail_bar.addWidget(self.export_btn)

        dc_layout.addLayout(detail_bar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self._drawer_width: int = 520
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        self.splitter.setStyleSheet("""
            QSplitter::handle:horizontal {
                background: transparent;
                border-left: 1px solid #E2E8F0;
                margin: 4px 1px;
            }
            QSplitter::handle:horizontal:hover {
                background: #93C5FD;
                border-left: 2px solid #2563EB;
            }
        """)

        # Left: Analytics Dashboards (top) + Unified Detail Table (bottom)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)

        left_table_widget = QWidget()
        lt_layout = QVBoxLayout(left_table_widget)
        lt_layout.setContentsMargins(0, 0, 0, 0)
        lt_layout.setSpacing(10)

        # -----------------------------------------------------------------
        # 1. 顶部六张统计卡片（持有中与已清仓各自独立，高度均为 60px）
        # -----------------------------------------------------------------
        self.open_kpi_bar = OpenHoldingsKpiBar(self)
        self.closed_kpi_bar = ClosedHoldingsKpiBar(self)
        self.closed_kpi_bar.hide()
        lt_layout.addWidget(self.open_kpi_bar)
        lt_layout.addWidget(self.closed_kpi_bar)

        # 3/4/5. 券商持仓分布 + 类型持仓分布 + 持仓市值趋势
        self.open_charts_row = OpenHoldingsChartsRow(self)
        self.open_charts_row.broker_clicked.connect(self._on_open_broker_donut_clicked)
        self.open_charts_row.industry_clicked.connect(self._on_open_industry_donut_clicked)
        self.open_charts_row.trend_window_changed.connect(self._on_open_trend_window_changed)
        lt_layout.addWidget(self.open_charts_row)

        # 5.5 人员持仓情况汇总表（当全局人员选择「全部」或「他人」时展示每个人的持仓情况）
        self.open_person_card = QFrame(self)
        self.open_person_card.setObjectName("OpenPersonCard")
        self.open_person_card.setStyleSheet("""
            QFrame#OpenPersonCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
        """)
        opc_layout = QVBoxLayout(self.open_person_card)
        opc_layout.setContentsMargins(14, 10, 14, 10)
        opc_layout.setSpacing(8)

        opc_hdr = QHBoxLayout()
        self.open_person_title_lbl = QLabel("各人员持仓情况")
        self.open_person_title_lbl.setStyleSheet("color: #0F172A; font-size: 14px; font-weight: 700; border: none; background: transparent;")
        opc_hdr.addWidget(self.open_person_title_lbl)
        opc_hdr.addStretch()
        opc_hint = QLabel("当前全局人员为多人员范围 · 点击人员行可筛选下方明细，再次点击恢复全部")
        opc_hint.setStyleSheet("color: #64748B; font-size: 11px; border: none; background: transparent;")
        opc_hdr.addWidget(opc_hint)
        opc_layout.addLayout(opc_hdr)

        self.open_person_table = FinanceTable(freeze=False)
        self.open_person_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.open_person_table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_person_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.open_person_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.open_person_model = HoldingsTableModel(self.open_person_table)
        self.open_person_model.columns = OPEN_PERSON_SUMMARY_COLUMNS
        self.open_person_table.setModel(self.open_person_model)
        self.open_person_table.horizontalHeader().setFixedHeight(34)
        self.open_person_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.open_person_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.open_person_table.horizontalHeader().setStretchLastSection(True)
        self.open_person_table.horizontalHeader().setStyleSheet("background: #F8FAFC; border: none; border-bottom: 1px solid #E2E8F0;")
        self.open_person_table.setStyleSheet("QHeaderView, QHeaderView::section { background: #F8FAFC; border: none; border-bottom: 1px solid #E2E8F0; }")
        self.open_person_table.horizontalHeader().sectionClicked.connect(self.open_person_model.toggle_sort)
        self.open_person_table.clicked.connect(self._on_open_person_table_clicked)
        opc_layout.addWidget(self.open_person_table)
        self.open_person_card.hide()
        lt_layout.addWidget(self.open_person_card)

        # 5.6 持有中股票盈亏贡献 Top N
        self.open_top10_card = OpenTop10Card(self)
        self.open_top10_card.ticker_clicked.connect(self._on_open_ticker_clicked)
        lt_layout.addWidget(self.open_top10_card)

        # 6. 持仓明细列表顶部标题与 全部 / 盈利 / 亏损 状态切换栏
        self.open_table_header = QFrame(self)
        self.open_table_header.setObjectName("OpenTableHeader")
        self.open_table_header.setStyleSheet("""
            QFrame#OpenTableHeader {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom: none;
            }
        """)
        oth_layout = QHBoxLayout(self.open_table_header)
        oth_layout.setContentsMargins(14, 10, 14, 6)
        oth_layout.setSpacing(10)

        self.open_table_title_lbl = QLabel("持仓明细 (共 0 只)")
        self.open_table_title_lbl.setStyleSheet("color: #0F172A; font-size: 14px; font-weight: 700; border: none; background: transparent;")
        oth_layout.addWidget(self.open_table_title_lbl)
        oth_layout.addStretch()

        self._open_pnl_btns: dict[str, QPushButton] = {}
        for key, label in (("all", "全部 (0)"), ("profit", "盈利 (0)"), ("loss", "亏损 (0)")):
            btn = QPushButton(label, self.open_table_header)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self._on_open_pnl_tab_clicked(k))
            self._open_pnl_btns[key] = btn
            oth_layout.addWidget(btn)
        self._update_open_pnl_btn_styles()
        lt_layout.addWidget(self.open_table_header)

        # -----------------------------------------------------------------
        # 已清仓 (Closed Holdings) 专属看板
        # -----------------------------------------------------------------
        self.closed_dashboard = ClosedDashboardWidget(self, kpi_bar=self.closed_kpi_bar)
        self.closed_dashboard.broker_clicked.connect(self._on_closed_dashboard_broker_clicked)
        self.closed_dashboard.structure_clicked.connect(self._on_structure_clicked)
        self.closed_dashboard.trend_window_changed.connect(self._on_closed_trend_window_changed)
        self.closed_dashboard.ticker_clicked.connect(self._on_closed_ticker_clicked)
        self.closed_dashboard.hide()
        lt_layout.addWidget(self.closed_dashboard)

        self.detail_info = QLabel("统一持仓明细 · 点击任意标的行在右侧抽屉查看交易流水与收益分析")
        self.detail_info.setObjectName("secondary")
        lt_layout.addWidget(self.detail_info)

        self.detail_table = FinanceTable(freeze=False)
        self.detail_model = HoldingsTableModel(self.detail_table)
        self.detail_model.columns = OPEN_HOLDINGS_COLUMNS
        self.detail_table.setModel(self.detail_model)
        self.detail_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.detail_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.detail_table.horizontalHeader().sectionClicked.connect(self._on_detail_header_clicked)
        self.detail_table.clicked.connect(self._on_detail_row_clicked)
        lt_layout.addWidget(self.detail_table, 1)

        # 持仓明细底部汇总与分页栏 (持有中专属)
        self.open_table_footer = QFrame(self)
        self.open_table_footer.setObjectName("OpenTableFooter")
        self.open_table_footer.setStyleSheet("""
            QFrame#OpenTableFooter {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
            }
        """)
        otf_layout = QHBoxLayout(self.open_table_footer)
        otf_layout.setContentsMargins(14, 8, 14, 8)
        otf_layout.setSpacing(10)

        self.open_summary_lbl = QLabel("合计：—")
        self.open_summary_lbl.setStyleSheet("color: #334155; font-size: 12px; font-weight: 600; border: none; background: transparent;")
        otf_layout.addWidget(self.open_summary_lbl, 1)

        self.open_page_size_combo = QComboBox(self.open_table_footer)
        for sz in (10, 20, 50, 100):
            self.open_page_size_combo.addItem(f"{sz} 条/页", sz)
        self.open_page_size_combo.setCurrentIndex(2)  # 默认 50 条/页
        self.open_page_size_combo.currentIndexChanged.connect(self._on_open_page_size_changed)
        otf_layout.addWidget(self.open_page_size_combo)

        self.open_prev_btn = QPushButton("‹", self.open_table_footer)
        self.open_prev_btn.setFixedWidth(28)
        self.open_prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_prev_btn.clicked.connect(lambda: self._on_open_page_step(-1))
        otf_layout.addWidget(self.open_prev_btn)

        self.open_page_lbl = QLabel("1 / 1")
        self.open_page_lbl.setStyleSheet("color: #1E293B; font-size: 12px; font-weight: 600; padding: 0 6px; border: none; background: transparent;")
        otf_layout.addWidget(self.open_page_lbl)

        self.open_next_btn = QPushButton("›", self.open_table_footer)
        self.open_next_btn.setFixedWidth(28)
        self.open_next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_next_btn.clicked.connect(lambda: self._on_open_page_step(1))
        otf_layout.addWidget(self.open_next_btn)

        lt_layout.addWidget(self.open_table_footer)

        left_scroll.setWidget(left_table_widget)
        self.splitter.addWidget(left_scroll)

        # Right: Stock Drawer
        self.drawer = StockDrawerWidget(self)
        self.drawer.closed.connect(self._close_drawer)
        self.drawer.hide()
        self.splitter.addWidget(self.drawer)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        dc_layout.addWidget(self.splitter, 1)
        self.stack.addWidget(detail_container)
        self.stack.setCurrentIndex(1)

        root_layout.addWidget(self.stack, 1)

        # 全局鼠标监听：抽屉展开时点击抽屉外部区域自动收起抽屉
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.MouseButtonPress
            and isinstance(watched, QWidget)
            and not self.drawer.isHidden()
            and self.isVisible()
        ):
            in_drawer = watched is self.drawer or self.drawer.isAncestorOf(watched)
            in_splitter_handle = isinstance(watched, QSplitterHandle) and watched.splitter() is self.splitter
            if not in_drawer and not in_splitter_handle:
                top_win = watched.window()
                parent_w = top_win.parentWidget() if top_win is not None else None
                if parent_w is not None and (parent_w is self.drawer or self.drawer.isAncestorOf(parent_w)):
                    return super().eventFilter(watched, event)
                vp = self.detail_table.viewport()
                if watched is vp or vp.isAncestorOf(watched):
                    global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                    idx = self.detail_table.indexAt(vp.mapFromGlobal(global_pos))
                    if idx.isValid():
                        return super().eventFilter(watched, event)
                self._close_drawer()
        return super().eventFilter(watched, event)

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
        for combo in (self.person_filter, self.closed_person_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("全部启用人员", None)
            combo.addItem("他人", "OTHERS")
            for p in persons:
                if p.get("enabled", True):
                    combo.addItem(p["name"], p["id"])
            if self._selected_person_filter is not None:
                combo.set_person_id(self._selected_person_filter)
            else:
                combo.reset()
            combo.blockSignals(False)

    def _on_person_filter_changed(self):
        p_id = self.person_filter.currentData()
        self._selected_person_filter = p_id
        self.closed_person_combo.blockSignals(True)
        if p_id is not None:
            self.closed_person_combo.set_person_id(p_id)
        else:
            self.closed_person_combo.reset()
        self.closed_person_combo.blockSignals(False)
        if self.view_mode == "dashboard":
            self.refresh_dashboard()
        else:
            self.refresh_detail()

    def set_inline_status(self, text: str = ""):
        """在持仓中/已清仓标签同一行展示状态提示，避免页面顶部横幅显隐引起布局抖动。"""
        if hasattr(self, "inline_status_lbl"):
            self.inline_status_lbl.setText(text or "")

    def _has_global_filter(self) -> bool:
        return bool(self.main_window and hasattr(self.main_window, "global_asset_filter"))

    def _apply_global_filter_to_local(self, state):
        self._selected_person_filter = state.person
        for combo in (self.person_filter, self.closed_person_combo):
            blocked = combo.blockSignals(True)
            if state.person is not None:
                combo.set_person_id(state.person)
            else:
                combo.reset()
            combo.blockSignals(blocked)
        broker_name = ""
        if self._has_global_filter():
            broker_name = self.main_window.global_asset_filter.account_filter.current_broker_name()
        self._sync_combo_by_data(self.broker_combo, broker_name)
        self._sync_combo_by_data(self.open_broker_combo, broker_name)

    def _get_global_ticker(self) -> str:
        if not self._has_global_filter():
            return ""
        g = self.main_window.global_asset_filter
        if not hasattr(g, "ticker_search"):
            return ""
        data = g.ticker_search.currentData()
        if data is None:
            return ""
        txt = str(g.ticker_search.currentText() or "").split(" · ")[0].strip().upper()
        return "" if txt in ("", "全部股票") else txt

    def sync_from_global_filter(self, state):
        """从主窗口唯一 GlobalAssetFilter 同步人员、券商渠道、股票状态。"""
        if self._selected_person_filter != state.person:
            self._open_person_drill_id = None
            self._open_person_drill_name = ""
        self._apply_global_filter_to_local(state)
        self._close_drawer()
        if self.analytics is not None:
            if self.view_mode == "dashboard":
                self.refresh_dashboard()
            else:
                self.refresh_detail()

    def _on_active_broker_chip_clicked(self):
        if self._has_global_filter() and hasattr(self.main_window, "sync_broker_from_holdings"):
            self.main_window.sync_broker_from_holdings("")
        else:
            self.broker_combo.setCurrentIndex(0)
            self._close_drawer()
            self.refresh_detail()

    def sync_person_from_main(self, person_id: Any):
        """接收主窗口全局人员筛选状态，并统一同步到本地查询作用域。"""
        self._selected_person_filter = person_id
        for combo in (self.person_filter, self.closed_person_combo):
            blocked = combo.blockSignals(True)
            if person_id is not None:
                combo.set_person_id(person_id)
            else:
                combo.reset()
            combo.blockSignals(blocked)
        self._close_drawer()
        if self.analytics is not None:
            if self.view_mode == "dashboard":
                self.refresh_dashboard()
            else:
                self.refresh_detail()

    def _on_closed_person_changed(self):
        p_id = self.closed_person_combo.currentData()
        self._selected_person_filter = p_id
        self.person_filter.blockSignals(True)
        if p_id is not None:
            self.person_filter.set_person_id(p_id)
        else:
            self.person_filter.reset()
        self.person_filter.blockSignals(False)
        self._close_drawer()
        self.refresh_detail()

    def _on_closed_dashboard_broker_clicked(self, broker_name: str):
        current_b = channel_label(self.broker_combo.currentData() or "").casefold()
        target = channel_label(broker_name).casefold() if broker_name else ""
        new_val = "" if (not target or target == current_b) else channel_label(broker_name)
        if self._has_global_filter() and hasattr(self.main_window, "sync_broker_from_holdings"):
            self.main_window.sync_broker_from_holdings(new_val)
            return
        blocked = self.broker_combo.blockSignals(True)
        if not new_val:
            self.broker_combo.setCurrentIndex(0)
        else:
            idx = 0
            for i in range(self.broker_combo.count()):
                txt = str(self.broker_combo.itemData(i) or self.broker_combo.itemText(i)).casefold()
                if txt == target:
                    idx = i
                    break
            self.broker_combo.setCurrentIndex(idx)
        self.broker_combo.blockSignals(blocked)
        self._close_drawer()
        self.refresh_detail()

    def _on_structure_clicked(self, pnl_status: str):
        self._closed_pnl_filter = pnl_status or ""
        self._close_drawer()
        self.refresh_detail()

    def _on_closed_ticker_clicked(self, ticker: str):
        self._closed_ticker_filter = (ticker or "").strip().upper()
        self._close_drawer()
        self.refresh_detail()

    def _on_open_ticker_clicked(self, ticker: str):
        self._open_ticker_filter = (ticker or "").strip().upper()
        self._close_drawer()
        self.refresh_detail()

    def _on_closed_trend_window_changed(self, window: str):
        self._closed_trend_window = window or "all"
        self.refresh_detail()

    def _sync_combo_by_data(self, combo: QComboBox, value: str):
        blocked = combo.blockSignals(True)
        norm = channel_label(value).casefold() if value else ""
        target_idx = 0
        if norm:
            for i in range(combo.count()):
                d_val = str(combo.itemData(i) or "").casefold()
                t_val = combo.itemText(i).casefold()
                if norm in (d_val, t_val) or str(value).strip().casefold() in (d_val, t_val):
                    target_idx = i
                    break
            else:
                disp = channel_label(value)
                combo.addItem(disp, disp)
                target_idx = combo.count() - 1
        combo.setCurrentIndex(target_idx)
        combo.blockSignals(blocked)

    def _on_broker_combo_changed(self, *_):
        val = str(self.broker_combo.currentData() or "")
        self._sync_combo_by_data(self.open_broker_combo, val)
        self._open_page = 1
        if self.view_mode == "dashboard":
            self.refresh_dashboard()
        else:
            self._close_drawer()
            self.refresh_detail()

    def _on_open_broker_combo_changed(self, *_):
        val = str(self.open_broker_combo.currentData() or "")
        self._sync_combo_by_data(self.broker_combo, val)
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _on_open_type_combo_changed(self, *_):
        self._open_stock_type = str(self.open_type_combo.currentData() or "")
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _on_open_industry_combo_changed(self, *_):
        self._open_industry = str(self.open_industry_combo.currentData() or "")
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _on_open_currency_combo_changed(self, *_):
        self._open_currency = ""
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _on_search_input_changed(self, text: str):
        self._open_page = 1
        self._on_filter_changed()

    def _on_open_search_input_changed(self, text: str):
        self._on_search_input_changed(text)

    def _on_local_reset_clicked(self):
        closed = self.status_tabs.currentIndex() == 1
        self.local_filter_bar.reset_current_mode()
        if closed:
            self._closed_pnl_filter = ""
            self._closed_ticker_filter = ""
            self.structure_chip.hide()
            self.closed_ticker_chip.hide()
            self._on_filter_changed()
        else:
            self._open_stock_type = ""
            self._open_industry = ""
            self._open_currency = ""
            self._open_pnl_filter = "all"
            self._open_ticker_filter = ""
            self._open_person_drill_id = None
            self._open_person_drill_name = ""
            self.active_industry_chip.hide()
            self.open_ticker_chip.hide()
            self._update_open_pnl_btn_styles()
            self._open_page = 1
            self._close_drawer()
            self.refresh_detail()

    def _reset_open_filters(self):
        self._on_local_reset_clicked()

    def _on_open_broker_donut_clicked(self, broker_name: str):
        current_b = channel_label(self.broker_combo.currentData() or "").casefold()
        target = channel_label(broker_name).casefold() if broker_name else ""
        new_val = "" if (not target or target == current_b) else channel_label(broker_name)
        if self._has_global_filter() and hasattr(self.main_window, "sync_broker_from_holdings"):
            self.main_window.sync_broker_from_holdings(new_val)
            return
        self._sync_combo_by_data(self.broker_combo, new_val)
        self._sync_combo_by_data(self.open_broker_combo, new_val)
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _on_open_industry_donut_clicked(self, industry_name: str):
        current_i = (self._open_industry or "").strip().casefold()
        target = (industry_name or "").strip()
        if not target or target.casefold() == current_i:
            self._open_industry = ""
        else:
            self._open_industry = target
        blocked = self.open_industry_combo.blockSignals(True)
        idx = 0
        if self._open_industry:
            for i in range(self.open_industry_combo.count()):
                if str(self.open_industry_combo.itemData(i) or "").casefold() == self._open_industry.casefold():
                    idx = i
                    break
            else:
                self.open_industry_combo.addItem(self._open_industry, self._open_industry)
                idx = self.open_industry_combo.count() - 1
        self.open_industry_combo.setCurrentIndex(idx)
        self.open_industry_combo.blockSignals(blocked)
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _on_open_trend_window_changed(self, window: str):
        self._open_trend_window = window or "ytd"
        self.refresh_detail()

    def _on_open_pnl_tab_clicked(self, key: str):
        self._open_pnl_filter = key if key in ("all", "profit", "loss") else "all"
        self._open_page = 1
        self._update_open_pnl_btn_styles()
        self._close_drawer()
        self.refresh_detail()

    def _update_open_pnl_btn_styles(self):
        for key, btn in self._open_pnl_btns.items():
            active = key == self._open_pnl_filter
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            if active:
                if key == "profit":
                    style = (
                        "QPushButton { background: #16A34A; color: #FFFFFF; border: 1px solid #16A34A; "
                        "border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 700; }"
                    )
                elif key == "loss":
                    style = (
                        "QPushButton { background: #DC2626; color: #FFFFFF; border: 1px solid #DC2626; "
                        "border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 700; }"
                    )
                else:
                    style = (
                        "QPushButton { background: #2563EB; color: #FFFFFF; border: 1px solid #2563EB; "
                        "border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 700; }"
                    )
            else:
                if key == "profit":
                    style = (
                        "QPushButton { background: #F0FDF4; color: #16A34A; border: 1px solid #BBF7D0; "
                        "border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 600; }"
                        "QPushButton:hover { background: #DCFCE7; }"
                    )
                elif key == "loss":
                    style = (
                        "QPushButton { background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; "
                        "border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 600; }"
                        "QPushButton:hover { background: #FEE2E2; }"
                    )
                else:
                    style = (
                        "QPushButton { background: #EFF6FF; color: #2563EB; border: 1px solid #BFDBFE; "
                        "border-radius: 6px; padding: 4px 12px; font-size: 12px; font-weight: 600; }"
                        "QPushButton:hover { background: #DBEAFE; }"
                    )
            btn.setStyleSheet(style)

    def _on_open_page_size_changed(self, *_):
        sz = self.open_page_size_combo.currentData()
        self._open_page_size = int(sz) if sz else 50
        self._open_page = 1
        self._apply_open_pagination()

    def _on_open_page_step(self, delta: int):
        total_rows = len(self._open_all_rows)
        total_pages = max(1, (total_rows + self._open_page_size - 1) // self._open_page_size)
        new_page = max(1, min(total_pages, self._open_page + delta))
        if new_page != self._open_page:
            self._open_page = new_page
            self._apply_open_pagination()

    def _on_detail_header_clicked(self, column: int):
        if self._detail_mode == "open" and self._open_all_rows:
            if 0 <= column < len(self.detail_model.columns) and self.detail_model.columns[column] in ("详情", "操作"):
                return
            if self.detail_model.sort_column == column:
                self.detail_model.sort_order = (
                    Qt.SortOrder.DescendingOrder
                    if self.detail_model.sort_order == Qt.SortOrder.AscendingOrder
                    else Qt.SortOrder.AscendingOrder
                )
            else:
                self.detail_model.sort_column = column
                self.detail_model.sort_order = Qt.SortOrder.DescendingOrder
            self._apply_open_pagination()
        else:
            self.detail_model.toggle_sort(column)

    def _apply_open_pagination(self):
        self.detail_model.replace(self._open_all_rows)
        sorted_rows = list(self.detail_model.rows)
        self._open_all_rows = sorted_rows
        total_rows = len(sorted_rows)
        page_size = max(1, self._open_page_size)
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        self._open_page = max(1, min(total_pages, self._open_page))
        start_idx = (self._open_page - 1) * page_size
        end_idx = start_idx + page_size
        page_rows = sorted_rows[start_idx:end_idx]

        if len(page_rows) != len(sorted_rows):
            self.detail_model.beginResetModel()
            self.detail_model.rows = tuple(page_rows)
            self.detail_model.endResetModel()

        self.open_page_lbl.setText(f"{self._open_page} / {total_pages}")
        self.open_prev_btn.setEnabled(self._open_page > 1)
        self.open_next_btn.setEnabled(self._open_page < total_pages)

    def switch_view_mode(self, mode: str):
        self.view_mode = mode
        self.mode_dash_btn.setChecked(mode == "dashboard")
        self.mode_detail_btn.setChecked(mode == "detail")
        self.stack.setCurrentIndex(0 if mode == "dashboard" else 1)
        if mode == "dashboard":
            self.clear_detail_filters()
            self.refresh_dashboard()
        else:
            self.refresh_detail()

    def _on_status_combo_changed(self, idx: int):
        target_tab = 1 if self.status_combo.currentData() == "已清仓" else 0
        blocked = self.status_tabs.blockSignals(True)
        self.status_tabs.setCurrentIndex(target_tab)
        self.status_tabs.blockSignals(blocked)
        self._on_filter_changed()

    def _on_status_tab_changed(self, idx: int):
        target_status = "已清仓" if idx == 1 else "持有中"
        s_idx = self.status_combo.findData(target_status)
        blocked = self.status_combo.blockSignals(True)
        self.status_combo.setCurrentIndex(s_idx if s_idx >= 0 else 0)
        self.status_combo.blockSignals(blocked)
        self._on_filter_changed()

    def set_view(self, mode: str, dimension: str = "person", status: str = "持有中", person_id: int | None = None, broker: str | None = None, ticker: str | None = None):
        self.dimension = dimension
        norm_status = "已清仓" if status == "已清仓" else "持有中"
        blocked_s = self.status_combo.blockSignals(True)
        s_idx = self.status_combo.findData(norm_status)
        self.status_combo.setCurrentIndex(s_idx if s_idx >= 0 else 0)
        self.status_combo.blockSignals(blocked_s)

        blocked_t = self.status_tabs.blockSignals(True)
        self.status_tabs.setCurrentIndex(1 if norm_status == "已清仓" else 0)
        self.status_tabs.blockSignals(blocked_t)

        self._sync_combo_by_data(self.broker_combo, broker or "")
        self._sync_combo_by_data(self.open_broker_combo, broker or "")

        for line_edit in (self.search_input, self.open_search_input):
            blocked_q = line_edit.blockSignals(True)
            if ticker:
                line_edit.setText(ticker)
            else:
                line_edit.clear()
            line_edit.blockSignals(blocked_q)

        if person_id is not None:
            self._selected_person_filter = person_id
            self.person_filter.blockSignals(True)
            self.person_filter.set_person_id(person_id)
            self.person_filter.blockSignals(False)
        self._open_page = 1
        self.switch_view_mode(mode)

    def _on_filter_changed(self):
        self._close_drawer()
        self.refresh_detail()

    def open_closed_positions(self):
        self.closed_mode.setCurrentIndex(0)
        blocked_t = self.status_tabs.blockSignals(True)
        self.status_tabs.setCurrentIndex(1)
        self.status_tabs.blockSignals(blocked_t)
        self.status_combo.setCurrentIndex(self.status_combo.findData("已清仓"))
        self.switch_view_mode("detail")

    def _is_multi_person_scope(self) -> bool:
        """判断当前人员筛选是否为『全部启用人员/全部历史人员』(None)、『他人』('OTHERS') 或多选人员。"""
        selected = self._selected_person_filter
        if selected is None and self.main_window and hasattr(self.main_window, "person"):
            try:
                selected = self.main_window.person.currentData()
            except Exception:
                pass
        if isinstance(selected, int):
            return False
        if isinstance(selected, (tuple, list)):
            return len(selected) != 1
        if selected == "OTHERS":
            return True
        if selected is None:
            scope = getattr(self.analytics, "scope", None) if self.analytics else None
            scope_pids = getattr(scope, "person_ids", None) if scope else None
            if scope_pids is not None and len(scope_pids) == 1:
                return False
            return True
        return True

    def _populate_open_person_summary_table(self, person_summary: list[dict[str, Any]]):
        """填充持有中『各人员持仓情况』汇总表，并附上合计行。"""
        def to_dec(val):
            return Decimal(str(val)) if val is not None else None

        p_rows: list[ReportRow] = []
        tot_mv = Decimal(0)
        tot_cost = Decimal(0)
        tot_pnl = Decimal(0)
        tot_stocks = 0
        tot_profit = 0
        tot_loss = 0

        for pr in person_summary:
            p_mv = to_dec(pr.get("market_value")) or Decimal(0)
            p_cost = to_dec(pr.get("cost_amount")) or Decimal(0)
            p_pnl = to_dec(pr.get("floating_pnl")) or Decimal(0)
            p_rate = to_dec(pr.get("return_rate"))
            p_weight = to_dec(pr.get("weight")) or Decimal(0)
            s_cnt = int(pr.get("stock_count") or 0)
            w_cnt = int(pr.get("profit_count") or 0)
            l_cnt = int(pr.get("loss_count") or 0)

            tot_mv += p_mv
            tot_cost += p_cost
            tot_pnl += p_pnl
            tot_stocks += s_cnt
            tot_profit += w_cnt
            tot_loss += l_cnt

            p_rows.append(ReportRow((
                pr.get("person_name") or pr.get("name") or "未知人员",
                p_mv,
                p_cost,
                p_pnl,
                p_rate,
                p_weight,
                s_cnt,
                w_cnt,
                l_cnt,
            ), {"person_id": pr.get("person_id")}))

        if p_rows:
            tot_rate = ratio(tot_pnl, tot_cost) if tot_cost > 0 else None
            p_rows.append(ReportRow((
                "合计",
                tot_mv,
                tot_cost,
                tot_pnl,
                tot_rate,
                Decimal(1) if tot_mv > 0 else Decimal(0),
                tot_stocks,
                tot_profit,
                tot_loss,
            ), {"is_total": True}))

        self.open_person_model.replace(p_rows)
        row_cnt = max(1, len(p_rows))
        table_h = min(260, 38 + row_cnt * 34 + 6)
        self.open_person_table.setFixedHeight(table_h)
        self.open_person_table.clearSelection()
        if self._open_person_drill_id is not None:
            for row_idx, r in enumerate(p_rows):
                if r.metadata.get("person_id") == self._open_person_drill_id:
                    self.open_person_table.selectRow(row_idx)
                    break

    def _on_open_person_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.open_person_model.rows[index.row()]
        if row.values[0] == "合计" or row.metadata.get("is_total"):
            return
        pid = row.metadata.get("person_id")
        pname = str(row.values[0] or "")
        if self._open_person_drill_id == pid:
            self._open_person_drill_id = None
            self._open_person_drill_name = ""
        else:
            self._open_person_drill_id = pid
            self._open_person_drill_name = pname
        self._open_page = 1
        self._close_drawer()
        self.refresh_detail()

    def _closed_person_ids(self):
        selected = self._selected_person_filter
        if selected == "OTHERS":
            return tuple(person["id"] for person in self._get_persons()
                         if person["name"] != "个人" and (person.get("enabled", True) or self.analytics.scope.include_disabled))
        if isinstance(selected, (tuple, list)):
            return tuple(selected)
        return None if selected is None else (selected,)

    def _closed_account_analytics(self):
        selected = self._closed_person_ids()
        scope = self.analytics.scope
        if selected is None:
            return self.analytics
        if scope.person_ids is not None:
            selected = tuple(person for person in selected if person in scope.person_ids)
        if not selected:
            return None
        if scope.person_ids == selected:
            return self.analytics
        if self.main_window is None or self.main_window.analytics_service is None:
            return None
        return self.main_window.analytics_service.calculate(replace(scope, person_ids=selected))

    def _on_dist_tab_changed(self, idx):
        if not hasattr(self, "_cached_dash_data") or not self._cached_dash_data:
            return
        keys = ("by_person", "by_broker", "by_stock")
        k = keys[idx] if idx < len(keys) else "by_person"
        dist = self._cached_dash_data.get("distribution", {}).get(k, [])
        cards = self._cached_dash_data.get("cards", {})
        total_mv = float(cards.get("total_market_value") or 0)
        self.dist_donut.set_data(total_mv, dist)

    def clear_detail_filters(self):
        self._selected_person_filter = None
        self._closed_pnl_filter = ""
        self._closed_ticker_filter = ""
        self._open_ticker_filter = ""
        self._open_stock_type = ""
        self._open_industry = ""
        self._open_currency = ""
        self._open_pnl_filter = "all"
        self._open_page = 1
        for control, reset in ((self.person_filter, self.person_filter.reset),
                               (self.closed_person_combo, self.closed_person_combo.reset),
                               (self.broker_combo, lambda: self.broker_combo.setCurrentIndex(0)),
                               (self.open_broker_combo, lambda: self.open_broker_combo.setCurrentIndex(0)),
                               (self.open_type_combo, lambda: self.open_type_combo.setCurrentIndex(0)),
                               (self.open_industry_combo, lambda: self.open_industry_combo.setCurrentIndex(0)),
                               (self.open_currency_combo, lambda: self.open_currency_combo.setCurrentIndex(0)),
                               (self.close_date_combo, lambda: self.close_date_combo.setCurrentIndex(0)),
                               (self.search_input, self.search_input.clear),
                               (self.open_search_input, self.open_search_input.clear)):
            blocked = control.blockSignals(True)
            reset()
            control.blockSignals(blocked)
        self._update_open_pnl_btn_styles()

    def set_analytics(self, analytics):
        self.analytics = analytics
        if self.person_filter.count() <= 1:
            self._populate_persons()
        if self._has_global_filter():
            self._apply_global_filter_to_local(self.main_window.global_asset_filter.state())
        self.refresh_dashboard()
        if self.view_mode != "dashboard":
            self.refresh_detail()

    def refresh_dashboard(self):
        if not self.analytics:
            return
        t0 = time.monotonic()
        broker_filter = self.broker_combo.currentData() or None
        dash = HoldingsManagementService.get_dashboard_data(
            self.analytics,
            person_id=self._selected_person_filter,
            broker=broker_filter,
        )
        self._cached_dash_data = dash

        # 1. Cards
        cards = dash.get("cards", {})
        mv = float(cards.get("total_market_value") or 0)
        fpnl = float(cards.get("floating_pnl") or 0)
        frate = float(cards.get("floating_pnl_rate") or 0)
        fpnl_color = "#16A34A" if fpnl > 0 else "#DC2626" if fpnl < 0 else "#64748B"
        sub_mv = f"{fpnl:+,.2f} ({frate*100:+.2f}%)" if fpnl != 0 else "—"
        self.card_mv.set_content(f"{mv:,.2f}", sub_mv, fpnl_color)

        tot_s = cards.get("total_stock_count") or 0
        op_s = cards.get("open_stock_count") or 0
        cl_s = cards.get("closed_stock_count") or 0
        self.card_count.set_content(f"{tot_s}", f"当前持仓 {op_s} | 已清仓 {cl_s}")

        cpnl = float(cards.get("cumulative_pnl") or 0)
        crate = float(cards.get("cumulative_pnl_rate") or 0)
        cpnl_color = "#16A34A" if cpnl > 0 else "#DC2626" if cpnl < 0 else "#64748B"
        self.card_pnl.set_content(f"{cpnl:+,.2f}", f"收益率 {crate*100:+.2f}%", cpnl_color)

        inv = float(cards.get("total_invested") or 0)
        rpnl = float(cards.get("realized_pnl") or 0)
        self.card_invest.set_content(f"{inv:,.2f}", f"已实现 {rpnl:,.2f} | 浮动 {fpnl:,.2f}")

        # 2. Tables
        # 人员持仓明细
        p_recs = dash.get("person_summary", [])
        p_rows = []
        tot_p_mv = Decimal(0)
        tot_p_principal = Decimal(0)
        tot_p_pnl = Decimal(0)
        tot_p_stocks = 0
        tot_p_open = 0
        tot_p_closed = 0
        for pr in p_recs:
            p_mv = Decimal(str(pr.get("market_value") or 0))
            p_principal = Decimal(str(pr.get("principal") if pr.get("principal") is not None else (p_mv - Decimal(str(pr.get("total_pnl") or 0)))))
            p_pnl = Decimal(str(pr.get("total_pnl") or 0))
            tot_p_mv += p_mv
            tot_p_principal += p_principal
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
                p_principal,
                p_pnl,
                Decimal(str(pr.get("pnl_rate") or 0)) if pr.get("pnl_rate") is not None else None,
                ts,
                os,
                cs,
            ), {"person_id": pr.get("person_id")}))

        if p_rows:
            p_base = tot_p_principal if tot_p_principal > 0 else (tot_p_mv - tot_p_pnl)
            p_tot_rate = ratio(tot_p_pnl, p_base) if p_base > 0 else None
            p_rows.append(ReportRow((
                "合计",
                tot_p_mv,
                tot_p_principal,
                tot_p_pnl,
                p_tot_rate,
                tot_p_stocks,
                tot_p_open,
                tot_p_closed,
            ), {"is_total": True}))
        p_scroll = self.p_table.verticalScrollBar().value()
        b_scroll = self.b_table.verticalScrollBar().value()
        s_scroll = self.s_table.verticalScrollBar().value()

        # 券商持仓汇总
        b_recs = dash.get("broker_summary", [])
        b_rows = []
        tot_b_mv = Decimal(0)
        tot_b_principal = Decimal(0)
        tot_b_pnl = Decimal(0)
        for br in b_recs:
            b_mv = Decimal(str(br.get("market_value") or 0))
            b_pnl = Decimal(str(br.get("total_pnl") or 0))
            b_principal = Decimal(str(br.get("principal") if br.get("principal") is not None else (b_mv - b_pnl)))
            tot_b_mv += b_mv
            tot_b_principal += b_principal
            tot_b_pnl += b_pnl
            b_rows.append(ReportRow((
                br.get("channel"),
                b_mv,
                b_principal,
                b_pnl,
                Decimal(str(br.get("pnl_rate") or 0)) if br.get("pnl_rate") is not None else None,
            ), {}))
        if b_rows:
            b_base = tot_b_principal if tot_b_principal > 0 else (tot_b_mv - tot_b_pnl)
            b_tot_rate = ratio(tot_b_pnl, b_base) if b_base > 0 else None
            b_rows.append(ReportRow((
                "合计",
                tot_b_mv,
                tot_b_principal,
                tot_b_pnl,
                b_tot_rate,
            ), {"is_total": True}))

        # 股票持仓汇总
        s_recs = dash.get("stock_summary", [])
        s_rows = []
        for sr in s_recs[:10]:
            s_mv = Decimal(str(sr.get("market_value") or 0))
            s_pnl = Decimal(str(sr.get("total_pnl") or 0))
            s_principal = Decimal(str(sr.get("principal") if sr.get("principal") is not None else (s_mv - s_pnl)))
            s_rows.append(ReportRow((
                sr.get("ticker"),
                s_mv,
                s_principal,
                s_pnl,
                Decimal(str(sr.get("pnl_rate") or 0)) if sr.get("pnl_rate") is not None else None,
            ), {}))

        self.p_model.replace(p_rows)
        self.b_model.replace(b_rows)
        self.s_model.replace(s_rows)

        self.p_table.verticalScrollBar().setValue(p_scroll)
        self.b_table.verticalScrollBar().setValue(b_scroll)
        self.s_table.verticalScrollBar().setValue(s_scroll)

        # 3. Donut Ring Chart
        self._on_dist_tab_changed(self.dist_tabs.currentIndex())
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[桌面端持仓] 仪表板总览刷新完成，耗时 {elapsed_ms:.1f}ms")

    def refresh_detail(self):
        if not self.analytics:
            return
        t0 = time.monotonic()
        status = self.status_combo.currentData() or "持有中"
        status_filter = status
        broker_filter = self.broker_combo.currentData() or None
        search_filter = self.search_input.text().strip() or None
        global_ticker = self._get_global_ticker()
        src_path = str(DATABASE_PATH)
        if self.main_window and hasattr(self.main_window, "service") and self.main_window.service:
            src_path = str(self.main_window.service.path)

        closed = status_filter == "已清仓"
        blocked_t = self.status_tabs.blockSignals(True)
        self.status_tabs.setCurrentIndex(1 if closed else 0)
        self.status_tabs.blockSignals(blocked_t)
        self.closed_sep.setVisible(closed)
        self.closed_mode.setVisible(closed)
        self.closed_broker_lbl.hide()
        self.broker_combo.hide()
        self.closed_person_lbl.hide()
        self.closed_person_combo.hide()

        # 顶部 KPI 栏与局部筛选栏
        self.open_kpi_bar.setVisible(not closed)
        self.closed_kpi_bar.setVisible(closed)
        self.local_filter_bar.setVisible(True)
        self.local_filter_bar.set_mode("closed" if closed else "open")

        # 图表切换
        self.closed_dashboard.setVisible(closed)
        self.open_charts_row.setVisible(not closed)
        self.open_top10_card.setVisible(not closed)
        if closed:
            self.open_person_card.hide()
        self.open_table_header.setVisible(not closed)
        self.open_table_footer.setVisible(not closed)
        self.detail_info.setVisible(closed)
        self.detail_table.setMinimumHeight(360 if closed else 340)
        mode = ("account" if self.closed_mode.currentIndex() == 1 else "person") if closed else "open"
        account_ids = self.main_window.accounts.selected_ids() if closed and self.main_window else None
        open_payload = None
        if mode == "account":
            try:
                account_analytics = self._closed_account_analytics()
                report = account_analytics.reports.get("清仓收益") if account_analytics else None
                if report and self.main_window and self.main_window.quote_store:
                    from datetime import datetime, timezone
                    from portfolio.services.closed_quotes import closed_current_prices
                    now = datetime.now(timezone.utc)
                    instruments = {item["id"]: item for item in self.main_window.info.instruments}
                    report = closed_current_prices(report, instruments, self.main_window.quote_store.load(now)[0],
                                                   now, currency=account_analytics.scope.reporting_currency)
                if closed and report:
                    self._sync_closed_broker_options(closed_account_rows(report, account_ids=account_ids, analytics=account_analytics, db_path=src_path))
                raw_rows = closed_account_rows(report, broker=broker_filter, ticker=global_ticker or None, search=search_filter,
                                               account_ids=account_ids, analytics=account_analytics, db_path=src_path) if report else []
            except Exception:
                logger.exception("读取已清仓账户汇总失败")
                self.detail_model.replace(())
                self.detail_info.setText("读取账户汇总失败，请刷新重试。")
                return
        elif closed:
            raw_rows = HoldingsManagementService.get_holdings_detail_rows(
                self.analytics, dimension=self.dimension, person_id=self._closed_person_ids(),
                broker=broker_filter, ticker=global_ticker or None, status=status_filter, search=search_filter, db_path=src_path,
                account_ids=account_ids,
            )
            if not broker_filter and not search_filter:
                self._sync_closed_broker_options(raw_rows)
            else:
                unfiltered_for_brokers = HoldingsManagementService.get_holdings_detail_rows(
                    self.analytics, dimension=self.dimension, person_id=self._closed_person_ids(),
                    broker=None, status=status_filter, search=None, db_path=src_path,
                    account_ids=account_ids,
                )
                self._sync_closed_broker_options(unfiltered_for_brokers)
        else:
            open_analytics = self._closed_account_analytics() or self.analytics
            all_open_rows = HoldingsManagementService.get_holdings_detail_rows(
                open_analytics,
                dimension=self.dimension,
                person_id=self._closed_person_ids(),
                broker=None,
                status="持有中",
                search=None,
                db_path=src_path,
            )
            open_payload = build_open_holdings_dashboard(
                open_analytics,
                all_open_rows,
                db_path=src_path,
                person_id=self._closed_person_ids(),
                broker=broker_filter,
                stock_type=self._open_stock_type,
                industry=self._open_industry,
                currency=self._open_currency,
                search=search_filter,
                pnl_filter=self._open_pnl_filter,
                trend_window=self._open_trend_window,
            )
            raw_rows = open_payload.get("rows", [])
            self._sync_open_filter_options(open_payload.get("available_filters", {}))

        if closed:
            self.active_industry_chip.hide()
            self.open_top10_card.hide()
            self.open_ticker_chip.hide()
            date_key = self.close_date_combo.currentData() or "all"
            start_d, end_d = resolve_close_date_bounds(date_key)
            scoped_closed_rows = filter_closed_rows(raw_rows, start_date=start_d, end_date=end_d)
            dash_analytics = self._closed_account_analytics() if mode == "account" else self.analytics
            dash_payload = build_closed_dashboard_analytics(
                dash_analytics or self.analytics,
                scoped_closed_rows,
                mode=mode,
                trend_window=self._closed_trend_window,
            )
            self.closed_dashboard.set_data(
                dash_payload,
                active_broker=channel_label(broker_filter) if broker_filter else "",
                active_structure=self._closed_pnl_filter,
                trend_window=self._closed_trend_window,
                active_ticker=self._closed_ticker_filter,
            )
            raw_rows = filter_closed_rows(scoped_closed_rows, pnl_status=self._closed_pnl_filter)
            if self._closed_ticker_filter:
                raw_rows = [
                    r for r in raw_rows
                    if str(r.get("ticker") or "").strip().upper() == self._closed_ticker_filter
                ]
                self.closed_ticker_chip.setText(f"股票筛选: {self._closed_ticker_filter} ✕")
                self.closed_ticker_chip.show()
            else:
                self.closed_ticker_chip.hide()
            if self._closed_pnl_filter:
                lbl_map = {"win": "盈利股票", "flat": "持平股票", "loss": "亏损股票"}
                self.structure_chip.setText(f"盈亏筛选: {lbl_map.get(self._closed_pnl_filter, self._closed_pnl_filter)} ✕")
                self.structure_chip.show()
            else:
                self.structure_chip.hide()
        else:
            self.structure_chip.hide()
            self.closed_ticker_chip.hide()
            if open_payload is not None:
                self.open_kpi_bar.set_cards_data(open_payload.get("cards", {}))
                self.open_charts_row.set_charts_data(
                    open_payload,
                    active_broker=channel_label(broker_filter) if broker_filter else "",
                    active_industry=self._open_industry,
                    trend_window=self._open_trend_window,
                )
                self.open_top10_card.set_data(
                    open_payload.get("ticker_top10") or {},
                    active_ticker=self._open_ticker_filter,
                )
                if self._open_ticker_filter:
                    raw_rows = [
                        r for r in raw_rows
                        if str(r.get("ticker") or "").strip().upper() == self._open_ticker_filter
                    ]
                    self.open_ticker_chip.setText(f"股票筛选: {self._open_ticker_filter} ✕")
                    self.open_ticker_chip.show()
                else:
                    self.open_ticker_chip.hide()
                t_counts = open_payload.get("table_counts", {})
                c_all = int(t_counts.get("all") or 0)
                c_prof = int(t_counts.get("profit") or 0)
                c_loss = int(t_counts.get("loss") or 0)
                self.open_table_title_lbl.setText(f"持仓明细 (共 {c_all} 只)")
                self._open_pnl_btns["all"].setText(f"全部 ({c_all})")
                self._open_pnl_btns["profit"].setText(f"盈利 ({c_prof})")
                self._open_pnl_btns["loss"].setText(f"亏损 ({c_loss})")
                self._update_open_pnl_btn_styles()

                t_sum = open_payload.get("table_summary", {})
                s_mv = float(t_sum.get("market_value") or 0.0)
                s_cost = float(t_sum.get("cost_amount") or 0.0)
                s_pnl = float(t_sum.get("floating_pnl") or 0.0)
                s_rate = float(t_sum.get("return_rate") or 0.0)
                pnl_col = "#16A34A" if s_pnl > 0 else "#DC2626" if s_pnl < 0 else "#475569"
                self.open_summary_lbl.setText(
                    f"合计：持仓市值 <span style='color:#0F172A;'>{s_mv:,.2f}</span>，"
                    f"持仓成本 <span style='color:#0F172A;'>{s_cost:,.2f}</span>，"
                    f"浮动盈亏 <span style='color:{pnl_col};'>{s_pnl:+,.2f}</span>，"
                    f"持仓收益率 <span style='color:{pnl_col};'>{s_rate * 100:+.2f}%</span>"
                )
                person_summary = open_payload.get("person_summary", [])
                show_person_card = self._is_multi_person_scope() and bool(person_summary)
                if not show_person_card:
                    self._open_person_drill_id = None
                    self._open_person_drill_name = ""
                elif self._open_person_drill_id is not None and not any(
                    pr.get("person_id") == self._open_person_drill_id for pr in person_summary
                ):
                    self._open_person_drill_id = None
                    self._open_person_drill_name = ""
                self.open_person_card.setVisible(show_person_card)
                if show_person_card:
                    self._populate_open_person_summary_table(person_summary)
                if self._open_person_drill_id is not None:
                    raw_rows = [r for r in raw_rows if r.get("person_id") == self._open_person_drill_id]
                    drill_all = len(raw_rows)
                    drill_prof = sum(1 for r in raw_rows if float(r.get("total_pnl") or 0) > 0)
                    drill_loss = sum(1 for r in raw_rows if float(r.get("total_pnl") or 0) < 0)
                    self.open_table_title_lbl.setText(f"持仓明细 · {self._open_person_drill_name} (共 {drill_all} 只)")
                    self._open_pnl_btns["all"].setText(f"全部 ({drill_all})")
                    self._open_pnl_btns["profit"].setText(f"盈利 ({drill_prof})")
                    self._open_pnl_btns["loss"].setText(f"亏损 ({drill_loss})")
                    d_mv = sum(float(r.get("market_value") or 0) for r in raw_rows)
                    d_cost = sum(float(r.get("cost_amount") or 0) for r in raw_rows)
                    d_pnl = sum(float(r.get("total_pnl") or 0) for r in raw_rows)
                    d_rate = (d_pnl / d_cost) if d_cost > 0 else 0.0
                    d_col = "#16A34A" if d_pnl > 0 else "#DC2626" if d_pnl < 0 else "#475569"
                    self.open_summary_lbl.setText(
                        f"合计：持仓市值 <span style='color:#0F172A;'>{d_mv:,.2f}</span>，"
                        f"持仓成本 <span style='color:#0F172A;'>{d_cost:,.2f}</span>，"
                        f"浮动盈亏 <span style='color:{d_col};'>{d_pnl:+,.2f}</span>，"
                        f"持仓收益率 <span style='color:{d_col};'>{d_rate * 100:+.2f}%</span>"
                    )
            if self._open_industry:
                self.active_industry_chip.setText(f"类型: {self._open_industry} ✕")
                self.active_industry_chip.show()
            else:
                self.active_industry_chip.hide()

        schema = closed_columns(mode) if closed else ()
        target_cols = tuple(label for _,label,_ in schema) if closed else OPEN_HOLDINGS_COLUMNS
        v_scroll = self.detail_table.verticalScrollBar().value()
        h_scroll = self.detail_table.horizontalScrollBar().value()
        if mode != self._detail_mode:
            self._detail_states[self._detail_mode] = (self.detail_model.sort_column, self.detail_model.sort_order, v_scroll, h_scroll)
            default_sort = (
                next((i for i,(key,_,_) in enumerate(schema) if key == "last_close_date"), -1)
                if closed else target_cols.index("持仓市值")
            )
            sort_column, sort_order, v_scroll, h_scroll = self._detail_states.get(
                mode, (default_sort, Qt.SortOrder.DescendingOrder, 0, 0)
            )
            if sort_column < 0 or sort_column >= len(target_cols):
                sort_column, sort_order = default_sort, Qt.SortOrder.DescendingOrder
            self.detail_model.sort_column = sort_column
            self.detail_model.sort_order = sort_order
            self._detail_mode = mode
        self.detail_model.columns = target_cols
        self.detail_model.nulls_last = closed

        def to_dec(val):
            return Decimal(str(val)) if val is not None else None

        rows = []
        if closed:
            for item in raw_rows:
                values = tuple(to_dec(item.get(key)) if kind in ("money", "gain", "rate", "quantity")
                               else item.get(key) for key,_,kind in schema)
                rows.append(ReportRow(values, item))
        else:
            for item in raw_rows:
                rows.append(ReportRow((
                    item.get("person_name"),
                    item.get("channel"),
                    item.get("ticker"),
                    item.get("industry") or "—",
                    to_dec(item.get("quantity")),
                    to_dec(item.get("cost_price")),
                    to_dec(item.get("current_price")),
                    to_dec(item.get("breakeven_needed_rate")),
                    to_dec(item.get("market_value")),
                    to_dec(item.get("cost_amount")),
                    to_dec(item.get("total_pnl")),
                    to_dec(item.get("pnl_rate")),
                    to_dec(item.get("weight")),
                    item.get("buy_count"),
                    item.get("sell_count"),
                    "查看 >",
                ), {
                    "person_id": item.get("person_id"),
                    "account_id": item.get("account_id"),
                    "instrument_id": item.get("instrument_id"),
                    "ticker": item.get("ticker"),
                    "currency": item.get("currency"),
                    "quote_status": item.get("quote_status"),
                    "quote_time": item.get("quote_time"),
                }))

        if broker_filter:
            b_disp = channel_label(broker_filter)
            self.active_broker_chip.setText(f"渠道: {b_disp} ✕")
            self.active_broker_chip.show()
        else:
            self.active_broker_chip.hide()

        if closed:
            self.detail_model.replace(rows)
        else:
            self._open_all_rows = list(rows)
            self._apply_open_pagination()
        self.detail_table.verticalScrollBar().setValue(v_scroll)
        self.detail_table.horizontalScrollBar().setValue(h_scroll)
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[桌面端持仓] 明细表格刷新完成 (状态: {status_filter})，耗时 {elapsed_ms:.1f}ms，共加载 {len(rows)} 条标的记录")
        view_title = ("已清仓 · " + self.closed_mode.tabText(self.closed_mode.currentIndex())) if closed else "当前持仓明细"
        broker_tag = f" · 渠道：{channel_label(broker_filter)}" if broker_filter else ""
        if not rows:
            self.detail_info.setText(f"{view_title}{broker_tag} · 没有符合筛选条件的记录")
            return
        note = "沿用原清仓收益报表的账本口径，操作笔数可包含划转" if mode == "account" else "点击任意行查看交易记录"
        self.detail_info.setText(f"{view_title}{broker_tag} · 共 {len(rows)} 条标的记录 · {note}")

    def _sync_open_filter_options(self, available_filters: dict[str, list[str]]):
        """根据真实持有中数据动态更新券商、股票类型、行业、币种下拉框选项。"""
        def _repopulate(combo: QComboBox, all_label: str, items: list[str], current_val: str):
            saved = current_val or ""
            merged = list(items)
            if saved and all(x.casefold() != saved.casefold() for x in merged):
                merged.append(saved)
            blocked = combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label, "")
            target_idx = 0
            for idx, val in enumerate(merged, start=1):
                combo.addItem(val, val)
                if saved and val.casefold() == saved.casefold():
                    target_idx = idx
            combo.setCurrentIndex(target_idx)
            combo.blockSignals(blocked)

        brokers = [channel_label(b) for b in available_filters.get("brokers", []) if b]
        cur_broker = channel_label(self.broker_combo.currentData() or "") if self.broker_combo.currentData() else ""
        _repopulate(self.broker_combo, "全部渠道", brokers, cur_broker)
        _repopulate(self.open_broker_combo, "全部", brokers, cur_broker)
        _repopulate(self.open_type_combo, "全部", available_filters.get("stock_types", []), self._open_stock_type)
        _repopulate(self.open_industry_combo, "全部", available_filters.get("industries", []), self._open_industry)
        _repopulate(self.open_currency_combo, "全部", available_filters.get("currencies", []), self._open_currency)

    def _sync_closed_broker_options(self, source_rows: list[dict[str, Any]]):
        """根据真实已清仓数据动态更新券商渠道下拉项，避免硬编码。"""
        channels = sorted({
            channel_label(str(r.get("channel") or "").split(" · #")[0].strip())
            for r in source_rows
            if r.get("channel")
        })
        saved = self.broker_combo.currentData() or ""
        if saved and channel_label(saved) not in channels:
            channels = sorted({*channels, channel_label(saved)})
        blocked = self.broker_combo.blockSignals(True)
        self.broker_combo.clear()
        self.broker_combo.addItem("全部渠道", "")
        for ch in channels:
            self.broker_combo.addItem(ch, ch)
        target_idx = 0
        if saved:
            norm = channel_label(saved).casefold()
            for i in range(1, self.broker_combo.count()):
                if str(self.broker_combo.itemData(i) or "").casefold() == norm:
                    target_idx = i
                    break
        self.broker_combo.setCurrentIndex(target_idx)
        self.broker_combo.blockSignals(blocked)

    def _on_person_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.p_model.rows[index.row()]
        if row.values[0] == "合计":
            return
        p_id = row.metadata.get("person_id")
        if self._has_global_filter() and hasattr(self.main_window, "sync_person_from_holdings"):
            self.set_view("detail", dimension="person")
            self.main_window.sync_person_from_holdings(p_id)
            return
        self.set_view("detail", dimension="person", person_id=p_id)

    def _on_broker_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.b_model.rows[index.row()]
        if row.values[0] == "合计":
            return
        broker_name = str(row.values[0])
        if self._has_global_filter() and hasattr(self.main_window, "sync_broker_from_holdings"):
            self.set_view("detail", dimension="broker", ticker=None)
            self.main_window.sync_broker_from_holdings(broker_name)
            return
        self.set_view("detail", dimension="broker", broker=broker_name, ticker=None)

    def _on_stock_table_clicked(self, index):
        if not index.isValid():
            return
        row = self.s_model.rows[index.row()]
        if row.values[0] == "合计":
            return
        ticker = str(row.values[0])
        if self._has_global_filter() and hasattr(self.main_window, "sync_ticker_from_holdings"):
            self.set_view("detail", dimension="stock", broker=None)
            self.main_window.sync_ticker_from_holdings(ticker)
            return
        self.set_view("detail", dimension="stock", broker=None, ticker=ticker)

    def _on_detail_row_clicked(self, index):
        if not index.isValid():
            return
        row = self.detail_model.rows[index.row()]
        ticker = row.metadata["ticker"]
        if self._detail_mode in ("account", "person"):
            self.open_stock_drawer(ticker, row=row.metadata)
        else:
            self.open_stock_drawer(ticker)

    def open_stock_drawer(self, ticker: str, *, row=None):
        if not ticker:
            return
        t0 = time.monotonic()
        src_path = str(DATABASE_PATH)
        if self.main_window and hasattr(self.main_window, "service") and self.main_window.service:
            src_path = str(self.main_window.service.path)
        account_mode = row is not None and self._detail_mode == "account"
        analytics = self._closed_account_analytics() if account_mode else self.analytics
        if analytics is None:
            return
        if row and analytics.scope.reporting_currency != "USD" and self.main_window and self.main_window.analytics_service:
            analytics = self.main_window.analytics_service.calculate(replace(analytics.scope, reporting_currency="USD"))
        drawer_data = HoldingsManagementService.get_stock_drawer_data(
            src_path,
            analytics,
            ticker,
            person_id=row.get("person_id") if row else self._selected_person_filter,
            broker=None if row else self.broker_combo.currentData() or None,
            account_id=row.get("account_id") if row else None,
            instrument_id=row.get("instrument_id") if row else None,
        )
        self.drawer.set_data(drawer_data)
        if self.drawer.isHidden():
            self.drawer.show()
            drawer_w = max(360, min(900, self._drawer_width))
            self.splitter.setSizes([max(200, self.width() - drawer_w), drawer_w])
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(f"[桌面端抽屉] 标的 {ticker} 抽屉展开就绪，耗时 {elapsed_ms:.1f}ms")

    def _on_splitter_moved(self, _pos: int, _index: int):
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self._drawer_width = sizes[1]

    def _close_drawer(self):
        sizes = self.splitter.sizes()
        if len(sizes) == 2 and sizes[1] > 0:
            self._drawer_width = sizes[1]
        self.drawer.hide()

    def _export_detail(self):
        source_rows = self._open_all_rows if self._detail_mode == "open" and self._open_all_rows else self.detail_model.rows
        if not source_rows:
            return
        name = {
            "account": "已清仓-按券商账户汇总.csv",
            "person": "已清仓-按人员明细.csv",
        }.get(self._detail_mode, "持仓明细.csv")
        path, _ = QFileDialog.getSaveFileName(self, "导出持仓明细", name, "CSV 文件 (*.csv)")
        if not path:
            return
        headers = self.detail_model.columns
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            for row in source_rows:
                if self._detail_mode in ("person", "account"):
                    writer.writerow(f"{value * 100:.2f}%" if kind == "rate" and value is not None else value
                                    for value, (_, _, kind) in zip(row.values, closed_columns(self._detail_mode)))
                else:
                    writer.writerow(row.values)
