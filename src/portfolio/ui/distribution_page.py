"""持仓分布页面 V2.0 (Holdings Distribution Page)

提供投资组合结构分析：
1. 顶部 4 张 KPI 卡片（当前股票市值、较上期市值变化、持仓标的数、前五大持仓集中度）；
2. 三个核心分布模块（股票持仓结构 Top 5 + 其他、券商渠道结构、股票分类结构）；
3. 底部 3 Tab 汇总表格（股票持仓明细、按渠道汇总、按分类汇总）；
4. 右侧滑出抽屉（DistributionDetailDrawer），支持股票详情、渠道详情、分类详情及交易下钻穿透。
"""

from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from portfolio.services.charts_service import build_charts
from portfolio.services.distribution_service import (
    AggregatedItem,
    DistributionSummary,
    HoldingItem,
    calculate_distribution,
    get_available_months,
    get_stock_holding_sources,
    to_chinese_channel,
)
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.theme import tokens


# ----------------------------------------------------------------------
# 辅助绘图与条形图组件
# ----------------------------------------------------------------------

class HorizontalBarItem(QWidget):
    """单条进度比例展示条"""
    clicked = Signal(str)

    def __init__(
        self,
        name: str,
        display_text: str,
        value_text: str,
        pct: float,
        diff_text: str = "",
        diff_val: float = 0.0,
        bar_color: str = "#2563EB",
        parent=None,
    ):
        super().__init__(parent)
        self.item_name = name
        self.display_text = display_text
        self.value_text = value_text
        self.pct = max(0.0, min(100.0, pct))
        self.diff_text = diff_text
        self.diff_val = diff_val
        self.bar_color = QColor(bar_color)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.item_name)
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # 1. 绘制左侧文本 (Ticker / 名称)
        name_font = QFont("PingFang SC", 12, QFont.Weight.Medium)
        painter.setFont(name_font)
        painter.setPen(QColor("#17253D"))
        painter.drawText(0, 0, 75, h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.display_text)

        # 2. 绘制中间水平条形进度槽
        bar_x = 80
        bar_w = max(40, w - 240)
        bar_h = 10
        bar_y = (h - bar_h) // 2

        # 背景槽
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#F1F5F9"))
        painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 5, 5)

        # 填充条
        fill_w = int(bar_w * (self.pct / 100.0))
        if fill_w > 0:
            painter.setBrush(self.bar_color)
            painter.drawRoundedRect(bar_x, bar_y, max(fill_w, 6), bar_h, 5, 5)

        # 3. 绘制右侧金额/占比
        val_x = bar_x + bar_w + 12
        painter.setFont(QFont("SF Pro Text", 12, QFont.Weight.Bold))
        painter.setPen(QColor("#17253D"))
        painter.drawText(val_x, 0, 70, h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.value_text)

        # 4. 绘制对比变化 (pp)
        if self.diff_text:
            diff_x = val_x + 75
            diff_font = QFont("SF Pro Text", 11, QFont.Weight.DemiBold)
            painter.setFont(diff_font)
            if self.diff_val > 0.001:
                painter.setPen(QColor("#16A34A"))
            elif self.diff_val < -0.001:
                painter.setPen(QColor("#DC2626"))
            else:
                painter.setPen(QColor("#64748B"))
            painter.drawText(diff_x, 0, w - diff_x, h, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.diff_text)


class DonutChartWidget(QWidget):
    """现代环形比例图"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: List[Tuple[str, float, str]] = []  # (name, pct, color)
        self.center_text_title = "总市值"
        self.center_text_value = "$0.00"
        self.setMinimumSize(180, 180)

    def set_data(self, items: List[Tuple[str, float, str]], title: str = "总市值", value: str = "$0.00"):
        self.items = items
        self.center_text_title = title
        self.center_text_value = value
        self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        size = min(w, h) - 20
        x = (w - size) // 2
        y = (h - size) // 2

        rect = QRect(x, y, size, size)

        # 绘制扇区
        start_angle = 90 * 16
        for name, pct, color_str in self.items:
            span_angle = -int(pct * 360 * 16 / 100.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color_str))
            painter.drawPie(rect, start_angle, span_angle)
            start_angle += span_angle

        # 内部掏空为环形
        inner_size = int(size * 0.65)
        inner_x = (w - inner_size) // 2
        inner_y = (h - inner_size) // 2
        inner_rect = QRect(inner_x, inner_y, inner_size, inner_size)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(inner_rect)

        # 绘制中间标题与数值
        painter.setPen(QColor("#64748B"))
        painter.setFont(QFont("PingFang SC", 10))
        painter.drawText(inner_rect.adjusted(0, -12, 0, 0), Qt.AlignmentFlag.AlignCenter, self.center_text_title)

        painter.setPen(QColor("#17253D"))
        painter.setFont(QFont("SF Pro Display", 13, QFont.Weight.Bold))
        painter.drawText(inner_rect.adjusted(0, 16, 0, 0), Qt.AlignmentFlag.AlignCenter, self.center_text_value)


# ----------------------------------------------------------------------
# 顶部 4 张 KPI 卡片
# ----------------------------------------------------------------------

class DistributionKpiCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("kpiCard")
        self.setStyleSheet("""
            QFrame#kpiCard {
                background: #FFFFFF;
                border: 1px solid #E5EAF1;
                border-radius: 12px;
                padding: 14px 18px;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        # 标题栏
        top_row = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-size: 13px; font-weight: 500; color: #64748B;")
        top_row.addWidget(self.title_lbl)
        top_row.addStretch()
        layout.addLayout(top_row)

        # 主数值
        self.value_lbl = QLabel("--")
        self.value_lbl.setStyleSheet("font-size: 24px; font-weight: 700; color: #17253D;")
        layout.addWidget(self.value_lbl)

        # 辅助信息标签
        self.sub_lbl = QLabel("--")
        self.sub_lbl.setStyleSheet("font-size: 12px; color: #64748B;")
        layout.addWidget(self.sub_lbl)

    def set_content(self, value: str, sub: str, sub_color: Optional[str] = None):
        self.value_lbl.setText(value)
        self.sub_lbl.setText(sub)
        if sub_color:
            self.sub_lbl.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {sub_color};")
        else:
            self.sub_lbl.setStyleSheet("font-size: 12px; color: #64748B;")


# ----------------------------------------------------------------------
# 右侧滑出抽屉 (DistributionDetailDrawer)
# ----------------------------------------------------------------------

