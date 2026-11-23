"""美股「持仓管理 → 汇总」专属看板与图表组件 (Mac Desktop / PySide6)。

与「持有中」「已清仓」保持完全一致的视觉语言和交互风格：
1. SummaryHoldingsKpiBar：顶部 6 张核心统计卡片（复用 HoldingsKpiCard）
   - 累计总盈亏 (USD)
   - 当前浮动盈亏 (USD)
   - 历史已实现盈亏 (USD)
   - 当前持仓总市值 (USD)
   - 交易股票总数 (去重)
   - 全周期股票总胜率
2. SummaryChartsRow：三联分析卡片
   - 左：汇总分布（按券商渠道），点击行联动筛选券商、点击合计恢复
   - 中：状态与盈亏结构环形图（持有中·总盈/亏、已清仓·总盈/亏），点击联动筛选明细
   - 右：股票类型总盈亏分解（大盘/杠杆/正股 浮盈+已实现=总盈亏），点击联动筛选类型
3. SummaryTopContributorsCard：全部股票盈亏贡献 Top N 双向红绿条形图
   - 支持切换「累计总盈亏 / 当前浮动盈亏 / 历史已实现盈亏」
   - 点击任意标的联动明细表与右侧跨渠道详情抽屉
"""
from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from portfolio.ui.open_holdings_dashboard import HoldingsKpiCard
from portfolio.ui.theme import numeric_font


def _fmt_usd(val: float | None, *, signed: bool = False) -> str:
    if val is None:
        return "—"
    v = float(val)
    if abs(v) < 0.005:
        return "0.00"
    if signed:
        sign = "+" if v > 0 else "-"
        return f"{sign}{abs(v):,.2f}"
    return f"{v:,.2f}"


def _fmt_pct(val: float | None, *, signed: bool = False) -> str:
    if val is None:
        return "—"
    pct = float(val) * 100.0
    if abs(pct) < 0.005:
        return "0.00%"
    if signed:
        return f"{pct:+.2f}%"
    return f"{pct:.2f}%"


def _pnl_color(val: float | None) -> str:
    if val is None or abs(float(val)) < 0.005:
        return "#0F172A"
    return "#16A34A" if float(val) > 0 else "#DC2626"


