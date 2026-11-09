# -*- coding: utf-8 -*-
"""
Monthly Scalping Performance Page V2.0 (每月微操收益页面 V2.0)
深度打造的主动交易微操操作绩效分析页面：
1. 核心指标卡片（含加权平均每股价差 = Σ(价差×微操数量)/Σ(微操数量)）；
2. 月度收益趋势柱状图（点击月份下钻不缩短全局日期轴，浅蓝遮罩高亮，再次点击取消）；
3. 券商渠道收益排行（删除占比列，按最大绝对收益归一化条形图，点击反选联动）；
4. 股票微操收益 TOP 5（列头更新为 # | Ticker | 微操收益 | 微操次数 | 微操数量）；
5. 全局筛选分离（待查询与已生效分离，点击查询生效，带当前状态信息条与取消 Pill）；
6. 14 列微操明细表格（默认三级稳定排序：月份降序->收益降序->Ticker升序，支持全列点击排序）；
7. 右侧详情抽屉（点击遮罩关闭）与双击历史交易回放。
"""

import csv
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableView,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


def numeric_font(size: int = 12, bold: bool = False) -> QFont:
    """金融数字等宽字体"""
    font = QFont(".AppleSystemUIFont", size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    return font


def fmt_money(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        f = float(val)
        return f"{f:,.{digits}f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_signed_money(val: Any, digits: int = 2) -> str:
    if val is None or val == "":
        return "—"
    try:
        f = float(val)
        if abs(f) < 1e-9:
            return f"$0.{'0' * digits}"
        sign = "+" if f > 0 else "-"
        return f"{sign}${abs(f):,.{digits}f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_broker_cn(name: Any) -> str:
    if not name:
        return "—"
    s = str(name).strip()
    upper = s.upper()
    if upper in ("LONGBRIDGE", "LONG BRIDGE", "长桥证券"):
        return "长桥"
    if upper in ("FUTU", "富途证券", "富途"):
        return "富途"
    if upper in ("TIGER", "老虎证券", "老虎"):
        return "老虎"
    if upper in ("FIRSTTRADE", "FIRST TRADE", "第一证券", "第一"):
        return "第一证券"
    if upper in ("IBKR", "IB", "INTERACTIVE BROKERS", "盈透证券", "盈透"):
        return "盈透"
    if upper in ("SCHWAB", "CHARLES SCHWAB", "嘉信理财", "嘉信"):
        return "嘉信"
    if upper in ("BBAE",):
        return "BBAE"
    return s


def calc_weighted_spread(rows: List[Tuple]) -> Tuple[Optional[float], str]:
    """
    加权平均每股价差 = Σ(每股价差 × 微操数量) / Σ(微操数量)
    仅对有效且微操数量 > 0 的记录进行加权
    """
    total_spread_qty_product = 0.0
    total_qty = 0.0
    for r in rows:
        try:
            qty = float(r[5] or 0)
            spread = float(r[8] or 0)
            if qty > 0:
                total_spread_qty_product += spread * qty
                total_qty += qty
        except (ValueError, TypeError):
            continue

    if total_qty > 0:
        val = total_spread_qty_product / total_qty
        sign = "+" if val > 0 else "-"
        return val, f"{sign}${abs(val):,.2f}"
    return None, "—"


# -------------------------------------------------------------------------
# 1. 现代线性矢量图标控件
# -------------------------------------------------------------------------
class ScalpingLinearIcon(QWidget):
    """纯代码绘制现代金融线性图标，无 Emoji，精致统一"""
    def __init__(self, icon_type: str, color: str = "#2563EB", parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.color = color
        self.setFixedSize(36, 36)

    def set_color(self, color: str):
        self.color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 背景浅色圆角方块
        painter.setPen(Qt.PenStyle.NoPen)
        bg_col = QColor(self.color)
        bg_col.setAlpha(26)  # ~10% 透明度
        painter.setBrush(QBrush(bg_col))
        painter.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)

        # 线性前景绘制
        pen = QPen(QColor(self.color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.icon_type == "pnl":
            # 趋势柱状线
            painter.drawLine(10, 26, 10, 18)
            painter.drawLine(16, 26, 16, 12)
            painter.drawLine(22, 26, 22, 15)
            painter.drawLine(26, 26, 26, 9)
        elif self.icon_type == "trades":
            # 双向交换箭头 ⇄
            painter.drawLine(9, 14, 25, 14)
            painter.drawLine(21, 10, 25, 14)
            painter.drawLine(21, 18, 25, 14)
            painter.drawLine(25, 22, 9, 22)
            painter.drawLine(13, 18, 9, 22)
            painter.drawLine(13, 26, 9, 22)
        elif self.icon_type == "buy":
            # 向上折线箭头 ↗
            painter.drawLine(10, 24, 24, 10)
            painter.drawLine(15, 10, 24, 10)
            painter.drawLine(24, 10, 24, 19)
        elif self.icon_type == "sell":
            # 向下折线箭头 ↘
            painter.drawLine(10, 10, 24, 24)
            painter.drawLine(15, 24, 24, 24)
            painter.drawLine(24, 15, 24, 24)
        elif self.icon_type == "spread":
            # 天平 / 加权图标
            painter.drawLine(18, 9, 18, 25)
            painter.drawLine(10, 13, 26, 13)
            painter.drawLine(13, 25, 23, 25)
            painter.drawLine(10, 13, 7, 20)
            painter.drawLine(10, 13, 13, 20)
            painter.drawLine(7, 20, 13, 20)
            painter.drawLine(26, 13, 23, 20)
            painter.drawLine(26, 13, 29, 20)
            painter.drawLine(23, 20, 29, 20)


class ScalpingMetricCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, icon_type: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("kpiCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(82)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # 线性图标
        self.icon_widget = ScalpingLinearIcon(icon_type, "#2563EB", self)
        layout.addWidget(self.icon_widget)

        # 文本信息列
        text_vbox = QVBoxLayout()
        text_vbox.setContentsMargins(0, 0, 0, 0)
        text_vbox.setSpacing(1)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        text_vbox.addWidget(self.title_label)

        self.value_label = QLabel("—")
        self.value_label.setFont(numeric_font(18, bold=True))
        self.value_label.setStyleSheet("color: #0F172A;")
        text_vbox.addWidget(self.value_label)

        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet("color: #94A3B8; font-size: 10.5px;")
        text_vbox.addWidget(self.sub_label)

        layout.addLayout(text_vbox)

        self.setStyleSheet("""
            QFrame#kpiCard {
                background-color: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
            }
            QFrame#kpiCard:hover {
                border-color: #93C5FD;
                background-color: #FAFCFF;
            }
        """)

    def set_data(self, val: str, sub: str, status: Optional[str] = None):
        self.value_label.setText(val)
        self.sub_label.setText(sub)
        if status == "profit":
            self.value_label.setStyleSheet("color: #10B981;")
            self.icon_widget.set_color("#10B981")
        elif status == "loss":
            self.value_label.setStyleSheet("color: #EF4444;")
            self.icon_widget.set_color("#EF4444")
        else:
            self.value_label.setStyleSheet("color: #0F172A;")
            self.icon_widget.set_color("#2563EB")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# 2. 月度微操收益趋势图画布 (MonthlyScalpingTrendCanvas V2.0)
# -------------------------------------------------------------------------
class MonthlyScalpingTrendCanvas(QWidget):
    month_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "monthly"  # "monthly" or "cum"
        self.data_points: List[Tuple[str, float, float]] = []  # (month, monthly_pnl, cum_pnl)
        self.selected_month = ""
        self.hovered_index = -1
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

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

        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor("#FFFFFF"))

        if not self.data_points:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无月度微操收益趋势数据")
            return

        margin_left = 55
        margin_right = 20
        margin_top = 28
        margin_bottom = 28

        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        if plot_w <= 10 or plot_h <= 10:
            return

        vals = [p[1] if self.mode == "monthly" else p[2] for p in self.data_points]
        raw_min = min(vals)
        raw_max = max(vals)

        max_abs = max(abs(raw_max), abs(raw_min), 50.0) * 1.15
        if self.mode == "monthly":
            max_val = max_abs
            min_val = -max_abs
        else:
            min_val = min(0.0, raw_min * 1.15)
            max_val = max(100.0, raw_max * 1.15)

        val_range = max(1e-5, max_val - min_val)

        def to_y(v):
            ratio = (v - min_val) / val_range
            return margin_top + plot_h * (1.0 - ratio)

        zero_y = to_y(0.0)
        zero_y = max(margin_top, min(margin_top + plot_h, zero_y))

        # 1. 绘制网格线与 Y 轴刻度
        painter.setPen(QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.DashLine))
        y_ticks = 4
        for i in range(y_ticks + 1):
            cur_v = min_val + (val_range / y_ticks) * i
            cur_y = to_y(cur_v)
            painter.drawLine(margin_left, int(cur_y), margin_left + plot_w, int(cur_y))

            painter.setPen(QColor("#94A3B8"))
            painter.setFont(numeric_font(9))
            lbl_str = f"${cur_v:,.0f}" if cur_v >= 0 else f"-${abs(cur_v):,.0f}"
            painter.drawText(QRectF(0, cur_y - 8, margin_left - 8, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lbl_str)
            painter.setPen(QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.DashLine))

        # 2. 绘制 0 水平基准线
        painter.setPen(QPen(QColor("#CBD5E1"), 1.2, Qt.PenStyle.SolidLine))
        painter.drawLine(margin_left, int(zero_y), margin_left + plot_w, int(zero_y))

        # 3. 绘制柱体与选中背景遮罩
        n = len(self.data_points)
        slot_w = plot_w / n
        bar_w = max(4.0, min(22.0, slot_w * 0.58))

        for i, (m, m_pnl, cum_pnl) in enumerate(self.data_points):
            val = m_pnl if self.mode == "monthly" else cum_pnl
            cx = margin_left + slot_w * (i + 0.5)
            vy = to_y(val)

            top_y = min(vy, zero_y)
            bot_y = max(vy, zero_y)
            bar_h = max(2.0, bot_y - top_y)
            rx = cx - bar_w / 2.0

            is_sel = (m == self.selected_month)
            is_hover = (i == self.hovered_index)

            # 选中状态：在整列背景绘制半透明浅蓝色柱状遮罩
            if is_sel:
                painter.fillRect(QRectF(cx - slot_w * 0.46, margin_top, slot_w * 0.92, plot_h),
                                 QColor(239, 246, 255, 200))
                painter.setPen(QPen(QColor("#BFDBFE"), 1.0, Qt.PenStyle.DashLine))
                painter.drawRect(QRectF(cx - slot_w * 0.46, margin_top, slot_w * 0.92, plot_h))

            # 柱体颜色：正绿负红
            if val >= 0:
                fill_color = QColor("#10B981") if not is_hover else QColor("#059669")
            else:
                fill_color = QColor("#EF4444") if not is_hover else QColor("#DC2626")

            painter.fillRect(QRectF(rx, top_y, bar_w, bar_h), fill_color)

            # 选中高亮边框
            if is_sel:
                painter.setPen(QPen(QColor("#2563EB"), 2.0))
                painter.drawRect(QRectF(rx - 1, top_y - 1, bar_w + 2, bar_h + 2))

            # X 轴月份标签
            step = max(1, n // 14)
            if i % step == 0 or i == n - 1 or is_sel:
                painter.setPen(QColor("#2563EB" if is_sel else "#64748B"))
                painter.setFont(numeric_font(9, bold=is_sel))
                lbl_m = m[2:] if len(m) == 7 else m
                painter.drawText(QRectF(cx - slot_w / 2.0, margin_top + plot_h + 3, slot_w, 20),
                                 Qt.AlignmentFlag.AlignCenter, lbl_m)

    def mouseMoveEvent(self, event):
        w = self.width()
        margin_left = 55
        margin_right = 20
        plot_w = w - margin_left - margin_right
        n = len(self.data_points)

        if n > 0 and margin_left <= event.position().x() <= margin_left + plot_w:
            slot_w = plot_w / n
            idx = int((event.position().x() - margin_left) / slot_w)
            idx = max(0, min(n - 1, idx))
            if idx != self.hovered_index:
                self.hovered_index = idx
                self.update()

            m, m_pnl, cum_pnl = self.data_points[idx]
            tip = f"月份: {m}\n月度微操收益: {fmt_signed_money(m_pnl)}\n累计微操收益: {fmt_signed_money(cum_pnl)}\n(点击柱体筛选该月份明细)"
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            if self.hovered_index != -1:
                self.hovered_index = -1
                self.update()
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered_index = -1
        self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.data_points:
            w = self.width()
            margin_left = 55
            margin_right = 20
            plot_w = w - margin_left - margin_right
            n = len(self.data_points)
            if margin_left <= event.position().x() <= margin_left + plot_w:
                slot_w = plot_w / n
                idx = int((event.position().x() - margin_left) / slot_w)
                idx = max(0, min(n - 1, idx))
                clicked_m = self.data_points[idx][0]
                if self.selected_month == clicked_m:
                    self.selected_month = ""  # 再次点击取消反选
                    self.month_selected.emit("")
                else:
                    self.selected_month = clicked_m
                    self.month_selected.emit(clicked_m)
                self.update()
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# 3. 微操收益明细表格数据模型 (ModernScalpingTableModel V2.0)
# -------------------------------------------------------------------------
class ModernScalpingTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.columns: Tuple[str, ...] = (
            "月份", "券商渠道", "分类", "Ticker", "微操次数", "微操数量",
            "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)", "微操收益($)",
            "首笔微操时间", "最近微操时间"
        )
        self.all_rows: List[List[Any]] = []
        self.rows: List[List[Any]] = []
        self.page = 1
        self.page_size = 50
        self.sort_column: int = 0
        self.sort_order: Qt.SortOrder = Qt.SortOrder.DescendingOrder
        self.highlight_ticker: str = ""
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
        r, c = index.row(), index.column()
        if r >= len(self.rows) or c >= len(self.columns):
            return None

        val = self.rows[r][c]
        val_str = str(val) if val is not None else "—"
        col_name = self.columns[c]

        if role == Qt.ItemDataRole.DisplayRole:
            return val_str

        if role == Qt.ItemDataRole.BackgroundRole:
            row_data = self.rows[r]
            is_hl_m = self.highlight_month and len(row_data) > 0 and row_data[0] == self.highlight_month
            is_hl_t = self.highlight_ticker and len(row_data) > 3 and row_data[3] == self.highlight_ticker
            if is_hl_m or is_hl_t:
                return QColor("#EFF6FF")
            return None

        # 对齐规则：文本靠左，时间居中，所有金额和数量严格靠右
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col_name in ("月份", "券商渠道", "分类"):
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if col_name in ("Ticker", "首笔微操时间", "最近微操时间"):
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 颜色渲染：盈绿亏红
        if role == Qt.ItemDataRole.ForegroundRole:
            if col_name == "Ticker":
                return QColor("#2563EB")
            if col_name in ("每股价差($)", "微操收益($)"):
                if val_str.startswith("+"):
                    return QColor("#10B981")
                if val_str.startswith("-") or val_str.startswith("−"):
                    return QColor("#EF4444")
            if val_str in ("$0.00", "0.00", "0", "—"):
                return QColor("#64748B")
            return QColor("#1E293B")

        if role == Qt.ItemDataRole.FontRole:
            if col_name == "Ticker":
                return numeric_font(12, bold=True)
            if col_name in ("微操数量", "微操均买价($)", "微操均卖价($)", "每股价差($)", "买入金额($)", "卖出金额($)", "微操收益($)"):
                bold = col_name in ("微操收益($)",)
                return numeric_font(12, bold=bold)
            return QFont(".AppleSystemUIFont", 12)

        return None

    def _parse_sort_key(self, row: List[Any], col: int):
        if col >= len(row):
            return ""
        val = row[col]
        if val is None:
            return -float("inf")
        s = str(val).strip()
        # 清洗数字
        clean = s.replace("$", "").replace(",", "").replace("%", "").replace("+", "").replace("−", "-")
        try:
            return float(clean)
        except ValueError:
            return s

    def apply_sort_and_page(self):
        self.beginResetModel()
        # 默认排序或用户指定列排序
        if self.sort_column == 0 and self.sort_order == Qt.SortOrder.DescendingOrder:
            # 严格按照 PRD 三级稳定排序：第一级月份降序、第二级收益降序、第三级 Ticker 升序
            self.all_rows.sort(key=lambda r: str(r[3] or ""))
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, 11), reverse=True)
            self.all_rows.sort(key=lambda r: str(r[0] or ""), reverse=True)
        elif self.sort_column >= 0:
            rev = (self.sort_order == Qt.SortOrder.DescendingOrder)
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, self.sort_column), reverse=rev)

        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()

    def set_columns_and_rows(self, cols: Tuple[str, ...], rows: List[List[Any]]):
        self.columns = cols
        self.all_rows = list(rows)
        self.apply_sort_and_page()

    def toggle_sort(self, column: int):
        if column < 0 or column >= len(self.columns):
            return
        if self.sort_column == column:
            self.sort_order = Qt.SortOrder.DescendingOrder if self.sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            self.sort_column = column
            # 数值及月份默认降序，其他默认升序
            self.sort_order = Qt.SortOrder.DescendingOrder if column in (0, 4, 5, 6, 7, 8, 9, 10, 11) else Qt.SortOrder.AscendingOrder
        self.page = 1
        self.apply_sort_and_page()

    def set_page(self, page: int):
        total_pages = max(1, (len(self.all_rows) + self.page_size - 1) // self.page_size)
        new_p = max(1, min(page, total_pages))
        if new_p != self.page:
            self.page = new_p
            self.apply_sort_and_page()


# -------------------------------------------------------------------------
# 4. 主页面 MonthlyScalpingPage (V2.0 优化)
# -------------------------------------------------------------------------
class MonthlyScalpingPage(QWidget):
    drill = Signal(str, str, object)  # 信号：下钻或历史回放

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.raw_rows: List[Tuple] = []

        # 待查询与已生效筛选分离
        self.applied_person: Any = None
        self.applied_broker: str = ""
        self.applied_category: str = ""
        self.applied_ticker: str = ""
        self.applied_start_month: str = ""
        self.applied_end_month: str = ""

        # 下钻状态（不缩短全局日期轴）
        self.active_chart_month: str = ""
        self.active_ranking_broker: str = ""
        self.active_ranking_ticker: str = ""

        # UI 构建
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #F8FAFC; border: none; }")

        content_widget = QWidget()
        content_widget.setStyleSheet("background: #F8FAFC;")
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(18, 14, 18, 20)
        content_layout.setSpacing(12)

        # 1. 顶部 Header
        header_row = QHBoxLayout()
        header_text_vbox = QVBoxLayout()
        header_text_vbox.setSpacing(2)

        self.title_lbl = QLabel("每月微操收益")
        self.title_lbl.setFont(QFont(".AppleSystemUIFont", 18, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet("color: #0F172A;")
        header_text_vbox.addWidget(self.title_lbl)

        sub_desc = QLabel("分析主动交易的操作效果，比较不同月份、券商和股票的微操收益表现。")
        sub_desc.setStyleSheet("color: #64748B; font-size: 12px;")
        header_text_vbox.addWidget(sub_desc)

        header_row.addLayout(header_text_vbox)
        header_row.addStretch()

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; font-weight: 500; color: #334155;
            }
            QPushButton:hover { background: #F1F5F9; border-color: #94A3B8; }
        """)
        self.refresh_btn.clicked.connect(self.recalculate_and_refresh)
        header_row.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB; border: 1px solid #1D4ED8; border-radius: 6px;
                padding: 6px 16px; font-size: 12px; font-weight: 600; color: #FFFFFF;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        self.export_btn.clicked.connect(self.export_csv)
        header_row.addWidget(self.export_btn)
        content_layout.addLayout(header_row)

        # 2. 全局筛选卡片 (Filter Bar)
        filter_card = QFrame()
        filter_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        filter_layout.setSpacing(8)

        # 归属人员
        self.person_combo = QComboBox()
        self.person_combo.addItem("全部人员", None)
        self.person_combo.setFixedHeight(32)
        filter_layout.addWidget(self.person_combo)

        # 券商渠道
        self.broker_combo = QComboBox()
        self.broker_combo.addItem("全部渠道", "")
        self.broker_combo.setFixedHeight(32)
        filter_layout.addWidget(self.broker_combo)

        # 股票分类
        self.category_combo = QComboBox()
        self.category_combo.addItem("全部分类", "")
        self.category_combo.setFixedHeight(32)
        filter_layout.addWidget(self.category_combo)

        # 输入 Ticker
        self.ticker_edit = QLineEdit()
        self.ticker_edit.setPlaceholderText("输入股票代码")
        self.ticker_edit.setFixedHeight(32)
        self.ticker_edit.setFixedWidth(120)
        self.ticker_edit.setStyleSheet("QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 8px; font-size: 12px; }")
        self.ticker_edit.returnPressed.connect(self.on_query_clicked)
        filter_layout.addWidget(self.ticker_edit)

        # 开始月份
        self.start_month_combo = QComboBox()
        self.start_month_combo.setFixedHeight(32)
        self.start_month_combo.setMinimumWidth(105)
        filter_layout.addWidget(self.start_month_combo)

        tilde_lbl = QLabel("至")
        tilde_lbl.setStyleSheet("color: #64748B; font-size: 12px;")
        filter_layout.addWidget(tilde_lbl)

        # 结束月份
        self.end_month_combo = QComboBox()
        self.end_month_combo.setFixedHeight(32)
        self.end_month_combo.setMinimumWidth(105)
        filter_layout.addWidget(self.end_month_combo)

        filter_layout.addStretch()

        # 重置与查询按钮
        self.reset_btn = QPushButton("重置")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFixedHeight(32)
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9; color: #475569; font-weight: 600; font-size: 12px;
                border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 14px;
            }
            QPushButton:hover { background: #E2E8F0; color: #0F172A; }
        """)
        self.reset_btn.clicked.connect(self.on_reset_clicked)
        filter_layout.addWidget(self.reset_btn)

        self.search_btn = QPushButton("查询")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.setFixedHeight(32)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB; color: #FFFFFF; font-weight: 600; font-size: 12px;
                border: none; border-radius: 6px; padding: 0 18px;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        self.search_btn.clicked.connect(self.on_query_clicked)
        filter_layout.addWidget(self.search_btn)

        content_layout.addWidget(filter_card)

        # 2.5 筛选状态信息条 (Breadcrumb Status Bar V2.0)
        self.status_bar = QFrame()
        self.status_bar.setFixedHeight(34)
        self.status_bar.setStyleSheet("background: transparent;")
        status_lay = QHBoxLayout(self.status_bar)
        status_lay.setContentsMargins(4, 0, 4, 0)
        status_lay.setSpacing(10)

        self.status_desc_lbl = QLabel("")
        self.status_desc_lbl.setStyleSheet("color: #64748B; font-size: 12px;")
        status_lay.addWidget(self.status_desc_lbl)

        # 月份筛选药丸徽章
        self.month_pill = QFrame()
        self.month_pill.setStyleSheet("""
            QFrame {
                background-color: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 12px; padding: 2px 8px;
            }
        """)
        m_pill_lay = QHBoxLayout(self.month_pill)
        m_pill_lay.setContentsMargins(6, 2, 6, 2)
        m_pill_lay.setSpacing(6)
        self.month_pill_lbl = QLabel("")
        self.month_pill_lbl.setStyleSheet("color: #1D4ED8; font-size: 11.5px; font-weight: 600;")
        m_pill_lay.addWidget(self.month_pill_lbl)
        self.month_pill_close = QPushButton("✕")
        self.month_pill_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.month_pill_close.setStyleSheet("border: none; background: transparent; color: #2563EB; font-weight: bold; font-size: 11px;")
        self.month_pill_close.clicked.connect(lambda: self.on_trend_month_clicked(""))
        m_pill_lay.addWidget(self.month_pill_close)
        status_lay.addWidget(self.month_pill)
        self.month_pill.hide()

        # 取消月份筛选按钮
        self.clear_month_btn = QPushButton("取消月份筛选")
        self.clear_month_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_month_btn.setStyleSheet("border: none; background: transparent; color: #64748B; font-size: 11.5px; text-decoration: underline;")
        self.clear_month_btn.clicked.connect(lambda: self.on_trend_month_clicked(""))
        status_lay.addWidget(self.clear_month_btn)
        self.clear_month_btn.hide()

        status_lay.addStretch()
        content_layout.addWidget(self.status_bar)

        # 3. 核心指标卡片区域 (5张横向并排)
        kpi_grid = QHBoxLayout()
        kpi_grid.setSpacing(10)

        self.kpi_cards: Dict[str, ScalpingMetricCard] = {}
        configs = [
            ("pnl", "pnl", "微操收益合计"),
            ("trades", "trades", "微操次数"),
            ("buy", "buy", "买入金额合计"),
            ("sell", "sell", "卖出金额合计"),
            ("spread", "spread", "加权平均每股价差"),
        ]
        for key, icon_t, title_s in configs:
            card = ScalpingMetricCard(key, icon_t, title_s, self)
            self.kpi_cards[key] = card
            kpi_grid.addWidget(card)
        content_layout.addLayout(kpi_grid)

        # 4. 中部图表与渠道排行 (Splitter: 58% : 42%)
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 左侧：月度微操收益趋势卡片
        trend_card = QFrame()
        trend_card.setFixedHeight(230)
        trend_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        trend_vbox = QVBoxLayout(trend_card)
        trend_vbox.setContentsMargins(12, 8, 12, 8)
        trend_vbox.setSpacing(2)

        trend_top = QHBoxLayout()
        trend_title = QLabel("月度微操收益趋势")
        trend_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        trend_title.setStyleSheet("color: #0F172A;")
        trend_top.addWidget(trend_title)

        # 图例与说明
        legend_widget = QWidget()
        leg_lay = QHBoxLayout(legend_widget)
        leg_lay.setContentsMargins(10, 0, 0, 0)
        leg_lay.setSpacing(6)

        p_box = QLabel("■")
        p_box.setStyleSheet("color: #10B981; font-size: 10px;")
        leg_lay.addWidget(p_box)
        p_txt = QLabel("盈利")
        p_txt.setStyleSheet("color: #64748B; font-size: 11px;")
        leg_lay.addWidget(p_txt)

        l_box = QLabel("■")
        l_box.setStyleSheet("color: #EF4444; font-size: 10px; margin-left: 6px;")
        leg_lay.addWidget(l_box)
        l_txt = QLabel("亏损")
        l_txt.setStyleSheet("color: #64748B; font-size: 11px;")
        leg_lay.addWidget(l_txt)

        tip_lbl = QLabel("点击月份柱体筛选明细（不会缩短日期范围）")
        tip_lbl.setStyleSheet("color: #94A3B8; font-size: 10.5px; margin-left: 8px;")
        leg_lay.addWidget(tip_lbl)
        trend_top.addWidget(legend_widget)

        trend_top.addStretch()

        self.trend_tab_monthly = QPushButton("月度收益")
        self.trend_tab_cum = QPushButton("累计收益")
        for btn in (self.trend_tab_monthly, self.trend_tab_cum):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    border: none; background: #F1F5F9; color: #64748B; font-size: 11px;
                    font-weight: 600; padding: 4px 10px; border-radius: 4px;
                }
                QPushButton:checked { background: #2563EB; color: #FFFFFF; }
            """)
        self.trend_tab_monthly.setChecked(True)
        self.trend_tab_monthly.clicked.connect(lambda: self.switch_trend_mode("monthly"))
        self.trend_tab_cum.clicked.connect(lambda: self.switch_trend_mode("cum"))

        tab_group = QWidget()
        tab_group_lay = QHBoxLayout(tab_group)
        tab_group_lay.setContentsMargins(0, 0, 0, 0)
        tab_group_lay.setSpacing(2)
        tab_group_lay.addWidget(self.trend_tab_monthly)
        tab_group_lay.addWidget(self.trend_tab_cum)
        trend_top.addWidget(tab_group)

        trend_vbox.addLayout(trend_top)

        self.trend_canvas = MonthlyScalpingTrendCanvas(self)
        self.trend_canvas.month_selected.connect(self.on_trend_month_clicked)
        trend_vbox.addWidget(self.trend_canvas)
        self.mid_splitter.addWidget(trend_card)

        # 右侧：券商渠道微操收益排行卡片
        broker_card = QFrame()
        broker_card.setFixedHeight(230)
        broker_card.setMinimumWidth(340)
        broker_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        broker_vbox = QVBoxLayout(broker_card)
        broker_vbox.setContentsMargins(12, 8, 12, 8)
        broker_vbox.setSpacing(4)

        b_header = QHBoxLayout()
        b_title = QLabel("券商渠道微操收益排行")
        b_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        b_title.setStyleSheet("color: #0F172A;")
        b_header.addWidget(b_title)

        b_sub = QLabel("按收益从高到低；条形长度相对最大单渠道绝对收益。")
        b_sub.setStyleSheet("color: #94A3B8; font-size: 10.5px;")
        b_header.addWidget(b_sub)
        b_header.addStretch()
        broker_vbox.addLayout(b_header)

        # 渠道列表头（无占比列）
        b_th = QHBoxLayout()
        b_th.setContentsMargins(6, 2, 6, 2)
        b_th.setSpacing(8)
        lbl_th_b = QLabel("券商渠道")
        lbl_th_b.setFixedWidth(75)
        lbl_th_b.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        b_th.addWidget(lbl_th_b)

        lbl_th_pnl = QLabel("微操收益")
        lbl_th_pnl.setFixedWidth(80)
        lbl_th_pnl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl_th_pnl.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        b_th.addWidget(lbl_th_pnl)

        lbl_th_bar = QLabel("微操收益")
        lbl_th_bar.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8; margin-left: 8px;")
        b_th.addWidget(lbl_th_bar, 1)

        broker_vbox.addLayout(b_th)

        # 渠道列表滚动区
        self.broker_scroll = QScrollArea()
        self.broker_scroll.setWidgetResizable(True)
        self.broker_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.broker_scroll.setStyleSheet("background: transparent; border: none;")

        self.broker_list_widget = QWidget()
        self.broker_list_widget.setStyleSheet("background: transparent;")
        self.broker_list_layout = QVBoxLayout(self.broker_list_widget)
        self.broker_list_layout.setContentsMargins(0, 0, 0, 0)
        self.broker_list_layout.setSpacing(4)
        self.broker_list_layout.addStretch()

        self.broker_scroll.setWidget(self.broker_list_widget)
        broker_vbox.addWidget(self.broker_scroll)

        self.mid_splitter.addWidget(broker_card)
        self.mid_splitter.setSizes([750, 550])
        content_layout.addWidget(self.mid_splitter)

        # 5. 股票微操收益排行榜 (盈利 TOP 5 + 亏损 TOP 5)
        rank_row = QHBoxLayout()
        rank_row.setSpacing(10)

        # 左侧：盈利股票 TOP 5
        profit_card = QFrame()
        profit_card.setFixedHeight(195)
        profit_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        profit_vbox = QVBoxLayout(profit_card)
        profit_vbox.setContentsMargins(12, 8, 12, 8)
        profit_vbox.setSpacing(4)

        p_hdr = QHBoxLayout()
        p_title = QLabel("盈利股票 TOP 5")
        p_title.setFont(QFont(".AppleSystemUIFont", 12.5, QFont.Weight.Bold))
        p_title.setStyleSheet("color: #10B981;")
        p_hdr.addWidget(p_title)

        p_tag = QLabel("收益最高")
        p_tag.setStyleSheet("background: #ECFDF5; color: #10B981; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px;")
        p_hdr.addWidget(p_tag)
        p_hdr.addStretch()
        profit_vbox.addLayout(p_hdr)

        p_th = QHBoxLayout()
        p_th.setContentsMargins(4, 2, 4, 2)
        p_th.addWidget(self._make_th_label("#", 24))
        p_th.addWidget(self._make_th_label("Ticker", 75))
        p_th.addWidget(self._make_th_label("微操收益", 85, align_right=True))
        p_th.addWidget(self._make_th_label("微操次数", 70, align_right=True))
        p_th.addWidget(self._make_th_label("微操数量", 85, align_right=True))
        profit_vbox.addLayout(p_th)

        self.profit_top_vbox = QVBoxLayout()
        self.profit_top_vbox.setSpacing(3)
        profit_vbox.addLayout(self.profit_top_vbox)
        profit_vbox.addStretch()
        rank_row.addWidget(profit_card, 1)

        # 右侧：亏损股票 TOP 5
        loss_card = QFrame()
        loss_card.setFixedHeight(195)
        loss_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        loss_vbox = QVBoxLayout(loss_card)
        loss_vbox.setContentsMargins(12, 8, 12, 8)
        loss_vbox.setSpacing(4)

        l_hdr = QHBoxLayout()
        l_title = QLabel("亏损股票 TOP 5")
        l_title.setFont(QFont(".AppleSystemUIFont", 12.5, QFont.Weight.Bold))
        l_title.setStyleSheet("color: #EF4444;")
        l_hdr.addWidget(l_title)

        l_tag = QLabel("亏损最高")
        l_tag.setStyleSheet("background: #FEF2F2; color: #EF4444; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px;")
        l_hdr.addWidget(l_tag)
        l_hdr.addStretch()
        loss_vbox.addLayout(l_hdr)

        l_th = QHBoxLayout()
        l_th.setContentsMargins(4, 2, 4, 2)
        l_th.addWidget(self._make_th_label("#", 24))
        l_th.addWidget(self._make_th_label("Ticker", 75))
        l_th.addWidget(self._make_th_label("微操收益", 85, align_right=True))
        l_th.addWidget(self._make_th_label("微操次数", 70, align_right=True))
        l_th.addWidget(self._make_th_label("微操数量", 85, align_right=True))
        loss_vbox.addLayout(l_th)

        self.loss_top_vbox = QVBoxLayout()
        self.loss_top_vbox.setSpacing(3)
        loss_vbox.addLayout(self.loss_top_vbox)
        loss_vbox.addStretch()
        rank_row.addWidget(loss_card, 1)

        content_layout.addLayout(rank_row)

        # 6. 微操收益明细表格卡片 (底部全宽)
        table_card = QFrame()
        table_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        table_vbox = QVBoxLayout(table_card)
        table_vbox.setContentsMargins(14, 10, 14, 10)
        table_vbox.setSpacing(8)

        table_top_h = QHBoxLayout()
        tbl_title = QLabel("微操收益明细")
        tbl_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        tbl_title.setStyleSheet("color: #0F172A;")
        table_top_h.addWidget(tbl_title)

        self.table_sub_lbl = QLabel("共 0 条记录 · 点击行查看详情，双击行进入交易回放")
        self.table_sub_lbl.setStyleSheet("font-size: 11px; color: #64748B; margin-left: 8px;")
        table_top_h.addWidget(self.table_sub_lbl)
        table_top_h.addStretch()

        self.page_size_lbl = QLabel("每页 50 条")
        self.page_size_lbl.setStyleSheet("font-size: 11px; color: #64748B; margin-right: 8px;")
        table_top_h.addWidget(self.page_size_lbl)

        # 分页控制器
        self.prev_page_btn = QPushButton("‹")
        self.next_page_btn = QPushButton("›")
        for btn in (self.prev_page_btn, self.next_page_btn):
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 4px;
                    font-size: 13px; font-weight: bold; color: #334155;
                }
                QPushButton:hover:!disabled { background: #EFF6FF; border-color: #3B82F6; color: #2563EB; }
                QPushButton:disabled { color: #CBD5E1; border-color: #E2E8F0; background: #F8FAFC; }
            """)
        self.prev_page_btn.clicked.connect(lambda: self.change_page(-1))
        self.next_page_btn.clicked.connect(lambda: self.change_page(1))

        self.page_num_lbl = QLabel("1 / 1")
        self.page_num_lbl.setStyleSheet("font-size: 11px; color: #1E293B; font-weight: 600; margin: 0 4px;")

        table_top_h.addWidget(self.prev_page_btn)
        table_top_h.addWidget(self.page_num_lbl)
        table_top_h.addWidget(self.next_page_btn)

        table_vbox.addLayout(table_top_h)

        self.table_model = ModernScalpingTableModel(self)
        self.table_view = QTableView(self)
        self.table_view.setModel(self.table_model)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setShowGrid(False)
        self.table_view.setWordWrap(False)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table_view.verticalHeader().hide()
        self.table_view.verticalHeader().setDefaultSectionSize(34)
        self.table_view.horizontalHeader().setFixedHeight(34)
        self.table_view.horizontalHeader().setHighlightSections(False)
        self.table_view.horizontalHeader().setSectionsClickable(True)
        self.table_view.horizontalHeader().sectionClicked.connect(self.on_header_section_clicked)
        self.table_view.setMinimumHeight(360)
        self.table_view.setStyleSheet("""
            QTableView {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 4px;
                gridline-color: transparent;
                selection-background-color: #EFF6FF;
                selection-color: #1E293B;
            }
            QHeaderView::section {
                background-color: #F8FAFC;
                color: #475569;
                font-size: 11.5px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                padding: 4px 8px;
            }
        """)
        self.table_view.clicked.connect(self.on_table_row_clicked)
        self.table_view.doubleClicked.connect(self.on_table_row_double_clicked)
        table_vbox.addWidget(self.table_view)

        content_layout.addWidget(table_card)

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll)

        # 7. 右侧详情抽屉 (Drawer)
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
        drawer_vbox.setContentsMargins(18, 16, 18, 16)
        drawer_vbox.setSpacing(14)

        d_top = QHBoxLayout()
        self.drawer_title = QLabel("微操详情")
        self.drawer_title.setFont(QFont(".AppleSystemUIFont", 15, QFont.Weight.Bold))
        self.drawer_title.setStyleSheet("color: #0F172A;")
        d_top.addWidget(self.drawer_title)
        d_top.addStretch()

        d_close = QPushButton("✕")
        d_close.setFixedSize(26, 26)
        d_close.setCursor(Qt.CursorShape.PointingHandCursor)
        d_close.setStyleSheet("""
            QPushButton {
                border: none; background: #F1F5F9; border-radius: 13px;
                color: #64748B; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background: #E2E8F0; color: #0F172A; }
        """)
        d_close.clicked.connect(self.close_drawer)
        d_top.addWidget(d_close)
        drawer_vbox.addLayout(d_top)

        self.drawer_subtitle = QLabel("2026-11 · 长桥 · 杠杆")
        self.drawer_subtitle.setStyleSheet("color: #64748B; font-size: 12px;")
        drawer_vbox.addWidget(self.drawer_subtitle)

        # 抽屉内容列表
        self.drawer_list_layout = QVBoxLayout()
        self.drawer_list_layout.setSpacing(10)
        drawer_vbox.addLayout(self.drawer_list_layout)

    def _make_th_label(self, text: str, width: int, align_right: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setFixedWidth(width)
        lbl.setStyleSheet("font-size: 11px; font-weight: 600; color: #94A3B8;")
        if align_right:
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def apply_proportional_column_widths(self):
        """按 14 列基准宽度平摊视口剩余空间"""
        if not hasattr(self, "table_view") or not self.table_view:
            return
        header = self.table_view.horizontalHeader()
        header.setStretchLastSection(False)

        base_widths = [85, 85, 75, 80, 70, 85, 95, 95, 90, 105, 105, 110, 135, 135]
        n = len(base_widths)
        if self.table_model.columnCount() != n:
            return

        viewport_w = self.table_view.viewport().width()
        if viewport_w <= 20:
            viewport_w = self.table_view.width() - 4
        if viewport_w <= 20:
            viewport_w = 1360

        base_sum = sum(base_widths)
        extra = max(0, viewport_w - base_sum)
        per_col = extra // n
        rem = extra % n

        for i, w in enumerate(base_widths):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            extra_px = per_col + (1 if i < rem else 0)
            self.table_view.setColumnWidth(i, w + extra_px)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "mid_splitter") and self.mid_splitter:
            w = self.mid_splitter.width()
            if w > 100:
                self.mid_splitter.setSizes([int(w * 0.58), int(w * 0.42)])
        if self.drawer_frame.isVisible():
            w = 420
            self.drawer_overlay.setGeometry(self.rect())
            self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
            self.drawer_overlay.raise_()
            self.drawer_frame.raise_()
        self.apply_proportional_column_widths()

    # -------------------------------------------------------------------------
    # 数据加载与处理
    # -------------------------------------------------------------------------
    def activate(self):
        if self.person_combo.count() <= 1:
            self._populate_persons()
        if not self.raw_rows and self.analytics:
            self.update_data(self.analytics)
        self.apply_proportional_column_widths()

    def sync_person_from_main(self, person_id: Any):
        if getattr(self, "_syncing_person", False):
            return
        self._syncing_person = True
        try:
            self.applied_person = person_id
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
            self.applied_broker = clean
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
        if self.applied_person is not None:
            idx = self.person_combo.findData(self.applied_person)
            if idx >= 0:
                self.person_combo.setCurrentIndex(idx)
        self.person_combo.blockSignals(False)

    def update_data(self, analytics):
        self.analytics = analytics
        if not analytics or not hasattr(analytics, "reports"):
            return

        report = analytics.reports.get("每月微操收益")
        if not report:
            return

        # 提取原始数据元组
        self.raw_rows = [tuple(r.values) for r in report.rows]

        # 填充券商渠道下拉框
        brokers = sorted({fmt_broker_cn(r[1]) for r in self.raw_rows if len(r) > 1 and r[1]})
        self.broker_combo.blockSignals(True)
        self.broker_combo.clear()
        self.broker_combo.addItem("全部渠道", "")
        for b in brokers:
            self.broker_combo.addItem(b, b)
        self.broker_combo.blockSignals(False)

        # 填充分类下拉框
        categories = sorted({str(r[2]) for r in self.raw_rows if len(r) > 2 and r[2]})
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类", "")
        for c in categories:
            self.category_combo.addItem(c, c)
        self.category_combo.blockSignals(False)

        # 填充月份下拉框
        months = sorted({str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]})
        self.start_month_combo.blockSignals(True)
        self.end_month_combo.blockSignals(True)
        self.start_month_combo.clear()
        self.end_month_combo.clear()
        for m in months:
            self.start_month_combo.addItem(m, m)
            self.end_month_combo.addItem(m, m)
        if months:
            self.start_month_combo.setCurrentIndex(0)
            self.end_month_combo.setCurrentIndex(len(months) - 1)
            self.applied_start_month = months[0]
            self.applied_end_month = months[-1]
        self.start_month_combo.blockSignals(False)
        self.end_month_combo.blockSignals(False)

        self._populate_persons()
        self.recalculate_and_refresh()

    def on_query_clicked(self):
        """点击查询按钮，正式应用筛选条件"""
        p_id = self.person_combo.currentData()
        person_changed = (p_id != self.applied_person)
        self.applied_person = p_id

        # 同步给主窗口人员筛选
        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(p_id)
            finally:
                self._syncing_person = False

        self.applied_broker = self.broker_combo.currentData() or ""
        self.applied_category = self.category_combo.currentData() or ""
        self.applied_ticker = self.ticker_edit.text().strip().upper()

        s_m = self.start_month_combo.currentData() or ""
        e_m = self.end_month_combo.currentData() or ""
        if s_m and e_m and s_m > e_m:
            # 自动校正
            s_m, e_m = e_m, s_m
            self.start_month_combo.setCurrentText(s_m)
            self.end_month_combo.setCurrentText(e_m)

        self.applied_start_month = s_m
        self.applied_end_month = e_m

        # 清除不再适用的下钻反选
        self.active_chart_month = ""
        self.active_ranking_broker = ""
        self.active_ranking_ticker = ""

        self.table_model.page = 1
        self.recalculate_and_refresh()

    def on_reset_clicked(self):
        """点击重置按钮，恢复全部默认条件"""
        self.person_combo.setCurrentIndex(0)
        self.broker_combo.setCurrentIndex(0)
        self.category_combo.setCurrentIndex(0)
        self.ticker_edit.clear()

        months = sorted({str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]})
        if months:
            self.start_month_combo.setCurrentIndex(0)
            self.end_month_combo.setCurrentIndex(len(months) - 1)
            self.applied_start_month = months[0]
            self.applied_end_month = months[-1]
        else:
            self.applied_start_month = ""
            self.applied_end_month = ""

        self.applied_person = None
        self.applied_broker = ""
        self.applied_category = ""
        self.applied_ticker = ""

        self.active_chart_month = ""
        self.active_ranking_broker = ""
        self.active_ranking_ticker = ""

        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(None)
            finally:
                self._syncing_person = False

        self.table_model.page = 1
        self.recalculate_and_refresh()

    def recalculate_and_refresh(self):
        if not self.raw_rows:
            return

        # 1. 计算全局生效筛选条件下的记录集 (global_scope_rows)
        # 趋势图完全基于 global_scope_rows 绘制，绝不因点击月份而截断日期轴！
        global_scope_rows: List[Tuple] = []
        for r in self.raw_rows:
            m = str(r[0]) if len(r) > 0 else ""
            b_cn = fmt_broker_cn(r[1]) if len(r) > 1 else ""
            cat = str(r[2]) if len(r) > 2 else ""
            sym = str(r[3]).upper() if len(r) > 3 else ""

            # 全局条件过滤
            if self.applied_broker and b_cn != self.applied_broker:
                continue
            if self.applied_category and cat != self.applied_category:
                continue
            if self.applied_ticker and self.applied_ticker not in sym:
                continue
            if self.applied_start_month and m < self.applied_start_month:
                continue
            if self.applied_end_month and m > self.applied_end_month:
                continue

            global_scope_rows.append(r)

        # 2. 统计月度趋势柱状图数据 (完整保留全局月份区间)
        month_map: Dict[str, float] = {}
        for r in global_scope_rows:
            m = str(r[0])
            month_map[m] = month_map.get(m, 0.0) + float(r[11] or 0)

        # 补充全局日期范围内的全部月份（即零收益月份也保留，不缺失）
        trend_pts: List[Tuple[str, float, float]] = []
        cum_pnl = 0.0
        all_months_in_scope = sorted(month_map.keys())
        for m in all_months_in_scope:
            pnl = month_map[m]
            cum_pnl += pnl
            trend_pts.append((m, pnl, cum_pnl))

        self.trend_canvas.set_data(trend_pts, self.active_chart_month)

        # 3. 计算用于展示在 KPI、排行榜与明细表格的记录集 (display_rows)
        # 受到 active_chart_month、active_ranking_broker、active_ranking_ticker 的进一步筛选
        display_rows: List[Tuple] = []
        for r in global_scope_rows:
            m = str(r[0]) if len(r) > 0 else ""
            b_cn = fmt_broker_cn(r[1]) if len(r) > 1 else ""
            sym = str(r[3]).upper() if len(r) > 3 else ""

            if self.active_chart_month and m != self.active_chart_month:
                continue
            if self.active_ranking_broker and b_cn != self.active_ranking_broker:
                continue
            if self.active_ranking_ticker and sym != self.active_ranking_ticker:
                continue

            display_rows.append(r)

        # 4. 更新全局筛选状态信息条
        p_text = self.person_combo.currentText() or "全部人员"
        b_text = self.applied_broker or "全部渠道"
        d_range = f"{self.applied_start_month} 至 {self.applied_end_month}" if self.applied_start_month else "全周期"
        self.status_desc_lbl.setText(f"当前人员: {p_text}   |   渠道: {b_text}   |   日期: {d_range}")

        if self.active_chart_month:
            self.month_pill_lbl.setText(f"月份: {self.active_chart_month}")
            self.month_pill.show()
            self.clear_month_btn.show()
        else:
            self.month_pill.hide()
            self.clear_month_btn.hide()

        # 5. 计算 5 项核心指标 (基于 display_rows)
        tot_pnl = sum(float(r[11] or 0) for r in display_rows)
        tot_trades_cnt = sum(int(r[4] or 0) for r in display_rows)
        tot_buy_amt = sum(float(r[9] or 0) for r in display_rows)
        tot_sell_amt = sum(float(r[10] or 0) for r in display_rows)
        unique_tickers = len({r[3] for r in display_rows if len(r) > 3 and r[3]})

        # 加权平均每股价差
        weighted_spread_val, weighted_spread_str = calc_weighted_spread(display_rows)

        pnl_status = "profit" if tot_pnl > 0 else ("loss" if tot_pnl < 0 else None)
        self.kpi_cards["pnl"].set_data(fmt_signed_money(tot_pnl, 2), "当前筛选范围 · 已实现收益", pnl_status)
        self.kpi_cards["trades"].set_data(str(tot_trades_cnt), f"涉及 {unique_tickers} 只股票")
        self.kpi_cards["buy"].set_data(f"${fmt_money(tot_buy_amt, 2)}", "累计微操买入")
        self.kpi_cards["sell"].set_data(f"${fmt_money(tot_sell_amt, 2)}", "累计微操卖出")

        spread_status = None
        if weighted_spread_val is not None:
            spread_status = "profit" if weighted_spread_val > 0 else ("loss" if weighted_spread_val < 0 else None)
        self.kpi_cards["spread"].set_data(weighted_spread_str, "按微操数量加权", spread_status)

        # 6. 统计券商渠道收益排行 (基于当前生效的月份 display_rows)
        broker_map: Dict[str, float] = {}
        for r in display_rows:
            b_cn = fmt_broker_cn(r[1])
            broker_map[b_cn] = broker_map.get(b_cn, 0.0) + float(r[11] or 0)
        self.update_broker_rankings(broker_map)

        # 7. 统计股票微操收益 TOP 5 (盈利 TOP 5 & 亏损 TOP 5)
        stock_map: Dict[str, Dict[str, Any]] = {}
        for r in display_rows:
            sym = str(r[3])
            if sym not in stock_map:
                stock_map[sym] = {"pnl": 0.0, "trades": 0, "qty": 0.0}
            stock_map[sym]["pnl"] += float(r[11] or 0)
            stock_map[sym]["trades"] += int(r[4] or 0)
            stock_map[sym]["qty"] += float(r[5] or 0)
        self.update_stock_top5(stock_map)

        # 8. 更新明细表格数据
        table_rows: List[List[Any]] = []
        for r in display_rows:
            pnl_val = float(r[11] or 0)
            spread_val = float(r[8] or 0)
            table_rows.append([
                str(r[0]),                                  # 月份
                fmt_broker_cn(r[1]),                        # 券商渠道
                str(r[2]),                                  # 分类
                str(r[3]),                                  # Ticker
                str(r[4]),                                  # 微操次数
                fmt_money(r[5], 2),                         # 微操数量
                f"${fmt_money(r[6], 2)}",                   # 微操均买价($)
                f"${fmt_money(r[7], 2)}",                   # 微操均卖价($)
                fmt_signed_money(spread_val, 2),            # 每股价差($)
                f"${fmt_money(r[9], 2)}",                   # 买入金额($)
                f"${fmt_money(r[10], 2)}",                  # 卖出金额($)
                fmt_signed_money(pnl_val, 2),               # 微操收益($)
                str(r[12]) if len(r) > 12 else "—",         # 首笔微操时间
                str(r[13]) if len(r) > 13 else "—",         # 最近微操时间
            ])

        self.table_model.highlight_month = self.active_chart_month
        self.table_model.highlight_ticker = self.active_ranking_ticker
        self.table_model.set_columns_and_rows(self.table_model.columns, table_rows)

        self.table_sub_lbl.setText(f"共 {len(table_rows)} 条记录 · 点击行查看详情，双击行进入交易回放")
        self.update_pagination_ui()
        self.apply_proportional_column_widths()

    # -------------------------------------------------------------------------
    # 券商渠道与股票排行榜渲染
    # -------------------------------------------------------------------------
    def update_broker_rankings(self, broker_map: Dict[str, float]):
        """渲染券商渠道收益排行：取消占比列，按最大绝对收益归一化进度条"""
        while self.broker_list_layout.count():
            item = self.broker_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not broker_map:
            empty_lbl = QLabel("暂无渠道微操记录")
            empty_lbl.setStyleSheet("font-size: 11px; color: #94A3B8; padding: 20px 0;")
            empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.broker_list_layout.addWidget(empty_lbl)
            return

        sorted_brokers = sorted(broker_map.items(), key=lambda x: x[1], reverse=True)
        max_abs_pnl = max(abs(val) for _, val in sorted_brokers) or 1.0

        for b_name, b_pnl in sorted_brokers:
            row_frame = QFrame()
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)
            is_active = (self.active_ranking_broker == b_name)
            border_col = "#2563EB" if is_active else "#F1F5F9"
            bg_col = "#EFF6FF" if is_active else "#FFFFFF"

            row_frame.setStyleSheet(f"""
                QFrame {{
                    background: {bg_col}; border: 1px solid {border_col}; border-radius: 4px; padding: 2px 4px;
                }}
                QFrame:hover {{ background: #F8FAFC; border-color: #CBD5E1; }}
            """)
            r_lay = QHBoxLayout(row_frame)
            r_lay.setContentsMargins(6, 3, 6, 3)
            r_lay.setSpacing(8)

            name_lbl = QLabel(b_name)
            name_lbl.setFixedWidth(75)
            name_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #1E293B;")
            r_lay.addWidget(name_lbl)

            pnl_lbl = QLabel(fmt_signed_money(b_pnl, 2))
            pnl_lbl.setFixedWidth(80)
            pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pnl_lbl.setFont(numeric_font(11, bold=True))
            pnl_lbl.setStyleSheet(f"color: {'#10B981' if b_pnl >= 0 else '#EF4444'}; font-size: 11.5px;")
            r_lay.addWidget(pnl_lbl)

            # 横向条形进度条（按最大绝对收益归一化）
            bar = QProgressBar()
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            ratio = (abs(b_pnl) / max_abs_pnl) * 1000 if max_abs_pnl > 0 else 0
            bar.setValue(int(max(8, min(1000, ratio))))
            color_hex = "#10B981" if b_pnl >= 0 else "#EF4444"
            bg_hex = "#DCFCE7" if b_pnl >= 0 else "#FEE2E2"
            bar.setStyleSheet(f"""
                QProgressBar {{ background-color: {bg_hex}; border: none; border-radius: 4px; }}
                QProgressBar::chunk {{ background-color: {color_hex}; border-radius: 4px; }}
            """)
            r_lay.addWidget(bar, 1)

            def make_broker_click(name=b_name):
                return lambda e: self.filter_by_broker(name)
            row_frame.mousePressEvent = make_broker_click(b_name)
            self.broker_list_layout.addWidget(row_frame)

        self.broker_list_layout.addStretch()

    def update_stock_top5(self, stock_map: Dict[str, Dict[str, Any]]):
        """渲染盈利与亏损 TOP 5：列头更新为 # | Ticker | 微操收益 | 微操次数 | 微操数量"""
        # 清空现有项
        for lay in (self.profit_top_vbox, self.loss_top_vbox):
            while lay.count():
                it = lay.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()

        all_stocks = [{"sym": k, **v} for k, v in stock_map.items()]

        # 盈利 TOP 5 (收益降序，只展示 > 0)
        profit_stocks = [s for s in all_stocks if s["pnl"] > 0]
        profit_stocks.sort(key=lambda x: x["pnl"], reverse=True)
        top_profit = profit_stocks[:5]

        # 亏损 TOP 5 (收益升序，最亏损排在第一位，只展示 < 0)
        loss_stocks = [s for s in all_stocks if s["pnl"] < 0]
        loss_stocks.sort(key=lambda x: x["pnl"])
        top_loss = loss_stocks[:5]

        for i, s in enumerate(top_profit):
            item_frame = self._build_top5_row(i + 1, s, is_profit=True)
            self.profit_top_vbox.addWidget(item_frame)

        for i, s in enumerate(top_loss):
            item_frame = self._build_top5_row(i + 1, s, is_profit=False)
            self.loss_top_vbox.addWidget(item_frame)

        if not top_profit:
            empty = QLabel("暂无盈利股票")
            empty.setStyleSheet("font-size: 11px; color: #94A3B8; padding: 12px 0;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.profit_top_vbox.addWidget(empty)

        if not top_loss:
            empty = QLabel("暂无亏损股票")
            empty.setStyleSheet("font-size: 11px; color: #94A3B8; padding: 12px 0;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.loss_top_vbox.addWidget(empty)

    def _build_top5_row(self, rank: int, d: Dict[str, Any], is_profit: bool) -> QWidget:
        frame = QFrame()
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        sym = d["sym"]
        is_active = (self.active_ranking_ticker == sym)
        border_col = "#2563EB" if is_active else "#F1F5F9"
        bg_col = "#EFF6FF" if is_active else "#FFFFFF"

        frame.setStyleSheet(f"""
            QFrame {{
                background: {bg_col}; border: 1px solid {border_col}; border-radius: 4px; padding: 1px 4px;
            }}
            QFrame:hover {{ background: #F8FAFC; border-color: #CBD5E1; }}
        """)
        h = QHBoxLayout(frame)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(6)

        # 排名徽章
        rank_badge = QLabel(str(rank))
        rank_badge.setFixedSize(18, 18)
        rank_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_bg = "#10B981" if is_profit and rank <= 3 else ("#EF4444" if not is_profit and rank <= 3 else "#E2E8F0")
        badge_fg = "#FFFFFF" if rank <= 3 else "#64748B"
        rank_badge.setStyleSheet(f"background-color: {badge_bg}; color: {badge_fg}; font-size: 10px; font-weight: bold; border-radius: 9px;")
        h.addWidget(rank_badge)

        # Ticker
        sym_lbl = QLabel(sym)
        sym_lbl.setFixedWidth(70)
        sym_lbl.setStyleSheet("font-size: 11.5px; font-weight: 700; color: #2563EB;")
        h.addWidget(sym_lbl)

        # 微操收益
        pnl_lbl = QLabel(fmt_signed_money(d["pnl"], 2))
        pnl_lbl.setFixedWidth(85)
        pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pnl_lbl.setFont(numeric_font(11, bold=True))
        pnl_lbl.setStyleSheet(f"color: {'#10B981' if is_profit else '#EF4444'}; font-size: 11.5px;")
        h.addWidget(pnl_lbl)

        # 微操次数
        trades_lbl = QLabel(str(d["trades"]))
        trades_lbl.setFixedWidth(70)
        trades_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        trades_lbl.setFont(numeric_font(11))
        trades_lbl.setStyleSheet("color: #64748B;")
        h.addWidget(trades_lbl)

        # 微操数量
        qty_lbl = QLabel(fmt_money(d["qty"], 2))
        qty_lbl.setFixedWidth(85)
        qty_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        qty_lbl.setFont(numeric_font(11))
        qty_lbl.setStyleSheet("color: #475569;")
        h.addWidget(qty_lbl)

        def make_click(s=sym):
            return lambda e: self.filter_by_ticker(s)
        frame.mousePressEvent = make_click(sym)
        return frame

    def filter_by_broker(self, broker_name: str):
        """点击券商渠道下钻反选"""
        if self.active_ranking_broker == broker_name:
            self.active_ranking_broker = ""
        else:
            self.active_ranking_broker = broker_name
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def filter_by_ticker(self, ticker: str):
        """点击股票代码下钻反选"""
        if self.active_ranking_ticker == ticker:
            self.active_ranking_ticker = ""
        else:
            self.active_ranking_ticker = ticker
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def on_trend_month_clicked(self, month: str):
        """点击月度柱体下钻筛选，严格不改变全局起止月份范围！"""
        self.active_chart_month = month
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def switch_trend_mode(self, mode: str):
        self.trend_tab_monthly.setChecked(mode == "monthly")
        self.trend_tab_cum.setChecked(mode == "cum")
        self.trend_canvas.set_mode(mode)

    def on_header_section_clicked(self, section: int):
        self.table_model.toggle_sort(section)
        self.update_pagination_ui()

    def change_page(self, delta: int):
        self.table_model.set_page(self.table_model.page + delta)
        self.update_pagination_ui()

    def update_pagination_ui(self):
        total_pages = max(1, (len(self.table_model.all_rows) + self.table_model.page_size - 1) // self.table_model.page_size)
        cur_p = self.table_model.page
        self.page_num_lbl.setText(f"{cur_p} / {total_pages}")
        self.prev_page_btn.setEnabled(cur_p > 1)
        self.next_page_btn.setEnabled(cur_p < total_pages)

    def on_table_row_clicked(self, index: QModelIndex):
        r = index.row()
        if 0 <= r < len(self.table_model.rows):
            record = self.table_model.rows[r]
            self.open_drawer(record)

    def on_table_row_double_clicked(self, index: QModelIndex):
        r = index.row()
        if 0 <= r < len(self.table_model.rows):
            record = self.table_model.rows[r]
            sym = str(record[3])
            self.drill.emit("replay", sym, record)

    def open_drawer(self, record: List[Any]):
        """在右侧展开抽屉，展示选中的微操明细卡片与原始交易回放入口"""
        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        m, broker, cat, sym, cnt, qty, b_price, s_price, spread, b_amt, s_amt, pnl, t_first, t_last = record

        self.drawer_title.setText(f"微操详情 · {sym}")
        self.drawer_subtitle.setText(f"{m} · {broker} · {cat}")

        # 核心结果卡片
        c1 = QFrame()
        c1.setStyleSheet("background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;")
        c1_v = QVBoxLayout(c1)
        c1_v.setContentsMargins(8, 8, 8, 8)
        c1_v.setSpacing(4)
        c1_v.addWidget(QLabel("微操收益"))

        pnl_lbl = QLabel(f"{pnl} USD")
        pnl_lbl.setFont(numeric_font(20, bold=True))
        pnl_lbl.setStyleSheet(f"color: {'#10B981' if str(pnl).startswith('+') else '#EF4444'};")
        c1_v.addWidget(pnl_lbl)

        spread_h = QHBoxLayout()
        spread_h.addWidget(QLabel("每股价差:"))
        spread_val_lbl = QLabel(f"{spread} USD")
        spread_val_lbl.setFont(numeric_font(12, bold=True))
        spread_val_lbl.setStyleSheet(f"color: {'#10B981' if str(spread).startswith('+') else '#EF4444'};")
        spread_h.addWidget(spread_val_lbl)
        spread_h.addStretch()
        c1_v.addLayout(spread_h)
        self.drawer_list_layout.addWidget(c1)

        # 规模明细卡片
        c2 = QFrame()
        c2.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;")
        c2_v = QVBoxLayout(c2)
        c2_v.setContentsMargins(8, 8, 8, 8)
        c2_v.setSpacing(6)
        c2_v.addWidget(QLabel("交易规模与均价"))
        c2_v.addWidget(self._make_drawer_kv("微操次数", f"{cnt} 次"))
        c2_v.addWidget(self._make_drawer_kv("微操数量", f"{qty} 股"))
        c2_v.addWidget(self._make_drawer_kv("微操均买价", str(b_price)))
        c2_v.addWidget(self._make_drawer_kv("微操均卖价", str(s_price)))
        c2_v.addWidget(self._make_drawer_kv("买入总金额", str(b_amt)))
        c2_v.addWidget(self._make_drawer_kv("卖出总金额", str(s_amt)))
        self.drawer_list_layout.addWidget(c2)

        # 时间记录卡片
        c3 = QFrame()
        c3.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;")
        c3_v = QVBoxLayout(c3)
        c3_v.setContentsMargins(8, 8, 8, 8)
        c3_v.setSpacing(6)
        c3_v.addWidget(QLabel("时间追溯"))
        c3_v.addWidget(self._make_drawer_kv("首笔微操时间", str(t_first)))
        c3_v.addWidget(self._make_drawer_kv("最近微操时间", str(t_last)))

        replay_btn = QPushButton(f"查看 {sym} 原始交易回放与明细")
        replay_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        replay_btn.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 6px;
                color: #2563EB; font-weight: 600; font-size: 12px; padding: 8px 12px; margin-top: 6px;
            }
            QPushButton:hover { background: #DBEAFE; }
        """)
        replay_btn.clicked.connect(lambda: self.drill.emit("replay", sym, record))
        c3_v.addWidget(replay_btn)

        self.drawer_list_layout.addWidget(c3)
        self.drawer_list_layout.addStretch()

        self.show_drawer()

    def _make_drawer_kv(self, k: str, v: str, bold: bool = False, color: str = "#0F172A") -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lbl_k = QLabel(k)
        lbl_k.setStyleSheet("color: #64748B; font-size: 12px;")
        lbl_v = QLabel(v)
        lbl_v.setFont(numeric_font(12, bold=bold))
        lbl_v.setStyleSheet(f"color: {color};")
        lay.addWidget(lbl_k)
        lay.addStretch()
        lay.addWidget(lbl_v)
        return w

    def show_drawer(self):
        w = 420
        self.drawer_overlay.setGeometry(self.rect())
        self.drawer_overlay.show()
        self.drawer_overlay.raise_()
        self.drawer_frame.setGeometry(self.width() - w, 0, w, self.height())
        self.drawer_frame.show()
        self.drawer_frame.raise_()

    def close_drawer(self):
        self.drawer_overlay.hide()
        self.drawer_frame.hide()

    def export_csv(self):
        if not self.table_model.all_rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出微操收益明细", "每月微操收益明细.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(self.table_model.columns)
                for row in self.table_model.all_rows:
                    writer.writerow(row)
        except Exception as e:
            pass