class DistributionDetailDrawer(QWidget):
    """右侧滑出抽屉：支持股票详情、渠道详情、分类详情，支持交易下钻穿透"""
    drill_record_signal = Signal(dict)
    closed = Signal()

    MODE_STOCK = "STOCK"
    MODE_BROKER = "BROKER"
    MODE_CATEGORY = "CATEGORY"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = self.MODE_STOCK
        self.current_data = None
        self.analytics = None

        # 半透明全屏遮罩 + 右侧抽屉卡片
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 遮罩区（点击遮罩关闭）
        self.mask_btn = QPushButton(self)
        self.mask_btn.setStyleSheet("background: rgba(15, 23, 42, 0.40); border: none;")
        self.mask_btn.clicked.connect(self.close_drawer)
        self.mask_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main_layout.addWidget(self.mask_btn, 1)

        # 抽屉卡片容器
        self.panel = QFrame(self)
        self.panel.setObjectName("drawerPanel")
        self.panel.setStyleSheet("""
            QFrame#drawerPanel {
                background: #FFFFFF;
                border-left: 1px solid #E2E8F0;
                border-top-left-radius: 14px;
                border-bottom-left-radius: 14px;
            }
        """)
        self.panel.setFixedWidth(640)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(24, 20, 24, 24)
        panel_layout.setSpacing(16)

        # 1. 顶部 Header
        header_row = QHBoxLayout()
        header_info = QVBoxLayout()
        header_info.setSpacing(4)
        self.title_lbl = QLabel("持仓详情")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: 700; color: #17253D;")
        self.sub_title_lbl = QLabel("查看持仓构成、来源与盈亏情况。")
        self.sub_title_lbl.setStyleSheet("font-size: 12px; color: #64748B;")
        header_info.addWidget(self.title_lbl)
        header_info.addWidget(self.sub_title_lbl)
        header_row.addLayout(header_info)
        header_row.addStretch()

        close_btn = QPushButton("✕", self.panel)
        close_btn.setFixedSize(30, 30)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9;
                border: none;
                border-radius: 15px;
                color: #64748B;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #E2E8F0;
                color: #0F172A;
            }
        """)
        close_btn.clicked.connect(self.close_drawer)
        header_row.addWidget(close_btn)
        panel_layout.addLayout(header_row)

        # 2. KPI 概要卡片区 (4 张小指标卡并排)
        self.kpi_container = QFrame(self.panel)
        self.kpi_container.setStyleSheet("background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px;")
        self.kpi_layout = QGridLayout(self.kpi_container)
        self.kpi_layout.setContentsMargins(10, 8, 10, 8)
        self.kpi_layout.setSpacing(12)
        panel_layout.addWidget(self.kpi_container)

        # 3. 内容滚动区
        self.scroll = QScrollArea(self.panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 8, 0, 8)
        self.content_layout.setSpacing(16)
        self.scroll.setWidget(self.content_widget)
        panel_layout.addWidget(self.scroll, 1)

        main_layout.addWidget(self.panel)

    def close_drawer(self):
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_drawer()
        else:
            super().keyPressEvent(event)

    def open_stock_detail(self, stock_item: AggregatedItem, analytics, summary: DistributionSummary):
        self.mode = self.MODE_STOCK
        self.current_data = stock_item
        self.analytics = analytics

        self.title_lbl.setText(f"{stock_item.name} 持仓详情")
        self.sub_title_lbl.setText("查看单只股票的持仓构成、来源与盈亏情况。")

        # 填充 KPI
        self._clear_layout(self.kpi_layout)
        kpis = [
            ("当前市值", f"${stock_item.market_value:,.2f}", "#17253D"),
            ("当前占比", f"{stock_item.weight:.2f}%", "#2563EB"),
            ("上期占比", f"{stock_item.prev_weight:.2f}%", "#64748B"),
            ("占比变化", f"{stock_item.diff_pp:+.2f}%", "#16A34A" if stock_item.diff_pp >= 0 else "#DC2626"),
        ]
        for col, (lbl, val, col_str) in enumerate(kpis):
            vbox = QVBoxLayout()
            l1 = QLabel(lbl)
            l1.setStyleSheet("font-size: 11px; color: #64748B;")
            l2 = QLabel(val)
            l2.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {col_str};")
            vbox.addWidget(l1)
            vbox.addWidget(l2)
            self.kpi_layout.addLayout(vbox, 0, col)

        # 填充主内容
        self._clear_layout(self.content_layout)

        # Tab 选项卡：持仓来源 / 当前 vs 上月末
        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #E2E8F0; border-radius: 8px; background: #FFFFFF; }
            QTabBar::tab { padding: 6px 14px; font-size: 12px; font-weight: 500; color: #64748B; }
            QTabBar::tab:selected { color: #2563EB; border-bottom: 2px solid #2563EB; font-weight: 700; }
        """)

        # Tab 1: 持仓来源（按人员/渠道拆分）
        source_tab = QWidget()
        source_vbox = QVBoxLayout(source_tab)
        source_vbox.setContentsMargins(12, 12, 12, 12)
        source_vbox.setSpacing(8)

        source_hint = QLabel("持仓明细（按归属人员 / 渠道）：")
        source_hint.setStyleSheet("font-size: 13px; font-weight: 600; color: #17253D;")
        source_vbox.addWidget(source_hint)

        sources = get_stock_holding_sources(analytics, stock_item.name)
        source_table = QTableWidget(len(sources), 7)
        source_table.setHorizontalHeaderLabels(["归属人员", "渠道", "持仓数量", "当前市值", "当前占比", "收益率", "累计盈亏"])
        source_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        source_table.verticalHeader().setVisible(False)
        source_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        source_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        source_table.setStyleSheet("font-size: 12px; border: none; gridline-color: #F1F5F9;")

        for row_idx, src in enumerate(sources):
            person_str = "个人"
            if src.get("person_ids") and src["person_ids"]:
                pid = src["person_ids"][0]
                person_str = f"人员 {pid}" if pid != 1 else "个人"

            sh = src["shares"]
            mv = src["market_value"]
            pw = (mv / stock_item.market_value * Decimal("100")) if stock_item.market_value > 0 else Decimal("0")
            pr = src.get("profit_rate")
            pf = src.get("profit")

            source_table.setItem(row_idx, 0, QTableWidgetItem(person_str))
            source_table.setItem(row_idx, 1, QTableWidgetItem(src["channel"]))
            source_table.setItem(row_idx, 2, QTableWidgetItem(f"{sh:,.2f}"))
            source_table.setItem(row_idx, 3, QTableWidgetItem(f"${mv:,.2f}"))
            source_table.setItem(row_idx, 4, QTableWidgetItem(f"{pw:.2f}%"))

            pr_item = QTableWidgetItem(f"{pr * 100:+.2f}%" if pr is not None else "--")
            if pr is not None:
                pr_item.setForeground(QColor("#16A34A" if pr >= 0 else "#DC2626"))
            source_table.setItem(row_idx, 5, pr_item)

            pf_item = QTableWidgetItem(f"${pf:+,.2f}" if pf is not None else "--")
            if pf is not None:
                pf_item.setForeground(QColor("#16A34A" if pf >= 0 else "#DC2626"))
            source_table.setItem(row_idx, 6, pf_item)

        # 双击下钻
        def on_source_double_click(r, _):
            if r < len(sources):
                meta = sources[r].get("metadata", {})
                self.drill_record_signal.emit(meta)

        source_table.cellDoubleClicked.connect(on_source_double_click)
        source_vbox.addWidget(source_table)

        tip_lbl = QLabel("ⓘ 双击记录或点击下方按钮可进一步穿透至交易明细。")
        tip_lbl.setStyleSheet("font-size: 11px; color: #64748B;")
        source_vbox.addWidget(tip_lbl)

        drill_btn = QPushButton("查看交易明细下钻")
        drill_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        drill_btn.clicked.connect(lambda: self.drill_record_signal.emit({
            "symbol": stock_item.name,
            "instrument_id": sources[0].get("instrument_id") if sources else None,
        }))
        source_vbox.addWidget(drill_btn)

        tabs.addTab(source_tab, "持仓来源")

        # Tab 2: 当前 vs 上期对比
        cmp_tab = QWidget()
        cmp_vbox = QVBoxLayout(cmp_tab)
        cmp_vbox.setContentsMargins(12, 12, 12, 12)
        cmp_vbox.setSpacing(12)

        cmp_table = QTableWidget(4, 3)
        cmp_table.setHorizontalHeaderLabels(["指标", summary.as_of_label, summary.prev_as_of_label])
        cmp_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        cmp_table.verticalHeader().setVisible(False)
        cmp_table.setStyleSheet("font-size: 12px; border: none; gridline-color: #F1F5F9;")

        cmp_rows = [
            ("持仓市值", f"${stock_item.market_value:,.2f}", f"${stock_item.prev_market_value:,.2f}"),
            ("组合占比", f"{stock_item.weight:.2f}%", f"{stock_item.prev_weight:.2f}%"),
            ("持仓股数", f"{stock_item.shares:,.2f}", "--"),
            ("所属分类", stock_item.category, stock_item.category),
        ]
        for idx, (m, c1, c2) in enumerate(cmp_rows):
            cmp_table.setItem(idx, 0, QTableWidgetItem(m))
            cmp_table.setItem(idx, 1, QTableWidgetItem(c1))
            cmp_table.setItem(idx, 2, QTableWidgetItem(c2))

        cmp_vbox.addWidget(cmp_table)
        tabs.addTab(cmp_tab, "当前 vs 对比期")

        self.content_layout.addWidget(tabs)
        self.show()

    def open_broker_detail(self, channel_item: AggregatedItem, summary: DistributionSummary):
        self.mode = self.MODE_BROKER
        self.current_data = channel_item

        self.title_lbl.setText(f"{channel_item.name} 渠道分布详情")
        self.sub_title_lbl.setText("查看各券商渠道的持仓市值、占比及变化情况。")

        # 4 个指标卡
        self._clear_layout(self.kpi_layout)
        kpis = [
            ("渠道数量", f"{len(summary.channels)} 个", "#17253D"),
            ("当前渠道市值", f"${channel_item.market_value:,.2f}", "#2563EB"),
            ("当前占比", f"{channel_item.weight:.2f}%", "#2563EB"),
            ("较上期变化", f"{channel_item.diff_pp:+.2f}%", "#16A34A" if channel_item.diff_pp >= 0 else "#DC2626"),
        ]
        for col, (lbl, val, col_str) in enumerate(kpis):
            vbox = QVBoxLayout()
            l1 = QLabel(lbl)
            l1.setStyleSheet("font-size: 11px; color: #64748B;")
            l2 = QLabel(val)
            l2.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {col_str};")
            vbox.addWidget(l1)
            vbox.addWidget(l2)
            self.kpi_layout.addLayout(vbox, 0, col)

        # 内容区
        self._clear_layout(self.content_layout)

        # 渠道内股票持仓明细
        sub_title = QLabel(f"{channel_item.name} 内持仓股票清单：")
        sub_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #17253D;")
        self.content_layout.addWidget(sub_title)

        ch_holdings = [h for h in summary.raw_holdings if h.channel == channel_item.name]
        ch_table = QTableWidget(len(ch_holdings), 6)
        ch_table.setHorizontalHeaderLabels(["Ticker", "分类", "持仓股数", "现价", "持仓市值", "渠道内占比"])
        ch_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        ch_table.verticalHeader().setVisible(False)
        ch_table.setStyleSheet("font-size: 12px; border: none; gridline-color: #F1F5F9;")

        for idx, h in enumerate(ch_holdings):
            ratio_in_ch = (h.market_value / channel_item.market_value * Decimal("100")) if channel_item.market_value > 0 else Decimal("0")
            ch_table.setItem(idx, 0, QTableWidgetItem(h.ticker))
            ch_table.setItem(idx, 1, QTableWidgetItem(h.category))
            ch_table.setItem(idx, 2, QTableWidgetItem(f"{h.shares:,.2f}"))
            ch_table.setItem(idx, 3, QTableWidgetItem(f"${h.price:,.2f}"))
            ch_table.setItem(idx, 4, QTableWidgetItem(f"${h.market_value:,.2f}"))
            ch_table.setItem(idx, 5, QTableWidgetItem(f"{ratio_in_ch:.2f}%"))

        self.content_layout.addWidget(ch_table)
        self.show()

    def open_category_detail(self, cat_item: AggregatedItem, summary: DistributionSummary):
        self.mode = self.MODE_CATEGORY
        self.current_data = cat_item

        self.title_lbl.setText(f"{cat_item.name} 股票分类详情")
        self.sub_title_lbl.setText("查看各股票分类的持仓市值、占比及变化情况。")

        # 4 个指标卡
        self._clear_layout(self.kpi_layout)
        kpis = [
            ("分类数量", f"{len(summary.categories)} 类", "#17253D"),
            ("当前分类市值", f"${cat_item.market_value:,.2f}", "#2563EB"),
            ("当前占比", f"{cat_item.weight:.2f}%", "#2563EB"),
            ("较上期变化", f"{cat_item.diff_pp:+.2f}%", "#16A34A" if cat_item.diff_pp >= 0 else "#DC2626"),
        ]
        for col, (lbl, val, col_str) in enumerate(kpis):
            vbox = QVBoxLayout()
            l1 = QLabel(lbl)
            l1.setStyleSheet("font-size: 11px; color: #64748B;")
            l2 = QLabel(val)
            l2.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {col_str};")
            vbox.addWidget(l1)
            vbox.addWidget(l2)
            self.kpi_layout.addLayout(vbox, 0, col)

        # 内容区
        self._clear_layout(self.content_layout)

        # 分类内股票明细
        sub_title = QLabel(f"{cat_item.name} 分类内股票持仓：")
        sub_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #17253D;")
        self.content_layout.addWidget(sub_title)

        cat_holdings = [h for h in summary.raw_holdings if h.category == cat_item.name]
        cat_table = QTableWidget(len(cat_holdings), 6)
        cat_table.setHorizontalHeaderLabels(["Ticker", "渠道", "持仓股数", "现价", "持仓市值", "分类内占比"])
        cat_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        cat_table.verticalHeader().setVisible(False)
        cat_table.setStyleSheet("font-size: 12px; border: none; gridline-color: #F1F5F9;")

        for idx, h in enumerate(cat_holdings):
            ratio_in_cat = (h.market_value / cat_item.market_value * Decimal("100")) if cat_item.market_value > 0 else Decimal("0")
            cat_table.setItem(idx, 0, QTableWidgetItem(h.ticker))
            cat_table.setItem(idx, 1, QTableWidgetItem(h.channel))
            cat_table.setItem(idx, 2, QTableWidgetItem(f"{h.shares:,.2f}"))
            cat_table.setItem(idx, 3, QTableWidgetItem(f"${h.price:,.2f}"))
            cat_table.setItem(idx, 4, QTableWidgetItem(f"${h.market_value:,.2f}"))
            cat_table.setItem(idx, 5, QTableWidgetItem(f"{ratio_in_cat:.2f}%"))

        self.content_layout.addWidget(cat_table)
        self.show()

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())


