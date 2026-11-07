"""Modern Monthly Performance Analysis Dashboard for Mac Desktop (PySide6).

Parity with Daily Performance:
1. 7 Metric KPI Cards with click-to-filter, drill-down and selection states.
2. Compact Filter Card: Person, broker, ticker, month range, category, search/reset & active chips.
3. Monthly Profit Trend (Bar/Line toggle) & Broker Channel Cumulative Overview with colored bars.
4. Full-width Monthly Attribution Module:
   - Selected Month PnL structure (Ring Chart + Profit/Loss stock count & amount, prev/next month nav)
   - Loss Contribution Ranking Top 5 (Rank, Ticker, Broker, Monthly Loss, Contribution Bar)
   - Profit Contribution Ranking Top 5 (Rank, Ticker, Broker, Monthly Profit, Contribution Bar)
5. Full-width Modern Data Tables:
   - Month Summary
   - Channel Details
   - Stock Details
   - Strict requirement: NO "操作" (Action) column, entire row is clickable.
   - Proportional viewport column widths, right-aligned numbers including 0.00, no currency dollar signs.
6. Right Slide-out Drawer for Month, Channel, and Stock history details.
"""
from datetime import date, datetime
from decimal import Decimal
import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QDate, QModelIndex, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from portfolio.paths import DATABASE_PATH
from portfolio.ui.icons import icon
from portfolio.ui.theme import numeric_font, tokens

logger = logging.getLogger(__name__)


