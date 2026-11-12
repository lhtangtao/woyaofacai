"""美股「持仓管理 → 持有中」专属看板与图表组件 (Mac Desktop / PySide6)。

包含：
1. OpenHoldingsKpiBar：顶部 6 张核心统计卡片
   - 持仓总市值（USD）
   - 持仓总成本（USD）
   - 浮动盈亏（USD）
   - 持仓收益率
   - 持仓股票数量
   - 前五大持仓集中度
2. OpenDistributionDonut：交互式环形分布图（复用为「券商持仓分布」与「行业持仓分布」）
   - 中心展示当前筛选范围总市值
   - 右侧图例展示：名称、持仓占比、持仓市值、股票数量（X 只）
   - 点击扇区或图例行联动筛选
3. OpenMvTrendChart：真实历史持仓市值趋势面积折线图
   - 支持切换：近1月 / 近3月 / 近6月 / 今年 / 全部
   - 若时间段内存在缺失快照的交易日，显式展示数据不完整提示，绝不补造数据
"""
from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from portfolio.ui.theme import numeric_font

DONUT_PALETTE = [
    "#2563EB",
    "#8B5CF6",
    "#10B981",
    "#F59E0B",
    "#06B6D4",
    "#EC4899",
    "#6366F1",
    "#94A3B8",
]


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