class SummaryHoldingsKpiBar(QWidget):
    """美股「汇总」页面顶部 6 张核心 KPI 卡片栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.card_total = HoldingsKpiCard("累计总盈亏 (USD)", "∑", "当前浮动盈亏 + 历史已实现盈亏（跨渠道全生命周期总账）")
        self.card_float = HoldingsKpiCard("当前浮动盈亏 (USD)", "±", "当前仍在场持仓的未实现浮动盈亏总额（与「持仓中」一致）")
        self.card_real = HoldingsKpiCard("历史已实现盈亏 (USD)", "✓", "已清仓标的的净已实现盈亏总额（与「已清仓」一致）")
        self.card_mv = HoldingsKpiCard("当前持仓总市值 (USD)", "$", "当前持有中股票的总市值（悬停查看持仓总成本）")
        self.card_count = HoldingsKpiCard("交易股票总数 (去重)", "#", "当前筛选范围内历史交易过的去重股票总数（持有中 + 已全清仓）")
        self.card_winrate = HoldingsKpiCard("全周期股票总胜率", "%", "累计总盈亏 > 0 的股票数 ÷ 交易股票总数")

        for c in (
            self.card_total,
            self.card_float,
            self.card_real,
            self.card_mv,
            self.card_count,
            self.card_winrate,
        ):
            layout.addWidget(c, 1)

    def set_cards_data(self, cards: dict[str, Any]):
        total_pnl = cards.get("total_pnl")
        total_rate = cards.get("total_pnl_rate")
        float_pnl = cards.get("floating_pnl")
        float_rate = cards.get("floating_pnl_rate")
        real_pnl = cards.get("realized_pnl")
        mv = cards.get("market_value")
        cost = cards.get("cost_amount")
        cnt = int(cards.get("stock_count") or 0)
        open_cnt = int(cards.get("open_stock_count") or 0)
        closed_cnt = int(cards.get("closed_stock_count") or 0)
        win_rate = cards.get("win_rate")
        win_cnt = int(cards.get("win_count") or 0)
        loss_cnt = int(cards.get("loss_count") or 0)

        def _badge(v):
            if (v or 0) > 0:
                return "#DCFCE7", "#16A34A"
            if (v or 0) < 0:
                return "#FEE2E2", "#DC2626"
            return "#EFF6FF", "#2563EB"

        t_bg, t_fg = _badge(total_pnl)
        self.card_total.set_value(_fmt_usd(total_pnl, signed=True), _pnl_color(total_pnl), badge_bg=t_bg, badge_fg=t_fg)
        self.card_total.setToolTip(
            f"累计总盈亏：{_fmt_usd(total_pnl, signed=True)} USD\n"
            f"综合总收益率：{_fmt_pct(total_rate, signed=True)}\n"
            f"构成：浮动盈亏 {_fmt_usd(float_pnl, signed=True)} + 已实现盈亏 {_fmt_usd(real_pnl, signed=True)}"
        )

        f_bg, f_fg = _badge(float_pnl)
        self.card_float.set_value(_fmt_usd(float_pnl, signed=True), _pnl_color(float_pnl), badge_bg=f_bg, badge_fg=f_fg)
        self.card_float.setToolTip(
            f"当前浮动盈亏：{_fmt_usd(float_pnl, signed=True)} USD\n"
            f"当前持仓收益率：{_fmt_pct(float_rate, signed=True)}"
        )

        r_bg, r_fg = _badge(real_pnl)
        self.card_real.set_value(_fmt_usd(real_pnl, signed=True), _pnl_color(real_pnl), badge_bg=r_bg, badge_fg=r_fg)
        self.card_real.setToolTip(f"历史已实现盈亏：{_fmt_usd(real_pnl, signed=True)} USD（与「已清仓」口径一致）")

        self.card_mv.set_value(_fmt_usd(mv))
        self.card_mv.setToolTip(
            f"当前持仓总市值：{_fmt_usd(mv)} USD\n"
            f"当前持仓总成本：{_fmt_usd(cost)} USD"
        )

        self.card_count.set_value(f"{cnt} 只", "#0F172A", badge_bg="#F1F5F9", badge_fg="#475569")
        self.card_count.setToolTip(f"交易股票总数：{cnt} 只\n持有中：{open_cnt} 只 · 已全清仓：{closed_cnt} 只")

        self.card_winrate.set_value(_fmt_pct(win_rate, signed=False), "#0F172A", badge_bg="#F5F3FF", badge_fg="#7C3AED")
        self.card_winrate.setToolTip(
            f"全周期股票总胜率：{_fmt_pct(win_rate, signed=False)}\n"
            f"累计总盈利：{win_cnt} 只 / 累计总亏损：{loss_cnt} 只"
        )


class SummaryStructureDonut(QWidget):
    """持有中(盈/亏) 与 已清仓(盈/亏) 四象限状态结构环形图，支持点击联动筛选。"""

    structure_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.total_count = 0
        self.active_key = ""
        self.items: list[dict[str, Any]] = []
        self._slice_arcs: list[tuple[str, float, float]] = []
        self._legend_rects: list[tuple[str, QRectF]] = []
        self._center_pt = QPointF(0, 0)
        self._outer_r = 0.0
        self._inner_r = 0.0

    def set_data(self, structure: dict[str, Any], active_key: str = ""):
        self.total_count = int(structure.get("total") or 0)
        self.active_key = active_key or ""
        tot = max(1, self.total_count)
        self.items = [
            {
                "key": "open_win",
                "label": "持有中 · 总盈利",
                "count": int(structure.get("open_win") or 0),
                "pct": int(structure.get("open_win") or 0) / tot,
                "color": "#16A34A",
            },
            {
                "key": "open_loss",
                "label": "持有中 · 总亏损",
                "count": int(structure.get("open_loss") or 0),
                "pct": int(structure.get("open_loss") or 0) / tot,
                "color": "#DC2626",
            },
            {
                "key": "closed_win",
                "label": "已清仓 · 总盈利",
                "count": int(structure.get("closed_win") or 0),
                "pct": int(structure.get("closed_win") or 0) / tot,
                "color": "#2563EB",
            },
            {
                "key": "closed_loss",
                "label": "已清仓 · 总亏损",
                "count": int(structure.get("closed_loss") or 0),
                "pct": int(structure.get("closed_loss") or 0) / tot,
                "color": "#F59E0B",
            },
        ]
        self.update()

    def _hit_key(self, pos: QPointF) -> str | None:
        for key, rect in self._legend_rects:
            if rect.contains(pos):
                return key
        dx = pos.x() - self._center_pt.x()
        dy = pos.y() - self._center_pt.y()
        dist = math.hypot(dx, dy)
        if self._inner_r <= dist <= self._outer_r and self._slice_arcs:
            deg = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
            from_top = (90.0 - deg + 360.0) % 360.0
            for key, s_deg, e_deg in self._slice_arcs:
                if s_deg <= from_top <= e_deg:
                    return key
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_key(event.position())
            if hit:
                self.structure_clicked.emit("" if hit == self.active_key else hit)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        hit = self._hit_key(event.position())
        if hit:
            for it in self.items:
                if it["key"] == hit:
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        f"{it['label']}：{it['count']} 只 ({it['pct'] * 100:.1f}%)\n点击联动筛选明细表",
                        self,
                    )
                    break
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        self._slice_arcs.clear()
        self._legend_rects.clear()

        if self.total_count <= 0:
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "暂无汇总标的")
            return

        donut_area_w = min(w * 0.44, h * 0.92, 160.0)
        outer_r = max(42.0, donut_area_w * 0.44)
        inner_r = outer_r * 0.62
        cx = outer_r + 14.0
        cy = h * 0.5
        self._center_pt = QPointF(cx, cy)
        self._outer_r = outer_r
        self._inner_r = inner_r

        rect = QRectF(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2)
        inner_rect = QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)

        acc_deg = 0.0
        for it in self.items:
            cnt = it["count"]
            if cnt <= 0:
                continue
            span_deg = (cnt / self.total_count) * 360.0
            start_qt = 90.0 - acc_deg
            path = QPainterPath()
            path.arcMoveTo(rect, start_qt)
            path.arcTo(rect, start_qt, -span_deg)
            path.arcTo(inner_rect, start_qt - span_deg, span_deg)
            path.closeSubpath()

            col = QColor(it["color"])
            if self.active_key and self.active_key != it["key"]:
                col.setAlpha(80)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
            painter.setBrush(col)
            painter.drawPath(path)

            self._slice_arcs.append((it["key"], acc_deg, acc_deg + span_deg))
            acc_deg += span_deg

        # 中心数字
        f_num = numeric_font(15, bold=True)
        painter.setFont(f_num)
        painter.setPen(QColor("#0F172A"))
        painter.drawText(QRectF(cx - inner_r, cy - 16, inner_r * 2, 20), Qt.AlignmentFlag.AlignCenter, f"{self.total_count} 只")

        f_sub = QFont()
        f_sub.setPointSize(10)
        painter.setFont(f_sub)
        painter.setPen(QColor("#64748B"))
        painter.drawText(QRectF(cx - inner_r, cy + 4, inner_r * 2, 16), Qt.AlignmentFlag.AlignCenter, "全部标的")

        # 右侧图例
        leg_x = cx + outer_r + 16.0
        leg_w = max(80.0, w - leg_x - 6.0)
        row_h = 30.0
        total_leg_h = len(self.items) * row_h
        start_y = max(10.0, (h - total_leg_h) * 0.5)

        for idx, it in enumerate(self.items):
            ry = start_y + idx * row_h
            lrect = QRectF(leg_x, ry, leg_w, row_h - 4)
            self._legend_rects.append((it["key"], lrect))

            if self.active_key == it["key"]:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#EFF6FF"))
                painter.drawRoundedRect(lrect, 6, 6)

            dot_col = QColor(it["color"])
            if self.active_key and self.active_key != it["key"]:
                dot_col.setAlpha(90)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_col)
            painter.drawEllipse(QPointF(leg_x + 8, ry + (row_h - 4) * 0.5), 4.2, 4.2)

            f_lbl = QFont()
            f_lbl.setPointSize(11)
            f_lbl.setBold(self.active_key == it["key"])
            painter.setFont(f_lbl)
            painter.setPen(QColor("#0F172A") if (not self.active_key or self.active_key == it["key"]) else QColor("#94A3B8"))
            painter.drawText(QRectF(leg_x + 18, ry, leg_w * 0.58, row_h - 4), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, it["label"])

            f_val = numeric_font(11, bold=True)
            painter.setFont(f_val)
            painter.drawText(
                QRectF(leg_x + leg_w * 0.55, ry, leg_w * 0.43, row_h - 4),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{it['count']} 只 ({it['pct'] * 100:.0f}%)",
            )


class _ClickableTypeCard(QFrame):
    """右侧「股票类型总盈亏分解」可点击卡片，避免 QPushButton 嵌套布局在 macOS 下被压缩裁剪。"""

    clicked = Signal(str)

    def __init__(self, name: str, count: int, total_pnl: float | None, floating_pnl: float | None, realized_pnl: float | None, *, selected: bool = False, parent=None):
        super().__init__(parent)
        self._name = name
        self.setObjectName("SummaryTypeItemCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(56)
        self.setStyleSheet(f"""
            QFrame#SummaryTypeItemCard {{
                background: {'#EFF6FF' if selected else '#F8FAFC'};
                border: 1px solid {'#93C5FD' if selected else '#E2E8F0'};
                border-radius: 8px;
            }}
            QFrame#SummaryTypeItemCard:hover {{
                border-color: #93C5FD;
                background: #EFF6FF;
            }}
        """)

        bl = QVBoxLayout(self)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        l_name = QLabel(f"{name} ({count} 只)", self)
        l_name.setStyleSheet("color: #0F172A; font-size: 12px; font-weight: 700; border: none; background: transparent;")
        l_tot = QLabel(f"总盈亏 {_fmt_usd(total_pnl, signed=True)}", self)
        l_tot.setFont(numeric_font(12, bold=True))
        l_tot.setStyleSheet(f"color: {_pnl_color(total_pnl)}; border: none; background: transparent;")
        top_row.addWidget(l_name)
        top_row.addStretch()
        top_row.addWidget(l_tot)
        bl.addLayout(top_row)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        l_fl = QLabel(f"浮动盈亏: <span style='color:{_pnl_color(floating_pnl)};font-weight:600;'>{_fmt_usd(floating_pnl, signed=True)}</span>", self)
        l_fl.setStyleSheet("color: #64748B; font-size: 11px; border: none; background: transparent;")
        l_rl = QLabel(f"已实现: <span style='color:{_pnl_color(realized_pnl)};font-weight:600;'>{_fmt_usd(realized_pnl, signed=True)}</span>", self)
        l_rl.setStyleSheet("color: #64748B; font-size: 11px; border: none; background: transparent;")
        sub_row.addWidget(l_fl)
        sub_row.addStretch()
        sub_row.addWidget(l_rl)
        bl.addLayout(sub_row)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._name)
        super().mousePressEvent(event)


class SummaryChartsRow(QWidget):
    """汇总页中部三联分析卡片：左券商汇总表 + 中状态结构环图 + 右股票类型分解。"""

    broker_clicked = Signal(str)
    structure_clicked = Signal(str)
    industry_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_industry = ""
        self._broker_names: list[str] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # 1. 左卡：券商渠道汇总表
        self.broker_card = self._make_card("汇总分布 (按券商渠道)", "点击行筛选券商，点击合计恢复")
        self.broker_table = QTableWidget(0, 6, self.broker_card)
        self.broker_table.setHorizontalHeaderLabels(["券商渠道", "当前市值", "浮动盈亏", "已实现盈亏", "累计总盈亏", "标的数"])
        self.broker_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.broker_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.broker_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.broker_table.verticalHeader().setVisible(False)
        self.broker_table.setShowGrid(False)
        self.broker_table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.broker_table.setStyleSheet("""
            QTableWidget {
                background: transparent; border: none; font-size: 12px; color: #0F172A;
                selection-background-color: #EFF6FF; selection-color: #0F172A;
            }
            QHeaderView::section {
                background: transparent; color: #64748B; font-size: 11px; font-weight: 600;
                border: none; border-bottom: 1px solid #F1F5F9; padding: 4px 6px;
            }
            QTableWidget::item { padding: 4px 6px; border-bottom: 1px solid #F8FAFC; }
        """)
        hdr = self.broker_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for col in range(1, 6):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.broker_table.cellClicked.connect(self._on_broker_cell_clicked)
        self.broker_card.layout().addWidget(self.broker_table, 1)
        layout.addWidget(self.broker_card, 14)

        # 2. 中卡：状态与盈亏结构环形图
        self.struct_card = self._make_card("状态与盈亏结构", "点击扇区/图例联动筛选")
        self.struct_donut = SummaryStructureDonut(self.struct_card)
        self.struct_donut.structure_clicked.connect(self.structure_clicked.emit)
        self.struct_card.layout().addWidget(self.struct_donut, 1)
        layout.addWidget(self.struct_card, 10)

        # 3. 右卡：股票类型总盈亏分解
        self.type_card = self._make_card("股票类型总盈亏分解", "浮盈 + 已实现 = 总盈亏，点击筛选")
        self.type_list_layout = QVBoxLayout()
        self.type_list_layout.setContentsMargins(0, 2, 0, 0)
        self.type_list_layout.setSpacing(8)
        self.type_card.layout().addLayout(self.type_list_layout, 1)
        layout.addWidget(self.type_card, 11)

        self.setMinimumHeight(255)

    @staticmethod
    def _make_card(title: str, hint: str) -> QFrame:
        card = QFrame()
        card.setObjectName("SummaryChartCard")
        card.setStyleSheet("""
            QFrame#SummaryChartCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
        """)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(14, 12, 14, 12)
        vl.setSpacing(8)

        hl = QHBoxLayout()
        hl.setContentsMargins(0, 0, 0, 0)
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        h_lbl = QLabel(hint)
        h_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; border: none; background: transparent;")
        hl.addWidget(t_lbl)
        hl.addStretch()
        hl.addWidget(h_lbl)
        vl.addLayout(hl)
        return card

    def _on_broker_cell_clicked(self, row: int, _col: int):
        if row < 0 or row >= len(self._broker_names):
            self.broker_clicked.emit("")
            return
        name = self._broker_names[row]
        self.broker_clicked.emit("" if name == "合计" else name)

    def set_data(
        self,
        payload: dict[str, Any],
        *,
        active_broker: str = "",
        active_structure: str = "",
        active_industry: str = "",
    ):
        self._active_industry = active_industry or ""

        # 1. 填充左侧券商渠道汇总表
        b_dist = payload.get("broker_distribution") or {}
        items = list(b_dist.get("items") or [])
        total = b_dist.get("total") or {}
        row_count = len(items) + (1 if items else 0)
        self.broker_table.setRowCount(row_count)
        self._broker_names = []

        f_num = numeric_font(12, bold=False)
        f_bold = numeric_font(12, bold=True)

        def _make_item(text: str, align_right: bool = True, color: str = "#0F172A", bold: bool = False) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setFont(f_bold if bold else f_num)
            it.setForeground(QColor(color))
            it.setTextAlignment(
                (Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft)
                | Qt.AlignmentFlag.AlignVCenter
            )
            return it

        for idx, b in enumerate(items):
            name = str(b.get("name") or "")
            self._broker_names.append(name)
            is_act = bool(active_broker and active_broker.casefold() == name.casefold())
            mv = b.get("market_value")
            fl = b.get("floating_pnl")
            rl = b.get("realized_pnl")
            tp = b.get("total_pnl")
            cnt = int(b.get("stock_count") or 0)

            self.broker_table.setRowHeight(idx, 30)
            self.broker_table.setItem(idx, 0, _make_item(f"{name} ✓" if is_act else name, False, "#2563EB" if is_act else "#0F172A", True))
            self.broker_table.setItem(idx, 1, _make_item(_fmt_usd(mv)))
            self.broker_table.setItem(idx, 2, _make_item(_fmt_usd(fl, signed=True), True, _pnl_color(fl)))
            self.broker_table.setItem(idx, 3, _make_item(_fmt_usd(rl, signed=True), True, _pnl_color(rl)))
            self.broker_table.setItem(idx, 4, _make_item(_fmt_usd(tp, signed=True), True, _pnl_color(tp), True))
            self.broker_table.setItem(idx, 5, _make_item(str(cnt)))
            if is_act:
                for c in range(6):
                    self.broker_table.item(idx, c).setBackground(QColor("#EFF6FF"))

        if items:
            t_idx = len(items)
            self._broker_names.append("合计")
            self.broker_table.setRowHeight(t_idx, 32)
            self.broker_table.setItem(t_idx, 0, _make_item("合计", False, "#0F172A", True))
            self.broker_table.setItem(t_idx, 1, _make_item(_fmt_usd(total.get("market_value")), True, "#0F172A", True))
            self.broker_table.setItem(t_idx, 2, _make_item(_fmt_usd(total.get("floating_pnl"), signed=True), True, _pnl_color(total.get("floating_pnl")), True))
            self.broker_table.setItem(t_idx, 3, _make_item(_fmt_usd(total.get("realized_pnl"), signed=True), True, _pnl_color(total.get("realized_pnl")), True))
            self.broker_table.setItem(t_idx, 4, _make_item(_fmt_usd(total.get("total_pnl"), signed=True), True, _pnl_color(total.get("total_pnl")), True))
            self.broker_table.setItem(t_idx, 5, _make_item(f"{int(total.get('stock_count') or 0)} (去重)", True, "#0F172A", True))

        # 2. 中间状态结构环图
        self.struct_donut.set_data(payload.get("structure") or {}, active_key=active_structure)

        # 3. 右侧股票类型分解卡片列表
        while self.type_list_layout.count():
            child = self.type_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        type_items = list(payload.get("type_breakdown") or [])
        if not type_items:
            empty = QLabel("暂无分类汇总数据")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #94A3B8; font-size: 12px; border: none;")
            self.type_list_layout.addWidget(empty)
        else:
            for ti in type_items[:4]:
                name = str(ti.get("name") or "未分类")
                cnt = int(ti.get("stock_count") or 0)
                fl = ti.get("floating_pnl")
                rl = ti.get("realized_pnl")
                tp = ti.get("total_pnl")
                is_sel = bool(self._active_industry and self._active_industry.casefold() == name.casefold())

                card_item = _ClickableTypeCard(
                    name,
                    cnt,
                    tp,
                    fl,
                    rl,
                    selected=is_sel,
                    parent=self.type_card,
                )
                card_item.clicked.connect(
                    lambda n: self.industry_clicked.emit("" if (self._active_industry.casefold() == n.casefold()) else n)
                )
                self.type_list_layout.addWidget(card_item)
            self.type_list_layout.addStretch()


class _SummaryTopBarsCanvas(QWidget):
    """汇总页双向红绿条形图画布，点击条形发射 ticker_clicked。"""

    ticker_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gains: list[dict[str, Any]] = []
        self.losses: list[dict[str, Any]] = []
        self.active_ticker = ""
        self._hit_rects: list[tuple[str, QRectF, dict[str, Any]]] = []

    def set_data(self, gains: list[dict[str, Any]], losses: list[dict[str, Any]], active_ticker: str = ""):
        self.gains = list(gains or [])
        self.losses = list(losses or [])
        self.active_ticker = (active_ticker or "").strip().upper()
        rows = max(len(self.gains), len(self.losses), 1)
        self.setFixedHeight(max(72, rows * 28 + 12))
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            for tk, rect, _ in self._hit_rects:
                if rect.contains(pos):
                    self.ticker_clicked.emit(tk)
                    break
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        for tk, rect, item in self._hit_rects:
            if rect.contains(pos):
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{tk} ({item.get('status', '')})\n"
                    f"累计总盈亏：{_fmt_usd(item.get('total_pnl'), signed=True)} USD\n"
                    f"浮动盈亏：{_fmt_usd(item.get('floating_pnl'), signed=True)} USD\n"
                    f"已实现盈亏：{_fmt_usd(item.get('realized_pnl'), signed=True)} USD\n"
                    f"点击联动下方明细并查看抽屉",
                    self,
                )
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        self._hit_rects.clear()

        row_count = max(len(self.gains), len(self.losses))
        if row_count == 0:
            painter.setPen(QColor("#94A3B8"))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "暂无盈亏排行数据")
            return

        max_abs = max(
            [abs(float(x.get("pnl") or 0)) for x in (*self.gains, *self.losses)] or [1.0]
        )
        if max_abs <= 0:
            max_abs = 1.0

        label_w = 210.0
        gap = 12.0
        mid_x = w * 0.5
        bar_max_w = max(40.0, mid_x - label_w - gap * 1.5)
        row_h = 28.0
        bar_h = 15.0

        f_lbl = QFont()
        f_lbl.setPointSize(11)
        f_num = numeric_font(11, bold=True)

        def _status_tag(item: dict[str, Any]) -> str:
            is_cross = bool(item.get("is_cross_channel"))
            is_closed = item.get("status") == "已全清仓"
            if is_cross and is_closed:
                return " (跨/清)"
            if is_cross:
                return " (跨/持)"
            if is_closed:
                return " (清)"
            return ""

        # 中轴线
        painter.setPen(QPen(QColor("#E2E8F0"), 1.0))
        painter.drawLine(QPointF(mid_x, 4), QPointF(mid_x, row_count * row_h + 2))

        for i in range(row_count):
            y = 4.0 + i * row_h
            by = y + (row_h - bar_h) * 0.5

            # 左侧亏损
            if i < len(self.losses):
                item = self.losses[i]
                tk = str(item.get("ticker") or "")
                val = float(item.get("pnl") or 0.0)
                is_sel = bool(self.active_ticker and self.active_ticker == tk.upper())
                bw = max(8.0, (abs(val) / max_abs) * bar_max_w)
                bar_rect = QRectF(mid_x - 4.0 - bw, by, bw, bar_h)
                full_rect = QRectF(4.0, y, mid_x - 8.0, row_h)
                self._hit_rects.append((tk, full_rect, item))

                if is_sel:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor("#FEF2F2"))
                    painter.drawRoundedRect(full_rect, 4, 4)

                tag = _status_tag(item)
                painter.setFont(f_lbl)
                painter.setPen(QColor("#0F172A"))
                painter.drawText(QRectF(8.0, y, 122.0, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{i + 1}. {tk}{tag}")

                painter.setFont(f_num)
                painter.setPen(QColor("#DC2626"))
                painter.drawText(QRectF(128.0, y, label_w - 130.0, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, _fmt_usd(val, signed=True))

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#EF4444"))
                painter.drawRoundedRect(bar_rect, 3, 3)

            # 右侧盈利
            if i < len(self.gains):
                item = self.gains[i]
                tk = str(item.get("ticker") or "")
                val = float(item.get("pnl") or 0.0)
                is_sel = bool(self.active_ticker and self.active_ticker == tk.upper())
                bw = max(8.0, (abs(val) / max_abs) * bar_max_w)
                bar_rect = QRectF(mid_x + 4.0, by, bw, bar_h)
                full_rect = QRectF(mid_x + 4.0, y, mid_x - 8.0, row_h)
                self._hit_rects.append((tk, full_rect, item))

                if is_sel:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor("#F0FDF4"))
                    painter.drawRoundedRect(full_rect, 4, 4)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#10B981"))
                painter.drawRoundedRect(bar_rect, 3, 3)

                tag = _status_tag(item)
                rx = w - label_w
                painter.setFont(f_lbl)
                painter.setPen(QColor("#0F172A"))
                painter.drawText(QRectF(rx, y, 122.0, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{i + 1}. {tk}{tag}")

                painter.setFont(f_num)
                painter.setPen(QColor("#16A34A"))
                painter.drawText(QRectF(rx + 120.0, y, label_w - 128.0, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, _fmt_usd(val, signed=True))


class SummaryTopContributorsCard(QFrame):
    """汇总页全部股票盈亏贡献 Top N 卡片，支持累计总盈亏 / 浮动盈亏 / 已实现盈亏切换。"""

    ticker_clicked = Signal(str)
    metric_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SummaryTopCard")
        self.setStyleSheet("""
            QFrame#SummaryTopCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
        """)
        self._metric = "total"
        self._top_n = 5
        self._last_data: dict[str, Any] = {}
        self._active_ticker = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        title_lbl = QLabel("全部股票盈亏贡献 Top")
        title_lbl.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        head.addWidget(title_lbl)

        self.top_spin = QSpinBox(self)
        self.top_spin.setRange(3, 15)
        self.top_spin.setValue(5)
        self.top_spin.setFixedWidth(52)
        self.top_spin.setStyleSheet("QSpinBox { background: #F8FAFC; border: 1px solid #CBD5E1; border-radius: 6px; padding: 2px 6px; font-size: 12px; }")
        self.top_spin.valueChanged.connect(self._on_top_n_changed)
        head.addWidget(self.top_spin)

        hint_lbl = QLabel("按单只股票跨渠道合并盈亏排序，可点击联动下方明细与右侧抽屉")
        hint_lbl.setStyleSheet("color: #94A3B8; font-size: 11px; border: none; background: transparent;")
        head.addWidget(hint_lbl)
        head.addStretch()

        self._metric_btns: dict[str, QPushButton] = {}
        for key, lbl in (("total", "累计总盈亏"), ("floating", "当前浮动盈亏"), ("realized", "历史已实现盈亏")):
            btn = QPushButton(lbl, self)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self._on_metric_clicked(k))
            self._metric_btns[key] = btn
            head.addWidget(btn)
        self._update_metric_styles()
        layout.addLayout(head)

        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        self.loss_badge = QLabel("亏损贡献")
        self.loss_badge.setStyleSheet(
            "background: #FEF2F2; color: #DC2626; border: 1px solid #FECACA; "
            "border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 700;"
        )
        self.win_badge = QLabel("盈利贡献")
        self.win_badge.setStyleSheet(
            "background: #F0FDF4; color: #16A34A; border: 1px solid #BBF7D0; "
            "border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 700;"
        )
        badges.addWidget(self.loss_badge)
        badges.addStretch()
        badges.addWidget(self.win_badge)
        layout.addLayout(badges)

        self.canvas = _SummaryTopBarsCanvas(self)
        self.canvas.ticker_clicked.connect(self.ticker_clicked.emit)
        layout.addWidget(self.canvas)

    def _update_metric_styles(self):
        for key, btn in self._metric_btns.items():
            active = key == self._metric
            if active:
                btn.setStyleSheet(
                    "QPushButton { background: #2563EB; color: #FFFFFF; border: 1px solid #2563EB; "
                    "border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 700; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #F8FAFC; color: #475569; border: 1px solid #CBD5E1; "
                    "border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 600; }"
                    "QPushButton:hover { background: #EFF6FF; color: #2563EB; border-color: #93C5FD; }"
                )

    def _on_metric_clicked(self, key: str):
        if key == self._metric:
            return
        self._metric = key
        self._update_metric_styles()
        self.metric_changed.emit(key)

    def _on_top_n_changed(self, val: int):
        self._top_n = max(3, int(val))
        self._rerender()

    def set_data(self, data: dict[str, Any], active_ticker: str = ""):
        self._last_data = data or {}
        self._active_ticker = active_ticker or ""
        m = str(self._last_data.get("metric") or self._metric)
        if m != self._metric and m in self._metric_btns:
            self._metric = m
            self._update_metric_styles()
        self._rerender()

    def _rerender(self):
        gains = list(self._last_data.get("gains") or [])[: self._top_n]
        losses = list(self._last_data.get("losses") or [])[: self._top_n]
        tot_loss = self._last_data.get("total_loss")
        tot_gain = self._last_data.get("total_gain")
        self.loss_badge.setText(f"亏损贡献 (Top {self._top_n}) ｜ 当前筛选全部亏损合计 {_fmt_usd(tot_loss, signed=True)} USD")
        self.win_badge.setText(f"盈利贡献 (Top {self._top_n}) ｜ 当前筛选全部盈利合计 {_fmt_usd(tot_gain, signed=True)} USD")
        self.canvas.set_data(gains, losses, active_ticker=self._active_ticker)