# ----------------------------------------------------------------------
# 主页面：HoldingsDistributionPage
# ----------------------------------------------------------------------

class HoldingsDistributionPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.charts = ()
        self.canvases = []  # 保留兼容 main_window 与自动化测试
        for _ in range(6):
            c = ChartCanvas(card_mode=True)
            c.selected.connect(self.drill)
            self.canvases.append(c)

        self.summary: Optional[DistributionSummary] = None

        # 主网格布局
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 滚动区域（包裹主页面全部内容）
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.page_container = QWidget()
        self.page_layout = QVBoxLayout(self.page_container)
        self.page_layout.setContentsMargins(24, 20, 24, 28)
        self.page_layout.setSpacing(20)

        # 1. 筛选栏（含刷新与导出）
        self._build_filter_bar()

        # 2. 4 张 KPI 卡片
        self._build_kpi_cards()

        # 3. 三个核心分布模块
        self._build_distribution_modules()

        # 4. 底部 3 Tab 汇总表格
        self._build_detail_tabs()

        self.scroll_area.setWidget(self.page_container)
        root_layout.addWidget(self.scroll_area)

        # 6. 右侧滑出抽屉组件（浮动覆盖层）
        self.drawer = DistributionDetailDrawer(self)
        self.drawer.drill_record_signal.connect(self._on_drill_record)
        self.drawer.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 调整抽屉大小覆盖整个主页面
        if hasattr(self, "drawer"):
            self.drawer.setGeometry(0, 0, self.width(), self.height())

    def _on_drill_record(self, meta: dict):
        # 寻找主窗口并穿透
        win = self.window()
        if hasattr(win, "drill_record") and callable(win.drill_record):
            win.drill_record(meta)
        else:
            self.drill.emit("symbol", meta.get("symbol", ""))

    def _build_filter_bar(self):
        filter_card = QFrame()
        filter_card.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5EAF1;
                border-radius: 12px;
                padding: 10px 16px;
            }
        """)
        row = QHBoxLayout(filter_card)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(14)

        # 观察时点
        row.addWidget(QLabel("观察时点:"))
        self.obs_combo = QComboBox()
        self.obs_combo.setMinimumWidth(130)
        self.obs_combo.addItem("当前 / 最新", None)
        self.obs_combo.currentIndexChanged.connect(self._on_obs_month_changed)
        self.month = self.obs_combo  # 兼容旧属性名
        row.addWidget(self.obs_combo)

        # 对比时点
        row.addWidget(QLabel("对比时点:"))
        self.cmp_combo = QComboBox()
        self.cmp_combo.setMinimumWidth(150)
        self.cmp_combo.currentIndexChanged.connect(self.update_distribution)
        row.addWidget(self.cmp_combo)

        # 人员
        row.addWidget(QLabel("人员:"))
        self.person_combo = QComboBox()
        self.person_combo.setMinimumWidth(100)
        self.person_combo.addItem("全部人员", None)
        self.person_combo.currentIndexChanged.connect(self.update_distribution)
        row.addWidget(self.person_combo)

        # 渠道
        row.addWidget(QLabel("渠道:"))
        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(110)
        self.channel_combo.addItem("全部渠道", None)
        self.channel_combo.currentIndexChanged.connect(self.update_distribution)
        row.addWidget(self.channel_combo)

        # 分类
        row.addWidget(QLabel("股票分类:"))
        self.cat_combo = QComboBox()
        self.cat_combo.setMinimumWidth(110)
        self.cat_combo.addItem("全部分类", None)
        self.cat_combo.currentIndexChanged.connect(self.update_distribution)
        row.addWidget(self.cat_combo)

        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索 Ticker 或名称...")
        self.search_input.setFixedWidth(160)
        self.search_input.textChanged.connect(self.update_distribution)
        row.addWidget(self.search_input)

        # 重置按钮
        reset_btn = QPushButton("重置")
        reset_btn.setStyleSheet("""
            QPushButton {
                background: #F1F5F9;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                color: #475569;
            }
            QPushButton:hover { background: #E2E8F0; }
        """)
        reset_btn.clicked.connect(self.reset_filters)
        row.addWidget(reset_btn)

        row.addStretch()

        # 刷新与导出按钮
        refresh_btn = QPushButton("↻ 刷新")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                color: #334155;
            }
            QPushButton:hover { background: #F8FAFC; }
        """)
        refresh_btn.clicked.connect(self.update_distribution)
        row.addWidget(refresh_btn)

        export_btn = QPushButton("⤓ 导出 PNG")
        export_btn.setStyleSheet("""
            QPushButton {
                background: #2563EB;
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
                font-weight: 600;
                color: #FFFFFF;
            }
            QPushButton:hover { background: #1D4ED8; }
        """)
        export_btn.clicked.connect(self.export)
        row.addWidget(export_btn)

        self.page_layout.addWidget(filter_card)

    def _build_kpi_cards(self):
        grid = QGridLayout()
        grid.setSpacing(16)

        self.kpi_mv = DistributionKpiCard("当前股票市值")
        self.kpi_count = DistributionKpiCard("持仓标的数")
        self.kpi_conc = DistributionKpiCard("前五大持仓集中度")

        grid.addWidget(self.kpi_mv, 0, 0)
        grid.addWidget(self.kpi_count, 0, 1)
        grid.addWidget(self.kpi_conc, 0, 2)

        self.page_layout.addLayout(grid)

    def _build_distribution_modules(self):
        grid = QGridLayout()
        grid.setSpacing(16)

        # 模块 1：股票持仓结构 Top 5 + 其他
        self.mod_stock = self._create_card_container("股票持仓结构 Top 5 + 其他", "点击股票查看持仓详情 >", self._open_top_stock_drawer)
        self.stock_bars_layout = QVBoxLayout()
        self.stock_bars_layout.setSpacing(6)
        self.mod_stock.layout().insertLayout(1, self.stock_bars_layout)
        grid.addWidget(self.mod_stock, 0, 0)

        # 模块 2：券商渠道结构
        self.mod_broker = self._create_card_container("券商渠道结构", "点击渠道查看渠道详情 >", self._open_top_broker_drawer)
        self.broker_bars_layout = QVBoxLayout()
        self.broker_bars_layout.setSpacing(6)
        self.mod_broker.layout().insertLayout(1, self.broker_bars_layout)
        grid.addWidget(self.mod_broker, 0, 1)

        # 模块 3：股票分类结构
        self.mod_cat = self._create_card_container("股票分类结构", "点击分类查看分类详情 >", self._open_top_category_drawer)
        self.cat_bars_layout = QVBoxLayout()
        self.cat_bars_layout.setSpacing(6)
        self.mod_cat.layout().insertLayout(1, self.cat_bars_layout)
        grid.addWidget(self.mod_cat, 0, 2)

        self.page_layout.addLayout(grid)

    def _create_card_container(self, title: str, link_text: str, link_callback):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #FFFFFF;
                border: 1px solid #E5EAF1;
                border-radius: 12px;
                padding: 16px;
            }
        """)
        vbox = QVBoxLayout(card)
        vbox.setContentsMargins(16, 16, 16, 14)
        vbox.setSpacing(12)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #17253D;")
        vbox.addWidget(title_lbl)

        vbox.addStretch()

        # 底部链接
        link_btn = QPushButton(f"ⓘ {link_text}")
        link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        link_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #2563EB;
                font-size: 12px;
                text-align: left;
                padding: 0;
            }
            QPushButton:hover { text-decoration: underline; color: #1D4ED8; }
        """)
        link_btn.clicked.connect(link_callback)
        vbox.addWidget(link_btn)

        return card

    def _build_detail_tabs(self):
        tab_card = QFrame()
        tab_card.setObjectName("detailCard")
        tab_card.setStyleSheet("""
            QFrame#detailCard {
                background: #FFFFFF;
                border: 1px solid #E5EAF1;
                border-radius: 12px;
            }
            QTabWidget::pane {
                border: none;
                background: transparent;
            }
            QTabBar {
                background: transparent;
                border: none;
            }
            QTabBar::tab {
                padding: 8px 22px;
                font-size: 13px;
                font-weight: 600;
                color: #64748B;
                background: #F1F5F9;
                border-radius: 6px;
                margin-right: 8px;
            }
            QTabBar::tab:selected {
                background: #2563EB;
                color: #FFFFFF;
            }
        """)
        card_vbox = QVBoxLayout(tab_card)
        card_vbox.setContentsMargins(16, 16, 16, 16)
        card_vbox.setSpacing(12)

        self.detail_tabs = QTabWidget()

        # Tab 1: 股票持仓明细（无编号、无重复名称、包含股数变化与市值变化）
        tab1 = QWidget()
        tab1_vbox = QVBoxLayout(tab1)
        tab1_vbox.setContentsMargins(0, 10, 0, 0)
        self.table_stocks = self._create_modern_table([
            "Ticker", "分类", "渠道", "持仓股数", "股数变化", "当前市值 (USD)", "市值变化", "当前占比", "上期占比", "占比变化"
        ])
        self.table_stocks.cellClicked.connect(self._on_stock_table_clicked)
        self.table_stocks.cellDoubleClicked.connect(self._on_stock_table_double_clicked)
        tab1_vbox.addWidget(self.table_stocks)
        self.detail_tabs.addTab(tab1, "股票持仓明细")

        # Tab 2: 按渠道汇总（无编号，包含市值变化）
        tab2 = QWidget()
        tab2_vbox = QVBoxLayout(tab2)
        tab2_vbox.setContentsMargins(0, 10, 0, 0)
        self.table_channels = self._create_modern_table([
            "渠道", "持仓标的数", "当前市值", "市值变化", "当前占比", "上期市值", "上期占比", "占比变化"
        ])
        self.table_channels.cellClicked.connect(self._on_channel_table_clicked)
        tab2_vbox.addWidget(self.table_channels)
        self.detail_tabs.addTab(tab2, "按渠道汇总")

        # Tab 3: 按分类汇总（无编号，包含市值变化）
        tab3 = QWidget()
        tab3_vbox = QVBoxLayout(tab3)
        tab3_vbox.setContentsMargins(0, 10, 0, 0)
        self.table_categories = self._create_modern_table([
            "分类", "持仓标的数", "当前市值", "市值变化", "当前占比", "上期市值", "上期占比", "占比变化"
        ])
        self.table_categories.cellClicked.connect(self._on_category_table_clicked)
        tab3_vbox.addWidget(self.table_categories)
        self.detail_tabs.addTab(tab3, "按分类汇总")

        card_vbox.addWidget(self.detail_tabs)
        self.page_layout.addWidget(tab_card)

    def _create_modern_table(self, headers: List[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setFixedHeight(44)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setStyleSheet("""
            QTableWidget {
                border: none;
                gridline-color: #F1F5F9;
                font-size: 13px;
                background-color: #FFFFFF;
                alternate-background-color: #F8FAFC;
            }
            QHeaderView {
                background-color: #F8FAFC;
                border: none;
                border-bottom: 1px solid #E2E8F0;
            }
            QHeaderView::section {
                background-color: #F8FAFC;
                color: #475569;
                font-weight: 600;
                font-size: 13px;
                height: 42px;
                min-height: 42px;
                padding: 0px 4px;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                text-align: center;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QTableWidget::item:selected {
                background-color: #EFF6FF;
                color: #1E40AF;
            }
        """)
        table.setMinimumHeight(380)
        return table

    # ------------------------------------------------------------------
    # 状态与数据交互
    # ------------------------------------------------------------------

    def reset_filters(self):
        self.obs_combo.blockSignals(True)
        self.cmp_combo.blockSignals(True)
        self.person_combo.blockSignals(True)
        self.channel_combo.blockSignals(True)
        self.cat_combo.blockSignals(True)
        self.search_input.blockSignals(True)

        self.obs_combo.setCurrentIndex(0)
        self.person_combo.setCurrentIndex(0)
        self.channel_combo.setCurrentIndex(0)
        self.cat_combo.setCurrentIndex(0)
        self.search_input.clear()

        self._populate_compare_combo()

        self.obs_combo.blockSignals(False)
        self.cmp_combo.blockSignals(False)
        self.person_combo.blockSignals(False)
        self.channel_combo.blockSignals(False)
        self.cat_combo.blockSignals(False)
        self.search_input.blockSignals(False)

        self.update_distribution()

    def set_analytics(self, analytics):
        self.analytics = analytics
        self._populate_filter_options()
        self.update_distribution()

    def clear(self):
        self.analytics = None
        self.charts = ()
        self.summary = None
        for c in self.canvases:
            c.chart = None
            c.update()
        self._update_ui_with_summary(None)

    def _populate_filter_options(self):
        if not self.analytics:
            return

        months = get_available_months(self.analytics)

        # 观察时点
        self.obs_combo.blockSignals(True)
        self.obs_combo.clear()
        self.obs_combo.addItem("当前 / 最新", None)
        for m in months:
            self.obs_combo.addItem(f"{m} 月末", m)
        self.obs_combo.setCurrentIndex(0)
        self.obs_combo.blockSignals(False)

        # 对比时点
        self._populate_compare_combo()

        # 渠道与分类
        channels = set()
        categories = set()
        if "当前持仓" in self.analytics.reports:
            for r in self.analytics.reports["当前持仓"].rows:
                if r.values:
                    channels.add(str(r.values[0]))
                    categories.add(str(r.values[1]))
        if "每月持仓收益" in self.analytics.reports:
            for r in self.analytics.reports["每月持仓收益"].rows:
                if r.values and len(r.values) > 2:
                    channels.add(str(r.values[1]))
                    categories.add(str(r.values[2]))

        cn_channels = set()
        for ch in channels:
            cn = to_chinese_channel(ch)
            if cn:
                cn_channels.add(cn)

        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItem("全部渠道", None)
        for ch in sorted(cn_channels):
            self.channel_combo.addItem(ch, ch)
        self.channel_combo.setCurrentIndex(0)
        self.channel_combo.blockSignals(False)

        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        self.cat_combo.addItem("全部分类", None)
        for cat in sorted(categories):
            self.cat_combo.addItem(cat, cat)
        self.cat_combo.setCurrentIndex(0)
        self.cat_combo.blockSignals(False)

    def _on_obs_month_changed(self):
        self._populate_compare_combo()
        self.update_distribution()

    def _populate_compare_combo(self):
        if not self.analytics:
            return
        months = get_available_months(self.analytics)
        sel_obs = self.obs_combo.currentData()

        self.cmp_combo.blockSignals(True)
        self.cmp_combo.clear()

        if not sel_obs:
            # 观察时点为当前/最新，对比时点默认取上月末
            last_month = months[1] if len(months) > 1 else (months[0] if months else "")
            default_label = f"上月末 ({last_month})" if last_month else "无对比"
            self.cmp_combo.addItem(default_label, last_month)
        else:
            try:
                sy, sm = map(int, sel_obs.split("-"))
                prev_cal = f"{sy if sm > 1 else sy - 1:04d}-{sm - 1 if sm > 1 else 12:02d}"
                prev_m = prev_cal if prev_cal in months else (months[months.index(sel_obs) + 1] if months.index(sel_obs) + 1 < len(months) else "")
            except Exception:
                prev_m = months[1] if len(months) > 1 else (months[0] if months else "")
            default_label = f"上月末 ({prev_m})" if prev_m else "无对比"
            self.cmp_combo.addItem(default_label, prev_m)

        # 添加其他各月末选项
        for m in months:
            self.cmp_combo.addItem(f"{m} 月末", m)

        self.cmp_combo.setCurrentIndex(0)
        self.cmp_combo.blockSignals(False)

    def update_distribution(self):
        if not self.analytics:
            self.clear()
            return

        sel_obs = self.obs_combo.currentData()
        sel_cmp = self.cmp_combo.currentData()
        ch_filter = self.channel_combo.currentData()
        cat_filter = self.cat_combo.currentData()
        kw = self.search_input.text().strip()

        self.summary = calculate_distribution(
            self.analytics,
            selected_month=sel_obs,
            compare_month=sel_cmp,
            channel_filter=ch_filter,
            category_filter=cat_filter,
            search_keyword=kw,
        )

        try:
            self.charts = build_charts(self.analytics, selected_month=sel_obs)
            for i in range(min(6, len(self.charts))):
                self.canvases[i].chart = self.charts[i]
                self.canvases[i].update()
        except Exception:
            pass

        self._update_ui_with_summary(self.summary)

    def _update_ui_with_summary(self, summary: Optional[DistributionSummary]):
        if not summary:
            self.kpi_mv.set_content("--", "--")
            self.kpi_count.set_content("--", "--")
            self.kpi_conc.set_content("--", "--")
            self._clear_distribution_bars()
            self.table_stocks.setRowCount(0)
            self.table_channels.setRowCount(0)
            self.table_categories.setRowCount(0)
            return

        # 1. 刷新 3 张核心 KPI 卡片（合并重复信息，每张卡片展示核心数值及较上期变动）
        # 卡片 1：当前股票市值 -> 下方直接展示较上期变化金额与幅度
        chg_sign = "+" if summary.value_change >= 0 else ""
        chg_rate_str = f"({chg_sign}{summary.value_change_rate:.2f}%)" if summary.value_change_rate is not None else ""
        chg_color = "#16A34A" if summary.value_change >= 0 else "#DC2626"
        self.kpi_mv.set_content(
            f"${summary.current_market_value:,.2f}",
            f"{chg_sign}${summary.value_change:,.2f} {chg_rate_str}".strip(),
            chg_color,
        )

        # 卡片 2：持仓标的数 -> 下方展示标的数变动及百分比变动
        cnt_diff = summary.holding_count_change
        cnt_sign = "+" if cnt_diff > 0 else ""
        cnt_color = "#16A34A" if cnt_diff > 0 else ("#DC2626" if cnt_diff < 0 else "#64748B")
        cnt_rate_str = f"({cnt_sign}{summary.holding_count_change_rate:.2f}%)" if summary.holding_count_change_rate is not None else ""
        cnt_sub = f"{cnt_sign}{cnt_diff} 只 {cnt_rate_str}".strip() if cnt_diff != 0 else "0 只 (0.00%)"
        self.kpi_count.set_content(
            f"{summary.holding_count} 只",
            cnt_sub,
            cnt_color,
        )

        # 卡片 3：前五大持仓集中度 -> 下方展示集中度百分点变动及百分比变动
        conc_diff = summary.top5_concentration_diff_pp
        conc_sign = "+" if conc_diff > Decimal("0.001") else ""
        conc_color = "#16A34A" if conc_diff > Decimal("0.001") else ("#DC2626" if conc_diff < Decimal("-0.001") else "#64748B")
        conc_rate_str = f"({conc_sign}{summary.top5_concentration_change_rate:.2f}%)" if summary.top5_concentration_change_rate is not None else ""
        conc_sub = f"{conc_sign}{conc_diff:.2f}% {conc_rate_str}".strip() if abs(conc_diff) >= Decimal("0.001") else "0.00% (0.00%)"
        self.kpi_conc.set_content(
            f"{summary.top5_concentration:.2f}%",
            conc_sub,
            conc_color,
        )

        # 2. 刷新三大分布模块
        self._render_stock_bars(summary.stocks_top)
        self._render_channel_bars(summary.channels)
        self._render_category_bars(summary.categories)

        # 3. 刷新三大 Tab 明细表
        self._render_tables(summary)

    def _clear_distribution_bars(self):
        while self.stock_bars_layout.count():
            item = self.stock_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.broker_bars_layout.count():
            item = self.broker_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.cat_bars_layout.count():
            item = self.cat_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_stock_bars(self, items: List[AggregatedItem]):
        while self.stock_bars_layout.count():
            item = self.stock_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for s in items:
            diff_str = f"{s.diff_pp:+.2f}%"
            bar = HorizontalBarItem(
                name=s.name,
                display_text=s.name,
                value_text=f"{s.weight:.2f}%",
                pct=float(s.weight),
                diff_text=diff_str,
                diff_val=float(s.diff_pp),
                bar_color="#2563EB",
            )
            bar.clicked.connect(self._open_stock_drawer_by_name)
            self.stock_bars_layout.addWidget(bar)

    def _render_channel_bars(self, channels: List[AggregatedItem]):
        while self.broker_bars_layout.count():
            item = self.broker_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for ch in channels:
            diff_str = f"{ch.diff_pp:+.2f}%"
            bar = HorizontalBarItem(
                name=ch.name,
                display_text=ch.name,
                value_text=f"{ch.weight:.2f}%",
                pct=float(ch.weight),
                diff_text=diff_str,
                diff_val=float(ch.diff_pp),
                bar_color="#0D9488",
            )
            bar.clicked.connect(self._open_broker_drawer_by_name)
            self.broker_bars_layout.addWidget(bar)

    def _render_category_bars(self, categories: List[AggregatedItem]):
        while self.cat_bars_layout.count():
            item = self.cat_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for cat in categories:
            diff_str = f"{cat.diff_pp:+.2f}%"
            color_map = {"大盘": "#2563EB", "正股": "#16A34A", "杠杆": "#D97706"}
            col = color_map.get(cat.name, "#8B5CF6")
            bar = HorizontalBarItem(
                name=cat.name,
                display_text=cat.name,
                value_text=f"{cat.weight:.2f}%",
                pct=float(cat.weight),
                diff_text=diff_str,
                diff_val=float(cat.diff_pp),
                bar_color=col,
            )
            bar.clicked.connect(self._open_category_drawer_by_name)
            self.cat_bars_layout.addWidget(bar)

    def _render_tables(self, summary: DistributionSummary):
        def make_center_item(text: str, color: Optional[str] = None, bold: bool = False) -> QTableWidgetItem:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if color:
                item.setForeground(QColor(color))
            if bold:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            return item

        # 1. 股票持仓明细（无编号、无重复名称、包含股数变化与市值变化、百分比统一用 %、全量居中）
        stocks = summary.all_stocks
        self.table_stocks.setRowCount(len(stocks))
        for r, s in enumerate(stocks):
            # 0: Ticker
            self.table_stocks.setItem(r, 0, make_center_item(s.name, bold=True))
            # 1: 分类
            self.table_stocks.setItem(r, 1, make_center_item(s.category))
            # 2: 渠道
            self.table_stocks.setItem(r, 2, make_center_item(s.channel))
            # 3: 持仓股数
            self.table_stocks.setItem(r, 3, make_center_item(f"{s.shares:,.2f}"))

            # 4: 股数变化 (正绿负红居中)
            sh_chg = s.shares_change
            sh_sign = "+" if sh_chg > Decimal("0.001") else ""
            sh_col = "#16A34A" if sh_chg > Decimal("0.001") else ("#DC2626" if sh_chg < Decimal("-0.001") else None)
            self.table_stocks.setItem(r, 4, make_center_item(f"{sh_sign}{sh_chg:,.2f}", sh_col))

            # 5: 当前市值
            self.table_stocks.setItem(r, 5, make_center_item(f"${s.market_value:,.2f}"))

            # 6: 市值变化 (正绿负红居中)
            mv_chg = s.market_value_change
            mv_sign = "+" if mv_chg > Decimal("0.001") else ""
            mv_col = "#16A34A" if mv_chg > Decimal("0.001") else ("#DC2626" if mv_chg < Decimal("-0.001") else None)
            self.table_stocks.setItem(r, 6, make_center_item(f"{mv_sign}${mv_chg:,.2f}", mv_col))

            # 7: 当前占比
            self.table_stocks.setItem(r, 7, make_center_item(f"{s.weight:.2f}%"))
            # 8: 上期占比
            self.table_stocks.setItem(r, 8, make_center_item(f"{s.prev_weight:.2f}%"))

            # 9: 占比变化 (%) (正绿负红居中)
            diff_col = "#16A34A" if s.diff_pp > Decimal("0.001") else ("#DC2626" if s.diff_pp < Decimal("-0.001") else None)
            self.table_stocks.setItem(r, 9, make_center_item(f"{s.diff_pp:+.2f}%", diff_col))

        # 2. 渠道汇总（无编号、包含市值变化、百分比统一用 %、全量居中）
        chs = summary.channels
        self.table_channels.setRowCount(len(chs))
        for r, ch in enumerate(chs):
            # 0: 渠道
            self.table_channels.setItem(r, 0, make_center_item(ch.name, bold=True))
            # 1: 持仓标的数
            self.table_channels.setItem(r, 1, make_center_item(f"{ch.items_count} 只"))
            # 2: 当前市值
            self.table_channels.setItem(r, 2, make_center_item(f"${ch.market_value:,.2f}"))

            # 3: 市值变化 (正绿负红居中)
            ch_mv_chg = ch.market_value_change
            ch_mv_sign = "+" if ch_mv_chg > Decimal("0.001") else ""
            ch_mv_col = "#16A34A" if ch_mv_chg > Decimal("0.001") else ("#DC2626" if ch_mv_chg < Decimal("-0.001") else None)
            self.table_channels.setItem(r, 3, make_center_item(f"{ch_mv_sign}${ch_mv_chg:,.2f}", ch_mv_col))

            # 4: 当前占比
            self.table_channels.setItem(r, 4, make_center_item(f"{ch.weight:.2f}%"))
            # 5: 上期市值
            self.table_channels.setItem(r, 5, make_center_item(f"${ch.prev_market_value:,.2f}"))
            # 6: 上期占比
            self.table_channels.setItem(r, 6, make_center_item(f"{ch.prev_weight:.2f}%"))

            # 7: 占比变化 (%) (正绿负红居中)
            ch_diff_col = "#16A34A" if ch.diff_pp > Decimal("0.001") else ("#DC2626" if ch.diff_pp < Decimal("-0.001") else None)
            self.table_channels.setItem(r, 7, make_center_item(f"{ch.diff_pp:+.2f}%", ch_diff_col))

        # 3. 分类汇总（无编号、包含市值变化、百分比统一用 %、全量居中）
        cats = summary.categories
        self.table_categories.setRowCount(len(cats))
        for r, ct in enumerate(cats):
            # 0: 分类
            self.table_categories.setItem(r, 0, make_center_item(ct.name, bold=True))
            # 1: 持仓标的数
            self.table_categories.setItem(r, 1, make_center_item(f"{ct.items_count} 只"))
            # 2: 当前市值
            self.table_categories.setItem(r, 2, make_center_item(f"${ct.market_value:,.2f}"))

            # 3: 市值变化 (正绿负红居中)
            ct_mv_chg = ct.market_value_change
            ct_mv_sign = "+" if ct_mv_chg > Decimal("0.001") else ""
            ct_mv_col = "#16A34A" if ct_mv_chg > Decimal("0.001") else ("#DC2626" if ct_mv_chg < Decimal("-0.001") else None)
            self.table_categories.setItem(r, 3, make_center_item(f"{ct_mv_sign}${ct_mv_chg:,.2f}", ct_mv_col))

            # 4: 当前占比
            self.table_categories.setItem(r, 4, make_center_item(f"{ct.weight:.2f}%"))
            # 5: 上期市值
            self.table_categories.setItem(r, 5, make_center_item(f"${ct.prev_market_value:,.2f}"))
            # 6: 上期占比
            self.table_categories.setItem(r, 6, make_center_item(f"{ct.prev_weight:.2f}%"))

            # 7: 占比变化 (%) (正绿负红居中)
            ct_diff_col = "#16A34A" if ct.diff_pp > Decimal("0.001") else ("#DC2626" if ct.diff_pp < Decimal("-0.001") else None)
            self.table_categories.setItem(r, 7, make_center_item(f"{ct.diff_pp:+.2f}%", ct_diff_col))

    # ------------------------------------------------------------------
    # 抽屉唤起事件
    # ------------------------------------------------------------------

    def _open_top_stock_drawer(self):
        if self.summary and self.summary.all_stocks:
            self.drawer.open_stock_detail(self.summary.all_stocks[0], self.analytics, self.summary)

    def _open_top_broker_drawer(self):
        if self.summary and self.summary.channels:
            self.drawer.open_broker_detail(self.summary.channels[0], self.summary)

    def _open_top_category_drawer(self):
        if self.summary and self.summary.categories:
            self.drawer.open_category_detail(self.summary.categories[0], self.summary)

    def _open_stock_drawer_by_name(self, ticker: str):
        if not self.summary:
            return
        match = next((s for s in self.summary.all_stocks if s.name.upper() == ticker.upper()), None)
        if match:
            self.drawer.open_stock_detail(match, self.analytics, self.summary)

    def _open_broker_drawer_by_name(self, channel_name: str):
        if not self.summary:
            return
        match = next((c for c in self.summary.channels if c.name == channel_name), None)
        if match:
            self.drawer.open_broker_detail(match, self.summary)

    def _open_category_drawer_by_name(self, cat_name: str):
        if not self.summary:
            return
        match = next((c for c in self.summary.categories if c.name == cat_name), None)
        if match:
            self.drawer.open_category_detail(match, self.summary)

    def _on_stock_table_clicked(self, row, col):
        if self.summary and row < len(self.summary.all_stocks):
            item = self.summary.all_stocks[row]
            self.drawer.open_stock_detail(item, self.analytics, self.summary)

    def _on_stock_table_double_clicked(self, row, col):
        if self.summary and row < len(self.summary.all_stocks):
            ticker = self.summary.all_stocks[row].name
            # 获取该 ticker 的 metadata 穿透
            sources = get_stock_holding_sources(self.analytics, ticker)
            meta = sources[0].get("metadata", {}) if sources else {"symbol": ticker}
            self._on_drill_record(meta)

    def _on_channel_table_clicked(self, row, col):
        if self.summary and row < len(self.summary.channels):
            item = self.summary.channels[row]
            self.drawer.open_broker_detail(item, self.summary)

    def _on_category_table_clicked(self, row, col):
        if self.summary and row < len(self.summary.categories):
            item = self.summary.categories[row]
            self.drawer.open_category_detail(item, self.summary)

    # ------------------------------------------------------------------
    # 导出与兼容接口
    # ------------------------------------------------------------------

    def update_month(self):
        """兼容原有接口"""
        self.update_distribution()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出持仓分布图表", "持仓分布.png", "PNG (*.png)")
        if path:
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not self.page_container.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except Exception:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
