"""Modern Daily Performance Analysis Dashboard for Mac Desktop (PySide6).

Parity with Web:
1. 7 Metric KPI Cards with click-to-filter, drill-down and selection states.
2. Compact Filter Card: Person, broker, ticker, date range, category, search/reset & active chips.
3. Daily Profit Trend (Bar/Line toggle) & Broker Channel Cumulative Overview with penetration.
4. Multi-dimensional Tables:
   - Date Summary (Top 3 contributors)
   - Channel Details
   - Stock Details
   - Strict requirement: NO "操作" (Action) column, entire row is clickable.
5. Selected Date Donut Distribution with Profit/Loss/Flat quick filters.
6. Right Slide-out Drawer for Date, Channel, and Stock history details.
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
    return BROKER_NAME_MAP.get(s.upper(), BROKER_NAME_MAP.get(s, s))


class KpiMetricCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(78)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(3)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(4)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-size: 11px; font-weight: 600; color: #64748B;")
        self.arrow_label = QLabel(">")
        self.arrow_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #94A3B8;")
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.arrow_label)

        self.value_label = QLabel("—")
        self.value_label.setFont(numeric_font(15, bold=True))
        self.value_label.setStyleSheet("color: #1E293B;")

        self.sub_label = QLabel("—")
        self.sub_label.setStyleSheet("font-size: 10px; color: #94A3B8;")

        layout.addLayout(header_layout)
        layout.addWidget(self.value_label)
        layout.addWidget(self.sub_label)

        self.update_style()

    def set_data(self, val_text: str, sub_text: str, color_mode: str = "normal"):
        self.value_label.setText(val_text)
        self.sub_label.setText(sub_text)
        if color_mode == "profit":
            self.value_label.setStyleSheet("color: #10B981; font-weight: bold;")
        elif color_mode == "loss":
            self.value_label.setStyleSheet("color: #EF4444; font-weight: bold;")
        else:
            self.value_label.setStyleSheet("color: #1E293B; font-weight: bold;")

    def set_selected(self, selected: bool):
        self.selected = selected
        self.update_style()

    def update_style(self):
        if self.selected:
            self.setStyleSheet("""
                KpiMetricCard {
                    background-color: #F8FAFC;
                    border: 1.5px solid #2563EB;
                    border-radius: 8px;
                }
            """)
        else:
            self.setStyleSheet("""
                KpiMetricCard {
                    background-color: #FFFFFF;
                    border: 1px solid #E2E8F0;
                    border-radius: 8px;
                }
                KpiMetricCard:hover {
                    border: 1px solid #93C5FD;
                    background-color: #FAFCFF;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class DailyTrendCanvas(QWidget):
    date_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "daily"  # "daily" or "cum"
        self.data_points: List[Tuple[str, float, float]] = []  # (date, dailyProfit, cumProfit)
        self.hit_rects: List[Tuple[QRectF, str]] = []
        self.selected_date = ""
        self.setMouseTracking(True)

    def set_data(self, points: List[Tuple[str, float, float]], selected_date: str = ""):
        self.data_points = points
        self.selected_date = selected_date
        self.update()

    def set_mode(self, mode: str):
        self.mode = mode
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        self.hit_rects.clear()

        painter.fillRect(0, 0, w, h, QColor("#FFFFFF"))

        if not self.data_points:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无收益走势数据")
            return

        margin_left, margin_right = 58, 16
        margin_top, margin_bottom = 10, 20
        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        n = len(self.data_points)
        if n == 0 or plot_w <= 10 or plot_h <= 10:
            return

        if self.mode == "daily":
            # 每日收益柱状图：基于分位数基准（Quantile Benchmark）分层映射，彻底消除死白大面积空白
            pos_vals = [p[1] for p in self.data_points if p[1] > 0]
            neg_vals = [abs(p[1]) for p in self.data_points if p[1] < 0]

            max_pos = max(pos_vals) if pos_vals else 100.0
            max_neg = max(neg_vals) if neg_vals else 100.0

            # 计算正负收益高位基准值（88% 分位数），让 88% 的常态交易日柱子充分舒展覆盖 72% 的半区高度
            if pos_vals:
                s_pos = sorted(pos_vals)
                v_bench_pos = max(50.0, s_pos[int(len(s_pos) * 0.88)])
            else:
                v_bench_pos = 100.0

            if neg_vals:
                s_neg = sorted(neg_vals)
                v_bench_neg = max(50.0, s_neg[int(len(s_neg) * 0.88)])
            else:
                v_bench_neg = 100.0

            # 零轴自适应居中（正负各约 50% 高度）
            zero_y = margin_top + plot_h * 0.48
            h_pos_max = max(10.0, zero_y - margin_top - 4.0)
            h_neg_max = max(10.0, margin_top + plot_h - zero_y - 4.0)

            # 绘制参考线与 Y 轴刻度标签
            painter.setFont(numeric_font(9))

            # 正收益常态参考线（约 72% 高度处）
            y_bench_pos = zero_y - h_pos_max * 0.72
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            painter.drawLine(int(margin_left), int(y_bench_pos), int(w - margin_right), int(y_bench_pos))
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(QRectF(0, y_bench_pos - 8, margin_left - 6, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"+{v_bench_pos:,.0f}")

            # 负收益常态参考线（约 72% 高度处）
            y_bench_neg = zero_y + h_neg_max * 0.72
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            painter.drawLine(int(margin_left), int(y_bench_neg), int(w - margin_right), int(y_bench_neg))
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(QRectF(0, y_bench_neg - 8, margin_left - 6, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"-{v_bench_neg:,.0f}")

            # 零轴基准线
            painter.setPen(QPen(QColor("#CBD5E1"), 1.2, Qt.PenStyle.DashLine))
            painter.drawLine(int(margin_left), int(zero_y), int(w - margin_right), int(zero_y))
            painter.setPen(QColor("#64748B"))
            painter.drawText(QRectF(0, zero_y - 8, margin_left - 6, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             "$0")

            # 绘制柱状图：常态充分展开，极值平滑收顶，消除大量空白
            step_x = plot_w / n
            bar_w = max(1.8, min(14.0, step_x * 0.85))

            for i, (dt, d_pnl, _) in enumerate(self.data_points):
                cx = margin_left + (i + 0.5) * step_x
                bx = cx - bar_w / 2.0

                if d_pnl > 0:
                    if d_pnl <= v_bench_pos:
                        ratio = (d_pnl / v_bench_pos) * 0.72
                    else:
                        excess = (d_pnl - v_bench_pos) / max(1.0, max_pos - v_bench_pos)
                        ratio = 0.72 + 0.28 * (excess ** 0.5)
                    bar_h = max(3.0, min(h_pos_max, ratio * h_pos_max))
                    by = zero_y - bar_h
                    color = QColor("#10B981")
                elif d_pnl < 0:
                    abs_val = abs(d_pnl)
                    if abs_val <= v_bench_neg:
                        ratio = (abs_val / v_bench_neg) * 0.72
                    else:
                        excess = (abs_val - v_bench_neg) / max(1.0, max_neg - v_bench_neg)
                        ratio = 0.72 + 0.28 * (excess ** 0.5)
                    bar_h = max(3.0, min(h_neg_max, ratio * h_neg_max))
                    by = zero_y
                    color = QColor("#EF4444")
                else:
                    bar_h = 1.0
                    by = zero_y - 0.5
                    color = QColor("#CBD5E1")

                rect = QRectF(bx, by, bar_w, bar_h)
                hit_rect = QRectF(cx - step_x / 2.0, margin_top, step_x, plot_h)
                self.hit_rects.append((hit_rect, dt))

                if dt == self.selected_date:
                    painter.fillRect(hit_rect, QColor(37, 99, 235, 30))
                    painter.setPen(QPen(QColor("#2563EB"), 1.5))
                else:
                    painter.setPen(Qt.PenStyle.NoPen)

                painter.setBrush(color)
                painter.drawRoundedRect(rect, 1.5, 1.5)
        else:
            # Cumulative Line Chart
            vals = [p[2] for p in self.data_points]
            min_val, max_val = min(vals), max(vals)
            span = max(1e-5, max_val - min_val)
            pad = span * 0.08
            disp_min = min_val - pad
            disp_max = max_val + pad
            disp_range = disp_max - disp_min

            # Y-Axis labels & Grid
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            steps = 4
            painter.setFont(numeric_font(9))
            for i in range(steps + 1):
                y = margin_top + plot_h * (i / steps)
                painter.drawLine(int(margin_left), int(y), int(w - margin_right), int(y))
                val_at_y = disp_max - (i / steps) * disp_range
                painter.setPen(QColor("#94A3B8"))
                painter.drawText(QRectF(0, y - 8, margin_left - 6, 16),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 f"{val_at_y:,.0f}")
                painter.setPen(QPen(QColor("#F1F5F9"), 1))

            # Zero Line (if within range)
            if disp_min <= 0 <= disp_max:
                zero_y = margin_top + (disp_max / disp_range) * plot_h
                painter.setPen(QPen(QColor("#CBD5E1"), 1.2, Qt.PenStyle.DashLine))
                painter.drawLine(int(margin_left), int(zero_y), int(w - margin_right), int(zero_y))

            # Cumulative Line Chart
            step_x = plot_w / n
            path = QPainterPath()
            pts = []
            for i, (dt, _, c_pnl) in enumerate(self.data_points):
                cx = margin_left + (i + 0.5) * step_x
                cy = margin_top + (disp_max - c_pnl) / disp_range * plot_h
                pts.append((cx, cy, dt))
                hit_rect = QRectF(cx - step_x / 2.0, margin_top, step_x, plot_h)
                self.hit_rects.append((hit_rect, dt))

            if pts:
                path.moveTo(pts[0][0], pts[0][1])
                for pt in pts[1:]:
                    path.lineTo(pt[0], pt[1])

                area_path = QPainterPath(path)
                area_path.lineTo(pts[-1][0], margin_top + plot_h)
                area_path.lineTo(pts[0][0], margin_top + plot_h)
                area_path.closeSubpath()
                painter.fillPath(area_path, QColor(37, 99, 235, 25))

                painter.setPen(QPen(QColor("#2563EB"), 2))
                painter.drawPath(path)

                for cx, cy, dt in pts:
                    if dt == self.selected_date:
                        painter.setBrush(QColor("#2563EB"))
                        painter.setPen(QPen(QColor("#FFFFFF"), 2))
                        painter.drawEllipse(QRectF(cx - 5, cy - 5, 10, 10))

        # X-Axis labels
        painter.setPen(QColor("#64748B"))
        painter.setFont(QFont(".AppleSystemUIFont", 9))
        if n > 0:
            painter.drawText(QRectF(margin_left, h - margin_bottom + 4, 80, 16),
                             Qt.AlignmentFlag.AlignLeft, self.data_points[0][0])
            painter.drawText(QRectF(w - margin_right - 80, h - margin_bottom + 4, 80, 16),
                             Qt.AlignmentFlag.AlignRight, self.data_points[-1][0])
            if n > 2:
                mid_idx = n // 2
                painter.drawText(QRectF(margin_left + plot_w * 0.4, h - margin_bottom + 4, 80, 16),
                                 Qt.AlignmentFlag.AlignCenter, self.data_points[mid_idx][0])

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            for rect, dt in self.hit_rects:
                if rect.contains(pos):
                    self.selected_date = dt
                    self.date_clicked.emit(dt)
                    self.update()
                    break
        super().mousePressEvent(event)


class DailyDistributionDonut(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.total_profit = 0.0
        self.profit_amt = 0.0
        self.loss_amt = 0.0
        self.setFixedHeight(140)

    def set_data(self, total: float, p_amt: float, l_amt: float):
        self.total_profit = total
        self.profit_amt = p_amt
        self.loss_amt = l_amt
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(0, 0, w, h, QColor("#FFFFFF"))

        side = min(w, h) - 16
        cx, cy = w / 2.0, h / 2.0
        radius = side / 2.0
        inner_radius = radius * 0.72

        total_abs = max(1e-5, abs(self.profit_amt) + abs(self.loss_amt))
        profit_angle = (abs(self.profit_amt) / total_abs) * 360.0
        loss_angle = 360.0 - profit_angle

        outer_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#10B981"))
        painter.drawPie(outer_rect, int(90 * 16), int(-profit_angle * 16))

        painter.setBrush(QColor("#EF4444"))
        painter.drawPie(outer_rect, int((90 - profit_angle) * 16), int(-loss_angle * 16))

        painter.setBrush(QColor("#FFFFFF"))
        inner_rect = QRectF(cx - inner_radius, cy - inner_radius, inner_radius * 2, inner_radius * 2)
        painter.drawEllipse(inner_rect)

        # Center Text
        painter.setFont(numeric_font(13, bold=True))
        pnl_color = QColor("#10B981" if self.total_profit >= 0 else "#EF4444")
        painter.setPen(pnl_color)
        val_str = fmt_signed_money(self.total_profit, 0)
        painter.drawText(QRectF(cx - inner_radius, cy - 13, inner_radius * 2, 16),
                         Qt.AlignmentFlag.AlignCenter, val_str)

        painter.setFont(QFont(".AppleSystemUIFont", 9))
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(QRectF(cx - inner_radius, cy + 3, inner_radius * 2, 14),
                         Qt.AlignmentFlag.AlignCenter, "当日收益")


def _parse_sort_val(val: Any) -> Tuple[bool, Any]:
    """解析单元格数据用于表头排序：返回 (has_value, normalized_value)。"""
    if val is None:
        return (False, 0)
    s = str(val).strip()
    if s in ("", "—", "-"):
        return (False, 0)

    # 尝试提取只数，如 "19 只"
    if s.endswith("只"):
        num_str = s[:-1].strip()
        try:
            return (True, float(num_str.replace(",", "")))
        except ValueError:
            pass

    # 尝试提取百分比，如 "+4.90%"
    if s.endswith("%"):
        num_str = s[:-1].replace("+", "").strip()
        try:
            return (True, float(num_str.replace(",", "")))
        except ValueError:
            pass

    # 尝试提取金额或数字，如 "$2,113.71", "+$103.55", "-$440.88"
    clean_num = s.replace("$", "").replace(",", "").replace("+", "").strip()
    if clean_num.startswith("−"):  # Unicode minus
        clean_num = "-" + clean_num[1:]
    try:
        return (True, float(clean_num))
    except ValueError:
        pass

    # 日期或普通文本
    return (True, s.lower())


class ModernTableModel(QAbstractTableModel):
    """Clean, dedicated table model for daily performance views with correct headers, sorting and colors."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: Tuple[str, ...] = ()
        self.all_rows: List[List[Any]] = []
        self.rows: List[List[Any]] = []
        self.page = 1
        self.page_size = 20
        self.sort_column: int = -1
        self.sort_order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
        self.highlight_date: str = ""

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
            if getattr(self, "highlight_date", "") and len(self.rows[row_idx]) > 0 and self.rows[row_idx][0] == self.highlight_date:
                return QColor("#EFF6FF")
            return None

        is_text_col = col_name in ("券商渠道", "Ticker", "分类", "股票分类")
        is_center_col = col_name in ("日期 (收盘)", "日期", "上涨股票数", "下跌股票数") or ("只" in val_str)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if is_center_col:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if is_text_col:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # 期初/期末市值、当日买入/卖出、当日净投入、当日收益/收益率、累计收益、持仓数量、成本价、收盘价等所有数值一律靠右对齐
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
                if col_name in ("当日净投入", "当日收益", "当日收益率", "持仓盈亏率"):
                    return QColor("#64748B")
            if val_str == "—":
                return QColor("#94A3B8")
            if col_name == "Ticker":
                return QColor("#2563EB")
            return QColor("#1E293B")

        if role == Qt.ItemDataRole.FontRole:
            if is_text_col or col_name in ("日期 (收盘)", "日期"):
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
        self._apply_sort()

    def _apply_sort(self):
        if self.sort_column < 0 or not self.all_rows:
            self._apply_pagination()
            return

        col = self.sort_column
        reverse = (self.sort_order == Qt.SortOrder.DescendingOrder)

        def sort_key(row):
            if col >= len(row):
                return (-1 if reverse else 1, 0)
            val = row[col]
            has_val, v = _parse_sort_val(val)
            if not has_val:
                return (-1 if reverse else 1, 0)
            return (1 if reverse else 0, v)

        self.all_rows.sort(key=sort_key, reverse=reverse)
        self.page = 1
        self._apply_pagination()
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.columns) - 1)

    def set_columns_and_rows(self, columns: Tuple[str, ...], rows: List[List[Any]]):
        self.columns = columns
        self.all_rows = list(rows)
        if 0 <= self.sort_column < len(self.columns):
            self._apply_sort()
        else:
            self._apply_pagination()

    def set_page(self, page: int):
        self.page = max(1, page)
        self._apply_pagination()

    def _apply_pagination(self):
        self.beginResetModel()
        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()


class DailyPerformancePage(QWidget):
    """Full-featured Daily Performance Analysis Dashboard."""
    drill = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.analytics = None
        self.raw_rows: List[Tuple] = []

        # State
        self.selected_person_id: Optional[int] = None
        self.selected_broker: str = ""
        self.search_ticker: str = ""
        self.selected_category: str = ""
        self.start_date: str = "2025-01-01"
        self.end_date: str = ""
        self.active_kpi_filter: Optional[str] = None
        self.stock_pnl_filter: str = "all"
        self.stock_filter_date: str = ""
        self.selected_date: str = ""
        self.max_as_of_date: str = ""
        self._syncing_person = False
        self._syncing_broker = False

        # Root Layout with Outer Smooth Scrolling
        root_vbox = QVBoxLayout(self)
        root_vbox.setContentsMargins(0, 0, 0, 0)
        root_vbox.setSpacing(0)

        self.outer_scroll = QScrollArea(self)
        self.outer_scroll.setWidgetResizable(True)
        self.outer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.outer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.outer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.outer_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: #F1F5F9;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #CBD5E1;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94A3B8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        content_container = QWidget()
        content_container.setStyleSheet("background-color: transparent;")
        main_layout = QVBoxLayout(content_container)
        main_layout.setContentsMargins(14, 8, 14, 16)
        main_layout.setSpacing(10)

        self.outer_scroll.setWidget(content_container)
        root_vbox.addWidget(self.outer_scroll)

        # 1. Single-row Compact Filter Toolbar (NO huge blank space, NO duplicate headers)
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
        filter_h = QHBoxLayout(filter_card)
        filter_h.setContentsMargins(10, 8, 10, 8)
        filter_h.setSpacing(8)

        # Person Filter
        self.person_combo = QComboBox()
        self.person_combo.setFixedWidth(105)
        self._populate_persons()
        self.person_combo.currentIndexChanged.connect(self._on_person_combo_changed)
        filter_h.addWidget(self.person_combo)

        # Broker Filter
        self.broker_combo = QComboBox()
        self.broker_combo.setFixedWidth(110)
        self.broker_combo.addItem("全部渠道", "")
        filter_h.addWidget(self.broker_combo)

        # Ticker Search
        self.ticker_input = QLineEdit()
        self.ticker_input.setPlaceholderText("输入代码或名称")
        self.ticker_input.setFixedWidth(130)
        self.ticker_input.returnPressed.connect(self.apply_filter)
        filter_h.addWidget(self.ticker_input)

        # Date Range
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate(2025, 1, 1))
        self.start_date_edit.setFixedWidth(105)
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.setFixedWidth(105)

        filter_h.addWidget(self.start_date_edit)
        filter_h.addWidget(QLabel("~"))
        filter_h.addWidget(self.end_date_edit)

        # Category Filter
        self.category_combo = QComboBox()
        self.category_combo.setFixedWidth(100)
        self.category_combo.addItem("全部分类", "")
        filter_h.addWidget(self.category_combo)

        # Buttons
        self.query_btn = QPushButton("查询")
        self.query_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.query_btn.setFixedHeight(28)
        self.query_btn.setStyleSheet("""
            QPushButton {
                background-color: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 5px;
                padding: 0 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #1D4ED8; }
        """)
        self.query_btn.clicked.connect(self.apply_filter)
        filter_h.addWidget(self.query_btn)

        self.reset_btn = QPushButton("重置")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFixedHeight(28)
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #F1F5F9;
                color: #475569;
                border: 1px solid #CBD5E1;
                border-radius: 5px;
                padding: 0 10px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #E2E8F0; }
        """)
        self.reset_btn.clicked.connect(self.reset_filters)
        filter_h.addWidget(self.reset_btn)

        filter_h.addStretch()

        # Data As of Badge & Export on the right of toolbar
        self.as_of_badge = QLabel("数据截至：—（收盘）")
        self.as_of_badge.setStyleSheet("""
            background-color: #F1F5F9;
            color: #475569;
            font-size: 12px;
            font-weight: 500;
            padding: 4px 10px;
            border-radius: 5px;
        """)
        filter_h.addWidget(self.as_of_badge)

        self.export_btn = QPushButton("↓ 导出")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setFixedHeight(28)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #FFFFFF;
                color: #2563EB;
                border: 1px solid #CBD5E1;
                border-radius: 5px;
                padding: 0 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #EFF6FF;
                border-color: #2563EB;
            }
        """)
        self.export_btn.clicked.connect(self.export_csv)
        filter_h.addWidget(self.export_btn)

        main_layout.addWidget(filter_card)

        # 2. Seven KPI Cards Grid
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(8)
        self.kpi_cards: Dict[str, KpiMetricCard] = {}

        kpi_defs = [
            ("total", "期间总收益"),
            ("days", "交易日数"),
            ("avg", "日均收益"),
            ("up", "上涨天数"),
            ("down", "下跌天数"),
            ("avg_profit", "平均收益"),
            ("avg_loss", "平均亏损"),
        ]
        for col_idx, (key, title) in enumerate(kpi_defs):
            card = KpiMetricCard(key, title, self)
            card.clicked.connect(self.handle_kpi_clicked)
            self.kpi_cards[key] = card
            kpi_grid.addWidget(card, 0, col_idx)

        main_layout.addLayout(kpi_grid)

        # 3. Middle Section: Trend Chart (Left) + Broker Cumulative Overview (Right)
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
        self.trend_tab_daily = QPushButton("每日收益走势")
        self.trend_tab_cum = QPushButton("累计收益走势")
        for btn in (self.trend_tab_daily, self.trend_tab_cum):
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
        self.trend_tab_daily.setChecked(True)
        self.trend_tab_daily.clicked.connect(lambda: self.switch_trend_mode("daily"))
        self.trend_tab_cum.clicked.connect(lambda: self.switch_trend_mode("cum"))

        trend_top.addWidget(self.trend_tab_daily)
        trend_top.addWidget(self.trend_tab_cum)
        trend_top.addStretch()
        trend_vbox.addLayout(trend_top)

        self.trend_canvas = DailyTrendCanvas(self)
        self.trend_canvas.date_clicked.connect(self.select_date)
        trend_vbox.addWidget(self.trend_canvas)
        self.mid_splitter.addWidget(trend_card)

        # Broker Cumulative Overview Card
        broker_card = QFrame()
        broker_card.setFixedHeight(260)
        broker_card.setMinimumWidth(360)
        broker_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        broker_vbox = QVBoxLayout(broker_card)
        broker_vbox.setContentsMargins(12, 6, 12, 6)
        broker_vbox.setSpacing(4)

        b_header = QHBoxLayout()
        b_title = QLabel("渠道收益概览 <span style='font-size: 11px; color: #64748B;'>(期间累计)</span>")
        b_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #1E293B;")
        b_detail_link = QLabel("<a href='#channel' style='color: #2563EB; text-decoration: none;'>查看详情 →</a>")
        b_detail_link.setOpenExternalLinks(False)
        b_detail_link.linkActivated.connect(lambda: self.tab_bar.setCurrentIndex(1))
        b_header.addWidget(b_title)
        b_header.addStretch()
        b_header.addWidget(b_detail_link)
        broker_vbox.addLayout(b_header)

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
        main_layout.addWidget(self.mid_splitter)

        # 4. Full-Width Daily Attribution Section (单日收益归因)
        self.attr_card = QFrame()
        self.attr_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        attr_main_vbox = QVBoxLayout(self.attr_card)
        attr_main_vbox.setContentsMargins(14, 10, 14, 10)
        attr_main_vbox.setSpacing(8)

        # Header: Icon + Title + "点击联动" Badge + Subtitle + Stretch + Prev/Next Date Buttons
        attr_hdr = QHBoxLayout()
        attr_hdr.setSpacing(8)

        attr_icon_lbl = QLabel("📊")
        attr_icon_lbl.setStyleSheet("font-size: 15px;")
        attr_hdr.addWidget(attr_icon_lbl)

        self.attr_title = QLabel("单日收益归因 (—)")
        self.attr_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #1E293B;")
        attr_hdr.addWidget(self.attr_title)

        badge_lbl = QLabel("点击联动")
        badge_lbl.setStyleSheet("""
            background-color: #EFF6FF;
            color: #2563EB;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 4px;
            border: 1px solid #BFDBFE;
        """)
        attr_hdr.addWidget(badge_lbl)

        attr_sub_lbl = QLabel("深度拆解单日收益构成：亏损/盈利贡献排行、盈亏结构与股票明细。")
        attr_sub_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        attr_hdr.addWidget(attr_sub_lbl)

        attr_hdr.addStretch()

        self.prev_date_btn = QPushButton("◀ 上一日")
        self.next_date_btn = QPushButton("下一日 ▶")
        for btn in (self.prev_date_btn, self.next_date_btn):
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
        self.prev_date_btn.clicked.connect(lambda: self.navigate_date(-1))
        self.next_date_btn.clicked.connect(lambda: self.navigate_date(1))
        attr_hdr.addWidget(self.prev_date_btn)
        attr_hdr.addWidget(self.next_date_btn)
        attr_main_vbox.addLayout(attr_hdr)

        # Three-Column Horizontal Layout
        attr_cols_layout = QHBoxLayout()
        attr_cols_layout.setSpacing(10)

        # Column 1: 当日盈亏结构
        col1_card = QFrame()
        col1_card.setStyleSheet("background-color: #FAFCFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        col1_vbox = QVBoxLayout(col1_card)
        col1_vbox.setContentsMargins(10, 8, 10, 8)
        col1_vbox.setSpacing(6)

        c1_hdr = QLabel("当日盈亏结构 <span style='color:#94A3B8; font-size:11px;'>ⓘ</span>")
        c1_hdr.setStyleSheet("font-size: 13px; font-weight: 700; color: #1E293B;")
        col1_vbox.addWidget(c1_hdr)

        c1_body = QHBoxLayout()
        c1_body.setContentsMargins(0, 0, 0, 0)
        c1_body.setSpacing(8)

        self.donut_canvas = DailyDistributionDonut(self)
        self.donut_canvas.setFixedSize(130, 130)
        c1_body.addWidget(self.donut_canvas)

        c1_btn_vbox = QVBoxLayout()
        c1_btn_vbox.setSpacing(6)
        c1_btn_vbox.setAlignment(Qt.AlignmentFlag.AlignVCenter)

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
        self.donut_p_btn.clicked.connect(lambda: self.filter_stock_pnl_by_date("profit"))
        self.donut_l_btn.clicked.connect(lambda: self.filter_stock_pnl_by_date("loss"))

        c1_btn_vbox.addWidget(self.donut_p_btn)
        c1_btn_vbox.addWidget(self.donut_l_btn)
        c1_body.addLayout(c1_btn_vbox)
        col1_vbox.addLayout(c1_body)
        attr_cols_layout.addWidget(col1_card, 1)

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
        th_pnl = QLabel("当日收益")
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

        self.loss_top_vbox = QVBoxLayout()
        self.loss_top_vbox.setSpacing(3)
        col2_vbox.addLayout(self.loss_top_vbox)
        col2_vbox.addStretch()
        attr_cols_layout.addWidget(col2_card, 1)

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
        th3_pnl = QLabel("当日收益")
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

        self.profit_top_vbox = QVBoxLayout()
        self.profit_top_vbox.setSpacing(3)
        col3_vbox.addLayout(self.profit_top_vbox)
        col3_vbox.addStretch()
        attr_cols_layout.addWidget(col3_card, 1)

        attr_main_vbox.addLayout(attr_cols_layout)

        # Footer Hint
        hint_bar = QFrame()
        hint_bar.setStyleSheet("background-color: #F8FAFC; border-radius: 4px; padding: 2px 8px;")
        hb_layout = QHBoxLayout(hint_bar)
        hb_layout.setContentsMargins(6, 3, 6, 3)
        hint_lbl = QLabel("ⓘ 点击日期走势、渠道概览或股票排行可联动下方明细与右侧抽屉。")
        hint_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        hb_layout.addWidget(hint_lbl)
        attr_main_vbox.addWidget(hint_bar)

        main_layout.addWidget(self.attr_card)

        # 5. Full-Width Multi-dimensional Table
        table_card = QFrame()
        table_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        table_vbox = QVBoxLayout(table_card)
        table_vbox.setContentsMargins(12, 8, 12, 8)
        table_vbox.setSpacing(6)

        tab_h = QHBoxLayout()
        self.tab_bar = QTabBar()
        self.tab_bar.setExpanding(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.tab_bar.setUsesScrollButtons(False)
        self.tab_bar.addTab("📅 按日期汇总")
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
                border: none;
                margin-right: 28px;
            }
            QTabBar::tab:selected {
                color: #2563EB;
                border-bottom: 2.5px solid #2563EB;
            }
            QTabBar::tab:hover:!selected {
                color: #1E293B;
            }
        """)
        self.tab_bar.currentChanged.connect(self.on_table_tab_changed)
        tab_h.addWidget(self.tab_bar)
        tab_h.addStretch()
        self.table_count_lbl = QLabel("共 0 条记录")
        self.table_count_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        tab_h.addWidget(self.table_count_lbl)
        table_vbox.addLayout(tab_h)

        # Standard QTableView (NO OPERATION COLUMN, NO TRADE_COLUMNS OVERWRITE)
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
        # 完全展示 20 行高度并禁用内层纵向滚动条，由最外层页面滚动
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
        self.page_num_lbl = QLabel("第 1 页")
        self.page_num_lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #1E293B;")
        self.next_page_btn = QPushButton("下一页")
        for btn in (self.prev_page_btn, self.next_page_btn):
            btn.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF;
                    border: 1px solid #CBD5E1;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                }
                QPushButton:hover:!disabled {
                    border-color: #2563EB;
                    color: #2563EB;
                }
                QPushButton:disabled {
                    opacity: 0.5;
                }
            """)
        self.prev_page_btn.clicked.connect(lambda: self.change_page(-1))
        self.next_page_btn.clicked.connect(lambda: self.change_page(1))
        page_h.addWidget(self.prev_page_btn)
        page_h.addWidget(self.page_num_lbl)
        page_h.addWidget(self.next_page_btn)
        table_vbox.addLayout(page_h)

        main_layout.addWidget(table_card)

        # 5. Right Side Detail Drawer & Transparent Overlay
        self.drawer_overlay = QWidget(self)
        self.drawer_overlay.setStyleSheet("background-color: rgba(15, 23, 42, 0.20);")
        self.drawer_overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.drawer_overlay.hide()
        self.drawer_overlay.mousePressEvent = lambda e: self.close_drawer()

        self.drawer_frame = QFrame(self)
        self.drawer_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-left: 1px solid #CBD5E1;
            }
        """)
        self.drawer_frame.hide()

        drawer_vbox = QVBoxLayout(self.drawer_frame)
        drawer_vbox.setContentsMargins(14, 12, 14, 12)
        drawer_vbox.setSpacing(10)

        drawer_top = QHBoxLayout()
        self.drawer_title = QLabel("单日收益详情")
        self.drawer_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #1E293B;")
        self.drawer_close_btn = QPushButton("✕")
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

    def _get_persons(self) -> List[Dict[str, Any]]:
        main_win = getattr(self, "main_window", None) or self.parent()
        if main_win and hasattr(main_win, "info") and main_win.info:
            persons = getattr(main_win.info, "persons", None)
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
        main_win = getattr(self, "main_window", None) or self.parent()
        self.person_combo.blockSignals(True)
        cur_data = self.selected_person_id if self.selected_person_id is not None else self.person_combo.currentData()
        self.person_combo.clear()

        # 优先复用全局人员筛选器中的选项（保持完全一致的名称和顺序）
        if main_win and hasattr(main_win, "person") and main_win.person.count() > 0:
            for i in range(main_win.person.count()):
                text = main_win.person.itemText(i)
                data = main_win.person.itemData(i)
                label = "全部人员" if "全部" in text else text
                self.person_combo.addItem(label, data)
        else:
            self.person_combo.addItem("全部人员", None)
            self.person_combo.addItem("他人", "OTHERS")
            persons = self._get_persons()
            for p in persons:
                if p.get("enabled", True):
                    self.person_combo.addItem(p["name"], p["id"])

        idx = self.person_combo.findData(cur_data)
        if idx >= 0:
            self.person_combo.setCurrentIndex(idx)
        else:
            self.person_combo.setCurrentIndex(0)
        self.person_combo.blockSignals(False)

    def _on_person_combo_changed(self):
        if getattr(self, "_syncing_person", False):
            return
        self.apply_filter()

    def activate(self):
        self.show()

    def update_data(self, analytics):
        self.analytics = analytics
        if not analytics or "每日持仓收益" not in analytics.reports:
            return

        report = analytics.reports["每日持仓收益"]
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

        # Max date
        dates = [r[0] for r in self.raw_rows if r[0]]
        if dates:
            self.max_as_of_date = max(dates)
            self.as_of_badge.setText(f"数据截至：{self.max_as_of_date}（收盘）")
            if not self.selected_date:
                self.selected_date = self.max_as_of_date
            self.end_date_edit.setDate(QDate.fromString(self.max_as_of_date, "yyyy-MM-dd"))

        self.recalculate_and_refresh()

    def sync_person_from_main(self, person_id: Any):
        """主窗口全局人员切换时，同步更新本地下拉框。"""
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
        """主窗口全局渠道切换时，同步更新本地下拉框。"""
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
                    if (
                        self.broker_combo.itemData(i) == clean
                        or self.broker_combo.itemText(i) == clean
                        or self.broker_combo.itemText(i) == fmt_broker_cn(clean)
                    ):
                        idx = i
                        break
            self.broker_combo.setCurrentIndex(idx)
            self.broker_combo.blockSignals(False)
            self.recalculate_and_refresh()
        finally:
            self._syncing_broker = False

    def apply_filter(self):
        p_id = self.person_combo.currentData()
        b_data = self.broker_combo.currentData()

        person_changed = (p_id != self.selected_person_id)
        self.selected_person_id = p_id
        self.selected_broker = b_data or ""
        self.search_ticker = self.ticker_input.text().strip().upper()
        self.selected_category = self.category_combo.currentText()
        if self.selected_category == "全部分类":
            self.selected_category = ""
        self.start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        self.end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        self.table_model.page = 1

        # 反向同步全局人员和渠道，确保全局统一
        main_win = getattr(self, "main_window", None) or self.parent()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(p_id)
            finally:
                self._syncing_person = False

        self.recalculate_and_refresh()

    def reset_filters(self):
        self.person_combo.setCurrentIndex(0)
        self.broker_combo.setCurrentIndex(0)
        self.ticker_input.clear()
        self.category_combo.setCurrentIndex(0)
        self.start_date_edit.setDate(QDate(2025, 1, 1))
        if self.max_as_of_date:
            self.end_date_edit.setDate(QDate.fromString(self.max_as_of_date, "yyyy-MM-dd"))
        else:
            self.end_date_edit.setDate(QDate.currentDate())
        
        person_changed = (self.selected_person_id is not None)
        self.selected_person_id = None
        self.selected_broker = ""
        self.search_ticker = ""
        self.selected_category = ""
        self.start_date = "2025-01-01"
        self.end_date = self.max_as_of_date
        self.active_kpi_filter = None
        self.stock_pnl_filter = "all"
        self.table_model.page = 1

        main_win = getattr(self, "main_window", None) or self.parent()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(None)
            finally:
                self._syncing_person = False

        self.recalculate_and_refresh()

    def recalculate_and_refresh(self):
        filtered: List[Tuple] = []
        broker_overview_rows: List[Tuple] = []
        for r in self.raw_rows:
            d, b, cat, sym = r[0], r[1], r[2], r[3]
            if self.search_ticker and self.search_ticker not in sym:
                continue
            if self.selected_category and cat != self.selected_category:
                continue
            if self.start_date and d < self.start_date:
                continue
            if self.end_date and d > self.end_date:
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

        # Group by Date
        date_map: Dict[str, Dict[str, Any]] = {}
        for r in filtered:
            d = r[0]
            if d not in date_map:
                date_map[d] = {
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
            item = date_map[d]
            item["init"] += float(r[15] or 0)
            item["end"] += float(r[11] or 0)
            pnl = float(r[19] or 0)
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

        dates_asc = sorted(date_map.keys())
        trend_pts = []
        cum_pnl = 0.0
        date_summaries = []

        total_pnl = 0.0
        up_days = 0
        down_days = 0
        max_pnl = -float("inf")
        max_pnl_d = "—"
        max_loss = float("inf")
        max_loss_d = "—"

        base_init = 0.0
        for d in dates_asc:
            if date_map[d]["init"] > 0:
                base_init = date_map[d]["init"]
                break
            elif date_map[d]["end"] > 0:
                base_init = date_map[d]["end"]
                break

        up_pnl_sum = 0.0
        down_pnl_sum = 0.0
        for d in dates_asc:
            data = date_map[d]
            d_pnl = data["pnl"]
            cum_pnl += d_pnl
            trend_pts.append((d, d_pnl, cum_pnl))

            total_pnl += d_pnl
            if d_pnl > 0:
                up_days += 1
                up_pnl_sum += d_pnl
            elif d_pnl < 0:
                down_days += 1
                down_pnl_sum += d_pnl

            if d_pnl > max_pnl:
                max_pnl = d_pnl
                max_pnl_d = d
            if d_pnl < max_loss:
                max_loss = d_pnl
                max_loss_d = d

            init_v = data["init"]
            rate = (d_pnl / init_v) if init_v > 0 else 0.0

            date_summaries.append([
                d,
                fmt_money(init_v, 2),
                fmt_money(data['end'], 2),
                fmt_money(data['buy'], 2),
                fmt_money(data['sell'], 2),
                fmt_signed_money(data['net'], 2),
                fmt_signed_money(d_pnl, 2),
                fmt_percent(rate, 2),
                fmt_signed_money(cum_pnl, 2),
                f"{data['up_stocks']} 只",
                f"{data['down_stocks']} 只",
            ])

        total_days = len(dates_asc)
        avg_pnl = (total_pnl / total_days) if total_days > 0 else 0.0
        avg_up_pnl = (up_pnl_sum / up_days) if up_days > 0 else 0.0
        avg_down_pnl = (down_pnl_sum / down_days) if down_days > 0 else 0.0

        # Update 7 KPI Cards
        self.kpi_cards["total"].set_data(
            fmt_signed_money(total_pnl),
            "",
            "profit" if total_pnl >= 0 else "loss",
        )
        self.kpi_cards["days"].set_data(
            f"{total_days} 天",
            f"{dates_asc[0] if dates_asc else '—'} 至 {dates_asc[-1] if dates_asc else '—'}",
        )
        self.kpi_cards["avg"].set_data(
            fmt_signed_money(avg_pnl),
            "日均净现金额",
            "profit" if avg_pnl >= 0 else "loss",
        )
        self.kpi_cards["up"].set_data(
            f"{up_days} 天",
            f"占比 {(up_days / total_days * 100):.1f}%" if total_days > 0 else "—",
            "profit",
        )
        self.kpi_cards["down"].set_data(
            f"{down_days} 天",
            f"占比 {(down_days / total_days * 100):.1f}%" if total_days > 0 else "—",
            "loss",
        )
        self.kpi_cards["avg_profit"].set_data(
            fmt_signed_money(avg_up_pnl),
            f"上涨日均值 ({up_days}天)",
            "profit",
        )
        self.kpi_cards["avg_loss"].set_data(
            fmt_signed_money(avg_down_pnl),
            f"下跌日均值 ({down_days}天)",
            "loss",
        )

        # Update Trend Canvas
        self.trend_canvas.set_data(trend_pts, self.selected_date)

        # Update Channel Cumulative Overview (保留所有券商渠道以供全局对比，选中的渠道以蓝色高亮显式呈现)
        self.update_broker_overview(broker_overview_rows)

        # Update Selected Date Distribution
        self.current_filtered_rows = filtered
        self.update_date_distribution(filtered)

        # Update Active Table Tab
        self.refresh_table_view(filtered, date_summaries)

    def update_broker_overview(self, overview_rows: List[Tuple]):
        while self.broker_list_layout.count():
            item = self.broker_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        broker_pnl: Dict[str, float] = {}
        for r in overview_rows:
            b = r[1]
            broker_pnl[b] = broker_pnl.get(b, 0.0) + float(r[19] or 0)

        total_abs = max(1e-5, sum(abs(v) for v in broker_pnl.values()))
        sorted_brokers = sorted(broker_pnl.items(), key=lambda x: x[1], reverse=True)

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

            # 鲜明蓝色条形柱状图（使用 QProgressBar 确保高对比度和真实填充）
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
            pnl_lbl.setFixedWidth(85)
            pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pnl_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {'#10B981' if pnl >= 0 else '#EF4444'};")
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
        self.apply_filter()

    def get_available_dates(self) -> List[str]:
        rows = self.current_filtered_rows if self.current_filtered_rows else self.raw_rows
        dates = sorted({r[0] for r in rows if r[0]})
        if not dates and self.raw_rows:
            dates = sorted({r[0] for r in self.raw_rows if r[0]})
        return dates

    def update_date_nav_buttons(self):
        dates = self.get_available_dates()
        if not dates or not hasattr(self, "prev_date_btn"):
            return

        target_d = self.selected_date or self.max_as_of_date or dates[-1]
        try:
            idx = dates.index(target_d)
        except ValueError:
            idx = len(dates) - 1

        self.prev_date_btn.setEnabled(idx > 0)
        self.next_date_btn.setEnabled(idx < len(dates) - 1)

    def navigate_date(self, direction: int):
        dates = self.get_available_dates()
        if not dates:
            return

        target_d = self.selected_date or self.max_as_of_date or dates[-1]
        try:
            idx = dates.index(target_d)
        except ValueError:
            idx = len(dates) - 1

        new_idx = idx + direction
        if 0 <= new_idx < len(dates):
            new_date = dates[new_idx]
            self.selected_date = new_date
            self.table_model.highlight_date = new_date
            self.table_model.layoutChanged.emit()
            self.update_date_distribution(self.current_filtered_rows or self.raw_rows)
            self.trend_canvas.selected_date = new_date
            self.trend_canvas.update()
            self.update_date_nav_buttons()
            if getattr(self, "drawer_frame", None) and self.drawer_frame.isVisible():
                self.open_date_drawer(new_date)

    def update_date_distribution(self, filtered_rows: List[Tuple]):
        target_d = self.selected_date or self.max_as_of_date
        self.update_attribution_section(target_d, filtered_rows)

    def update_attribution_section(self, target_d: str, filtered_rows: Optional[List[Tuple]] = None):
        if not target_d:
            target_d = self.selected_date or self.max_as_of_date
        if filtered_rows is None:
            filtered_rows = self.current_filtered_rows or self.raw_rows

        if not hasattr(self, "attr_title"):
            return

        self.attr_title.setText(f"单日收益归因 ({target_d})")

        p_cnt, p_amt = 0, 0.0
        l_cnt, l_amt = 0, 0.0
        f_cnt, f_amt = 0, 0.0
        tot = 0.0

        day_rows = []
        for r in filtered_rows:
            if r[0] == target_d:
                day_rows.append(r)
                pnl = float(r[19] or 0)
                tot += pnl
                if pnl > 0:
                    p_cnt += 1
                    p_amt += pnl
                elif pnl < 0:
                    l_cnt += 1
                    l_amt += pnl
                else:
                    f_cnt += 1
                    f_amt += pnl

        # 1. 更新左侧 Donut 与盈亏概览按钮
        self.donut_canvas.set_data(tot, p_amt, l_amt)
        self.donut_p_btn.setText(f"● 盈利股票   {p_cnt} 只    {fmt_signed_money(p_amt)}")
        self.donut_l_btn.setText(f"● 亏损股票   {l_cnt} 只    {fmt_signed_money(l_amt)}")

        # 2. 清空并填充中栏：亏损贡献排行 Top 5
        while self.loss_top_vbox.count():
            item = self.loss_top_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        loss_rows = [r for r in day_rows if float(r[19] or 0) < 0]
        loss_rows.sort(key=lambda x: float(x[19] or 0))  # 亏损金额最大的排在最前
        top5_loss = loss_rows[:5]
        total_loss_abs = max(1e-5, abs(l_amt))

        if not top5_loss:
            empty_lbl = QLabel("当日无亏损标的")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lbl.setStyleSheet("font-size: 12px; color: #94A3B8; padding: 24px 0;")
            self.loss_top_vbox.addWidget(empty_lbl)
        else:
            rank_bg_colors = ["#EF4444", "#F97316", "#FB923C", "#94A3B8", "#CBD5E1"]
            for idx, r in enumerate(top5_loss):
                rank = idx + 1
                sym = r[3]
                broker = fmt_broker_cn(r[1])
                pnl = float(r[19] or 0)
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

                def make_click_handler(s=sym, d=target_d):
                    return lambda e: self.open_stock_drawer(s, d)
                item_frame.mousePressEvent = make_click_handler(sym, target_d)

                self.loss_top_vbox.addWidget(item_frame)

        # 3. 清空并填充右栏：盈利贡献排行 Top 5
        while self.profit_top_vbox.count():
            item = self.profit_top_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        profit_rows = [r for r in day_rows if float(r[19] or 0) > 0]
        profit_rows.sort(key=lambda x: float(x[19] or 0), reverse=True)  # 盈利金额最大的排在最前
        top5_profit = profit_rows[:5]
        total_profit_abs = max(1e-5, p_amt)

        if not top5_profit:
            empty_box = QFrame()
            empty_box.setStyleSheet("background: transparent; border: none;")
            eb_lay = QVBoxLayout(empty_box)
            eb_lay.setContentsMargins(10, 18, 10, 18)
            eb_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            eb_lay.setSpacing(4)

            icon_lbl = QLabel("📊")
            icon_lbl.setStyleSheet("font-size: 26px;")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t_lbl = QLabel("当日无盈利标的")
            t_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700; color: #1E293B;")
            t_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sub_lbl = QLabel("当日所有股票均为亏损或持平，暂无盈利贡献。")
            sub_lbl.setStyleSheet("font-size: 11px; color: #94A3B8;")
            sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            eb_lay.addWidget(icon_lbl)
            eb_lay.addWidget(t_lbl)
            eb_lay.addWidget(sub_lbl)
            self.profit_top_vbox.addWidget(empty_box)
        else:
            rank_bg_colors = ["#10B981", "#34D399", "#6EE7B7", "#94A3B8", "#CBD5E1"]
            for idx, r in enumerate(top5_profit):
                rank = idx + 1
                sym = r[3]
                broker = fmt_broker_cn(r[1])
                pnl = float(r[19] or 0)
                contrib = (pnl / total_profit_abs) * 100.0

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
                        background: #ECFDF5;
                        border-color: #A7F3D0;
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
                pnl_lbl.setStyleSheet("font-size: 11.5px; font-weight: 600; color: #10B981;")
                i_lay.addWidget(pnl_lbl)

                bar = QProgressBar()
                bar.setFixedHeight(7)
                bar.setTextVisible(False)
                bar.setRange(0, 1000)
                bar.setValue(int(max(1, min(100, contrib)) * 10))
                bar.setStyleSheet("""
                    QProgressBar {
                        background-color: #D1FAE5;
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

                def make_click_handler(s=sym, d=target_d):
                    return lambda e: self.open_stock_drawer(s, d)
                item_frame.mousePressEvent = make_click_handler(sym, target_d)

                self.profit_top_vbox.addWidget(item_frame)

        self.update_date_nav_buttons()

    def filter_stock_pnl_by_date(self, mode: str):
        self.stock_pnl_filter = mode
        self.stock_filter_date = self.selected_date or self.max_as_of_date
        self.tab_bar.setCurrentIndex(2)
        self.recalculate_and_refresh()

    def filter_stock_pnl(self, mode: str):
        self.filter_stock_pnl_by_date(mode)

    def on_table_tab_changed(self, idx: int):
        self.table_model.page = 1
        self.table_model.sort_column = -1
        # 如果切出股票明细且并非点击归因卡片进入，恢复全部日期
        if idx != 2:
            self.stock_filter_date = ""
            self.stock_pnl_filter = "all"
        self.recalculate_and_refresh()

    def on_header_section_clicked(self, logical_index: int):
        self.table_model.toggle_sort(logical_index)
        self.update_pagination_ui()

    def refresh_table_view(self, filtered_rows: List[Tuple], date_summaries: List[List[Any]]):
        self.table_model.highlight_date = self.selected_date
        current_tab = self.tab_bar.currentIndex()
        header = self.table_view.horizontalHeader()

        if current_tab == 0:
            # 📅 按日期汇总
            cols = ("日期 (收盘)", "期初市值", "期末市值", "当日买入", "当日卖出", "当日净投入", "当日收益", "当日收益率", "累计收益", "上涨股票数", "下跌股票数")
            rows = list(reversed(date_summaries))
            if self.active_kpi_filter == "up":
                rows = [r for r in rows if "+" in str(r[6])]
            elif self.active_kpi_filter == "down":
                rows = [r for r in rows if "-" in str(r[6])]

            self.table_model.set_columns_and_rows(cols, rows)
            self.table_count_lbl.setText(f"共 {len(rows)} 条记录")

        elif current_tab == 1:
            # 🏛️ 按渠道明细（与按日期汇总展示相同结构的资金流动与收益指标）
            cols = ("日期", "券商渠道", "期初市值", "期末市值", "当日买入", "当日卖出", "当日净投入", "当日收益", "当日收益率", "累计收益", "上涨股票数", "下跌股票数")
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
                pnl = float(r[19] or 0)
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

            # 各渠道分别计算按时间升序的累计收益
            broker_history: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
            for (d, b), data in channel_map.items():
                if b not in broker_history:
                    broker_history[b] = []
                broker_history[b].append((d, data))

            c_rows = []
            for b, entries in broker_history.items():
                entries.sort(key=lambda x: x[0])
                b_cum = 0.0
                for d, data in entries:
                    b_cum += data["pnl"]
                    init_v = data["init"]
                    rate = (data["pnl"] / init_v) if init_v > 0 else (data["pnl"] / data["buy"] if data["buy"] > 0 else 0.0)
                    c_rows.append([
                        d,
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

            # 默认按日期降序，同日期按渠道名称排序
            c_rows.sort(key=lambda x: (x[0], x[1]), reverse=True)

            self.table_model.set_columns_and_rows(cols, c_rows)
            self.table_count_lbl.setText(f"共 {len(c_rows)} 条记录")

        else:
            # 📈 按股票明细
            cols = ("日期", "券商渠道", "Ticker", "分类", "持仓数量", "成本价", "收盘价", "当日收益", "当日收益率", "累计收益", "持仓盈亏率", "日末市值")

            # 按时间升序计算每只标的随日期的累计收益
            stock_cum_map: Dict[Tuple[str, str, str], float] = {}
            sorted_raw = sorted(self.raw_rows, key=lambda x: x[0])
            cum_tracker: Dict[Tuple[str, str], float] = {}
            for sr in sorted_raw:
                k = (sr[1], sr[3])
                cum_tracker[k] = cum_tracker.get(k, 0.0) + float(sr[19] or 0)
                stock_cum_map[(sr[0], sr[1], sr[3])] = cum_tracker[k]

            s_rows = []
            for r in filtered_rows:
                if self.stock_filter_date and r[0] != self.stock_filter_date:
                    continue
                pnl = float(r[19] or 0)
                if self.stock_pnl_filter == "profit" and pnl <= 0:
                    continue
                if self.stock_pnl_filter == "loss" and pnl >= 0:
                    continue
                if self.stock_pnl_filter == "flat" and pnl != 0:
                    continue

                init_v = float(r[15] or 0)
                buy_v = float(r[16] or 0)
                base_v = init_v if init_v > 0 else buy_v
                daily_rate = (pnl / base_v) if base_v > 0 else (float(r[13] or 0) if r[13] else 0.0)
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
                    fmt_percent(daily_rate),
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

    def select_date(self, target_date: str):
        self.selected_date = target_date
        self.update_date_distribution(self.current_filtered_rows or self.raw_rows)
        self.trend_canvas.selected_date = target_date
        self.trend_canvas.update()
        self.update_date_nav_buttons()
        self.open_date_drawer(target_date)

    def handle_kpi_clicked(self, key: str):
        for k, c in self.kpi_cards.items():
            c.set_selected(k == key)

        if key in ("total", "days"):
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
                self.open_date_drawer(r_vals[0])
            elif current_tab == 1:
                self.open_channel_drawer(r_vals[1], r_vals[0])
            else:
                self.open_stock_drawer(r_vals[2], r_vals[0])

    def open_date_drawer(self, target_date: str):
        self.selected_date = target_date
        rows = getattr(self, "current_filtered_rows", self.raw_rows)
        self.update_date_distribution(rows)
        self.trend_canvas.selected_date = target_date
        self.trend_canvas.update()

        self.drawer_title.setText(f"单日收益详情 · {target_date}")
        day_rows = [r for r in rows if r[0] == target_date]
        if not day_rows:
            day_rows = [r for r in self.raw_rows if r[0] == target_date]
        tot_pnl = sum(float(r[19] or 0) for r in day_rows)

        self.drawer_content_lbl.setText(
            f"收盘日期：{target_date}\n"
            f"当日总收益：{fmt_signed_money(tot_pnl)}\n"
            f"持仓标的数：{len(day_rows)} 只"
        )

        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Sort by daily profit descending
        sorted_rows = sorted(day_rows, key=lambda x: float(x[19] or 0), reverse=True)

        for r in sorted_rows:
            card = QFrame()
            card.setStyleSheet("background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px;")
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

            pnl = float(r[19] or 0)
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

            self.drawer_list_layout.addWidget(card)

        self.show_drawer()

    def open_channel_drawer(self, channel: str, date_str: str = ""):
        if date_str:
            self.selected_date = date_str
            rows = getattr(self, "current_filtered_rows", self.raw_rows)
            self.update_date_distribution(rows)
            self.trend_canvas.selected_date = date_str
            self.trend_canvas.update()

        ch_cn = fmt_broker_cn(channel)
        self.drawer_title.setText(f"渠道收益详情 · {ch_cn}")
        c_rows = [r for r in self.raw_rows if r[1] == channel or fmt_broker_cn(r[1]) == ch_cn]
        tot_pnl = sum(float(r[19] or 0) for r in c_rows)

        self.drawer_content_lbl.setText(
            f"券商渠道：{ch_cn}\n"
            f"期间累计收益：{fmt_signed_money(tot_pnl)}\n"
            f"历史持仓记录数：{len(c_rows)} 条"
        )

        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 按日期降序展示该渠道最近收益记录
        sorted_c_rows = sorted(c_rows, key=lambda x: x[0], reverse=True)[:50]
        for r in sorted_c_rows:
            card = QFrame()
            card.setStyleSheet("background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px;")
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

            pnl = float(r[19] or 0)
            pnl_lbl = QLabel(fmt_signed_money(pnl))
            pnl_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {'#10B981' if pnl >= 0 else '#EF4444'};")
            rate = float(r[13] or 0) if r[13] else 0.0
            rate_lbl = QLabel(fmt_percent(rate))
            rate_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {'#10B981' if rate >= 0 else '#EF4444'};")

            pnl_v.addWidget(pnl_lbl)
            pnl_v.addWidget(rate_lbl)
            c_lay.addLayout(pnl_v)

            self.drawer_list_layout.addWidget(card)

        self.show_drawer()

    def open_stock_drawer(self, symbol: str, date_str: str = ""):
        if date_str:
            self.selected_date = date_str
            rows = getattr(self, "current_filtered_rows", self.raw_rows)
            self.update_date_distribution(rows)
            self.trend_canvas.selected_date = date_str
            self.trend_canvas.update()

        self.drawer_title.setText(f"标的历史收益 · {symbol}")
        s_rows = [r for r in self.raw_rows if r[3] == symbol]
        tot_pnl = sum(float(r[19] or 0) for r in s_rows)

        self.drawer_content_lbl.setText(
            f"Ticker 代码：{symbol}\n"
            f"期间累计收益：{fmt_signed_money(tot_pnl)}\n"
            f"历史交易天数：{len(s_rows)} 天"
        )

        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 按日期降序展示该股票的历史持仓记录
        sorted_s_rows = sorted(s_rows, key=lambda x: x[0], reverse=True)[:50]
        for r in sorted_s_rows:
            is_cur_date = (date_str and r[0] == date_str)
            card = QFrame()
            if is_cur_date:
                card.setStyleSheet("background: #EFF6FF; border: 1.5px solid #3B82F6; border-radius: 6px;")
            else:
                card.setStyleSheet("background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px;")
            c_lay = QHBoxLayout(card)
            c_lay.setContentsMargins(8, 6, 8, 6)

            info_v = QVBoxLayout()
            info_v.setSpacing(2)
            sym_lbl = QLabel(f"{r[0]} ({fmt_broker_cn(r[1])}) {'[选中日]' if is_cur_date else ''}")
            sym_lbl.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {'#1D4ED8' if is_cur_date else '#2563EB'};")
            sub_lbl = QLabel(f"持仓 {fmt_money(r[8])} 股 · 收盘 {fmt_money(r[10])}")
            sub_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
            info_v.addWidget(sym_lbl)
            info_v.addWidget(sub_lbl)
            c_lay.addLayout(info_v)
            c_lay.addStretch()

            pnl_v = QVBoxLayout()
            pnl_v.setSpacing(2)
            pnl_v.setAlignment(Qt.AlignmentFlag.AlignRight)

            pnl = float(r[19] or 0)
            pnl_lbl = QLabel(fmt_signed_money(pnl))
            pnl_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {'#10B981' if pnl >= 0 else '#EF4444'};")
            rate = float(r[13] or 0) if r[13] else 0.0
            rate_lbl = QLabel(fmt_percent(rate))
            rate_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {'#10B981' if rate >= 0 else '#EF4444'};")

            pnl_v.addWidget(pnl_lbl)
            pnl_v.addWidget(rate_lbl)
            c_lay.addLayout(pnl_v)

            self.drawer_list_layout.addWidget(card)

        self.show_drawer()

    def show_drawer(self):
        w = 440
        self.drawer_overlay.setGeometry(self.rect())
        self.drawer_overlay.show()
        self.drawer_overlay.raise_()
        self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
        self.drawer_frame.show()
        self.drawer_frame.raise_()

    def close_drawer(self):
        self.drawer_overlay.hide()
        self.drawer_frame.hide()

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
            self.apply_proportional_column_widths([135, 115, 115, 105, 105, 110, 115, 95, 115, 90, 90])
        elif current_tab == 1:
            self.apply_proportional_column_widths([115, 100, 110, 110, 100, 100, 105, 110, 90, 110, 85, 85])
        else:
            self.apply_proportional_column_widths([115, 95, 85, 80, 85, 85, 85, 105, 90, 105, 90, 110])

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

    def switch_trend_mode(self, mode: str):
        self.trend_tab_daily.setChecked(mode == "daily")
        self.trend_tab_cum.setChecked(mode == "cum")
        self.trend_canvas.set_mode(mode)

    def export_csv(self):
        if not self.raw_rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出每日持仓收益数据",
            f"每日持仓收益_{self.max_as_of_date or 'export'}.csv",
            "CSV 文件 (*.csv)",
        )
        if not path:
            return

        import csv
        headers = [
            "日期", "渠道", "分类", "Ticker", "持仓数量",
            "成本均价", "收盘价", "日末市值", "当日收益", "持仓盈亏比例"
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in self.raw_rows:
                writer.writerow([
                    r[0], r[1], r[2], r[3], r[8],
                    r[9], r[10], r[11], r[19], r[13]
                ])