def fmt_money(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        num = float(val)
        return f"{num:,.{digits}f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_signed_money(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        num = float(val)
        sign = "+" if num > 0 else "-" if num < 0 else ""
        return f"{sign}{abs(num):,.{digits}f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_percent(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        num = float(val) * 100
        sign = "+" if num > 0 else "-" if num < 0 else ""
        return f"{sign}{abs(num):.{digits}f}%"
    except (ValueError, TypeError):
        return str(val)


BROKER_NAME_MAP = {
    "LONGBRIDGE": "长桥",
    "LONG BRIDGE": "长桥",
    "FIRSTRADE": "第一证券",
    "SCHWAB": "嘉信",
    "CHARLES SCHWAB": "嘉信",
    "BBAE": "BBAE",
    "IBKR": "盈透证券",
    "INTERACTIVE BROKERS": "盈透证券",
    "FUTU": "富途证券",
    "TIGER": "老虎证券",
}


def fmt_broker_cn(name: Any) -> str:
    if not name:
        return "—"
    s = str(name).strip()
    return BROKER_NAME_MAP.get(s.upper(), s)


# -------------------------------------------------------------------------
# Custom KPI Metric Card
# -------------------------------------------------------------------------
class KpiMetricCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.title_text = title
        self.is_selected = False
        self.setObjectName("kpiCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-size: 11.5px; color: #64748B; font-weight: 500;")
        header_layout.addWidget(self.title_lbl)
        header_layout.addStretch()

        self.arrow_lbl = QLabel("›")
        self.arrow_lbl.setStyleSheet("font-size: 13px; color: #94A3B8; font-weight: bold;")
        header_layout.addWidget(self.arrow_lbl)
        layout.addLayout(header_layout)

        self.value_label = QLabel("—")
        self.value_label.setFont(numeric_font(15, bold=True))
        self.value_label.setStyleSheet("color: #0F172A;")
        layout.addWidget(self.value_label)

        self.sub_label = QLabel("—")
        self.sub_label.setStyleSheet("font-size: 11px; color: #94A3B8;")
        layout.addWidget(self.sub_label)

        self.update_style()

    def set_data(self, val: str, sub: str, status: Optional[str] = None):
        self.value_label.setText(val)
        self.sub_label.setText(sub)
        if status == "profit":
            self.value_label.setStyleSheet("color: #10B981;")
        elif status == "loss":
            self.value_label.setStyleSheet("color: #EF4444;")
        else:
            self.value_label.setStyleSheet("color: #0F172A;")

    def set_selected(self, sel: bool):
        self.is_selected = sel
        self.update_style()

    def update_style(self):
        border = "#2563EB" if self.is_selected else "#E2E8F0"
        bg = "#F8FAFC" if self.is_selected else "#FFFFFF"
        self.setStyleSheet(f"""
            QFrame#kpiCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QFrame#kpiCard:hover {{
                border-color: #93C5FD;
                background-color: #FAFCFF;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# Monthly Trend Canvas (Bar/Line ECharts-like canvas)
# -------------------------------------------------------------------------
class MonthlyTrendCanvas(QWidget):
    month_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "monthly"  # "monthly" or "cum"
        self.data_points: List[Tuple[str, float, float]] = []  # (month, monthly_pnl, cum_pnl)
        self.selected_month = ""
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hovered_index = -1

    def set_data(self, points: List[Tuple[str, float, float]], selected_month: str = ""):
        self.data_points = points
        self.selected_month = selected_month
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        painter.fillRect(0, 0, w, h, QColor("#FFFFFF"))

        if not self.data_points:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无月度收益走势数据")
            return

        margin_left = 55
        margin_right = 20
        margin_top = 25
        margin_bottom = 35

        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        if plot_w <= 10 or plot_h <= 10:
            return

        # Y scale calculation
        vals = [p[1] if self.mode == "monthly" else p[2] for p in self.data_points]
        raw_min = min(vals)
        raw_max = max(vals)

        if self.mode == "monthly":
            abs_vals = sorted([abs(v) for v in vals if abs(v) > 0.001])
            if abs_vals:
                q_idx = int(len(abs_vals) * 0.95)
                q95 = abs_vals[min(q_idx, len(abs_vals) - 1)]
                scale_limit = max(q95 * 1.5, max(abs(raw_max), abs(raw_min)) * 0.35)
                max_val = max(100.0, scale_limit)
                min_val = -max_val
            else:
                max_val = 1000.0
                min_val = -1000.0
        else:
            spread = max(abs(raw_max - raw_min), 100.0)
            max_val = raw_max + spread * 0.1
            min_val = raw_min - spread * 0.1

        val_range = max_val - min_val
        if val_range <= 0.0001:
            val_range = 1.0

        def y_coord(v: float) -> float:
            ratio = (v - min_val) / val_range
            return margin_top + plot_h * (1.0 - ratio)

        zero_y = y_coord(0.0)
        zero_y = max(margin_top, min(margin_top + plot_h, zero_y))

        # Draw grid lines
        grid_pen = QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        painter.drawLine(margin_left, int(margin_top), w - margin_right, int(margin_top))
        painter.drawLine(margin_left, int(margin_top + plot_h * 0.5), w - margin_right, int(margin_top + plot_h * 0.5))
        painter.drawLine(margin_left, int(margin_top + plot_h), w - margin_right, int(margin_top + plot_h))

        # Draw Zero Line
        zero_pen = QPen(QColor("#CBD5E1"), 1.2)
        painter.setPen(zero_pen)
        painter.drawLine(margin_left, int(zero_y), w - margin_right, int(zero_y))

        # Y Axis labels
        painter.setPen(QColor("#94A3B8"))
        painter.setFont(numeric_font(9))
        painter.drawText(5, int(margin_top + 10), fmt_signed_money(max_val, 0))
        painter.drawText(5, int(zero_y + 4), "0")
        painter.drawText(5, int(margin_top + plot_h), fmt_signed_money(min_val, 0))

        n = len(self.data_points)
        bar_step = plot_w / n

        # Draw series
        if self.mode == "monthly":
            bar_w = max(2.5, min(24.0, bar_step * 0.65))
            for i, (m, monthly_pnl, cum_pnl) in enumerate(self.data_points):
                cx = margin_left + i * bar_step + bar_step * 0.5
                y = y_coord(monthly_pnl)

                # Clamp y for visual stability
                y_clamped = max(margin_top, min(margin_top + plot_h, y))

                is_sel = (m == self.selected_month)
                is_hov = (i == self.hovered_index)

                if monthly_pnl >= 0:
                    c = QColor("#10B981") if not is_sel else QColor("#059669")
                else:
                    c = QColor("#EF4444") if not is_sel else QColor("#DC2626")

                painter.setBrush(c)
                if is_sel or is_hov:
                    painter.setPen(QPen(QColor("#1E293B"), 1.5))
                else:
                    painter.setPen(Qt.PenStyle.NoPen)

                bx = cx - bar_w * 0.5
                by = min(zero_y, y_clamped)
                bh = max(2.0, abs(zero_y - y_clamped))
                painter.drawRoundedRect(QRectF(bx, by, bar_w, bh), 2, 2)
        else:
            # Cumulative curve
            line_path = QPainterPath()
            area_path = QPainterPath()
            area_path.moveTo(margin_left, zero_y)

            for i, (m, monthly_pnl, cum_pnl) in enumerate(self.data_points):
                cx = margin_left + i * bar_step + bar_step * 0.5
                cy = y_coord(cum_pnl)
                if i == 0:
                    line_path.moveTo(cx, cy)
                    area_path.lineTo(cx, cy)
                else:
                    line_path.lineTo(cx, cy)
                    area_path.lineTo(cx, cy)

            area_path.lineTo(margin_left + (n - 1) * bar_step + bar_step * 0.5, zero_y)
            area_path.closeSubpath()

            # Fill soft gradient
            painter.fillPath(area_path, QColor(37, 99, 235, 25))

            # Stroke Line
            line_pen = QPen(QColor("#2563EB"), 2)
            painter.strokePath(line_path, line_pen)

            # Selected point highlight
            for i, (m, monthly_pnl, cum_pnl) in enumerate(self.data_points):
                if m == self.selected_month or i == self.hovered_index:
                    cx = margin_left + i * bar_step + bar_step * 0.5
                    cy = y_coord(cum_pnl)
                    painter.setPen(QPen(QColor("#FFFFFF"), 2))
                    painter.setBrush(QColor("#2563EB"))
                    painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))

        # X Axis labels (Show 3-5 representative months)
        painter.setPen(QColor("#94A3B8"))
        painter.setFont(numeric_font(9))
        label_indices = [0, n // 2, n - 1] if n >= 3 else list(range(n))
        for idx in set(label_indices):
            m = self.data_points[idx][0]
            cx = margin_left + idx * bar_step + bar_step * 0.5
            painter.drawText(int(cx - 25), int(h - 12), 50, 15, Qt.AlignmentFlag.AlignCenter, m)

        # Draw Tooltip on Hover
        if 0 <= self.hovered_index < len(self.data_points):
            hm, h_monthly, h_cum = self.data_points[self.hovered_index]
            tip_text = f"{hm}\n当月: {fmt_signed_money(h_monthly)}\n累计: {fmt_signed_money(h_cum)}"
            cx = margin_left + self.hovered_index * bar_step + bar_step * 0.5
            tx = min(w - 130, max(margin_left, cx - 60))
            ty = margin_top + 15
            painter.setBrush(QColor(15, 23, 42, 220))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(tx, ty, 120, 52), 4, 4)
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(QFont(".AppleSystemUIFont", 9.5))
            painter.drawText(QRectF(tx, ty + 2, 120, 48), Qt.AlignmentFlag.AlignCenter, tip_text)

    def mouseMoveEvent(self, event):
        w = self.width()
        margin_left = 55
        margin_right = 20
        plot_w = w - margin_left - margin_right
        n = len(self.data_points)

        if n > 0 and margin_left <= event.position().x() <= w - margin_right:
            bar_step = plot_w / n
            idx = int((event.position().x() - margin_left) / bar_step)
            idx = max(0, min(n - 1, idx))
            if idx != self.hovered_index:
                self.hovered_index = idx
                self.update()
        else:
            if self.hovered_index != -1:
                self.hovered_index = -1
                self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered_index = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and 0 <= self.hovered_index < len(self.data_points):
            selected = self.data_points[self.hovered_index][0]
            self.selected_month = selected
            self.month_selected.emit(selected)
            self.update()
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# Monthly Distribution Donut (Selected month profit vs loss breakdown)
# -------------------------------------------------------------------------
class MonthlyDistributionDonut(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.p_amt = 0.0
        self.l_amt = 0.0
        self.total_pnl = 0.0
        self.setFixedSize(130, 130)

    def set_data(self, p_amt: float, l_amt: float, total_pnl: float):
        self.p_amt = max(0.0, p_amt)
        self.l_amt = max(0.0, abs(l_amt))
        self.total_pnl = total_pnl
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(10, 10, -10, -10)
        total = self.p_amt + self.l_amt

        if total <= 0.0001:
            painter.setPen(QPen(QColor("#E2E8F0"), 12))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect)
        else:
            p_span = int((self.p_amt / total) * 360 * 16)
            l_span = 360 * 16 - p_span

            # Profit Arc (Green)
            if p_span > 0:
                p_pen = QPen(QColor("#10B981"), 12)
                p_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(p_pen)
                painter.drawArc(rect, 90 * 16, -p_span)

            # Loss Arc (Red)
            if l_span > 0:
                l_pen = QPen(QColor("#EF4444"), 12)
                l_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(l_pen)
                painter.drawArc(rect, (90 * 16) - p_span, -l_span)

        # Center Text
        painter.setPen(QColor("#10B981" if self.total_pnl >= 0 else "#EF4444"))
        painter.setFont(numeric_font(13, bold=True))
        val_str = fmt_signed_money(self.total_pnl, 0)
        painter.drawText(rect.adjusted(0, -6, 0, 0), Qt.AlignmentFlag.AlignCenter, val_str)

        painter.setPen(QColor("#94A3B8"))
        painter.setFont(QFont(".AppleSystemUIFont", 9))
        painter.drawText(rect.adjusted(0, 16, 0, 0), Qt.AlignmentFlag.AlignCenter, "当月收益")


# -------------------------------------------------------------------------
# Modern Table Model with Header Sorting, Numeric Right-alignment, and Proportional Widths
# -------------------------------------------------------------------------
class ModernTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: Tuple[str, ...] = ()
        self.all_rows: List[List[Any]] = []
        self.rows: List[List[Any]] = []
        self.page = 1
        self.page_size = 20
        self.sort_column: int = -1
        self.sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
        self.highlight_month: str = ""

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if 0 <= section < len(self.columns):
                col_name = self.columns[section]
                if self.sort_column == section:
                    arrow = " ▲" if self.sort_order == Qt.SortOrder.AscendingOrder else " ▼"
                    return f"{col_name}{arrow}"
                return col_name
        if orientation == Qt.Orientation.Vertical and role == Qt.ItemDataRole.DisplayRole:
            return str((self.page - 1) * self.page_size + section + 1)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row_idx = index.row()
        col_idx = index.column()
        if row_idx >= len(self.rows) or col_idx >= len(self.columns):
            return None

        val = self.rows[row_idx][col_idx]
        val_str = str(val) if val is not None else "—"

        if role == Qt.ItemDataRole.DisplayRole:
            return val_str

        col_name = self.columns[col_idx] if (self.columns and col_idx < len(self.columns)) else ""

        if role == Qt.ItemDataRole.BackgroundRole:
            if getattr(self, "highlight_month", "") and len(self.rows[row_idx]) > 0 and self.rows[row_idx][0] == self.highlight_month:
                return QColor("#EFF6FF")
            return None

        is_text_col = col_name in ("月份 (收盘)", "月份", "券商渠道", "Ticker", "分类", "股票分类")
        is_center_col = col_name in ("上涨股票数", "下跌股票数") or ("只" in val_str)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if is_center_col:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if is_text_col:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # 期初/期末市值、本月买入/卖出、本月净投入、本月收益/收益率、累计收益、持仓数量、成本价、收盘价等所有数值一律靠右对齐
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if role == Qt.ItemDataRole.ForegroundRole:
            if col_name == "上涨股票数":
                return QColor("#10B981") if not val_str.startswith("0") else QColor("#94A3B8")
            if col_name == "下跌股票数":
                return QColor("#EF4444") if not val_str.startswith("0") else QColor("#94A3B8")
            if not ("|" in val_str):
                if val_str.startswith("+"):
                    return QColor("#10B981")
                elif val_str.startswith("-") or val_str.startswith("−"):
                    return QColor("#EF4444")
            if val_str in ("0.00", "0", "—"):
                if col_name in ("本月净投入", "本月收益", "本月收益率", "持仓盈亏率"):
                    return QColor("#64748B")
            if val_str == "—":
                return QColor("#94A3B8")
            if col_name == "Ticker":
                return QColor("#2563EB")
            return QColor("#1E293B")

        if role == Qt.ItemDataRole.FontRole:
            if is_text_col or col_name in ("月份 (收盘)", "月份"):
                return QFont(".AppleSystemUIFont", 12)
            # 所有金额、数值、百分比及只数列统一采用等宽数字字体，保证数字对齐
            return numeric_font(12, bold=True)

        return None

    def toggle_sort(self, column: int):
        """点击表头：首次点击正排，再次点击倒排。"""
        if column < 0 or column >= len(self.columns):
            return
        if self.sort_column == column:
            if self.sort_order == Qt.SortOrder.AscendingOrder:
                self.sort_order = Qt.SortOrder.DescendingOrder
            else:
                self.sort_order = Qt.SortOrder.AscendingOrder
        else:
            self.sort_column = column
            self.sort_order = Qt.SortOrder.AscendingOrder

        self.apply_sort_and_page()

    def _parse_sort_key(self, row: List[Any], col: int):
        if col >= len(row):
            return ""
        val = row[col]
        if val is None:
            return -float("inf")
        s = str(val).strip()
        if s.endswith("只"):
            s = s[:-1].strip()
        clean = s.replace("$", "").replace(",", "").replace("%", "").replace("+", "").replace("−", "-")
        try:
            return float(clean)
        except ValueError:
            return s

    def apply_sort_and_page(self):
        self.beginResetModel()
        if self.sort_column >= 0:
            rev = (self.sort_order == Qt.SortOrder.DescendingOrder)
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, self.sort_column), reverse=rev)

        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()

    def set_columns_and_rows(self, cols: Tuple[str, ...], rows: List[List[Any]]):
        self.beginResetModel()
        self.columns = cols
        self.all_rows = list(rows)
        if self.sort_column >= 0:
            rev = (self.sort_order == Qt.SortOrder.DescendingOrder)
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, self.sort_column), reverse=rev)
        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()

    def set_page(self, page: int):
        total_pages = max(1, (len(self.all_rows) + self.page_size - 1) // self.page_size)
        new_p = max(1, min(page, total_pages))
        if new_p != self.page:
            self.page = new_p
            self.apply_sort_and_page()


# -------------------------------------------------------------------------
# Main Page: MonthlyPerformancePage
# -------------------------------------------------------------------------
class MonthlyPerformancePage(QWidget):
    drill = Signal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.raw_rows: List[Tuple] = []
        self.current_filtered_rows: List[Tuple] = []
        self.selected_month = ""
        self.max_as_of_month = ""
        self.active_kpi_filter: Optional[str] = None
        self.stock_pnl_filter: str = "all"
        self.stock_filter_month: str = ""

        # Filters
        self.selected_person_id: Any = None
        self.selected_broker = ""
        self.search_ticker = ""
        self.start_month = ""
        self.end_month = ""
        self.selected_category = ""

        self.setup_ui()

    def setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Main Scroll Area
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background-color: #F8FAFC;")

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(14, 8, 14, 16)
        content_layout.setSpacing(10)

        # 1. Single-row Compact Filter Toolbar (Aligning with DailyPerformancePage, no redundant header)
        filter_card = QFrame()
        filter_card.setFixedHeight(48)
        filter_card.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QComboBox, QLineEdit, QDateEdit {
                height: 28px;
                border: 1px solid #CBD5E1;
                border-radius: 5px;
                padding: 0 8px;
                font-size: 12px;
                color: #1E293B;
                background: #FFFFFF;
            }
            QComboBox:focus, QLineEdit:focus, QDateEdit:focus {
                border-color: #2563EB;
            }
        """)
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(10, 8, 10, 8)
        filter_layout.setSpacing(8)

        # Person
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(110)
        self.person_combo.addItem("全部人员", "")
        self.person_combo.currentIndexChanged.connect(self.on_person_changed)
        filter_layout.addWidget(self.person_combo)

        # Broker
        self.broker_combo = QComboBox()
        self.broker_combo.setMinimumWidth(110)
        self.broker_combo.addItem("全部渠道", "")
        self.broker_combo.currentIndexChanged.connect(self.on_broker_changed)
        filter_layout.addWidget(self.broker_combo)

        # Ticker input
        self.ticker_input = QLineEdit()
        self.ticker_input.setPlaceholderText("输入代码或名称，如 AAPL")
        self.ticker_input.setFixedWidth(160)
        self.ticker_input.setStyleSheet("padding: 4px 8px; border: 1px solid #E2E8F0; border-radius: 4px; font-size: 12px;")
        filter_layout.addWidget(self.ticker_input)

        # Month range (Start & End)
        date_box = QHBoxLayout()
        date_box.setSpacing(4)
        self.start_month_edit = QDateEdit()
        self.start_month_edit.setDisplayFormat("yyyy-MM")
        self.start_month_edit.setCalendarPopup(True)
        self.start_month_edit.setDate(QDate(2025, 1, 1))
        self.start_month_edit.setStyleSheet("border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 6px; font-size: 12px;")

        dash_lbl = QLabel("—")
        dash_lbl.setStyleSheet("color: #94A3B8;")

        self.end_month_edit = QDateEdit()
        self.end_month_edit.setDisplayFormat("yyyy-MM")
        self.end_month_edit.setCalendarPopup(True)
        self.end_month_edit.setDate(QDate.currentDate())
        self.end_month_edit.setStyleSheet("border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 6px; font-size: 12px;")

        date_box.addWidget(self.start_month_edit)
        date_box.addWidget(dash_lbl)
        date_box.addWidget(self.end_month_edit)
        filter_layout.addLayout(date_box)

        # Category
        self.category_combo = QComboBox()
        self.category_combo.setMinimumWidth(100)
        self.category_combo.addItem("全部分类", "")
        self.category_combo.currentIndexChanged.connect(self.on_category_changed)
        filter_layout.addWidget(self.category_combo)

        # Search & Reset buttons
        search_btn = QPushButton("查询")
        search_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1D4ED8;
            }
        """)
        search_btn.clicked.connect(self.on_search_clicked)
        filter_layout.addWidget(search_btn)

        reset_btn = QPushButton("重置")
        reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #F1F5F9;
                color: #475569;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #E2E8F0;
            }
        """)
        reset_btn.clicked.connect(self.on_reset_clicked)
        filter_layout.addWidget(reset_btn)

        filter_layout.addStretch()

        # As of badge
        self.as_of_badge = QLabel("数据截至：—")
        self.as_of_badge.setStyleSheet("font-size: 11px; color: #64748B; background-color: #F8FAFC; padding: 4px 8px; border-radius: 4px;")
        filter_layout.addWidget(self.as_of_badge)

        export_btn = QPushButton("导出")
        export_btn.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                color: #334155;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #F8FAFC;
            }
        """)
        export_btn.clicked.connect(self.export_csv)
        filter_layout.addWidget(export_btn)

        content_layout.addWidget(filter_card)

        # 3. Seven KPI Metric Cards
        kpi_grid = QGridLayout()
        kpi_grid.setContentsMargins(0, 0, 0, 0)
        kpi_grid.setSpacing(10)

        self.kpi_cards: Dict[str, KpiMetricCard] = {
            "total": KpiMetricCard("total", "期间总收益"),
            "months": KpiMetricCard("months", "交易月数"),
            "avg": KpiMetricCard("avg", "月均收益"),
            "up": KpiMetricCard("up", "上涨月数"),
            "down": KpiMetricCard("down", "下跌月数"),
            "avg_profit": KpiMetricCard("avg_profit", "平均盈利月"),
            "avg_loss": KpiMetricCard("avg_loss", "平均亏损月"),
        }

        col_idx = 0
        for key, card in self.kpi_cards.items():
            card.clicked.connect(self.handle_kpi_clicked)
            kpi_grid.addWidget(card, 0, col_idx)
            col_idx += 1
        content_layout.addLayout(kpi_grid)

        # 4. Middle Section: Monthly Trend Chart (Left) + Broker Overview (Right)
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        # Trend Chart Card
        trend_card = QFrame()
        trend_card.setFixedHeight(260)
        trend_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        trend_vbox = QVBoxLayout(trend_card)
        trend_vbox.setContentsMargins(12, 6, 12, 6)
        trend_vbox.setSpacing(2)

        trend_top = QHBoxLayout()
        self.trend_tab_monthly = QPushButton("月度收益走势")
        self.trend_tab_cum = QPushButton("累计收益走势")
        for btn in (self.trend_tab_monthly, self.trend_tab_cum):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #64748B;
                    font-size: 12px;
                    font-weight: 600;
                    border: none;
                    padding: 3px 8px;
                    border-radius: 4px;
                }
                QPushButton:checked {
                    background: #EFF6FF;
                    color: #2563EB;
                }
            """)
        self.trend_tab_monthly.setChecked(True)
        self.trend_tab_monthly.clicked.connect(lambda: self.switch_trend_mode("monthly"))
        self.trend_tab_cum.clicked.connect(lambda: self.switch_trend_mode("cum"))

        trend_top.addWidget(self.trend_tab_monthly)
        trend_top.addWidget(self.trend_tab_cum)
        trend_top.addStretch()
        trend_vbox.addLayout(trend_top)

        self.trend_canvas = MonthlyTrendCanvas()
        self.trend_canvas.month_selected.connect(self.select_month)
        trend_vbox.addWidget(self.trend_canvas)
        self.mid_splitter.addWidget(trend_card)

        # Broker Cumulative Overview Card
        broker_card = QFrame()
        broker_card.setFixedHeight(260)
        broker_card.setMinimumWidth(360)
        broker_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        broker_vbox = QVBoxLayout(broker_card)
        broker_vbox.setContentsMargins(16, 12, 16, 12)
        broker_vbox.setSpacing(8)

        broker_top = QHBoxLayout()
        broker_title = QLabel("渠道收益概览 (期间累计)")
        broker_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        broker_title.setStyleSheet("color: #0F172A; border: none; background: transparent;")
        broker_top.addWidget(broker_title)
        broker_top.addStretch()

        detail_link = QLabel("查看详情 →")
        detail_link.setFont(QFont(".AppleSystemUIFont", 11))
        detail_link.setStyleSheet("color: #2563EB; border: none; background: transparent;")
        detail_link.setCursor(Qt.CursorShape.PointingHandCursor)
        detail_link.mousePressEvent = lambda e: self.tab_bar.setCurrentIndex(1)
        broker_top.addWidget(detail_link)
        broker_vbox.addLayout(broker_top)

        # Broker column headers
        b_th = QHBoxLayout()
        b_th.setContentsMargins(6, 2, 6, 2)
        th_b_name = QLabel("券商渠道")
        th_b_name.setFixedWidth(75)
        th_b_name.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        th_b_bar = QLabel("收益率 (占总收益)")
        th_b_bar.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        th_b_pnl = QLabel("累计收益")
        th_b_pnl.setFixedWidth(85)
        th_b_pnl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        th_b_pnl.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        b_th.addWidget(th_b_name)
        b_th.addWidget(th_b_bar)
        b_th.addWidget(th_b_pnl)
        broker_vbox.addLayout(b_th)

        self.broker_scroll = QScrollArea()
        self.broker_scroll.setWidgetResizable(True)
        self.broker_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.broker_list_widget = QWidget()
        self.broker_list_layout = QVBoxLayout(self.broker_list_widget)
        self.broker_list_layout.setContentsMargins(0, 0, 0, 0)
        self.broker_list_layout.setSpacing(4)
        self.broker_scroll.setWidget(self.broker_list_widget)
        broker_vbox.addWidget(self.broker_scroll)

        self.mid_splitter.addWidget(broker_card)
        self.mid_splitter.setStretchFactor(0, 3)
        self.mid_splitter.setStretchFactor(1, 2)
        content_layout.addWidget(self.mid_splitter)

        # 5. Full-width Monthly Attribution Module
        self.attr_card = QFrame()
        self.attr_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        attr_card_v = QVBoxLayout(self.attr_card)
        attr_card_v.setContentsMargins(14, 10, 14, 10)
        attr_card_v.setSpacing(8)

        # Attribution Header: Icon + Title + "点击联动" Badge + Subtitle + Stretch + Prev/Next Month Buttons
        attr_head = QHBoxLayout()
        attr_head.setSpacing(8)

        attr_icon_lbl = QLabel("📊")
        attr_icon_lbl.setStyleSheet("font-size: 15px;")
        attr_head.addWidget(attr_icon_lbl)

        self.attr_title_lbl = QLabel("单月收益归因 (—)")
        self.attr_title_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #1E293B;")
        attr_head.addWidget(self.attr_title_lbl)

        linked_badge = QLabel("点击联动")
        linked_badge.setStyleSheet("""
            background-color: #EFF6FF;
            color: #2563EB;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 4px;
            border: 1px solid #BFDBFE;
        """)
        attr_head.addWidget(linked_badge)

        attr_subtitle = QLabel("深度拆解单月收益构成：亏损/盈利贡献排行、盈亏结构与股票明细。")
        attr_subtitle.setStyleSheet("font-size: 11.5px; color: #64748B;")
        attr_head.addWidget(attr_subtitle)
        attr_head.addStretch()

        # Month Navigation Buttons
        self.prev_month_btn = QPushButton("◀ 上一月")
        self.next_month_btn = QPushButton("下一月 ▶")
        for btn in (self.prev_month_btn, self.next_month_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #F8FAFC;
                    border: 1px solid #CBD5E1;
                    border-radius: 4px;
                    color: #334155;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 10px;
                }
                QPushButton:hover:!disabled {
                    background-color: #EFF6FF;
                    border-color: #3B82F6;
                    color: #2563EB;
                }
                QPushButton:disabled {
                    background-color: #F1F5F9;
                    border-color: #E2E8F0;
                    color: #94A3B8;
                }
            """)
        self.prev_month_btn.clicked.connect(self.select_prev_month)
        self.next_month_btn.clicked.connect(self.select_next_month)
        attr_head.addWidget(self.prev_month_btn)
        attr_head.addWidget(self.next_month_btn)
        attr_card_v.addLayout(attr_head)

        # Attribution 3 Columns: 1:1:1 Equal Proportion
        attr_cols = QHBoxLayout()
        attr_cols.setSpacing(10)

        # Column 1: 当月盈亏结构
        col1_card = QFrame()
        col1_card.setStyleSheet("background-color: #FAFCFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        col1_vbox = QVBoxLayout(col1_card)
        col1_vbox.setContentsMargins(10, 8, 10, 8)
        col1_vbox.setSpacing(6)

        c1_hdr = QLabel("当月盈亏结构 <span style='color:#94A3B8; font-size:11px;'>ⓘ</span>")
        c1_hdr.setStyleSheet("font-size: 13px; font-weight: 700; color: #1E293B;")
        col1_vbox.addWidget(c1_hdr)

        c1_body = QHBoxLayout()
        c1_body.setContentsMargins(0, 0, 0, 0)
        c1_body.setSpacing(8)

        self.donut_widget = MonthlyDistributionDonut()
        c1_body.addWidget(self.donut_widget)

        donut_btns_v = QVBoxLayout()
        donut_btns_v.setSpacing(6)
        donut_btns_v.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.donut_p_btn = QPushButton("● 盈利股票   0 只   +0.00")
        self.donut_l_btn = QPushButton("● 亏损股票   0 只   -0.00")

        for btn, hover_color in ((self.donut_p_btn, "#10B981"), (self.donut_l_btn, "#EF4444")):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(34)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: #FFFFFF;
                    border: 1px solid #E2E8F0;
                    border-radius: 6px;
                    padding: 4px 10px;
                    text-align: left;
                    font-size: 11.5px;
                    font-weight: 500;
                    color: #1E293B;
                }}
                QPushButton:hover {{
                    background-color: #F1F5F9;
                    border-color: #CBD5E1;
                    color: {hover_color};
                }}
            """)
        self.donut_p_btn.clicked.connect(lambda: self.filter_stock_pnl("profit"))
        self.donut_l_btn.clicked.connect(lambda: self.filter_stock_pnl("loss"))

        donut_btns_v.addWidget(self.donut_p_btn)
        donut_btns_v.addWidget(self.donut_l_btn)
        c1_body.addLayout(donut_btns_v)
        col1_vbox.addLayout(c1_body)
        attr_cols.addWidget(col1_card, 1)

        # Column 2: 亏损贡献排行 Top 5
        col2_card = QFrame()
        col2_card.setStyleSheet("background-color: #FAFCFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        col2_vbox = QVBoxLayout(col2_card)
        col2_vbox.setContentsMargins(10, 8, 10, 8)
        col2_vbox.setSpacing(4)

        c2_hdr = QLabel("↓ 亏损贡献排行 Top 5")
        c2_hdr.setStyleSheet("font-size: 13px; font-weight: 700; color: #EF4444;")
        col2_vbox.addWidget(c2_hdr)

        c2_th = QHBoxLayout()
        c2_th.setContentsMargins(4, 2, 4, 2)
        th_rank = QLabel("排名")
        th_rank.setFixedWidth(24)
        th_rank.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th_sym = QLabel("股票 (Ticker)")
        th_sym.setFixedWidth(65)
        th_sym.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th_broker = QLabel("券商渠道")
        th_broker.setFixedWidth(55)
        th_broker.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th_pnl = QLabel("当月收益")
        th_pnl.setFixedWidth(70)
        th_pnl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        th_pnl.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th_bar = QLabel("贡献度")
        th_bar.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600; padding-left: 8px;")
        c2_th.addWidget(th_rank)
        c2_th.addWidget(th_sym)
        c2_th.addWidget(th_broker)
        c2_th.addWidget(th_pnl)
        c2_th.addWidget(th_bar)
        col2_vbox.addLayout(c2_th)

        self.loss_rows_v = QVBoxLayout()
        self.loss_rows_v.setSpacing(3)
        col2_vbox.addLayout(self.loss_rows_v)
        col2_vbox.addStretch()
        attr_cols.addWidget(col2_card, 1)

        # Column 3: 盈利贡献排行 Top 5
        col3_card = QFrame()
        col3_card.setStyleSheet("background-color: #FAFCFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        col3_vbox = QVBoxLayout(col3_card)
        col3_vbox.setContentsMargins(10, 8, 10, 8)
        col3_vbox.setSpacing(4)

        c3_hdr = QLabel("↑ 盈利贡献排行 Top 5")
        c3_hdr.setStyleSheet("font-size: 13px; font-weight: 700; color: #10B981;")
        col3_vbox.addWidget(c3_hdr)

        c3_th = QHBoxLayout()
        c3_th.setContentsMargins(4, 2, 4, 2)
        th3_rank = QLabel("排名")
        th3_rank.setFixedWidth(24)
        th3_rank.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th3_sym = QLabel("股票 (Ticker)")
        th3_sym.setFixedWidth(65)
        th3_sym.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th3_broker = QLabel("券商渠道")
        th3_broker.setFixedWidth(55)
        th3_broker.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th3_pnl = QLabel("当月收益")
        th3_pnl.setFixedWidth(70)
        th3_pnl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        th3_pnl.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        th3_bar = QLabel("贡献度")
        th3_bar.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600; padding-left: 8px;")
        c3_th.addWidget(th3_rank)
        c3_th.addWidget(th3_sym)
        c3_th.addWidget(th3_broker)
        c3_th.addWidget(th3_pnl)
        c3_th.addWidget(th3_bar)
        col3_vbox.addLayout(c3_th)

        self.profit_rows_v = QVBoxLayout()
        self.profit_rows_v.setSpacing(3)
        col3_vbox.addLayout(self.profit_rows_v)
        col3_vbox.addStretch()
        attr_cols.addWidget(col3_card, 1)

        attr_card_v.addLayout(attr_cols)

        # Footer Hint
        hint_bar = QFrame()
        hint_bar.setStyleSheet("background-color: #F8FAFC; border-radius: 4px; padding: 2px 8px;")
        hb_layout = QHBoxLayout(hint_bar)
        hb_layout.setContentsMargins(6, 3, 6, 3)
        hint_lbl = QLabel("ⓘ 点击走势图月份、渠道概览或股票排行可联动下方明细与右侧抽屉。")
        hint_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        hb_layout.addWidget(hint_lbl)
        attr_card_v.addWidget(hint_bar)

        content_layout.addWidget(self.attr_card)

        # 6. Bottom Table Card (Full Width, 3 Tabs)
        table_card = QFrame()
        table_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        table_vbox = QVBoxLayout(table_card)
        table_vbox.setContentsMargins(12, 8, 12, 8)
        table_vbox.setSpacing(6)

        # Tab Bar
        tab_h = QHBoxLayout()
        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.addTab("📅 按月份汇总")
        self.tab_bar.addTab("🏛️ 按渠道明细")
        self.tab_bar.addTab("📈 按股票明细")
        self.tab_bar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_bar.setStyleSheet("""
            QTabBar::tab {
                min-width: 135px;
                height: 34px;
                padding: 0 16px;
                font-size: 13px;
                font-weight: 600;
                color: #64748B;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected {
                color: #2563EB;
                border-bottom: 2px solid #2563EB;
            }
            QTabBar::tab:hover:!selected {
                color: #1E293B;
            }
        """)
        self.tab_bar.currentChanged.connect(self.on_table_tab_changed)
        tab_h.addWidget(self.tab_bar)
        tab_h.addStretch()

        self.table_count_lbl = QLabel("共 0 条记录")
        self.table_count_lbl.setStyleSheet("font-size: 11.5px; color: #94A3B8;")
        tab_h.addWidget(self.table_count_lbl)
        table_vbox.addLayout(tab_h)

        # Standard QTableView (NO OPERATION COLUMN)
        self.table_model = ModernTableModel(self)
        self.table_view = QTableView(self)
        self.table_view.setModel(self.table_model)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setShowGrid(False)
        self.table_view.setWordWrap(False)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.verticalHeader().hide()
        self.table_view.verticalHeader().setDefaultSectionSize(36)
        self.table_view.horizontalHeader().setFixedHeight(36)
        self.table_view.horizontalHeader().setHighlightSections(False)
        self.table_view.horizontalHeader().setSectionsClickable(True)
        self.table_view.horizontalHeader().sectionClicked.connect(self.on_header_section_clicked)
        # 展示 20 行高度并禁用内层纵向滚动条，由最外层页面滚动
        self.table_view.setMinimumHeight(760)
        self.table_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table_view.setStyleSheet("""
            QTableView {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                alternate-background-color: #FAFCFF;
            }
            QTableView::item:selected {
                background-color: #EFF6FF;
                color: #1E293B;
            }
            QTableView::item:hover {
                background-color: #F8FAFC;
            }
            QHeaderView::section {
                background-color: #F8FAFC;
                color: #475569;
                font-size: 12px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                padding: 4px 8px;
            }
            QHeaderView::section:hover {
                background-color: #F1F5F9;
                color: #2563EB;
            }
        """)
        self.table_view.clicked.connect(self.on_table_row_clicked)
        table_vbox.addWidget(self.table_view)

        # Pagination Bar
        page_h = QHBoxLayout()
        self.page_info_lbl = QLabel("每页 20 条")
        self.page_info_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        page_h.addWidget(self.page_info_lbl)
        page_h.addStretch()

        self.prev_page_btn = QPushButton("上一页")
        self.prev_page_btn.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                color: #475569;
            }
            QPushButton:hover {
                background-color: #F8FAFC;
            }
            QPushButton:disabled {
                color: #CBD5E1;
            }
        """)
        self.prev_page_btn.clicked.connect(lambda: self.change_page(-1))
        page_h.addWidget(self.prev_page_btn)

        self.page_num_lbl = QLabel("第 1 / 1 页")
        self.page_num_lbl.setStyleSheet("font-size: 11.5px; color: #475569; font-weight: 500;")
        page_h.addWidget(self.page_num_lbl)

        self.next_page_btn = QPushButton("下一页")
        self.next_page_btn.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 11px;
                color: #475569;
            }
            QPushButton:hover {
                background-color: #F8FAFC;
            }
            QPushButton:disabled {
                color: #CBD5E1;
            }
        """)
        self.next_page_btn.clicked.connect(lambda: self.change_page(1))
        page_h.addWidget(self.next_page_btn)

        table_vbox.addLayout(page_h)
        content_layout.addWidget(table_card)

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll)

        # 7. Slide-out Drawer Frame (Floating Overlay)
        self.drawer_overlay = QWidget(self)
        self.drawer_overlay.setStyleSheet("background-color: rgba(15, 23, 42, 0.25);")
        self.drawer_overlay.hide()

        self.drawer_frame = QFrame(self)
        self.drawer_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-left: 1px solid #CBD5E1;
            }
        """)
        self.drawer_frame.hide()

        drawer_vbox = QVBoxLayout(self.drawer_frame)
        drawer_vbox.setContentsMargins(16, 16, 16, 16)
        drawer_vbox.setSpacing(10)

        drawer_top = QHBoxLayout()
        self.drawer_title = QLabel("月度详情")
        self.drawer_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #1E293B;")

        self.drawer_prev_btn = QPushButton("◀")
        self.drawer_prev_btn.setFixedSize(24, 24)
        self.drawer_prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.drawer_prev_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9; border: 1px solid #CBD5E1; border-radius: 4px;
                color: #475569; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover:!disabled { background: #EFF6FF; border-color: #3B82F6; color: #2563EB; }
            QPushButton:disabled { opacity: 0.4; color: #94A3B8; }
        """)
        self.drawer_prev_btn.clicked.connect(self.select_prev_month)

        self.drawer_next_btn = QPushButton("▶")
        self.drawer_next_btn.setFixedSize(24, 24)
        self.drawer_next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.drawer_next_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9; border: 1px solid #CBD5E1; border-radius: 4px;
                color: #475569; font-size: 11px; font-weight: bold;
            }
            QPushButton:hover:!disabled { background: #EFF6FF; border-color: #3B82F6; color: #2563EB; }
            QPushButton:disabled { opacity: 0.4; color: #94A3B8; }
        """)
        self.drawer_next_btn.clicked.connect(self.select_next_month)

        self.drawer_close_btn = QPushButton("✕")
        self.drawer_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.drawer_close_btn.setFixedSize(26, 26)
        self.drawer_close_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9;
                border: none;
                border-radius: 4px;
                color: #64748B;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #E2E8F0;
                color: #1E293B;
            }
        """)
        self.drawer_close_btn.clicked.connect(self.close_drawer)
        drawer_top.addWidget(self.drawer_title)
        drawer_top.addSpacing(6)
        drawer_top.addWidget(self.drawer_prev_btn)
        drawer_top.addWidget(self.drawer_next_btn)
        drawer_top.addStretch()
        drawer_top.addWidget(self.drawer_close_btn)
        drawer_vbox.addLayout(drawer_top)

        self.drawer_content_lbl = QLabel("")
        self.drawer_content_lbl.setStyleSheet("font-size: 12px; color: #475569;")
        self.drawer_content_lbl.setWordWrap(True)
        drawer_vbox.addWidget(self.drawer_content_lbl)

        self.drawer_scroll = QScrollArea()
        self.drawer_scroll.setWidgetResizable(True)
        self.drawer_scroll.setStyleSheet("border: none; background: transparent;")
        self.drawer_list_widget = QWidget()
        self.drawer_list_layout = QVBoxLayout(self.drawer_list_widget)
        self.drawer_list_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_list_layout.setSpacing(6)
        self.drawer_scroll.setWidget(self.drawer_list_widget)
        drawer_vbox.addWidget(self.drawer_scroll)

    # -------------------------------------------------------------------------
    # Column Widths Proportional Distribution
    # -------------------------------------------------------------------------
    def apply_proportional_column_widths(self, base_widths: List[int]):
        """均称自适应分配表格列宽，按内容基准宽度平摊视口剩余空间，杜绝最后一列孤立拉长产生大片空白"""
        if not hasattr(self, "table_view") or not self.table_view:
            return
        header = self.table_view.horizontalHeader()
        header.setStretchLastSection(False)

        n = len(base_widths)
        if n == 0 or self.table_model.columnCount() != n:
            return

        viewport_w = self.table_view.viewport().width()
        if viewport_w <= 20:
            viewport_w = self.table_view.width() - 4
        if viewport_w <= 20:
            viewport_w = 1260

        base_sum = sum(base_widths)
        extra = max(0, viewport_w - base_sum)
        per_col = extra // n
        rem = extra % n

        for i, w in enumerate(base_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            extra_px = per_col + (1 if i < rem else 0)
            self.table_view.setColumnWidth(i, w + extra_px)

    def adjust_current_table_columns(self):
        """根据当前 Tab 重新计算并应用平摊列宽"""
        if not hasattr(self, "tab_bar"):
            return
        current_tab = self.tab_bar.currentIndex()
        if current_tab == 0:
            # 11 列：月份, 期初市值, 期末市值, 本月买入, 本月卖出, 本月净投入, 本月收益, 本月收益率, 累计收益, 上涨股票数, 下跌股票数
            self.apply_proportional_column_widths([120, 115, 115, 105, 105, 110, 115, 95, 115, 90, 90])
        elif current_tab == 1:
            # 12 列：月份, 券商渠道, 期初市值, 期末市值, 本月买入, 本月卖出, 本月净投入, 本月收益, 本月收益率, 累计收益, 上涨股票数, 下跌股票数
            self.apply_proportional_column_widths([110, 100, 110, 110, 100, 100, 105, 110, 90, 110, 85, 85])
        else:
            # 12 列：月份, 券商渠道, Ticker, 分类, 持仓数量, 成本价, 收盘价, 本月收益, 本月收益率, 累计收益, 持仓盈亏率, 月末市值
            self.apply_proportional_column_widths([110, 95, 85, 80, 85, 85, 85, 105, 90, 105, 90, 110])

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "mid_splitter") and self.mid_splitter:
            w = self.mid_splitter.width()
            if w > 100:
                self.mid_splitter.setSizes([int(w * 0.58), int(w * 0.42)])
        QTimer.singleShot(50, self.adjust_current_table_columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "mid_splitter") and self.mid_splitter:
            w = self.mid_splitter.width()
            if w > 100:
                self.mid_splitter.setSizes([int(w * 0.58), int(w * 0.42)])
        if self.drawer_frame.isVisible():
            w = 440
            self.drawer_overlay.setGeometry(self.rect())
            self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
        self.adjust_current_table_columns()

    # -------------------------------------------------------------------------
    # Data Management
    # -------------------------------------------------------------------------
    def activate(self):
        """Called when sidebar navigates to this page."""
        if not self.raw_rows and self.analytics:
            self.update_data(self.analytics)
        self.adjust_current_table_columns()

    def update_data(self, analytics):
        self.analytics = analytics
        if not analytics or "每月持仓收益" not in analytics.reports:
            return

        report = analytics.reports["每月持仓收益"]
        self.raw_rows = [tuple(r.values) for r in report.rows]

        # Update person dropdown
        self._populate_persons()

        # Update broker & category dropdowns
        brokers = sorted({r[1] for r in self.raw_rows if r[1]})
        self.broker_combo.blockSignals(True)
        self.broker_combo.clear()
        self.broker_combo.addItem("全部渠道", "")
        for b in brokers:
            self.broker_combo.addItem(fmt_broker_cn(b), b)
        self.broker_combo.blockSignals(False)

        cats = sorted({r[2] for r in self.raw_rows if r[2]})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类", "")
        for c in cats:
            self.category_combo.addItem(c, c)
        self.category_combo.blockSignals(False)

        # Max month
        months = [r[0] for r in self.raw_rows if r[0]]
        if months:
            self.max_as_of_month = max(months)
            self.as_of_badge.setText(f"数据截至：{self.max_as_of_month}（月末）")
            if not self.selected_month:
                self.selected_month = self.max_as_of_month
            # Parse yyyy-MM
            try:
                parts = self.max_as_of_month.split("-")
                self.end_month_edit.setDate(QDate(int(parts[0]), int(parts[1]), 1))
            except Exception:
                pass

        self.recalculate_and_refresh()

    def sync_person_from_main(self, person_id: Any):
        if getattr(self, "_syncing_person", False):
            return
        self._syncing_person = True
        try:
            self.selected_person_id = person_id
            if self.person_combo.count() <= 1:
                self._populate_persons()
            self.person_combo.blockSignals(True)
            idx = self.person_combo.findData(person_id)
            self.person_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.person_combo.blockSignals(False)
            self.recalculate_and_refresh()
        finally:
            self._syncing_person = False

    def sync_broker_from_main(self, broker_name: Optional[str]):
        if getattr(self, "_syncing_broker", False):
            return
        self._syncing_broker = True
        try:
            clean = (broker_name or "").strip()
            self.selected_broker = clean
            self.broker_combo.blockSignals(True)
            idx = 0
            if clean:
                for i in range(self.broker_combo.count()):
                    if self.broker_combo.itemData(i) == clean or self.broker_combo.itemText(i) == clean or fmt_broker_cn(self.broker_combo.itemData(i)) == clean:
                        idx = i
                        break
            self.broker_combo.setCurrentIndex(idx)
            self.broker_combo.blockSignals(False)
            self.recalculate_and_refresh()
        finally:
            self._syncing_broker = False

    def _populate_persons(self):
        persons = []
        if self.analytics and hasattr(self.analytics, "persons") and self.analytics.persons:
            persons = self.analytics.persons
        elif hasattr(self.parent(), "person") and self.parent().person:
            combo = self.parent().person
            for i in range(combo.count()):
                d = combo.itemData(i)
                t = combo.itemText(i)
                if d is not None:
                    persons.append((d, t))

        self.person_combo.blockSignals(True)
        self.person_combo.clear()
        self.person_combo.addItem("全部人员", None)
        for p in persons:
            p_id = p[0] if isinstance(p, (list, tuple)) else getattr(p, "id", p)
            p_name = p[1] if isinstance(p, (list, tuple)) else getattr(p, "name", str(p))
            if p_id is not None:
                self.person_combo.addItem(p_name, p_id)
        if self.selected_person_id is not None:
            idx = self.person_combo.findData(self.selected_person_id)
            if idx >= 0:
                self.person_combo.setCurrentIndex(idx)
        self.person_combo.blockSignals(False)

    # -------------------------------------------------------------------------
    # Recalculation & Aggregation
    # -------------------------------------------------------------------------
    def recalculate_and_refresh(self):
        filtered: List[Tuple] = []
        broker_overview_rows: List[Tuple] = []

        start_m_str = self.start_month_edit.date().toString("yyyy-MM")
        end_m_str = self.end_month_edit.date().toString("yyyy-MM")

        for r in self.raw_rows:
            m, b, cat, sym = r[0], r[1], r[2], r[3]
            if self.search_ticker and self.search_ticker.upper() not in sym.upper():
                continue
            if self.selected_category and cat != self.selected_category:
                continue
            if start_m_str and m < start_m_str:
                continue
            if end_m_str and m > end_m_str:
                continue

            broker_overview_rows.append(r)

            if (
                self.selected_broker
                and b != self.selected_broker
                and fmt_broker_cn(b) != self.selected_broker
                and fmt_broker_cn(b) != fmt_broker_cn(self.selected_broker)
            ):
                continue
            filtered.append(r)

        # Group by Month
        month_map: Dict[str, Dict[str, Any]] = {}
        for r in filtered:
            m = r[0]
            if m not in month_map:
                month_map[m] = {
                    "init": 0.0,
                    "end": 0.0,
                    "pnl": 0.0,
                    "buy": 0.0,
                    "sell": 0.0,
                    "net": 0.0,
                    "up_stocks": 0,
                    "down_stocks": 0,
                    "stocks": {},
                }
            item = month_map[m]
            item["init"] += float(r[15] or 0)
            item["end"] += float(r[11] or 0)
            pnl = float(r[20] or 0)  # MONTHLY_COLUMNS index 20: 本月收益
            item["pnl"] += pnl
            b_amt = float(r[16] or 0)
            s_amt = float(r[17] or 0)
            item["buy"] += b_amt
            item["sell"] += s_amt
            item["net"] += (b_amt - s_amt)
            if pnl > 0:
                item["up_stocks"] += 1
            elif pnl < 0:
                item["down_stocks"] += 1
            sym = r[3]
            item["stocks"][sym] = item["stocks"].get(sym, 0.0) + pnl

        months_asc = sorted(month_map.keys())
        trend_pts = []
        cum_pnl = 0.0
        month_summaries = []

        total_pnl = 0.0
        up_months = 0
        down_months = 0
        up_pnl_sum = 0.0
        down_pnl_sum = 0.0

        for m in months_asc:
            data = month_map[m]
            m_pnl = data["pnl"]
            cum_pnl += m_pnl
            trend_pts.append((m, m_pnl, cum_pnl))

            total_pnl += m_pnl
            if m_pnl > 0:
                up_months += 1
                up_pnl_sum += m_pnl
            elif m_pnl < 0:
                down_months += 1
                down_pnl_sum += m_pnl

            init_v = data["init"]
            rate = (m_pnl / init_v) if init_v > 0 else 0.0

            month_summaries.append([
                m,
                fmt_money(init_v, 2),
                fmt_money(data['end'], 2),
                fmt_money(data['buy'], 2),
                fmt_money(data['sell'], 2),
                fmt_signed_money(data['net'], 2),
                fmt_signed_money(m_pnl, 2),
                fmt_percent(rate, 2),
                fmt_signed_money(cum_pnl, 2),
                f"{data['up_stocks']} 只",
                f"{data['down_stocks']} 只",
            ])

        total_months = len(months_asc)
        avg_pnl = (total_pnl / total_months) if total_months > 0 else 0.0
        avg_up_pnl = (up_pnl_sum / up_months) if up_months > 0 else 0.0
        avg_down_pnl = (down_pnl_sum / down_months) if down_months > 0 else 0.0

        # Update 7 KPI Cards
        self.kpi_cards["total"].set_data(
            fmt_signed_money(total_pnl),
            "",
            "profit" if total_pnl >= 0 else "loss",
        )
        self.kpi_cards["months"].set_data(
            f"{total_months} 个月",
            f"{months_asc[0] if months_asc else '—'} 至 {months_asc[-1] if months_asc else '—'}",
        )
        self.kpi_cards["avg"].set_data(
            fmt_signed_money(avg_pnl),
            "月均净现金额",
            "profit" if avg_pnl >= 0 else "loss",
        )
        self.kpi_cards["up"].set_data(
            f"{up_months} 个月",
            f"占比 {(up_months / total_months * 100):.1f}%" if total_months > 0 else "—",
            "profit",
        )
        self.kpi_cards["down"].set_data(
            f"{down_months} 个月",
            f"占比 {(down_months / total_months * 100):.1f}%" if total_months > 0 else "—",
            "loss",
        )
        self.kpi_cards["avg_profit"].set_data(
            fmt_signed_money(avg_up_pnl),
            f"盈利月份均值 ({up_months}月)",
            "profit",
        )
        self.kpi_cards["avg_loss"].set_data(
            fmt_signed_money(avg_down_pnl),
            f"亏损月份均值 ({down_months}月)",
            "loss",
        )

        # Update Trend Canvas
        if not self.selected_month and months_asc:
            self.selected_month = months_asc[-1]
        self.trend_canvas.set_data(trend_pts, self.selected_month)

        # Update Broker Channel Overview
        self.update_broker_overview(broker_overview_rows)

        # Update Monthly Attribution Module
        self.current_filtered_rows = filtered
        self.update_month_distribution(filtered)

        # Refresh Table
        self.refresh_table_view(filtered, month_summaries)

    def update_broker_overview(self, rows: List[Tuple]):
        while self.broker_list_layout.count():
            item = self.broker_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        broker_totals: Dict[str, float] = {}
        for r in self.raw_rows:
            b = r[1]
            if b:
                broker_totals[b] = 0.0

        for r in rows:
            b = r[1]
            if b in broker_totals:
                broker_totals[b] += float(r[20] or 0)

        total_abs = max(1e-5, sum(abs(v) for v in broker_totals.values()))
        sorted_brokers = sorted(broker_totals.items(), key=lambda x: x[1], reverse=True)

        for b, pnl in sorted_brokers:
            row_frame = QFrame()
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)

            b_cn = fmt_broker_cn(b)
            sel_cn = fmt_broker_cn(self.selected_broker) if self.selected_broker else ""
            is_selected = bool(self.selected_broker and (b == self.selected_broker or b_cn == self.selected_broker or b_cn == sel_cn))

            if is_selected:
                row_frame.setStyleSheet("""
                    QFrame {
                        background: #EFF6FF;
                        border: 1.5px solid #3B82F6;
                        border-radius: 5px;
                        padding: 2px 6px;
                    }
                """)
            else:
                row_frame.setStyleSheet("""
                    QFrame {
                        background: #F8FAFC;
                        border: 1px solid #F1F5F9;
                        border-radius: 5px;
                        padding: 2px 6px;
                    }
                    QFrame:hover {
                        background: #EFF6FF;
                        border-color: #BFDBFE;
                    }
                """)

            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(6, 4, 6, 4)
            r_layout.setSpacing(6)

            b_name = QLabel(b_cn)
            b_name.setFixedWidth(75)
            b_name.setStyleSheet(f"font-size: 12px; font-weight: {'700' if is_selected else '600'}; color: {'#1D4ED8' if is_selected else '#1E293B'};")
            r_layout.addWidget(b_name)

            contrib = (abs(pnl) / total_abs) * 100.0

            # 鲜明蓝色条形柱状图（与每日持仓收益 100% 对齐）
            bar = QProgressBar()
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            bar.setValue(int(max(1, min(100, contrib)) * 10))
            bar.setStyleSheet("""
                QProgressBar {
                    background-color: #E2E8F0;
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: #2563EB;
                    border-radius: 4px;
                }
            """)
            r_layout.addWidget(bar, 1)

            contrib_lbl = QLabel(f"{contrib:.1f}%")
            contrib_lbl.setFixedWidth(42)
            contrib_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            contrib_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
            r_layout.addWidget(contrib_lbl)

            pnl_lbl = QLabel(fmt_signed_money(pnl))
            pnl_lbl.setFont(numeric_font(12, bold=True))
            pnl_lbl.setStyleSheet(f"color: {'#10B981' if pnl >= 0 else '#EF4444'};")
            pnl_lbl.setFixedWidth(85)
            pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            r_layout.addWidget(pnl_lbl)

            def make_handler(broker_name):
                return lambda e: self.filter_by_broker(broker_name)
            row_frame.mousePressEvent = make_handler(b)

            self.broker_list_layout.addWidget(row_frame)

    def filter_by_broker(self, broker_name: str):
        target_cn = fmt_broker_cn(broker_name)
        current_cn = fmt_broker_cn(self.selected_broker) if self.selected_broker else ""

        # 如果当前已经选中该渠道，点击则取消筛选恢复全部；否则选中该渠道
        if self.selected_broker and (self.selected_broker == broker_name or current_cn == target_cn):
            self.selected_broker = ""
            self.broker_combo.setCurrentIndex(0)
        else:
            self.selected_broker = broker_name
            idx = 0
            for i in range(self.broker_combo.count()):
                if self.broker_combo.itemData(i) == broker_name or self.broker_combo.itemText(i) == target_cn:
                    idx = i
                    break
            self.broker_combo.setCurrentIndex(idx)

        # 留在本页面，且下方表格自动切换到「🏛️ 按渠道明细」Tab (Tab 1)
        self.tab_bar.setCurrentIndex(1)
        self.recalculate_and_refresh()

    def on_broker_overview_clicked(self, broker: str):
        self.filter_by_broker(broker)

    def update_month_distribution(self, rows: List[Tuple]):
        target_m = self.selected_month or self.max_as_of_month
        if not target_m and rows:
            target_m = rows[-1][0]
        self.attr_title_lbl.setText(f"单月收益归因 ({target_m or '—'})")

        m_rows = [r for r in rows if r[0] == target_m]
        p_cnt, p_amt = 0, 0.0
        l_cnt, l_amt = 0, 0.0
        f_cnt, f_amt = 0, 0.0
        tot = 0.0

        for r in m_rows:
            pnl = float(r[20] or 0)  # MONTHLY_COLUMNS index 20
            tot += pnl
            if pnl > 0.001:
                p_cnt += 1
                p_amt += pnl
            elif pnl < -0.001:
                l_cnt += 1
                l_amt += pnl
            else:
                f_cnt += 1

        self.donut_widget.set_data(p_amt, l_amt, tot)
        self.donut_p_btn.setText(f"● 盈利股票   {p_cnt} 只   {fmt_signed_money(p_amt)}")
        self.donut_l_btn.setText(f"● 亏损股票   {l_cnt} 只   {fmt_signed_money(l_amt)}")

        self.update_top5_rankings(m_rows, p_amt, l_amt)
        self.update_month_nav_buttons()

    def update_month_nav_buttons(self):
        all_months = sorted({r[0] for r in self.current_filtered_rows if r[0]})
        if not all_months or not self.selected_month or self.selected_month not in all_months:
            self.prev_month_btn.setEnabled(False)
            self.next_month_btn.setEnabled(False)
            if hasattr(self, "drawer_prev_btn"):
                self.drawer_prev_btn.setEnabled(False)
                self.drawer_next_btn.setEnabled(False)
            return

        cur_idx = all_months.index(self.selected_month)
        can_prev = cur_idx > 0
        can_next = cur_idx < len(all_months) - 1
        self.prev_month_btn.setEnabled(can_prev)
        self.next_month_btn.setEnabled(can_next)
        if hasattr(self, "drawer_prev_btn"):
            self.drawer_prev_btn.setEnabled(can_prev)
            self.drawer_next_btn.setEnabled(can_next)

    def select_prev_month(self):
        all_months = sorted({r[0] for r in self.current_filtered_rows if r[0]})
        if not all_months or not self.selected_month:
            return
        if self.selected_month in all_months:
            cur_idx = all_months.index(self.selected_month)
            if cur_idx > 0:
                self.select_month(all_months[cur_idx - 1])

    def select_next_month(self):
        all_months = sorted({r[0] for r in self.current_filtered_rows if r[0]})
        if not all_months or not self.selected_month:
            return
        if self.selected_month in all_months:
            cur_idx = all_months.index(self.selected_month)
            if cur_idx < len(all_months) - 1:
                self.select_month(all_months[cur_idx + 1])

    def update_top5_rankings(self, m_rows: List[Tuple], total_profit: float, total_loss: float):
        target_m = self.selected_month or self.max_as_of_month
        # Clear loss rows
        while self.loss_rows_v.count():
            item = self.loss_rows_v.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Clear profit rows
        while self.profit_rows_v.count():
            item = self.profit_rows_v.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        loss_candidates = [r for r in m_rows if float(r[20] or 0) < 0]
        loss_candidates.sort(key=lambda r: float(r[20] or 0))  # Most negative first
        top_loss = loss_candidates[:5]

        profit_candidates = [r for r in m_rows if float(r[20] or 0) > 0]
        profit_candidates.sort(key=lambda r: float(r[20] or 0), reverse=True)  # Most positive first
        top_profit = profit_candidates[:5]

        # 1. 渲染亏损排行 Top 5 (100% 对齐每日页面)
        if not top_loss:
            empty_lbl = QLabel("当月无亏损标的")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("font-size: 12px; color: #94A3B8; padding: 24px 0;")
            self.loss_rows_v.addWidget(empty_lbl)
        else:
            rank_bg_colors = ["#EF4444", "#F97316", "#FB923C", "#94A3B8", "#CBD5E1"]
            total_loss_abs = abs(total_loss) if abs(total_loss) > 0.001 else 1.0
            for idx, r in enumerate(top_loss):
                rank = idx + 1
                sym = r[3]
                broker = fmt_broker_cn(r[1])
                pnl = float(r[20] or 0)
                contrib = (abs(pnl) / total_loss_abs) * 100.0

                item_frame = QFrame()
                item_frame.setCursor(Qt.CursorShape.PointingHandCursor)
                item_frame.setStyleSheet("""
                    QFrame {
                        background: #FFFFFF;
                        border: 1px solid #F1F5F9;
                        border-radius: 4px;
                        padding: 1px 4px;
                    }
                    QFrame:hover {
                        background: #FEF2F2;
                        border-color: #FECACA;
                    }
                """)
                i_lay = QHBoxLayout(item_frame)
                i_lay.setContentsMargins(4, 2, 4, 2)
                i_lay.setSpacing(6)

                r_lbl = QLabel(str(rank))
                r_lbl.setFixedSize(18, 18)
                r_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                bg_col = rank_bg_colors[idx] if idx < len(rank_bg_colors) else "#E2E8F0"
                txt_col = "#FFFFFF" if idx < 3 else "#475569"
                r_lbl.setStyleSheet(f"background: {bg_col}; color: {txt_col}; border-radius: 9px; font-size: 10px; font-weight: 700;")
                i_lay.addWidget(r_lbl)

                sym_lbl = QLabel(sym)
                sym_lbl.setFixedWidth(65)
                sym_lbl.setStyleSheet("font-size: 11.5px; font-weight: 700; color: #1E293B;")
                i_lay.addWidget(sym_lbl)

                b_lbl = QLabel(broker)
                b_lbl.setFixedWidth(55)
                b_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
                i_lay.addWidget(b_lbl)

                pnl_lbl = QLabel(fmt_signed_money(pnl))
                pnl_lbl.setFixedWidth(70)
                pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                pnl_lbl.setFont(numeric_font(11, bold=True))
                pnl_lbl.setStyleSheet("font-size: 11.5px; font-weight: 600; color: #EF4444;")
                i_lay.addWidget(pnl_lbl)

                bar = QProgressBar()
                bar.setFixedHeight(7)
                bar.setTextVisible(False)
                bar.setRange(0, 1000)
                bar.setValue(int(max(1, min(100, contrib)) * 10))
                bar.setStyleSheet("""
                    QProgressBar {
                        background-color: #FEE2E2;
                        border: none;
                        border-radius: 3.5px;
                    }
                    QProgressBar::chunk {
                        background-color: #EF4444;
                        border-radius: 3.5px;
                    }
                """)
                i_lay.addWidget(bar, 1)

                c_lbl = QLabel(f"{contrib:.1f}%")
                c_lbl.setFixedWidth(40)
                c_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                c_lbl.setStyleSheet("font-size: 10.5px; color: #64748B;")
                i_lay.addWidget(c_lbl)

                def make_click_l(s=sym, m=target_m, brk=r[1]):
                    return lambda e: self.open_stock_drawer(s, m, brk)
                item_frame.mousePressEvent = make_click_l(sym, target_m, r[1])
                self.loss_rows_v.addWidget(item_frame)

        # 2. 渲染盈利排行 Top 5 (100% 对齐每日页面)
        if not top_profit:
            empty_lbl = QLabel("当月无盈利标的")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("font-size: 12px; color: #94A3B8; padding: 24px 0;")
            self.profit_rows_v.addWidget(empty_lbl)
        else:
            rank_bg_colors_p = ["#10B981", "#34D399", "#6EE7B7", "#94A3B8", "#CBD5E1"]
            total_profit_val = total_profit if total_profit > 0.001 else 1.0
            for idx, r in enumerate(top_profit):
                rank = idx + 1
                sym = r[3]
                broker = fmt_broker_cn(r[1])
                pnl = float(r[20] or 0)
                contrib = (pnl / total_profit_val) * 100.0

                item_frame = QFrame()
                item_frame.setCursor(Qt.CursorShape.PointingHandCursor)
                item_frame.setStyleSheet("""
                    QFrame {
                        background: #FFFFFF;
                        border: 1px solid #F1F5F9;
                        border-radius: 4px;
                        padding: 1px 4px;
                    }
                    QFrame:hover {
                        background: #F0FDF4;
                        border-color: #BBF7D0;
                    }
                """)
                i_lay = QHBoxLayout(item_frame)
                i_lay.setContentsMargins(4, 2, 4, 2)
                i_lay.setSpacing(6)

                r_lbl = QLabel(str(rank))
                r_lbl.setFixedSize(18, 18)
                r_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                bg_col = rank_bg_colors_p[idx] if idx < len(rank_bg_colors_p) else "#E2E8F0"
                txt_col = "#FFFFFF" if idx < 3 else "#475569"
                r_lbl.setStyleSheet(f"background: {bg_col}; color: {txt_col}; border-radius: 9px; font-size: 10px; font-weight: 700;")
                i_lay.addWidget(r_lbl)

                sym_lbl = QLabel(sym)
                sym_lbl.setFixedWidth(65)
                sym_lbl.setStyleSheet("font-size: 11.5px; font-weight: 700; color: #1E293B;")
                i_lay.addWidget(sym_lbl)

                b_lbl = QLabel(broker)
                b_lbl.setFixedWidth(55)
                b_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
                i_lay.addWidget(b_lbl)

                pnl_lbl = QLabel(fmt_signed_money(pnl))
                pnl_lbl.setFixedWidth(70)
                pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                pnl_lbl.setFont(numeric_font(11, bold=True))
                pnl_lbl.setStyleSheet("font-size: 11.5px; font-weight: 600; color: #10B981;")
                i_lay.addWidget(pnl_lbl)

                bar = QProgressBar()
                bar.setFixedHeight(7)
                bar.setTextVisible(False)
                bar.setRange(0, 1000)
                bar.setValue(int(max(1, min(100, contrib)) * 10))
                bar.setStyleSheet("""
                    QProgressBar {
                        background-color: #DCFCE7;
                        border: none;
                        border-radius: 3.5px;
                    }
                    QProgressBar::chunk {
                        background-color: #10B981;
                        border-radius: 3.5px;
                    }
                """)
                i_lay.addWidget(bar, 1)

                c_lbl = QLabel(f"{contrib:.1f}%")
                c_lbl.setFixedWidth(40)
                c_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                c_lbl.setStyleSheet("font-size: 10.5px; color: #64748B;")
                i_lay.addWidget(c_lbl)

                def make_click_p(s=sym, m=target_m, brk=r[1]):
                    return lambda e: self.open_stock_drawer(s, m, brk)
                item_frame.mousePressEvent = make_click_p(sym, target_m, r[1])
                self.profit_rows_v.addWidget(item_frame)

    def filter_stock_pnl_by_month(self, mode: str):
        self.tab_bar.setCurrentIndex(2)
        self.stock_pnl_filter = mode
        self.stock_filter_month = self.selected_month or self.max_as_of_month
        self.recalculate_and_refresh()

    def filter_stock_pnl(self, mode: str):
        self.filter_stock_pnl_by_month(mode)

    def on_table_tab_changed(self, idx: int):
        self.table_model.page = 1
        self.table_model.sort_column = -1
        if idx != 2:
            self.stock_filter_month = ""
            self.stock_pnl_filter = "all"
        self.recalculate_and_refresh()

    def on_header_section_clicked(self, logical_index: int):
        self.table_model.toggle_sort(logical_index)
        self.update_pagination_ui()

    def refresh_table_view(self, filtered_rows: List[Tuple], month_summaries: List[List[Any]]):
        self.table_model.highlight_month = self.selected_month
        current_tab = self.tab_bar.currentIndex()
        header = self.table_view.horizontalHeader()

        if current_tab == 0:
            # 📅 按月份汇总
            cols = ("月份 (收盘)", "期初市值", "期末市值", "本月买入", "本月卖出", "本月净投入", "本月收益", "本月收益率", "累计收益", "上涨股票数", "下跌股票数")
            rows = list(reversed(month_summaries))
            if self.active_kpi_filter == "up":
                rows = [r for r in rows if "+" in str(r[6])]
            elif self.active_kpi_filter == "down":
                rows = [r for r in rows if "-" in str(r[6])]

            self.table_model.set_columns_and_rows(cols, rows)
            self.table_count_lbl.setText(f"共 {len(rows)} 条记录")

        elif current_tab == 1:
            # 🏛️ 按渠道明细
            cols = ("月份", "券商渠道", "期初市值", "期末市值", "本月买入", "本月卖出", "本月净投入", "本月收益", "本月收益率", "累计收益", "上涨股票数", "下跌股票数")
            channel_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for r in filtered_rows:
                key = (r[0], r[1])
                if key not in channel_map:
                    channel_map[key] = {
                        "init": 0.0,
                        "end": 0.0,
                        "pnl": 0.0,
                        "buy": 0.0,
                        "sell": 0.0,
                        "net": 0.0,
                        "up_stocks": 0,
                        "down_stocks": 0,
                    }
                item = channel_map[key]
                item["init"] += float(r[15] or 0)
                item["end"] += float(r[11] or 0)
                pnl = float(r[20] or 0)
                item["pnl"] += pnl
                b_amt = float(r[16] or 0)
                s_amt = float(r[17] or 0)
                item["buy"] += b_amt
                item["sell"] += s_amt
                item["net"] += (b_amt - s_amt)
                if pnl > 0:
                    item["up_stocks"] += 1
                elif pnl < 0:
                    item["down_stocks"] += 1

            # Broker history for cumulative pnl
            broker_history: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
            for (m, b), data in channel_map.items():
                if b not in broker_history:
                    broker_history[b] = []
                broker_history[b].append((m, data))

            c_rows = []
            for b, entries in broker_history.items():
                entries.sort(key=lambda x: x[0])
                b_cum = 0.0
                for m, data in entries:
                    b_cum += data["pnl"]
                    init_v = data["init"]
                    rate = (data["pnl"] / init_v) if init_v > 0 else (data["pnl"] / data["buy"] if data["buy"] > 0 else 0.0)
                    c_rows.append([
                        m,
                        fmt_broker_cn(b),
                        fmt_money(init_v, 2),
                        fmt_money(data['end'], 2),
                        fmt_money(data['buy'], 2),
                        fmt_money(data['sell'], 2),
                        fmt_signed_money(data['net'], 2),
                        fmt_signed_money(data['pnl'], 2),
                        fmt_percent(rate, 2),
                        fmt_signed_money(b_cum, 2),
                        f"{data['up_stocks']} 只",
                        f"{data['down_stocks']} 只",
                    ])

            c_rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
            self.table_model.set_columns_and_rows(cols, c_rows)
            self.table_count_lbl.setText(f"共 {len(c_rows)} 条记录")

        else:
            # 📈 按股票明细
            cols = ("月份", "券商渠道", "Ticker", "分类", "持仓数量", "成本均价", "收盘价", "本月收益", "本月收益率", "累计收益", "收益率", "月末市值")

            stock_cum_map: Dict[Tuple[str, str, str], float] = {}
            sorted_raw = sorted(self.raw_rows, key=lambda x: x[0])
            cum_tracker: Dict[Tuple[str, str], float] = {}
            for sr in sorted_raw:
                k = (sr[1], sr[3])
                cum_tracker[k] = cum_tracker.get(k, 0.0) + float(sr[20] or 0)
                stock_cum_map[(sr[0], sr[1], sr[3])] = cum_tracker[k]

            s_rows = []
            for r in filtered_rows:
                if self.stock_filter_month and r[0] != self.stock_filter_month:
                    continue
                pnl = float(r[20] or 0)
                if self.stock_pnl_filter == "profit" and pnl <= 0:
                    continue
                if self.stock_pnl_filter == "loss" and pnl >= 0:
                    continue
                if self.stock_pnl_filter == "flat" and pnl != 0:
                    continue

                init_v = float(r[15] or 0)
                buy_v = float(r[16] or 0)
                base_v = init_v if init_v > 0 else buy_v
                monthly_rate = (pnl / base_v) if base_v > 0 else (float(r[13] or 0) if r[13] else 0.0)
                cum_pnl = stock_cum_map.get((r[0], r[1], r[3]), pnl)

                s_rows.append([
                    r[0],
                    fmt_broker_cn(r[1]),
                    r[3],
                    r[2],
                    fmt_money(r[8]),
                    fmt_money(r[9]),
                    fmt_money(r[10]),
                    fmt_signed_money(pnl),
                    fmt_percent(monthly_rate),
                    fmt_signed_money(cum_pnl),
                    fmt_percent(r[13]),
                    fmt_money(r[11]),
                ])
            s_rows.sort(key=lambda x: x[0], reverse=True)
            self.table_model.set_columns_and_rows(cols, s_rows)
            self.table_count_lbl.setText(f"共 {len(s_rows)} 条记录")

        self.adjust_current_table_columns()
        self.update_pagination_ui()

    def change_page(self, delta: int):
        self.table_model.set_page(self.table_model.page + delta)
        self.update_pagination_ui()

    def update_pagination_ui(self):
        total_rows = len(self.table_model.all_rows)
        cur_page = self.table_model.page
        page_sz = self.table_model.page_size
        max_page = max(1, (total_rows + page_sz - 1) // page_sz)

        self.page_num_lbl.setText(f"第 {cur_page} / {max_page} 页")
        self.prev_page_btn.setEnabled(cur_page > 1)
        self.next_page_btn.setEnabled(cur_page < max_page)

    def select_month(self, target_month: str):
        self.selected_month = target_month
        self.update_month_distribution(self.current_filtered_rows or self.raw_rows)
        self.trend_canvas.selected_month = target_month
        self.trend_canvas.update()
        self.update_month_nav_buttons()
        self.table_model.highlight_month = target_month
        self.table_model.layoutChanged.emit()
        if getattr(self, "drawer_frame", None) and self.drawer_frame.isVisible():
            self.open_month_drawer(target_month)

    def handle_kpi_clicked(self, key: str):
        for k, c in self.kpi_cards.items():
            c.set_selected(k == key)

        if key in ("total", "months"):
            self.tab_bar.setCurrentIndex(0)
            self.active_kpi_filter = None
            self.recalculate_and_refresh()
        elif key == "avg":
            self.trend_canvas.setFocus()
        elif key in ("up", "avg_profit"):
            self.active_kpi_filter = "up" if self.active_kpi_filter != "up" else None
            self.tab_bar.setCurrentIndex(0)
            self.recalculate_and_refresh()
        elif key in ("down", "avg_loss"):
            self.active_kpi_filter = "down" if self.active_kpi_filter != "down" else None
            self.tab_bar.setCurrentIndex(0)
            self.recalculate_and_refresh()

    def on_table_row_clicked(self, index):
        if not index.isValid():
            return
        row = index.row()
        current_tab = self.tab_bar.currentIndex()
        if row < len(self.table_model.rows):
            r_vals = self.table_model.rows[row]
            if current_tab == 0:
                month_val = r_vals[0]
                self.selected_month = month_val
                self.update_month_distribution(self.current_filtered_rows or self.raw_rows)
                self.trend_canvas.selected_month = month_val
                self.trend_canvas.update()
                self.update_month_nav_buttons()
                self.table_model.highlight_month = month_val
                self.table_model.layoutChanged.emit()
                self.open_month_drawer(month_val)
            elif current_tab == 1:
                month_val = r_vals[0]
                broker_val = r_vals[1]
                self.open_channel_drawer(broker_val, month_val)
            else:
                month_val = r_vals[0]
                broker_val = r_vals[1]
                sym_val = r_vals[2]
                self.open_stock_drawer(sym_val, month_val, broker_val)

    # -------------------------------------------------------------------------
    # Drawers (Aligning 100% with DailyPerformancePage Card-based Drawer)
    # -------------------------------------------------------------------------
    def open_month_drawer(self, target_month: str):
        self.selected_month = target_month
        rows = getattr(self, "current_filtered_rows", self.raw_rows)
        self.update_month_distribution(rows)
        self.trend_canvas.selected_month = target_month
        self.trend_canvas.update()
        self.table_model.highlight_month = target_month
        self.table_model.layoutChanged.emit()

        if hasattr(self, "drawer_prev_btn"):
            self.drawer_prev_btn.show()
            self.drawer_next_btn.show()
        self.update_month_nav_buttons()

        self.drawer_title.setText(f"单月收益详情 · {target_month}")
        m_rows = [r for r in rows if r[0] == target_month]
        if not m_rows:
            m_rows = [r for r in self.raw_rows if r[0] == target_month]
        tot_pnl = sum(float(r[20] or 0) for r in m_rows)

        self.drawer_content_lbl.setText(
            f"收盘月份：{target_month}\n"
            f"当月总收益：{fmt_signed_money(tot_pnl)}\n"
            f"持仓标的数：{len(m_rows)} 只"
        )

        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sorted_rows = sorted(m_rows, key=lambda x: float(x[20] or 0), reverse=True)
        for r in sorted_rows:
            card = QFrame()
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setStyleSheet("""
                QFrame {
                    background: #F8FAFC;
                    border: 1px solid #E2E8F0;
                    border-radius: 6px;
                }
                QFrame:hover {
                    background: #EFF6FF;
                    border: 1px solid #93C5FD;
                }
            """)
            c_lay = QHBoxLayout(card)
            c_lay.setContentsMargins(8, 6, 8, 6)

            info_v = QVBoxLayout()
            info_v.setSpacing(2)
            sym_lbl = QLabel(f"{r[3]} ({fmt_broker_cn(r[1])})")
            sym_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #2563EB;")
            sub_lbl = QLabel(f"持仓 {fmt_money(r[8])} 股 · 收盘 {fmt_money(r[10])}")
            sub_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
            info_v.addWidget(sym_lbl)
            info_v.addWidget(sub_lbl)
            c_lay.addLayout(info_v)
            c_lay.addStretch()

            pnl_v = QVBoxLayout()
            pnl_v.setSpacing(2)
            pnl_v.setAlignment(Qt.AlignmentFlag.AlignRight)

            pnl = float(r[20] or 0)
            pnl_lbl = QLabel(fmt_signed_money(pnl))
            pnl_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {'#10B981' if pnl >= 0 else '#EF4444'};")

            init_v = float(r[15] or 0)
            buy_v = float(r[16] or 0)
            base_v = init_v if init_v > 0 else buy_v
            rate = (pnl / base_v) if base_v > 0 else (float(r[13] or 0) if r[13] else 0.0)
            rate_lbl = QLabel(fmt_percent(rate))
            rate_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {'#10B981' if rate >= 0 else '#EF4444'};")

            pnl_v.addWidget(pnl_lbl)
            pnl_v.addWidget(rate_lbl)
            c_lay.addLayout(pnl_v)

            # 点击下钻到标的历史收益
            def make_stock_click(sym, ch):
                return lambda e: self.open_stock_drawer(sym, target_month, ch)
            card.mousePressEvent = make_stock_click(r[3], r[1])

            self.drawer_list_layout.addWidget(card)

        self.show_drawer()

    def open_channel_drawer(self, channel: str, month_str: str = ""):
        if month_str:
            self.selected_month = month_str
            rows = getattr(self, "current_filtered_rows", self.raw_rows)
            self.update_month_distribution(rows)
            self.trend_canvas.selected_month = month_str
            self.trend_canvas.update()

        if hasattr(self, "drawer_prev_btn"):
            self.drawer_prev_btn.hide()
            self.drawer_next_btn.hide()

        ch_cn = fmt_broker_cn(channel)
        self.drawer_title.setText(f"渠道收益详情 · {ch_cn}")
        c_rows = [r for r in self.raw_rows if r[1] == channel or fmt_broker_cn(r[1]) == ch_cn]
        tot_pnl = sum(float(r[20] or 0) for r in c_rows)

        self.drawer_content_lbl.setText(
            f"券商渠道：{ch_cn}\n"
            f"期间累计收益：{fmt_signed_money(tot_pnl)}\n"
            f"历史持仓记录数：{len(c_rows)} 条"
        )

        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sorted_c_rows = sorted(c_rows, key=lambda x: x[0], reverse=True)[:50]
        for r in sorted_c_rows:
            card = QFrame()
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setStyleSheet("""
                QFrame {
                    background: #F8FAFC;
                    border: 1px solid #E2E8F0;
                    border-radius: 6px;
                }
                QFrame:hover {
                    background: #EFF6FF;
                    border: 1px solid #93C5FD;
                }
            """)
            c_lay = QHBoxLayout(card)
            c_lay.setContentsMargins(8, 6, 8, 6)

            info_v = QVBoxLayout()
            info_v.setSpacing(2)
            sym_lbl = QLabel(f"{r[0]} · {r[3]}")
            sym_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #2563EB;")
            sub_lbl = QLabel(f"持仓 {fmt_money(r[8])} 股 · 收盘 {fmt_money(r[10])}")
            sub_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
            info_v.addWidget(sym_lbl)
            info_v.addWidget(sub_lbl)
            c_lay.addLayout(info_v)
            c_lay.addStretch()

            pnl_v = QVBoxLayout()
            pnl_v.setSpacing(2)
            pnl_v.setAlignment(Qt.AlignmentFlag.AlignRight)

            pnl = float(r[20] or 0)
            pnl_lbl = QLabel(fmt_signed_money(pnl))
            pnl_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {'#10B981' if pnl >= 0 else '#EF4444'};")
            rate = float(r[13] or 0) if r[13] else 0.0
            rate_lbl = QLabel(fmt_percent(rate))
            rate_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {'#10B981' if rate >= 0 else '#EF4444'};")

            pnl_v.addWidget(pnl_lbl)
            pnl_v.addWidget(rate_lbl)
            c_lay.addLayout(pnl_v)

            def make_m_click(m):
                return lambda e: self.open_month_drawer(m)
            card.mousePressEvent = make_m_click(r[0])

            self.drawer_list_layout.addWidget(card)

        self.show_drawer()

    def open_stock_drawer(self, symbol: str, month_str: str = "", broker_name: str = ""):
        if month_str:
            self.selected_month = month_str
            rows = getattr(self, "current_filtered_rows", self.raw_rows)
            self.update_month_distribution(rows)
            self.trend_canvas.selected_month = month_str
            self.trend_canvas.update()

        if hasattr(self, "drawer_prev_btn"):
            self.drawer_prev_btn.hide()
            self.drawer_next_btn.hide()

        self.drawer_title.setText(f"标的历史收益 · {symbol}")
        s_rows = [r for r in self.raw_rows if r[3] == symbol]
        if broker_name:
            s_rows = [r for r in s_rows if r[1] == broker_name or fmt_broker_cn(r[1]) == broker_name]
        tot_pnl = sum(float(r[20] or 0) for r in s_rows)

        self.drawer_content_lbl.setText(
            f"Ticker 代码：{symbol}\n"
            f"期间累计收益：{fmt_signed_money(tot_pnl)}\n"
            f"历史交易月数：{len(s_rows)} 个月"
        )

        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sorted_s_rows = sorted(s_rows, key=lambda x: x[0], reverse=True)[:50]
        for r in sorted_s_rows:
            is_cur_month = (month_str and r[0] == month_str)
            card = QFrame()
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            if is_cur_month:
                card.setStyleSheet("""
                    QFrame {
                        background: #EFF6FF;
                        border: 1.5px solid #3B82F6;
                        border-radius: 6px;
                    }
                    QFrame:hover {
                        background: #DBEAFE;
                    }
                """)
            else:
                card.setStyleSheet("""
                    QFrame {
                        background: #F8FAFC;
                        border: 1px solid #E2E8F0;
                        border-radius: 6px;
                    }
                    QFrame:hover {
                        background: #EFF6FF;
                        border: 1px solid #93C5FD;
                    }
                """)
            c_lay = QHBoxLayout(card)
            c_lay.setContentsMargins(8, 6, 8, 6)

            info_v = QVBoxLayout()
            info_v.setSpacing(2)
            sym_lbl = QLabel(f"{r[0]} ({fmt_broker_cn(r[1])}) {'[选中月]' if is_cur_month else ''}")
            sym_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {'#1D4ED8' if is_cur_month else '#2563EB'};")
            sub_lbl = QLabel(f"持仓 {fmt_money(r[8])} 股 · 收盘 {fmt_money(r[10])}")
            sub_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
            info_v.addWidget(sym_lbl)
            info_v.addWidget(sub_lbl)
            c_lay.addLayout(info_v)
            c_lay.addStretch()

            pnl_v = QVBoxLayout()
            pnl_v.setSpacing(2)
            pnl_v.setAlignment(Qt.AlignmentFlag.AlignRight)

            pnl = float(r[20] or 0)
            pnl_lbl = QLabel(fmt_signed_money(pnl))
            pnl_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {'#10B981' if pnl >= 0 else '#EF4444'};")
            rate = float(r[13] or 0) if r[13] else 0.0
            rate_lbl = QLabel(fmt_percent(rate))
            rate_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {'#10B981' if rate >= 0 else '#EF4444'};")

            pnl_v.addWidget(pnl_lbl)
            pnl_v.addWidget(rate_lbl)
            c_lay.addLayout(pnl_v)

            def make_m_click(m):
                return lambda e: self.open_month_drawer(m)
            card.mousePressEvent = make_m_click(r[0])

            self.drawer_list_layout.addWidget(card)

        self.show_drawer()

    def show_drawer(self):
        w = 440
        self.drawer_overlay.setGeometry(self.rect())
        self.drawer_overlay.show()
        self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
        self.drawer_frame.show()
        self.drawer_frame.raise_()

    def close_drawer(self):
        self.drawer_overlay.hide()
        self.drawer_frame.hide()

    def switch_trend_mode(self, mode: str):
        self.trend_tab_monthly.setChecked(mode == "monthly")
        self.trend_tab_cum.setChecked(mode == "cum")
        self.trend_canvas.set_mode(mode)

    def export_csv(self):
        if not self.raw_rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出每月持仓收益数据",
            f"每月持仓收益_{self.max_as_of_month or 'export'}.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return

        import csv
        headers = [
            "月份", "渠道", "分类", "Ticker", "持仓数量",
            "成本均价", "收盘价", "月末市值", "本月收益", "持仓盈亏比例"
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in self.raw_rows:
                writer.writerow([
                    r[0], r[1], r[2], r[3], r[8],
                    r[9], r[10], r[11], r[20], r[13]
                ])

    def on_person_changed(self):
        self.selected_person_id = self.person_combo.currentData()
        self.recalculate_and_refresh()

    def on_broker_changed(self):
        self.selected_broker = self.broker_combo.currentData() or ""
        self.recalculate_and_refresh()

    def on_category_changed(self):
        self.selected_category = self.category_combo.currentData() or ""
        self.recalculate_and_refresh()

    def on_search_clicked(self):
        p_id = self.person_combo.currentData()
        person_changed = (p_id != self.selected_person_id)
        self.selected_person_id = p_id
        self.selected_broker = self.broker_combo.currentData() or ""
        self.selected_category = self.category_combo.currentData() or ""
        self.search_ticker = self.ticker_input.text().strip()
        self.table_model.page = 1

        # 反向同步全局人员，确保全局统一
        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(p_id)
            finally:
                self._syncing_person = False

        self.recalculate_and_refresh()

    def on_reset_clicked(self):
        person_changed = (self.selected_person_id is not None)
        self.ticker_input.clear()
        self.search_ticker = ""
        self.category_combo.setCurrentIndex(0)
        self.selected_category = ""
        self.broker_combo.setCurrentIndex(0)
        self.selected_broker = ""
        self.person_combo.setCurrentIndex(0)
        self.selected_person_id = None
        self.active_kpi_filter = None
        self.stock_pnl_filter = "all"
        self.table_model.page = 1
        for c in self.kpi_cards.values():
            c.set_selected(False)

        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(None)
            finally:
                self._syncing_person = False

        self.recalculate_and_refresh()
