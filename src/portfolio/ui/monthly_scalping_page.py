# -*- coding: utf-8 -*-
"""
Monthly Scalping Performance Page (每月微操收益页面)
深度打造的主动交易微操操作绩效分析页面：
包含核心指标卡片、月度收益趋势柱状图、券商渠道收益排行、盈利/亏损股票 TOP 5、微操明细表格与抽屉联动。
"""

import csv
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
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
            return f"0.{'0' * digits}"
        sign = "+" if f > 0 else ""
        return f"{sign}{f:,.{digits}f}"
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


def numeric_font(size: int = 12, bold: bool = False) -> QFont:
    font = QFont(".AppleSystemUIFont", size)
    font.setFamilies([
        "SF Pro Display",
        "SF Pro Text",
        "-apple-system",
        "Helvetica Neue",
        "PingFang SC",
        "Microsoft YaHei",
        "sans-serif",
    ])
    font.setStyleHint(QFont.StyleHint.SansSerif)
    if bold:
        font.setWeight(QFont.Weight.Bold)
    return font


# -------------------------------------------------------------------------
# 1. 核心指标卡片 (Metric Card)
# -------------------------------------------------------------------------
class ScalpingMetricCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, icon_str: str, title: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("kpiCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(88)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        # Icon Circle
        self.icon_badge = QLabel(icon_str)
        self.icon_badge.setFixedSize(38, 38)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_badge.setStyleSheet("""
            background-color: #F1F5F9;
            color: #2563EB;
            font-size: 16px;
            border-radius: 19px;
            font-weight: 700;
        """)
        layout.addWidget(self.icon_badge)

        # Right Text Column
        text_vbox = QVBoxLayout()
        text_vbox.setContentsMargins(0, 0, 0, 0)
        text_vbox.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("color: #64748B; font-size: 11.5px; font-weight: 500;")
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
                border-radius: 8px;
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
            self.icon_badge.setStyleSheet("background-color: #ECFDF5; color: #10B981; font-size: 16px; border-radius: 19px; font-weight: 700;")
        elif status == "loss":
            self.value_label.setStyleSheet("color: #EF4444;")
            self.icon_badge.setStyleSheet("background-color: #FEF2F2; color: #EF4444; font-size: 16px; border-radius: 19px; font-weight: 700;")
        else:
            self.value_label.setStyleSheet("color: #0F172A;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# 2. 月度微操收益趋势图画布 (MonthlyScalpingTrendCanvas)
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
        margin_top = 25
        margin_bottom = 35

        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        if plot_w <= 10 or plot_h <= 10:
            return

        vals = [p[1] if self.mode == "monthly" else p[2] for p in self.data_points]
        raw_min = min(vals)
        raw_max = max(vals)

        # 计算对称或自适应坐标轴区间
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
        y_ticks = 5
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

        # 3. 绘制柱体
        n = len(self.data_points)
        slot_w = plot_w / n
        bar_w = max(4.0, min(24.0, slot_w * 0.60))

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

            # 颜色设定：正绿负红
            if val >= 0:
                fill_color = QColor("#10B981") if not is_hover else QColor("#059669")
            else:
                fill_color = QColor("#EF4444") if not is_hover else QColor("#DC2626")

            painter.fillRect(QRectF(rx, top_y, bar_w, bar_h), fill_color)

            # 选中高亮边框
            if is_sel:
                painter.setPen(QPen(QColor("#1E293B"), 2.0))
                painter.drawRect(QRectF(rx - 1, top_y - 1, bar_w + 2, bar_h + 2))

            # X 轴月份标签（适当抽样以防重叠）
            step = max(1, n // 12)
            if i % step == 0 or i == n - 1:
                painter.setPen(QColor("#64748B" if not is_sel else "#2563EB"))
                painter.setFont(numeric_font(9, bold=is_sel))
                lbl_m = m[2:] if len(m) == 7 else m  # "25-01" 缩写更整洁
                painter.drawText(QRectF(cx - slot_w / 2.0, margin_top + plot_h + 4, slot_w, 20),
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
            tip = f"月份: {m}\n当月微操收益: {fmt_signed_money(m_pnl)}\n累计微操收益: {fmt_signed_money(cum_pnl)}"
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
                    self.selected_month = ""  # 再次点击反选
                else:
                    self.selected_month = clicked_m
                self.month_selected.emit(self.selected_month)
                self.update()
        super().mousePressEvent(event)


# -------------------------------------------------------------------------
# 3. 现代微操收益明细表格数据模型 (ModernScalpingTableModel)
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
            if val_str in ("0.00", "0", "—"):
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
        clean = s.replace("$", "").replace(",", "").replace("%", "").replace("+", "").replace("−", "-")
        try:
            return float(clean)
        except ValueError:
            return s

    def apply_sort_and_page(self):
        self.beginResetModel()
        if self.sort_column >= 0:
            rev = (self.sort_order == Qt.SortOrder.DescendingOrder)
            # 如果没有主动排序，默认按第一级月份降序、第二级微操收益降序、第三级 Ticker 升序
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, self.sort_column), reverse=rev)

        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()

    def set_columns_and_rows(self, cols: Tuple[str, ...], rows: List[List[Any]]):
        self.beginResetModel()
        self.columns = cols
        self.all_rows = list(rows)
        # 默认排序规则：第一级：月份降序，最新月份在前；第二级：微操收益降序；第三级：Ticker 升序
        if self.sort_column == 0 and self.sort_order == Qt.SortOrder.DescendingOrder:
            self.all_rows.sort(key=lambda r: (
                str(r[0]) if len(r) > 0 else "",
                float(str(r[11]).replace("$", "").replace(",", "").replace("+", "") or 0) if len(r) > 11 else 0
            ), reverse=True)
        elif self.sort_column >= 0:
            rev = (self.sort_order == Qt.SortOrder.DescendingOrder)
            self.all_rows.sort(key=lambda r: self._parse_sort_key(r, self.sort_column), reverse=rev)

        start = (self.page - 1) * self.page_size
        end = start + self.page_size
        self.rows = self.all_rows[start:end]
        self.endResetModel()

    def toggle_sort(self, column: int):
        if column < 0 or column >= len(self.columns):
            return
        if self.sort_column == column:
            self.sort_order = Qt.SortOrder.DescendingOrder if self.sort_order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        else:
            self.sort_column = column
            self.sort_order = Qt.SortOrder.DescendingOrder if column in (0, 4, 5, 9, 10, 11) else Qt.SortOrder.AscendingOrder
        self.apply_sort_and_page()

    def set_page(self, page: int):
        total_pages = max(1, (len(self.all_rows) + self.page_size - 1) // self.page_size)
        new_p = max(1, min(page, total_pages))
        if new_p != self.page:
            self.page = new_p
            self.apply_sort_and_page()


# -------------------------------------------------------------------------
# 主页面：MonthlyScalpingPage
# -------------------------------------------------------------------------
class MonthlyScalpingPage(QWidget):
    drill = Signal(str, str, object)  # 信号：下钻或历史回放

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.raw_rows: List[Tuple] = []
        self.current_filtered_rows: List[Tuple] = []

        # 筛选状态
        self.selected_person_id: Any = None
        self.selected_broker: str = ""
        self.selected_category: str = ""
        self.filter_ticker: str = ""
        self.start_month: str = ""
        self.end_month: str = ""
        self.active_chart_month: str = ""

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
        content_layout.setContentsMargins(20, 16, 20, 24)
        content_layout.setSpacing(14)

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

        self.db_source_lbl = QLabel("🗄️ 数据来源：money.sqlite3")
        self.db_source_lbl.setStyleSheet("color: #94A3B8; font-size: 11px;")
        header_text_vbox.addWidget(self.db_source_lbl)

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

        self.export_btn = QPushButton("导出 ∨")
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
        filter_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        filter_layout = QHBoxLayout(filter_card)
        filter_layout.setContentsMargins(14, 10, 14, 10)
        filter_layout.setSpacing(10)

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
        self.ticker_edit.setPlaceholderText("输入 Ticker")
        self.ticker_edit.setFixedHeight(32)
        self.ticker_edit.setFixedWidth(130)
        self.ticker_edit.setStyleSheet("QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 8px; font-size: 12px; }")
        self.ticker_edit.returnPressed.connect(self.apply_filters)
        filter_layout.addWidget(self.ticker_edit)

        # 月份范围 (2025-01 ~ 2026-11)
        self.start_month_edit = QLineEdit()
        self.start_month_edit.setPlaceholderText("起始月份 如 2025-01")
        self.start_month_edit.setFixedHeight(32)
        self.start_month_edit.setFixedWidth(120)
        self.start_month_edit.setStyleSheet("QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 8px; font-size: 12px; }")
        filter_layout.addWidget(self.start_month_edit)

        tilde_lbl = QLabel("~")
        tilde_lbl.setStyleSheet("color: #64748B; font-weight: bold;")
        filter_layout.addWidget(tilde_lbl)

        self.end_month_edit = QLineEdit()
        self.end_month_edit.setPlaceholderText("结束月份 如 2026-11")
        self.end_month_edit.setFixedHeight(32)
        self.end_month_edit.setFixedWidth(120)
        self.end_month_edit.setStyleSheet("QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 8px; font-size: 12px; }")
        filter_layout.addWidget(self.end_month_edit)

        filter_layout.addStretch()

        # 查询与重置按钮
        self.search_btn = QPushButton("查询")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.setFixedHeight(32)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB; color: #FFFFFF; font-weight: 600; font-size: 12px;
                border: none; border-radius: 6px; padding: 0 16px;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        self.search_btn.clicked.connect(self.apply_filters)
        filter_layout.addWidget(self.search_btn)

        self.reset_btn = QPushButton("重置")
        self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setFixedHeight(32)
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9; color: #475569; font-weight: 600; font-size: 12px;
                border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 16px;
            }
            QPushButton:hover { background: #E2E8F0; color: #0F172A; }
        """)
        self.reset_btn.clicked.connect(self.reset_filters)
        filter_layout.addWidget(self.reset_btn)

        content_layout.addWidget(filter_card)

        # 3. 核心指标卡片区域 (5张横向并排)
        kpi_grid = QHBoxLayout()
        kpi_grid.setSpacing(12)

        self.kpi_cards: Dict[str, ScalpingMetricCard] = {}
        configs = [
            ("pnl", "🪙", "微操收益合计"),
            ("trades", "⇄", "微操次数"),
            ("buy", "↗", "买入金额合计"),
            ("sell", "↘", "卖出金额合计"),
            ("spread", "%", "平均每股价差"),
        ]
        for key, icon_s, title_s in configs:
            card = ScalpingMetricCard(key, icon_s, title_s, self)
            self.kpi_cards[key] = card
            kpi_grid.addWidget(card)
        content_layout.addLayout(kpi_grid)

        # 4. 中部图表与渠道排行 (Splitter: 58% : 42%)
        self.mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mid_splitter.setStyleSheet("QSplitter::handle { background: transparent; }")

        # 左侧：月度微操收益趋势卡片
        trend_card = QFrame()
        trend_card.setFixedHeight(260)
        trend_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        trend_vbox = QVBoxLayout(trend_card)
        trend_vbox.setContentsMargins(14, 10, 14, 10)
        trend_vbox.setSpacing(4)

        trend_top = QHBoxLayout()
        trend_title = QLabel("月度微操收益趋势")
        trend_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        trend_title.setStyleSheet("color: #0F172A;")
        trend_top.addWidget(trend_title)
        trend_top.addStretch()

        self.trend_tab_monthly = QPushButton("月度收益")
        self.trend_tab_cum = QPushButton("累计收益")
        for btn in (self.trend_tab_monthly, self.trend_tab_cum):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #64748B; font-size: 11.5px;
                    font-weight: 600; border: none; padding: 3px 8px; border-radius: 4px;
                }
                QPushButton:checked { background: #EFF6FF; color: #2563EB; }
            """)
        self.trend_tab_monthly.setChecked(True)
        self.trend_tab_monthly.clicked.connect(lambda: self.switch_trend_mode("monthly"))
        self.trend_tab_cum.clicked.connect(lambda: self.switch_trend_mode("cum"))

        trend_top.addWidget(self.trend_tab_monthly)
        trend_top.addWidget(self.trend_tab_cum)
        trend_vbox.addLayout(trend_top)

        self.trend_canvas = MonthlyScalpingTrendCanvas(self)
        self.trend_canvas.month_selected.connect(self.on_trend_month_clicked)
        trend_vbox.addWidget(self.trend_canvas)
        self.mid_splitter.addWidget(trend_card)

        # 右侧：券商渠道微操收益排行卡片
        broker_card = QFrame()
        broker_card.setFixedHeight(260)
        broker_card.setMinimumWidth(360)
        broker_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        broker_vbox = QVBoxLayout(broker_card)
        broker_vbox.setContentsMargins(14, 10, 14, 10)
        broker_vbox.setSpacing(6)

        b_header = QHBoxLayout()
        b_title = QLabel("券商渠道微操收益排行")
        b_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        b_title.setStyleSheet("color: #0F172A;")
        b_header.addWidget(b_title)
        b_header.addStretch()
        broker_vbox.addLayout(b_header)

        # 表头
        b_th = QHBoxLayout()
        b_th.setContentsMargins(6, 2, 6, 2)
        th_b_name = QLabel("券商渠道")
        th_b_name.setFixedWidth(75)
        th_b_name.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        th_b_pnl = QLabel("微操收益")
        th_b_pnl.setFixedWidth(80)
        th_b_pnl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        th_b_pnl.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        th_b_bar = QLabel("微操收益")
        th_b_bar.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600; padding-left: 8px;")
        th_b_share = QLabel("占比")
        th_b_share.setFixedWidth(50)
        th_b_share.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        th_b_share.setStyleSheet("font-size: 11px; color: #94A3B8; font-weight: 600;")
        b_th.addWidget(th_b_name)
        b_th.addWidget(th_b_pnl)
        b_th.addWidget(th_b_bar)
        b_th.addWidget(th_b_share)
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

        # 5. 股票微操收益排行榜 (左右并排 1:1)
        rank_row = QHBoxLayout()
        rank_row.setSpacing(12)

        # 左侧：盈利股票 TOP 5
        profit_card = QFrame()
        profit_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        profit_vbox = QVBoxLayout(profit_card)
        profit_vbox.setContentsMargins(14, 12, 14, 12)
        profit_vbox.setSpacing(8)

        p_hdr = QLabel("🏆 盈利股票 TOP 5")
        p_hdr.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        p_hdr.setStyleSheet("color: #10B981;")
        profit_vbox.addWidget(p_hdr)

        p_th = QHBoxLayout()
        p_th.setContentsMargins(4, 2, 4, 2)
        p_th.addWidget(self._make_th_label("#", 24))
        p_th.addWidget(self._make_th_label("Ticker", 65))
        p_th.addWidget(self._make_th_label("微操收益", 75, align_right=True))
        p_th.addWidget(self._make_th_label("微操次数", 60, align_right=True))
        p_th.addWidget(self._make_th_label("买入金额", 75, align_right=True))
        p_th.addWidget(self._make_th_label("卖出金额", 75, align_right=True))
        profit_vbox.addLayout(p_th)

        self.profit_top_vbox = QVBoxLayout()
        self.profit_top_vbox.setSpacing(4)
        profit_vbox.addLayout(self.profit_top_vbox)
        profit_vbox.addStretch()
        rank_row.addWidget(profit_card, 1)

        # 右侧：亏损股票 TOP 5
        loss_card = QFrame()
        loss_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        loss_vbox = QVBoxLayout(loss_card)
        loss_vbox.setContentsMargins(14, 12, 14, 12)
        loss_vbox.setSpacing(8)

        l_hdr = QLabel("🥊 亏损股票 TOP 5")
        l_hdr.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        l_hdr.setStyleSheet("color: #EF4444;")
        loss_vbox.addWidget(l_hdr)

        l_th = QHBoxLayout()
        l_th.setContentsMargins(4, 2, 4, 2)
        l_th.addWidget(self._make_th_label("#", 24))
        l_th.addWidget(self._make_th_label("Ticker", 65))
        l_th.addWidget(self._make_th_label("微操收益", 75, align_right=True))
        l_th.addWidget(self._make_th_label("微操次数", 60, align_right=True))
        l_th.addWidget(self._make_th_label("买入金额", 75, align_right=True))
        l_th.addWidget(self._make_th_label("卖出金额", 75, align_right=True))
        loss_vbox.addLayout(l_th)

        self.loss_top_vbox = QVBoxLayout()
        self.loss_top_vbox.setSpacing(4)
        loss_vbox.addLayout(self.loss_top_vbox)
        loss_vbox.addStretch()
        rank_row.addWidget(loss_card, 1)

        content_layout.addLayout(rank_row)

        # 6. 微操收益明细表格卡片 (底部全宽)
        table_card = QFrame()
        table_card.setStyleSheet("background-color: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 8px;")
        table_vbox = QVBoxLayout(table_card)
        table_vbox.setContentsMargins(16, 12, 16, 12)
        table_vbox.setSpacing(10)

        table_top_h = QHBoxLayout()
        tbl_title = QLabel("微操收益明细")
        tbl_title.setFont(QFont(".AppleSystemUIFont", 13, QFont.Weight.Bold))
        tbl_title.setStyleSheet("color: #0F172A;")
        table_top_h.addWidget(tbl_title)
        table_top_h.addStretch()

        self.table_count_lbl = QLabel("共 0 条记录")
        self.table_count_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        table_top_h.addWidget(self.table_count_lbl)
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
        self.table_view.verticalHeader().setDefaultSectionSize(36)
        self.table_view.horizontalHeader().setFixedHeight(36)
        self.table_view.horizontalHeader().setHighlightSections(False)
        self.table_view.horizontalHeader().setSectionsClickable(True)
        self.table_view.horizontalHeader().sectionClicked.connect(self.on_header_section_clicked)
        self.table_view.setMinimumHeight(540)
        self.table_view.setStyleSheet("""
            QTableView {
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
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

        # 分页控制器
        page_h = QHBoxLayout()
        page_h.addStretch()

        self.prev_page_btn = QPushButton("‹ 上一页")
        self.next_page_btn = QPushButton("下一页 ›")
        for btn in (self.prev_page_btn, self.next_page_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #FFFFFF; border: 1px solid #CBD5E1; border-radius: 4px;
                    padding: 3px 10px; font-size: 11px; font-weight: 500; color: #334155;
                }
                QPushButton:hover:!disabled { background: #EFF6FF; border-color: #3B82F6; color: #2563EB; }
                QPushButton:disabled { color: #CBD5E1; border-color: #E2E8F0; background: #F8FAFC; }
            """)
        self.prev_page_btn.clicked.connect(lambda: self.change_page(-1))
        self.next_page_btn.clicked.connect(lambda: self.change_page(1))

        self.page_num_lbl = QLabel("第 1 / 1 页")
        self.page_num_lbl.setStyleSheet("font-size: 11.5px; color: #64748B; margin: 0 8px;")

        page_h.addWidget(self.prev_page_btn)
        page_h.addWidget(self.page_num_lbl)
        page_h.addWidget(self.next_page_btn)
        table_vbox.addLayout(page_h)

        content_layout.addWidget(table_card)

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll)

        # 7. 右侧详情抽屉 (Floating Drawer Overlay)
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
        drawer_vbox.setContentsMargins(16, 16, 16, 16)
        drawer_vbox.setSpacing(12)

        drawer_top = QHBoxLayout()
        self.drawer_title = QLabel("微操收益详情")
        self.drawer_title.setFont(QFont(".AppleSystemUIFont", 14, QFont.Weight.Bold))
        self.drawer_title.setStyleSheet("color: #0F172A;")
        drawer_top.addWidget(self.drawer_title)
        drawer_top.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { border: none; background: transparent; font-size: 14px; color: #64748B; border-radius: 12px; }
            QPushButton:hover { background-color: #F1F5F9; color: #0F172A; }
        """)
        close_btn.clicked.connect(self.close_drawer)
        drawer_top.addWidget(close_btn)
        drawer_vbox.addLayout(drawer_top)

        self.drawer_scroll = QScrollArea()
        self.drawer_scroll.setWidgetResizable(True)
        self.drawer_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.drawer_list_widget = QWidget()
        self.drawer_list_layout = QVBoxLayout(self.drawer_list_widget)
        self.drawer_list_layout.setContentsMargins(0, 0, 0, 0)
        self.drawer_list_layout.setSpacing(10)
        self.drawer_scroll.setWidget(self.drawer_list_widget)
        drawer_vbox.addWidget(self.drawer_scroll)

    def _make_th_label(self, text: str, width: int = 60, align_right: bool = False) -> QLabel:
        lbl = QLabel(text)
        if width > 0:
            lbl.setFixedWidth(width)
        if align_right:
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet("font-size: 10.5px; color: #94A3B8; font-weight: 600;")
        return lbl

    # -------------------------------------------------------------------------
    # 尺寸与自适应布局
    # -------------------------------------------------------------------------
    def apply_proportional_column_widths(self):
        """按 14 列基准宽度平摊视口剩余空间，杜绝留白或最后一列拉长"""
        if not hasattr(self, "table_view") or not self.table_view:
            return
        header = self.table_view.horizontalHeader()
        header.setStretchLastSection(False)

        # 14 列基准宽度：
        # 月份(85), 券商渠道(90), 分类(75), Ticker(80), 微操次数(70), 微操数量(85),
        # 微操均买价(95), 微操均卖价(95), 每股价差(90), 买入金额(105), 卖出金额(105), 微操收益(110),
        # 首笔微操时间(135), 最近微操时间(135)
        base_widths = [85, 90, 75, 80, 70, 85, 95, 95, 90, 105, 105, 110, 135, 135]
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

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "mid_splitter") and self.mid_splitter:
            w = self.mid_splitter.width()
            if w > 100:
                self.mid_splitter.setSizes([int(w * 0.58), int(w * 0.42)])
        QTimer.singleShot(50, self.apply_proportional_column_widths)

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
            self.selected_person_id = person_id
            if self.person_combo.count() <= 1:
                self._populate_persons()
            self.person_combo.blockSignals(True)
            idx = self.person_combo.findData(person_id)
            self.person_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.person_combo.blockSignals(False)
            self.apply_filters()
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
            self.apply_filters()
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

    def update_data(self, analytics):
        self.analytics = analytics
        if not analytics or not hasattr(analytics, "reports"):
            return

        report = analytics.reports.get("每月微操收益")
        if not report:
            return

        # 提取原始数据元组
        self.raw_rows = [tuple(r.values) for r in report.rows]

        # 提取全部可用渠道与分类用于填充筛选框
        brokers = sorted({fmt_broker_cn(r[1]) for r in self.raw_rows if len(r) > 1 and r[1]})
        current_b = self.broker_combo.currentText()
        self.broker_combo.blockSignals(True)
        self.broker_combo.clear()
        self.broker_combo.addItem("全部渠道", "")
        for b in brokers:
            self.broker_combo.addItem(b, b)
        b_idx = self.broker_combo.findText(current_b)
        self.broker_combo.setCurrentIndex(max(0, b_idx))
        self.broker_combo.blockSignals(False)

        categories = sorted({str(r[2]) for r in self.raw_rows if len(r) > 2 and r[2]})
        current_cat = self.category_combo.currentText()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类", "")
        for c in categories:
            self.category_combo.addItem(c, c)
        c_idx = self.category_combo.findText(current_cat)
        self.category_combo.setCurrentIndex(max(0, c_idx))
        self.category_combo.blockSignals(False)

        # 起止月份默认值
        months = sorted({str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]})
        if months and not self.start_month_edit.text():
            self.start_month_edit.setText(months[0])
        if months and not self.end_month_edit.text():
            self.end_month_edit.setText(months[-1])

        self.recalculate_and_refresh()

    def apply_filters(self):
        p_id = self.person_combo.currentData()
        person_changed = (p_id != self.selected_person_id)
        self.selected_person_id = p_id
        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(p_id)
            finally:
                self._syncing_person = False
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def reset_filters(self):
        self.person_combo.setCurrentIndex(0)
        p_id = self.person_combo.currentData()
        person_changed = (self.selected_person_id is not None)
        self.selected_person_id = None
        main_win = getattr(self, "main_window", None) or self.parent() or self.window()
        if person_changed and hasattr(main_win, "sync_person_from_holdings") and not getattr(self, "_syncing_person", False):
            self._syncing_person = True
            try:
                main_win.sync_person_from_holdings(None)
            finally:
                self._syncing_person = False
        self.broker_combo.setCurrentIndex(0)
        self.category_combo.setCurrentIndex(0)
        self.ticker_edit.clear()
        self.active_chart_month = ""
        months = sorted({str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]})
        if months:
            self.start_month_edit.setText(months[0])
            self.end_month_edit.setText(months[-1])
        else:
            self.start_month_edit.clear()
            self.end_month_edit.clear()
        self.table_model.page = 1
        self.recalculate_and_refresh()

    def recalculate_and_refresh(self):
        if not self.raw_rows:
            return

        b_filter = self.broker_combo.currentData() or ""
        cat_filter = self.category_combo.currentData() or ""
        ticker_kw = self.ticker_edit.text().strip().upper()
        s_m = self.start_month_edit.text().strip()
        e_m = self.end_month_edit.text().strip()

        filtered: List[Tuple] = []
        for r in self.raw_rows:
            m = str(r[0]) if len(r) > 0 else ""
            b_cn = fmt_broker_cn(r[1]) if len(r) > 1 else ""
            cat = str(r[2]) if len(r) > 2 else ""
            sym = str(r[3]).upper() if len(r) > 3 else ""

            if b_filter and b_cn != b_filter:
                continue
            if cat_filter and cat != cat_filter:
                continue
            if ticker_kw and ticker_kw not in sym:
                continue
            if s_m and m < s_m:
                continue
            if e_m and m > e_m:
                continue

            filtered.append(r)

        self.current_filtered_rows = filtered

        # 1. 计算 5 项核心指标
        tot_pnl = sum(float(r[11] or 0) for r in filtered)
        tot_trades_cnt = sum(int(r[4] or 0) for r in filtered)
        tot_buy_amt = sum(float(r[9] or 0) for r in filtered)
        tot_sell_amt = sum(float(r[10] or 0) for r in filtered)
        unique_tickers = len({r[3] for r in filtered if len(r) > 3 and r[3]})
        avg_spread = (sum(float(r[8] or 0) for r in filtered) / len(filtered)) if filtered else 0.0

        pnl_status = "profit" if tot_pnl > 0 else ("loss" if tot_pnl < 0 else None)
        self.kpi_cards["pnl"].set_data(f"{'+' if tot_pnl > 0 else ''}${tot_pnl:,.2f}", "当前筛选期间", pnl_status)
        self.kpi_cards["trades"].set_data(str(tot_trades_cnt), f"涉及 {unique_tickers} 只股票")
        self.kpi_cards["buy"].set_data(f"${tot_buy_amt:,.2f}", "")
        self.kpi_cards["sell"].set_data(f"${tot_sell_amt:,.2f}", "")
        spread_status = "profit" if avg_spread > 0 else ("loss" if avg_spread < 0 else None)
        self.kpi_cards["spread"].set_data(f"{'+' if avg_spread > 0 else ''}${avg_spread:,.2f}", "已完成的微操交易", spread_status)

        # 2. 统计月度趋势柱状图数据 (month -> pnl)
        month_map: Dict[str, float] = {}
        for r in filtered:
            m = str(r[0])
            month_map[m] = month_map.get(m, 0.0) + float(r[11] or 0)

        trend_pts: List[Tuple[str, float, float]] = []
        cum_pnl = 0.0
        for m in sorted(month_map.keys()):
            pnl = month_map[m]
            cum_pnl += pnl
            trend_pts.append((m, pnl, cum_pnl))

        self.trend_canvas.set_data(trend_pts, self.active_chart_month)

        # 3. 统计券商渠道收益排行
        broker_map: Dict[str, float] = {}
        for r in filtered:
            b_cn = fmt_broker_cn(r[1])
            broker_map[b_cn] = broker_map.get(b_cn, 0.0) + float(r[11] or 0)

        self.update_broker_rankings(broker_map, tot_pnl)

        # 4. 统计股票微操收益 TOP 5 (盈利 TOP 5 & 亏损 TOP 5)
        stock_map: Dict[str, Dict[str, Any]] = {}
        for r in filtered:
            sym = str(r[3])
            if sym not in stock_map:
                stock_map[sym] = {"pnl": 0.0, "trades": 0, "buy": 0.0, "sell": 0.0}
            stock_map[sym]["pnl"] += float(r[11] or 0)
            stock_map[sym]["trades"] += int(r[4] or 0)
            stock_map[sym]["buy"] += float(r[9] or 0)
            stock_map[sym]["sell"] += float(r[10] or 0)

        self.update_stock_top5(stock_map)

        # 5. 更新明细表格数据
        table_rows: List[List[Any]] = []
        for r in filtered:
            # 格式化各列
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
                f"{'+' if spread_val > 0 else ''}${fmt_money(spread_val, 2)}", # 每股价差($)
                f"${fmt_money(r[9], 2)}",                   # 买入金额($)
                f"${fmt_money(r[10], 2)}",                  # 卖出金额($)
                f"{'+' if pnl_val > 0 else ''}${fmt_money(pnl_val, 2)}",      # 微操收益($)
                str(r[12]) if len(r) > 12 else "—",         # 首笔微操时间
                str(r[13]) if len(r) > 13 else "—",         # 最近微操时间
            ])

        self.table_model.highlight_month = self.active_chart_month
        self.table_model.set_columns_and_rows(self.table_model.columns, table_rows)
        self.update_pagination_ui()
        self.apply_proportional_column_widths()

    # -------------------------------------------------------------------------
    # 券商渠道与股票排行榜渲染
    # -------------------------------------------------------------------------
    def update_broker_rankings(self, broker_map: Dict[str, float], total_pnl: float):
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
        abs_tot = abs(total_pnl) if abs(total_pnl) > 0.001 else 1.0

        for b_name, b_pnl in sorted_brokers:
            row_frame = QFrame()
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)
            row_frame.setStyleSheet("""
                QFrame {
                    background: #FFFFFF; border: 1px solid #F1F5F9; border-radius: 4px; padding: 2px 4px;
                }
                QFrame:hover { background: #F8FAFC; border-color: #CBD5E1; }
            """)
            r_lay = QHBoxLayout(row_frame)
            r_lay.setContentsMargins(6, 4, 6, 4)
            r_lay.setSpacing(6)

            name_lbl = QLabel(b_name)
            name_lbl.setFixedWidth(75)
            name_lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #1E293B;")
            r_lay.addWidget(name_lbl)

            pnl_lbl = QLabel(fmt_signed_money(b_pnl))
            pnl_lbl.setFixedWidth(80)
            pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pnl_lbl.setFont(numeric_font(11, bold=True))
            pnl_lbl.setStyleSheet(f"color: {'#10B981' if b_pnl >= 0 else '#EF4444'}; font-size: 11.5px;")
            r_lay.addWidget(pnl_lbl)

            # 可视化条形进度条
            bar = QProgressBar()
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            ratio = (abs(b_pnl) / max_abs_pnl) * 1000
            bar.setValue(int(max(10, min(1000, ratio))))
            color_hex = "#10B981" if b_pnl >= 0 else "#EF4444"
            bg_hex = "#DCFCE7" if b_pnl >= 0 else "#FEE2E2"
            bar.setStyleSheet(f"""
                QProgressBar {{ background-color: {bg_hex}; border: none; border-radius: 4px; }}
                QProgressBar::chunk {{ background-color: {color_hex}; border-radius: 4px; }}
            """)
            r_lay.addWidget(bar, 1)

            share_pct = (abs(b_pnl) / abs_tot) * 100.0
            share_lbl = QLabel(f"{share_pct:.1f}%")
            share_lbl.setFixedWidth(50)
            share_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            share_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
            r_lay.addWidget(share_lbl)

            def make_broker_click(b=b_name):
                return lambda e: self.filter_by_broker(b)
            row_frame.mousePressEvent = make_broker_click(b_name)
            self.broker_list_layout.addWidget(row_frame)

    def filter_by_broker(self, broker: str):
        idx = self.broker_combo.findText(broker)
        if idx >= 0:
            if self.broker_combo.currentIndex() == idx:
                self.broker_combo.setCurrentIndex(0)  # 反选
            else:
                self.broker_combo.setCurrentIndex(idx)
            self.apply_filters()

    def update_stock_top5(self, stock_map: Dict[str, Dict[str, Any]]):
        # 清空
        while self.profit_top_vbox.count():
            item = self.profit_top_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.loss_top_vbox.count():
            item = self.loss_top_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 盈利 TOP 5：微操收益降序
        profit_stocks = [(sym, d) for sym, d in stock_map.items() if d["pnl"] > 0]
        profit_stocks.sort(key=lambda x: x[1]["pnl"], reverse=True)
        top_profit = profit_stocks[:5]

        # 亏损 TOP 5：微操收益升序（最负在最前）
        loss_stocks = [(sym, d) for sym, d in stock_map.items() if d["pnl"] < 0]
        loss_stocks.sort(key=lambda x: x[1]["pnl"])
        top_loss = loss_stocks[:5]

        # 渲染盈利 TOP 5
        if not top_profit:
            empty = QLabel("暂无盈利微操标的")
            empty.setStyleSheet("font-size: 11px; color: #94A3B8; padding: 20px 0;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.profit_top_vbox.addWidget(empty)
        else:
            rank_colors_p = ["#10B981", "#34D399", "#6EE7B7", "#94A3B8", "#CBD5E1"]
            for idx, (sym, d) in enumerate(top_profit):
                row = self._create_stock_rank_row(idx + 1, sym, d, is_profit=True, bg_col=rank_colors_p[idx])
                self.profit_top_vbox.addWidget(row)

        # 渲染亏损 TOP 5
        if not top_loss:
            empty = QLabel("暂无亏损微操标的")
            empty.setStyleSheet("font-size: 11px; color: #94A3B8; padding: 20px 0;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.loss_top_vbox.addWidget(empty)
        else:
            rank_colors_l = ["#EF4444", "#F97316", "#FB923C", "#94A3B8", "#CBD5E1"]
            for idx, (sym, d) in enumerate(top_loss):
                row = self._create_stock_rank_row(idx + 1, sym, d, is_profit=False, bg_col=rank_colors_l[idx])
                self.loss_top_vbox.addWidget(row)

    def _create_stock_rank_row(self, rank: int, sym: str, d: Dict[str, Any], is_profit: bool, bg_col: str) -> QFrame:
        frame = QFrame()
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        hover_bg = "#F0FDF4" if is_profit else "#FEF2F2"
        border_h = "#BBF7D0" if is_profit else "#FECACA"
        frame.setStyleSheet(f"""
            QFrame {{
                background: #FFFFFF; border: 1px solid #F1F5F9; border-radius: 4px; padding: 2px 4px;
            }}
            QFrame:hover {{ background: {hover_bg}; border-color: {border_h}; }}
        """)
        h = QHBoxLayout(frame)
        h.setContentsMargins(4, 2, 4, 2)
        h.setSpacing(6)

        # 排名徽章
        r_lbl = QLabel(str(rank))
        r_lbl.setFixedSize(18, 18)
        r_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        txt_col = "#FFFFFF" if rank <= 3 else "#475569"
        r_lbl.setStyleSheet(f"background: {bg_col}; color: {txt_col}; border-radius: 9px; font-size: 10px; font-weight: 700;")
        h.addWidget(r_lbl)

        # Ticker
        sym_lbl = QLabel(sym)
        sym_lbl.setFixedWidth(65)
        sym_lbl.setStyleSheet("font-size: 11.5px; font-weight: 700; color: #2563EB;")
        h.addWidget(sym_lbl)

        # 微操收益
        pnl_lbl = QLabel(fmt_signed_money(d["pnl"]))
        pnl_lbl.setFixedWidth(75)
        pnl_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pnl_lbl.setFont(numeric_font(11, bold=True))
        pnl_lbl.setStyleSheet(f"color: {'#10B981' if is_profit else '#EF4444'}; font-size: 11.5px;")
        h.addWidget(pnl_lbl)

        # 微操次数
        trades_lbl = QLabel(str(d["trades"]))
        trades_lbl.setFixedWidth(60)
        trades_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        trades_lbl.setFont(numeric_font(11))
        trades_lbl.setStyleSheet("color: #64748B;")
        h.addWidget(trades_lbl)

        # 买入金额
        buy_lbl = QLabel(f"${fmt_money(d['buy'], 2)}")
        buy_lbl.setFixedWidth(75)
        buy_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        buy_lbl.setFont(numeric_font(11))
        buy_lbl.setStyleSheet("color: #475569;")
        h.addWidget(buy_lbl)

        # 卖出金额
        sell_lbl = QLabel(f"${fmt_money(d['sell'], 2)}")
        sell_lbl.setFixedWidth(75)
        sell_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        sell_lbl.setFont(numeric_font(11))
        sell_lbl.setStyleSheet("color: #475569;")
        h.addWidget(sell_lbl)

        def make_click(s=sym):
            return lambda e: self.filter_by_ticker(s)
        frame.mousePressEvent = make_click(sym)
        return frame

    def filter_by_ticker(self, ticker: str):
        if self.ticker_edit.text().strip().upper() == ticker.upper():
            self.ticker_edit.clear()  # 再次点击反选恢复
        else:
            self.ticker_edit.setText(ticker)
        self.apply_filters()

    # -------------------------------------------------------------------------
    # 交互与抽屉联动
    # -------------------------------------------------------------------------
    def on_trend_month_clicked(self, month: str):
        self.active_chart_month = month
        if month:
            self.start_month_edit.setText(month)
            self.end_month_edit.setText(month)
        else:
            months = sorted({str(r[0]) for r in self.raw_rows if len(r) > 0 and r[0]})
            if months:
                self.start_month_edit.setText(months[0])
                self.end_month_edit.setText(months[-1])
        self.apply_filters()

    def switch_trend_mode(self, mode: str):
        self.trend_tab_monthly.setChecked(mode == "monthly")
        self.trend_tab_cum.setChecked(mode == "cum")
        self.trend_canvas.set_mode(mode)

    def on_header_section_clicked(self, section: int):
        self.table_model.toggle_sort(section)

    def change_page(self, delta: int):
        self.table_model.set_page(self.table_model.page + delta)
        self.update_pagination_ui()

    def update_pagination_ui(self):
        total_rows = len(self.table_model.all_rows)
        page_sz = self.table_model.page_size
        max_page = max(1, (total_rows + page_sz - 1) // page_sz)
        cur_page = self.table_model.page
        self.table_count_lbl.setText(f"共 {total_rows} 条记录")
        self.page_num_lbl.setText(f"第 {cur_page} / {max_page} 页")
        self.prev_page_btn.setEnabled(cur_page > 1)
        self.next_page_btn.setEnabled(cur_page < max_page)

    def on_table_row_clicked(self, index: QModelIndex):
        """单击表格行：拉出右侧详情抽屉"""
        if not index.isValid():
            return
        row = index.row()
        if row < len(self.table_model.rows):
            record = self.table_model.rows[row]
            self.open_drawer(record)

    def on_table_row_double_clicked(self, index: QModelIndex):
        """双击表格行：进入历史交易回放"""
        if not index.isValid():
            return
        row = index.row()
        if row < len(self.table_model.rows):
            record = self.table_model.rows[row]
            sym = record[3]
            # 发射下钻回放信号 (向主窗口路由至交易回放)
            self.drill.emit("replay", sym, record)

    def open_drawer(self, record: List[Any]):
        """在右侧展开抽屉，展示选中的微操明细卡片与原始交易回放入口"""
        # 清空已有抽屉卡片
        while self.drawer_list_layout.count():
            item = self.drawer_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        m, broker, cat, sym, cnt, qty, b_price, s_price, spread, b_amt, s_amt, pnl, t_first, t_last = record

        self.drawer_title.setText(f"微操收益详情 · {sym}")

        # 卡片 1：标的概况
        c1 = QFrame()
        c1.setStyleSheet("background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;")
        c1_v = QVBoxLayout(c1)
        c1_v.setSpacing(6)

        c1_v.addWidget(self._make_drawer_kv("股票代码 (Ticker)", str(sym), bold=True, color="#2563EB"))
        c1_v.addWidget(self._make_drawer_kv("所属月份", str(m)))
        c1_v.addWidget(self._make_drawer_kv("券商渠道", str(broker)))
        c1_v.addWidget(self._make_drawer_kv("股票分类", str(cat)))
        self.drawer_list_layout.addWidget(c1)

        # 卡片 2：微操损益与价差
        c2 = QFrame()
        c2.setStyleSheet("background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;")
        c2_v = QVBoxLayout(c2)
        c2_v.setSpacing(6)

        pnl_val_str = str(pnl)
        pnl_col = "#10B981" if "+" in pnl_val_str else ("#EF4444" if "-" in pnl_val_str else "#0F172A")
        c2_v.addWidget(self._make_drawer_kv("微操收益", pnl_val_str, bold=True, color=pnl_col))
        c2_v.addWidget(self._make_drawer_kv("微操次数", f"{cnt} 次"))
        c2_v.addWidget(self._make_drawer_kv("微操股数", f"{qty} 股"))
        c2_v.addWidget(self._make_drawer_kv("每股价差", str(spread)))
        c2_v.addWidget(self._make_drawer_kv("均买价", str(b_price)))
        c2_v.addWidget(self._make_drawer_kv("均卖价", str(s_price)))
        c2_v.addWidget(self._make_drawer_kv("买入总金额", str(b_amt)))
        c2_v.addWidget(self._make_drawer_kv("卖出总金额", str(s_amt)))
        self.drawer_list_layout.addWidget(c2)

        # 卡片 3：时间追溯与回放入口
        c3 = QFrame()
        c3.setStyleSheet("background-color: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; padding: 12px;")
        c3_v = QVBoxLayout(c3)
        c3_v.setSpacing(8)

        c3_v.addWidget(self._make_drawer_kv("首笔微操时间", str(t_first)))
        c3_v.addWidget(self._make_drawer_kv("最近微操时间", str(t_last)))

        replay_btn = QPushButton("查看该标的历史交易回放 →")
        replay_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        replay_btn.setStyleSheet("""
            QPushButton {
                background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 5px;
                padding: 6px 12px; font-size: 11.5px; font-weight: 600; color: #2563EB;
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
        k_lbl = QLabel(k)
        k_lbl.setStyleSheet("font-size: 11.5px; color: #64748B;")
        v_lbl = QLabel(v)
        v_lbl.setFont(numeric_font(12, bold=bold))
        v_lbl.setStyleSheet(f"font-size: 12px; color: {color};")
        lay.addWidget(k_lbl)
        lay.addStretch()
        lay.addWidget(v_lbl)
        return w

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