class OpenKpiCard(QFrame):
    """持有中页面顶部 6 张统计卡片之一，现代简约金融风格。"""

    def __init__(self, title: str, icon_text: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("openKpiCard")
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet("""
            QFrame#openKpiCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#openKpiCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(60)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(10)

        self.icon_badge = QLabel(icon_text)
        self.icon_badge.setFixedSize(38, 38)
        self.icon_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_badge.setStyleSheet("""
            background: #EFF6FF;
            color: #2563EB;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 700;
        """)
        outer.addWidget(self.icon_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        text_col.addWidget(self.title_lbl)

        self.val_lbl = QLabel("—")
        self.val_lbl.setFont(numeric_font(15, bold=True))
        self.val_lbl.setStyleSheet("color: #0F172A;")
        text_col.addWidget(self.val_lbl)

        outer.addLayout(text_col, 1)
        outer.setAlignment(text_col, Qt.AlignmentFlag.AlignVCenter)

    def set_value(self, text: str, color: str = "#0F172A", badge_bg: str = "#EFF6FF", badge_fg: str = "#2563EB"):
        self.val_lbl.setText(text)
        self.val_lbl.setStyleSheet(f"color: {color};")
        self.icon_badge.setStyleSheet(
            f"background: {badge_bg}; color: {badge_fg}; border-radius: 8px; font-size: 14px; font-weight: 700;"
        )


class OpenHoldingsKpiBar(QWidget):
    """美股持有中页面顶部六张核心统计卡片栏。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.card_mv = OpenKpiCard("持仓总市值 (USD)", "$", "当前筛选范围内持有中股票的总市值")
        self.card_cost = OpenKpiCard("持仓总成本 (USD)", "∑", "当前筛选范围内持有中股票的总持仓成本")
        self.card_pnl = OpenKpiCard("浮动盈亏 (USD)", "±", "当前筛选范围内持有中股票的未实现浮动盈亏总额")
        self.card_rate = OpenKpiCard("持仓收益率", "%", "浮动盈亏 ÷ 持仓总成本 × 100%")
        self.card_count = OpenKpiCard("持仓股票数量", "#", "当前筛选范围内持有中的股票记录数量")
        self.card_top5 = OpenKpiCard("前五大持仓集中度", "◔", "市值前五名持仓之和 ÷ 持仓总市值 × 100%")

        for c in (
            self.card_mv,
            self.card_cost,
            self.card_pnl,
            self.card_rate,
            self.card_count,
            self.card_top5,
        ):
            layout.addWidget(c, 1)

    def set_cards_data(self, cards: dict[str, Any]):
        mv = cards.get("total_market_value")
        cost = cards.get("total_cost_amount")
        pnl = cards.get("floating_pnl")
        rate = cards.get("return_rate")
        cnt = int(cards.get("stock_count") or 0)
        top5 = cards.get("top5_concentration")

        self.card_mv.set_value(_fmt_usd(mv))
        self.card_cost.set_value(_fmt_usd(cost))

        pnl_col = _pnl_color(pnl)
        pnl_bg = "#DCFCE7" if (pnl or 0) > 0 else "#FEE2E2" if (pnl or 0) < 0 else "#EFF6FF"
        pnl_fg = "#16A34A" if (pnl or 0) > 0 else "#DC2626" if (pnl or 0) < 0 else "#2563EB"
        self.card_pnl.set_value(_fmt_usd(pnl, signed=True), pnl_col, badge_bg=pnl_bg, badge_fg=pnl_fg)

        rate_col = _pnl_color(rate)
        self.card_rate.set_value(_fmt_pct(rate, signed=True), rate_col, badge_bg=pnl_bg, badge_fg=pnl_fg)

        self.card_count.set_value(f"{cnt} 只")
        self.card_top5.set_value(_fmt_pct(top5, signed=False))


class OpenDistributionDonut(QWidget):
    """支持点击联动的环形图（用于券商持仓分布和行业持仓分布）。"""

    item_clicked = Signal(str)

    def __init__(self, empty_text: str = "暂无持仓分布数据", parent=None):
        super().__init__(parent)
        self.empty_text = empty_text
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.total_mv = 0.0
        self.items: list[dict[str, Any]] = []
        self.active_key: str = ""
        self._slice_arcs: list[tuple[str, float, float]] = []
        self._legend_rects: list[tuple[str, QRectF]] = []
        self._center_pt = QPointF(0, 0)
        self._outer_r = 0.0
        self._inner_r = 0.0

    def set_data(self, items: list[dict[str, Any]], total_mv: float, active_key: str = ""):
        self.total_mv = float(total_mv or 0.0)
        self.active_key = (active_key or "").strip()
        self.items = []
        for idx, it in enumerate(items or []):
            name = str(it.get("name") or it.get("channel") or it.get("industry") or "")
            mv = float(it.get("market_value") or 0.0)
            pct = float(it.get("percent") or 0.0)
            cnt = int(it.get("stock_count") or 0)
            if mv <= 0 and cnt <= 0:
                continue
            self.items.append({
                "key": name,
                "name": name,
                "market_value": mv,
                "percent": pct,
                "stock_count": cnt,
                "color": DONUT_PALETTE[idx % len(DONUT_PALETTE)],
            })
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
            for key, start_deg, span_deg in self._slice_arcs:
                # start_deg 为逆时针起始角，顺时针跨度为 span_deg
                offset = (start_deg - deg + 360.0) % 360.0
                if offset <= span_deg:
                    return key
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._hit_key(event.position())
            if key:
                is_same = self.active_key.casefold() == key.casefold()
                self.item_clicked.emit("" if is_same else key)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        key = self._hit_key(event.position())
        if key:
            item = next((x for x in self.items if x["key"] == key), None)
            if item:
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{item['name']}\n持仓市值：{_fmt_usd(item['market_value'])}\n持仓占比：{item['percent']:.2f}%\n持仓数量：{item['stock_count']} 只\n（点击联动筛选列表）",
                    self,
                )
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        self._slice_arcs.clear()
        self._legend_rects.clear()

        if not self.items or self.total_mv <= 0:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.empty_text)
            return

        donut_d = min(max(120, h - 24), int(w * 0.38))
        cx = 12.0 + donut_d / 2.0
        cy = h / 2.0
        self._center_pt = QPointF(cx, cy)
        self._outer_r = donut_d / 2.0
        self._inner_r = self._outer_r * 0.62

        outer_rect = QRectF(cx - self._outer_r, cy - self._outer_r, donut_d, donut_d)
        inner_rect = QRectF(cx - self._inner_r, cy - self._inner_r, self._inner_r * 2, self._inner_r * 2)

        start_angle = 90.0
        for item in self.items:
            mv = item["market_value"]
            if mv <= 0:
                continue
            span = (mv / self.total_mv) * 360.0
            path = QPainterPath()
            path.arcMoveTo(outer_rect, start_angle)
            path.arcTo(outer_rect, start_angle, -span)
            path.arcTo(inner_rect, start_angle - span, span)
            path.closeSubpath()

            col = QColor(item["color"])
            if self.active_key and self.active_key.casefold() != item["key"].casefold():
                col.setAlpha(75)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.8))
            painter.setBrush(col)
            painter.drawPath(path)

            self._slice_arcs.append((item["key"], start_angle % 360.0, span))
            start_angle -= span

        # 中心总市值文字
        painter.setPen(QColor("#0F172A"))
        painter.setFont(numeric_font(12, bold=True))
        center_mv_str = _fmt_usd(self.total_mv)
        painter.drawText(
            QRectF(cx - self._inner_r + 4, cy - 16, (self._inner_r - 4) * 2, 18),
            Qt.AlignmentFlag.AlignCenter,
            center_mv_str,
        )
        painter.setPen(QColor("#64748B"))
        painter.setFont(QFont(".AppleSystemUIFont", 10))
        painter.drawText(
            QRectF(cx - self._inner_r + 4, cy + 2, (self._inner_r - 4) * 2, 16),
            Qt.AlignmentFlag.AlignCenter,
            "持仓市值",
        )

        # 右侧图例列表（名称 | 占比 | 市值 | X 只）
        legend_x = outer_rect.right() + 14.0
        legend_w = max(120.0, w - legend_x - 6.0)
        row_h = min(28.0, max(20.0, (h - 16.0) / max(1, len(self.items))))
        total_h = row_h * len(self.items)
        start_y = max(8.0, (h - total_h) / 2.0)

        for idx, item in enumerate(self.items):
            ry = start_y + idx * row_h
            row_rect = QRectF(legend_x - 4, ry, legend_w + 4, row_h)
            self._legend_rects.append((item["key"], row_rect))

            is_selected = bool(self.active_key and self.active_key.casefold() == item["key"].casefold())
            if is_selected:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#EFF6FF"))
                painter.drawRoundedRect(row_rect, 5, 5)

            # 颜色圆点
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(item["color"]))
            painter.drawEllipse(QRectF(legend_x, ry + (row_h - 8) / 2.0, 8, 8))

            # 名称
            painter.setPen(QColor("#2563EB" if is_selected else "#0F172A"))
            painter.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold if is_selected else QFont.Weight.Medium))
            name_w = legend_w * 0.27
            elided = painter.fontMetrics().elidedText(item["name"], Qt.TextElideMode.ElideRight, int(name_w))
            painter.drawText(
                QRectF(legend_x + 12, ry, name_w, row_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                elided,
            )

            # 占比
            painter.setFont(numeric_font(11, bold=True))
            painter.setPen(QColor("#334155"))
            pct_w = legend_w * 0.20
            painter.drawText(
                QRectF(legend_x + 12 + name_w, ry, pct_w, row_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{item['percent']:.1f}%",
            )

            # 市值
            painter.setFont(numeric_font(11))
            painter.setPen(QColor("#0F172A"))
            mv_w = legend_w * 0.34
            painter.drawText(
                QRectF(legend_x + 12 + name_w + pct_w, ry, mv_w, row_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                _fmt_usd(item["market_value"]),
            )

            # 股票数量 (X 只)
            painter.setFont(QFont(".AppleSystemUIFont", 10))
            painter.setPen(QColor("#64748B"))
            cnt_x = legend_x + 12 + name_w + pct_w + mv_w
            cnt_w = max(28.0, legend_w - (cnt_x - legend_x))
            painter.drawText(
                QRectF(cnt_x, ry, cnt_w, row_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                f"{item['stock_count']} 只",
            )


class OpenMvTrendChart(QWidget):
    """真实历史持仓市值趋势面积折线图组件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self.points: list[dict[str, Any]] = []
        self._hover_idx: int = -1
        self._plot_pts: list[QPointF] = []

    def set_data(self, points: list[dict[str, Any]]):
        self.points = list(points or [])
        self._hover_idx = -1
        self.update()

    def mouseMoveEvent(self, event):
        if not self._plot_pts:
            QToolTip.hideText()
            return
        mx = event.position().x()
        best_idx = min(range(len(self._plot_pts)), key=lambda i: abs(self._plot_pts[i].x() - mx))
        if best_idx != self._hover_idx:
            self._hover_idx = best_idx
            self.update()
        pt_data = self.points[best_idx]
        mv = float(pt_data.get("market_value") or 0.0)
        inc_tag = "\n（当日部分快照待补齐）" if pt_data.get("incomplete") else ""
        QToolTip.showText(
            event.globalPosition().toPoint(),
            f"日期：{pt_data.get('date')}\n持仓市值：{_fmt_usd(mv)}{inc_tag}",
            self,
        )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    @staticmethod
    def _fmt_axis_val(v: float) -> str:
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if abs(v) >= 1_000:
            return f"{v / 1_000:.0f}K"
        return f"{v:.0f}"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        self._plot_pts.clear()

        if not self.points:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "当前时间范围暂无真实历史市值快照")
            return

        left_pad = 46.0
        right_pad = 14.0
        top_pad = 14.0
        bottom_pad = 26.0
        plot_w = max(40.0, w - left_pad - right_pad)
        plot_h = max(40.0, h - top_pad - bottom_pad)

        vals = [float(p.get("market_value") or 0.0) for p in self.points]
        v_min = min(vals)
        v_max = max(vals)
        if abs(v_max - v_min) < 1e-6:
            v_min = max(0.0, v_min * 0.9)
            v_max = v_max * 1.1 + 100.0
        else:
            pad_v = (v_max - v_min) * 0.12
            v_min = max(0.0, v_min - pad_v)
            v_max = v_max + pad_v

        # 1. 绘制水平网格线与 Y 轴刻度
        grid_steps = 4
        painter.setFont(numeric_font(9))
        for i in range(grid_steps + 1):
            ratio_y = i / grid_steps
            y = top_pad + plot_h * (1.0 - ratio_y)
            val_y = v_min + (v_max - v_min) * ratio_y

            painter.setPen(QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(left_pad, y), QPointF(left_pad + plot_w, y))

            painter.setPen(QColor("#64748B"))
            painter.drawText(
                QRectF(0, y - 8, left_pad - 6, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._fmt_axis_val(val_y),
            )

        # 2. 计算坐标点
        n = len(self.points)
        for idx, v in enumerate(vals):
            x = left_pad + (plot_w * idx / (n - 1) if n > 1 else plot_w / 2.0)
            y = top_pad + plot_h * (1.0 - (v - v_min) / (v_max - v_min))
            self._plot_pts.append(QPointF(x, y))

        # 3. 渐变填充面积与折线
        line_path = QPainterPath()
        line_path.moveTo(self._plot_pts[0])
        for pt in self._plot_pts[1:]:
            line_path.lineTo(pt)

        area_path = QPainterPath(line_path)
        area_path.lineTo(QPointF(self._plot_pts[-1].x(), top_pad + plot_h))
        area_path.lineTo(QPointF(self._plot_pts[0].x(), top_pad + plot_h))
        area_path.closeSubpath()

        grad = QLinearGradient(0, top_pad, 0, top_pad + plot_h)
        grad.setColorAt(0.0, QColor(37, 99, 235, 68))
        grad.setColorAt(1.0, QColor(37, 99, 235, 4))
        painter.fillPath(area_path, grad)

        painter.setPen(QPen(QColor("#2563EB"), 2.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line_path)

        # 最新点高亮圆点
        last_pt = self._plot_pts[-1]
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.setBrush(QColor("#2563EB"))
        painter.drawEllipse(last_pt, 4.5, 4.5)

        # 悬停十字准星与圆点
        if 0 <= self._hover_idx < len(self._plot_pts):
            h_pt = self._plot_pts[self._hover_idx]
            painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(h_pt.x(), top_pad), QPointF(h_pt.x(), top_pad + plot_h))
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor("#1D4ED8"))
            painter.drawEllipse(h_pt, 5.0, 5.0)

        # 4. X 轴日期刻度
        painter.setPen(QColor("#64748B"))
        painter.setFont(numeric_font(9))
        tick_count = min(6, n)
        if tick_count == 1:
            tick_indices = [0]
        else:
            tick_indices = sorted({int(round(i * (n - 1) / (tick_count - 1))) for i in range(tick_count)})

        for ti in tick_indices:
            pt = self._plot_pts[ti]
            d_str = str(self.points[ti].get("date") or "")
            short_d = d_str[5:] if len(d_str) >= 10 else d_str
            painter.drawText(
                QRectF(pt.x() - 26, top_pad + plot_h + 4, 52, 18),
                Qt.AlignmentFlag.AlignCenter,
                short_d,
            )


class OpenHoldingsChartsRow(QWidget):
    """持有中页面中部三列并排图表区（券商持仓分布 | 行业持仓分布 | 持仓市值趋势）。"""

    broker_clicked = Signal(str)
    industry_clicked = Signal(str)
    trend_window_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        card_qss = """
            QFrame#openChartCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#openChartCard QLabel {
                border: none;
                background: transparent;
            }
        """

        # 1. 持仓分布（按券商渠道）
        self.broker_card = QFrame()
        self.broker_card.setObjectName("openChartCard")
        self.broker_card.setStyleSheet(card_qss)
        b_layout = QVBoxLayout(self.broker_card)
        b_layout.setContentsMargins(14, 12, 14, 12)
        b_layout.setSpacing(6)

        b_hdr = QHBoxLayout()
        b_title = QLabel("持仓分布 (按券商渠道)")
        b_title.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700;")
        b_hdr.addWidget(b_title)
        b_hdr.addStretch()
        b_hint = QLabel("点击联动筛选")
        b_hint.setStyleSheet("color: #94A3B8; font-size: 11px;")
        b_hdr.addWidget(b_hint)
        b_layout.addLayout(b_hdr)

        self.broker_donut = OpenDistributionDonut("暂无券商持仓数据", self.broker_card)
        self.broker_donut.item_clicked.connect(self.broker_clicked.emit)
        b_layout.addWidget(self.broker_donut, 1)
        layout.addWidget(self.broker_card, 1)

        # 2. 持仓分布（按类型）
        self.industry_card = QFrame()
        self.industry_card.setObjectName("openChartCard")
        self.industry_card.setStyleSheet(card_qss)
        i_layout = QVBoxLayout(self.industry_card)
        i_layout.setContentsMargins(14, 12, 14, 12)
        i_layout.setSpacing(6)

        i_hdr = QHBoxLayout()
        i_title = QLabel("持仓分布 (按类型)")
        i_title.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700;")
        i_hdr.addWidget(i_title)
        i_hdr.addStretch()
        i_hint = QLabel("点击联动筛选")
        i_hint.setStyleSheet("color: #94A3B8; font-size: 11px;")
        i_hdr.addWidget(i_hint)
        i_layout.addLayout(i_hdr)

        self.industry_donut = OpenDistributionDonut("暂无类型持仓数据", self.industry_card)
        self.industry_donut.item_clicked.connect(self.industry_clicked.emit)
        i_layout.addWidget(self.industry_donut, 1)
        layout.addWidget(self.industry_card, 1)

        # 3. 持仓市值趋势
        self.trend_card = QFrame()
        self.trend_card.setObjectName("openChartCard")
        self.trend_card.setStyleSheet(card_qss)
        t_layout = QVBoxLayout(self.trend_card)
        t_layout.setContentsMargins(14, 12, 14, 12)
        t_layout.setSpacing(6)

        t_hdr = QHBoxLayout()
        t_hdr.setSpacing(4)
        t_title = QLabel("持仓市值趋势")
        t_title.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700;")
        t_hdr.addWidget(t_title)
        t_hdr.addStretch()

        self.window_buttons: dict[str, QPushButton] = {}
        for label, key in (
            ("近1月", "1m"),
            ("近3月", "3m"),
            ("近6月", "6m"),
            ("今年", "ytd"),
            ("全部", "all"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #F1F5F9;
                    color: #64748B;
                    border: none;
                    border-radius: 5px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 500;
                }
                QPushButton:checked {
                    background: #2563EB;
                    color: #FFFFFF;
                    font-weight: 700;
                }
                QPushButton:hover:!checked {
                    background: #E2E8F0;
                    color: #0F172A;
                }
            """)
            btn.clicked.connect(lambda _=False, k=key: self._on_window_clicked(k))
            self.window_buttons[key] = btn
            t_hdr.addWidget(btn)
        t_layout.addLayout(t_hdr)

        self.incomplete_lbl = QLabel("")
        self.incomplete_lbl.setStyleSheet(
            "color: #B45309; background: #FEF3C7; border-radius: 4px; padding: 2px 6px; font-size: 10px;"
        )
        self.incomplete_lbl.hide()
        t_layout.addWidget(self.incomplete_lbl)

        self.trend_chart = OpenMvTrendChart(self.trend_card)
        t_layout.addWidget(self.trend_chart, 1)
        layout.addWidget(self.trend_card, 1)

    def _on_window_clicked(self, key: str):
        for k, btn in self.window_buttons.items():
            btn.setChecked(k == key)
        self.trend_window_changed.emit(key)

    def set_charts_data(
        self,
        payload: dict[str, Any],
        *,
        active_broker: str = "",
        active_industry: str = "",
        trend_window: str = "ytd",
    ):
        cards = payload.get("cards", {})
        total_mv = float(cards.get("total_market_value") or 0.0)

        self.broker_donut.set_data(
            payload.get("broker_distribution", []),
            total_mv,
            active_key=active_broker,
        )
        self.industry_donut.set_data(
            payload.get("industry_distribution", []),
            total_mv,
            active_key=active_industry,
        )

        win_key = (trend_window or "ytd").strip().lower()
        for k, btn in self.window_buttons.items():
            blocked = btn.blockSignals(True)
            btn.setChecked(k == win_key)
            btn.blockSignals(blocked)

        mv_trend = payload.get("mv_trend", {})
        self.trend_chart.set_data(mv_trend.get("series") or mv_trend.get("points") or [])
        if mv_trend.get("has_incomplete_data") and mv_trend.get("incomplete_note"):
            self.incomplete_lbl.setText(mv_trend["incomplete_note"])
            self.incomplete_lbl.show()
        else:
            self.incomplete_lbl.setText("")
            self.incomplete_lbl.hide()
