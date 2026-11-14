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

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
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


class HoldingsKpiCard(QFrame):
    """持仓管理（持有中 / 已清仓）统一顶部 KPI 统计卡片，现代简约金融风格。"""

    def __init__(self, title: str, icon_text: str = "•", tooltip: str = "", parent=None):
        super().__init__(parent)
        if not tooltip and len(icon_text) > 4:
            tooltip = icon_text
            icon_text = "•"
        self.setObjectName("holdingsKpiCard")
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet("""
            QFrame#holdingsKpiCard, QFrame#openKpiCard, QFrame#closedKpiCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#holdingsKpiCard:hover, QFrame#openKpiCard:hover, QFrame#closedKpiCard:hover {
                border-color: #CBD5E1;
            }
            QFrame#holdingsKpiCard QLabel, QFrame#openKpiCard QLabel, QFrame#closedKpiCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(60)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(10)

        self.icon_badge = QLabel(icon_text or "•")
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


OpenKpiCard = HoldingsKpiCard


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

        donut_d = min(max(110, h - 24), int(w * 0.44))
        cx = 16.0 + donut_d / 2.0
        cy = h / 2.0
        self._center_pt = QPointF(cx, cy)
        self._outer_r = donut_d / 2.0
        self._inner_r = self._outer_r * 0.58

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
        legend_x = cx + self._outer_r + 16.0
        legend_w = max(100.0, w - legend_x - 6.0)
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
    """持仓市值趋势双 Y 轴组合图：左侧持仓市值（始终非负），右侧可切换累计盈亏（真实累计/区间归零）或每日盈亏柱状图。"""

    def __init__(self, parent=None, *, currency_symbol: str = "$", currency_code: str | None = None):
        super().__init__(parent)
        self.currency_symbol = currency_symbol
        self.currency_code = currency_code or ("CNY" if currency_symbol == "¥" else "USD")
        self.setMinimumHeight(125)
        self.setMouseTracking(True)
        self.points: list[dict[str, Any]] = []
        self._hover_idx: int = -1
        self._plot_pts: list[QPointF] = []
        self._xs: list[float] = []
        self._last_plot_rect = QRectF()

        self.pnl_metric: str = "cum"   # "cum" | "daily"
        self.cum_mode: str = "real"    # "real" | "zeroed"
        self.show_mv: bool = True
        self.show_pnl: bool = True

        self._mv_bounds: tuple[float, float] = (0.0, 100.0)
        self._pnl_bounds: tuple[float, float] = (-100.0, 100.0)
        self._display_pnl: list[float | None] = []
        self._zero_base_pnl: float | None = None

    def set_data(self, points: list[dict[str, Any]]):
        self.points = list(points or [])
        self._hover_idx = -1
        self._recompute_series()
        self.update()

    def set_mode(self, *, pnl_metric: str | None = None, cum_mode: str | None = None):
        if pnl_metric in ("cum", "daily"):
            self.pnl_metric = pnl_metric
        if cum_mode in ("real", "zeroed"):
            self.cum_mode = cum_mode
        self._recompute_series()
        self.update()

    def set_visibility(self, *, show_mv: bool | None = None, show_pnl: bool | None = None):
        if show_mv is not None:
            self.show_mv = bool(show_mv)
        if show_pnl is not None:
            self.show_pnl = bool(show_pnl)
        self.update()

    def _recompute_series(self):
        mv_vals = [float(p["market_value"]) for p in self.points if p.get("market_value") is not None]
        if not mv_vals:
            self._mv_bounds = (0.0, 100.0)
        else:
            v_min, v_max = min(mv_vals), max(mv_vals)
            if abs(v_max - v_min) < 1e-6:
                self._mv_bounds = (max(0.0, v_min * 0.9), v_max * 1.1 + 100.0)
            else:
                pad = (v_max - v_min) * 0.12
                self._mv_bounds = (max(0.0, v_min - pad), v_max + pad)

        self._zero_base_pnl = None
        if self.pnl_metric == "daily":
            self._display_pnl = [
                float(p["daily_pnl"]) if p.get("daily_pnl") is not None else None
                for p in self.points
            ]
        else:
            raw_cum = [
                float(p["cumulative_pnl"]) if p.get("cumulative_pnl") is not None else None
                for p in self.points
            ]
            if self.cum_mode == "zeroed":
                first_valid = next((v for v in raw_cum if v is not None), None)
                self._zero_base_pnl = first_valid
                if first_valid is not None:
                    self._display_pnl = [(v - first_valid) if v is not None else None for v in raw_cum]
                else:
                    self._display_pnl = [None for _ in raw_cum]
            else:
                self._display_pnl = raw_cum

        valid_pnl = [v for v in self._display_pnl if v is not None and math.isfinite(v)]
        if not valid_pnl:
            self._pnl_bounds = (-100.0, 100.0)
        else:
            r_min = min(0.0, min(valid_pnl))
            r_max = max(0.0, max(valid_pnl))
            if abs(r_max - r_min) < 1e-6:
                self._pnl_bounds = (-100.0, 100.0)
            else:
                span = r_max - r_min
                pad_u = r_max * 1.12 if r_max > 0 else span * 0.08
                pad_d = r_min * 1.12 if r_min < 0 else -span * 0.08
                self._pnl_bounds = (min(0.0, pad_d), max(0.0, pad_u))

    def mouseMoveEvent(self, event):
        if not self._xs or self._last_plot_rect.isEmpty():
            return
        pos = event.position()
        if not self._last_plot_rect.adjusted(-12, -10, 12, 16).contains(pos):
            if self._hover_idx != -1:
                self._hover_idx = -1
                self.update()
            return
        mx = pos.x()
        best_idx = min(range(len(self._xs)), key=lambda i: abs(self._xs[i] - mx))
        if best_idx != self._hover_idx:
            self._hover_idx = best_idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    @staticmethod
    def _fmt_axis_val(v: float) -> str:
        if abs(v) < 1e-4:
            return "0"
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:.1f}M"
        if abs(v) >= 1_000:
            return f"{v / 1_000:.0f}K"
        return f"{v:.0f}"

    def _fmt_signed_usd(self, v: float | None) -> str:
        if v is None:
            return "—"
        if abs(v) < 0.005:
            return f"{self.currency_symbol}0.00"
        return f"{'+' if v > 0 else '-'}{self.currency_symbol}{abs(v):,.2f}"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        self._plot_pts.clear()
        self._xs.clear()

        if not self.points:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "当前时间范围暂无真实历史市值快照")
            self._last_plot_rect = QRectF()
            return

        left_pad = 46.0
        right_pad = 50.0
        top_pad = 20.0
        bottom_pad = 20.0
        plot_w = max(40.0, w - left_pad - right_pad)
        plot_h = max(40.0, h - top_pad - bottom_pad)
        plot = QRectF(left_pad, top_pad, plot_w, plot_h)
        self._last_plot_rect = plot

        v_min, v_max = self._mv_bounds
        r_min, r_max = self._pnl_bounds

        # 顶部双轴单位标签
        painter.setFont(QFont(".AppleSystemUIFont", 9, QFont.Weight.DemiBold))
        if self.show_mv:
            painter.setPen(QColor("#2563EB"))
            painter.drawText(QRectF(2, 2, 110, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"持仓市值 ({self.currency_code})")
        if self.show_pnl:
            r_title = f"每日盈亏 ({self.currency_code})" if self.pnl_metric == "daily" else f"累计盈亏 ({self.currency_code})"
            painter.setPen(QColor("#16A34A" if self.pnl_metric == "daily" else "#F59E0B"))
            painter.drawText(QRectF(w - 122, 2, 120, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, r_title)

        # 1. 绘制水平网格线与左右独立 Y 轴刻度
        grid_steps = 4
        painter.setFont(numeric_font(9))
        for i in range(grid_steps + 1):
            ratio_y = i / grid_steps
            y = top_pad + plot_h * (1.0 - ratio_y)
            painter.setPen(QPen(QColor("#F1F5F9"), 1, Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(left_pad, y), QPointF(left_pad + plot_w, y))

            if self.show_mv:
                val_y = v_min + (v_max - v_min) * ratio_y
                painter.setPen(QColor("#2563EB"))
                painter.drawText(
                    QRectF(0, y - 8, left_pad - 6, 16),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    self._fmt_axis_val(val_y),
                )
            if self.show_pnl:
                r_val = r_min + (r_max - r_min) * ratio_y
                painter.setPen(QColor("#D97706" if self.pnl_metric == "cum" else "#475569"))
                painter.drawText(
                    QRectF(left_pad + plot_w + 6, y - 8, right_pad - 8, 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    self._fmt_axis_val(r_val),
                )

        # 右轴 0 USD 基准线
        y0_right = top_pad + plot_h * (1.0 - (0.0 - r_min) / max(1e-9, r_max - r_min))
        if self.show_pnl and top_pad <= y0_right <= top_pad + plot_h:
            painter.setPen(QPen(QColor("#CBD5E1"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(left_pad, y0_right), QPointF(left_pad + plot_w, y0_right))

        # 2. 计算横坐标点
        n = len(self.points)
        xs = [left_pad + (plot_w * idx / (n - 1) if n > 1 else plot_w / 2.0) for idx in range(n)]
        self._xs = xs

        def y_mv(v: float) -> float:
            return top_pad + plot_h * (1.0 - (v - v_min) / max(1e-9, v_max - v_min))

        def y_pnl(v: float) -> float:
            return top_pad + plot_h * (1.0 - (v - r_min) / max(1e-9, r_max - r_min))

        # 3. 绘制右轴收益系列（每日盈亏柱状图 或 累计盈亏橙色折线）
        if self.show_pnl:
            if self.pnl_metric == "daily":
                bar_w = max(1.5, min(6.0, (plot_w / max(1, n)) * 0.58))
                for idx, val in enumerate(self._display_pnl):
                    if val is None or abs(val) < 1e-9:
                        continue
                    yv = y_pnl(val)
                    top_b = min(y0_right, yv)
                    bh = max(1.0, abs(yv - y0_right))
                    col = QColor(22, 163, 74, 190) if val >= 0 else QColor(220, 38, 38, 190)
                    painter.fillRect(QRectF(xs[idx] - bar_w / 2.0, top_b, bar_w, bh), col)
            else:
                segs: list[list[QPointF]] = []
                cur_seg: list[QPointF] = []
                for idx, val in enumerate(self._display_pnl):
                    if val is None:
                        if cur_seg:
                            segs.append(cur_seg)
                            cur_seg = []
                    else:
                        cur_seg.append(QPointF(xs[idx], y_pnl(val)))
                if cur_seg:
                    segs.append(cur_seg)
                painter.setPen(QPen(QColor("#F59E0B"), 1.8))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for seg in segs:
                    if len(seg) == 1:
                        painter.setBrush(QColor("#F59E0B"))
                        painter.drawEllipse(seg[0], 3.5, 3.5)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                    else:
                        p_path = QPainterPath(seg[0])
                        for pt in seg[1:]:
                            p_path.lineTo(pt)
                        painter.drawPath(p_path)

        # 4. 绘制左轴蓝色持仓市值折线 + 柔和填充
        mv_pts: list[tuple[int, QPointF]] = []
        for idx, p in enumerate(self.points):
            mv_raw = p.get("market_value")
            if mv_raw is not None:
                pt = QPointF(xs[idx], y_mv(float(mv_raw)))
                mv_pts.append((idx, pt))
                self._plot_pts.append(pt)

        if self.show_mv and mv_pts:
            if len(mv_pts) >= 2:
                line_path = QPainterPath(mv_pts[0][1])
                for _, pt in mv_pts[1:]:
                    line_path.lineTo(pt)
                area_path = QPainterPath(line_path)
                area_path.lineTo(QPointF(mv_pts[-1][1].x(), top_pad + plot_h))
                area_path.lineTo(QPointF(mv_pts[0][1].x(), top_pad + plot_h))
                area_path.closeSubpath()
                grad = QLinearGradient(0, top_pad, 0, top_pad + plot_h)
                grad.setColorAt(0.0, QColor(37, 99, 235, 58))
                grad.setColorAt(1.0, QColor(37, 99, 235, 4))
                painter.fillPath(area_path, grad)
                painter.setPen(QPen(QColor("#2563EB"), 2.1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(line_path)
            last_pt = mv_pts[-1][1]
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor("#2563EB"))
            painter.drawEllipse(last_pt, 4.0, 4.0)

        if not self.show_mv and not self.show_pnl:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 11))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "已隐藏全部系列，点击下方图例可恢复显示")

        # 5. X 轴自适应日期刻度（防止文字重叠）
        painter.setPen(QColor("#64748B"))
        painter.setFont(numeric_font(9))
        max_ticks = max(2, min(6, int(plot_w // 52)))
        if n <= max_ticks:
            tick_indices = list(range(n))
        else:
            tick_indices = sorted({int(round(i * (n - 1) / (max_ticks - 1))) for i in range(max_ticks)})
        spans_years = len({str(p.get("date") or "")[:4] for p in self.points}) > 1
        for ti in tick_indices:
            d_str = str(self.points[ti].get("date") or "")
            short_d = d_str[2:] if (spans_years and len(d_str) >= 10) else (d_str[5:] if len(d_str) >= 10 else d_str)
            rx = max(left_pad - 4, min(left_pad + plot_w + 4 - 52, xs[ti] - 26))
            painter.drawText(QRectF(rx, top_pad + plot_h + 2, 52, 16), Qt.AlignmentFlag.AlignCenter, short_d)

        # 6. 鼠标悬停垂直辅助线、高亮点与边界钳制 Tooltip
        if 0 <= self._hover_idx < n:
            hi = self._hover_idx
            hx = xs[hi]
            painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(hx, top_pad), QPointF(hx, top_pad + plot_h))

            pt_data = self.points[hi]
            mv_v = float(pt_data["market_value"]) if pt_data.get("market_value") is not None else None
            disp_v = self._display_pnl[hi]
            real_cum_v = float(pt_data["cumulative_pnl"]) if pt_data.get("cumulative_pnl") is not None else None

            if self.show_mv and mv_v is not None:
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
                painter.setBrush(QColor("#1D4ED8"))
                painter.drawEllipse(QPointF(hx, y_mv(mv_v)), 4.5, 4.5)
            if self.show_pnl and disp_v is not None and self.pnl_metric == "cum":
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
                painter.setBrush(QColor("#F59E0B"))
                painter.drawEllipse(QPointF(hx, y_pnl(disp_v)), 4.5, 4.5)

            tip_lines: list[tuple[str, str]] = [("", str(pt_data.get("date") or ""))]
            if self.show_mv:
                tip_lines.append(("#2563EB", f"持仓市值：{f'{self.currency_symbol}{mv_v:,.2f}' if mv_v is not None else '—'}"))
            if self.show_pnl:
                if self.pnl_metric == "daily":
                    dot = "#16A34A" if (disp_v is not None and disp_v >= 0) else "#DC2626" if disp_v is not None else "#64748B"
                    tip_lines.append((dot, f"当日盈亏：{self._fmt_signed_usd(disp_v)}"))
                else:
                    if self.cum_mode == "zeroed":
                        tip_lines.append(("#F59E0B", f"区间累计(归零)：{self._fmt_signed_usd(disp_v)}"))
                        tip_lines.append(("#64748B", f"真实累计盈亏：{self._fmt_signed_usd(real_cum_v)}"))
                        tip_lines.append(("", "模式：区间归零"))
                    else:
                        tip_lines.append(("#F59E0B", f"真实累计盈亏：{self._fmt_signed_usd(real_cum_v)}"))
                        tip_lines.append(("", "模式：真实累计"))
            if pt_data.get("incomplete"):
                tip_lines.append(("#B45309", "部分历史快照缺失"))

            tip_font = QFont(".AppleSystemUIFont", 10)
            tip_bold = QFont(".AppleSystemUIFont", 10, QFont.Weight.Bold)
            tfm = painter.fontMetrics()
            painter.setFont(tip_bold)
            tfm = painter.fontMetrics()
            tip_w = max(tfm.horizontalAdvance(t) for _, t in tip_lines) + 26.0
            tip_h = 10.0 + len(tip_lines) * 17.0
            tx = hx + 10.0
            if tx + tip_w > w - 4.0:
                tx = hx - tip_w - 10.0
            tx = max(4.0, min(w - tip_w - 4.0, tx))
            ty = max(4.0, min(top_pad + plot_h - tip_h - 4.0, top_pad + 4.0))

            painter.setPen(QPen(QColor("#CBD5E1"), 1))
            painter.setBrush(QColor(255, 255, 255, 245))
            painter.drawRoundedRect(QRectF(tx, ty, tip_w, tip_h), 7.0, 7.0)
            for li, (dot_c, txt) in enumerate(tip_lines):
                ly = ty + 5.0 + li * 17.0
                painter.setFont(tip_bold if li == 0 else tip_font)
                tx_pos = tx + 8.0
                if dot_c:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(dot_c))
                    painter.drawEllipse(QPointF(tx + 12.0, ly + 8.0), 3.0, 3.0)
                    tx_pos = tx + 19.0
                painter.setPen(QColor("#0F172A"))
                painter.drawText(QRectF(tx_pos, ly, tip_w - (tx_pos - tx) - 4.0, 16.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, txt)


class RatioProgressBarCell(QWidget):
    """带蓝色横向进度条与百分比数值的紧凑单元格组件（居中展示）。"""

    def __init__(
        self,
        ratio_val: float,
        percent_text: str,
        *,
        is_total: bool = False,
        is_selected: bool = False,
        tooltip: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if tooltip:
            self.setToolTip(tooltip)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pbar = QProgressBar(self)
        self.pbar.setFixedHeight(5)
        self.pbar.setMinimumWidth(20)
        self.pbar.setTextVisible(False)
        self.pbar.setRange(0, 1000)
        clamped = max(0.0, min(1.0, float(ratio_val or 0.0)))
        self.pbar.setValue(int(round(clamped * 1000)))
        bar_color = "#2563EB" if not is_total else "#3B82F6"
        self.pbar.setStyleSheet(f"""
            QProgressBar {{
                background: #E2E8F0;
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background: {bar_color};
                border-radius: 2px;
            }}
        """)
        layout.addWidget(self.pbar, 1)

        # 展示百分比标签（使用 QFontMetrics 动态计算实际文字宽度并预留内边距，确保 100.0% 和各类文字完整显示）
        if percent_text:
            self.lbl = QLabel(percent_text, self)
            self.lbl.setFont(numeric_font(10))
            txt_color = "#2563EB" if (is_selected or is_total) else "#0F172A"
            self.lbl.setStyleSheet(f"color: {txt_color}; background: transparent; border: none;")
            fm = QFontMetrics(self.lbl.font())
            text_w = fm.horizontalAdvance(percent_text)
            self.lbl.setFixedWidth(text_w + 6)
            self.lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(self.lbl)
        else:
            self.lbl = None


class OpenHoldingsChartsRow(QWidget):
    """持有中页面中部三列并排图表区（按券商渠道持仓汇总列表 | 股票类型分布环形图 | 持仓市值趋势）。"""

    broker_clicked = Signal(str)
    industry_clicked = Signal(str)
    trend_window_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_broker: str = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

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

        # 1. 持仓分布（按券商渠道）汇总列表 (约占 39%)
        self.broker_card = QFrame()
        self.broker_card.setObjectName("openChartCard")
        self.broker_card.setMinimumHeight(265)
        self.broker_card.setStyleSheet(card_qss)
        b_layout = QVBoxLayout(self.broker_card)
        b_layout.setContentsMargins(14, 10, 14, 10)
        b_layout.setSpacing(6)

        b_hdr = QHBoxLayout()
        b_title = QLabel("持仓分布 (按券商渠道)")
        b_title.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700;")
        b_hdr.addWidget(b_title)
        b_hdr.addStretch()
        b_hint = QLabel("点击行筛选券商，点击合计恢复")
        b_hint.setStyleSheet("color: #94A3B8; font-size: 11px;")
        b_hdr.addWidget(b_hint)
        b_layout.addLayout(b_hdr)

        self.broker_table = QTableWidget(0, 6)
        self.broker_table.setHorizontalHeaderLabels([
            "券商渠道", "市值占比", "持仓成本", "持仓市值", "浮动盈亏", "持仓数量"
        ])
        for c in range(6):
            it = self.broker_table.horizontalHeaderItem(c)
            if it:
                if c in (2, 3, 4):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.broker_table.verticalHeader().setVisible(False)
        self.broker_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.broker_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.broker_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.broker_table.setShowGrid(False)
        self.broker_table.setAlternatingRowColors(False)
        self.broker_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.broker_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.broker_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.broker_table.setStyleSheet("""
            QTableWidget {
                border: none;
                background: #FFFFFF;
                font-size: 11px;
            }
            QTableWidget::item:hover {
                background: #F8FAFC;
            }
            QHeaderView::section {
                background: #F8FAFC;
                color: #64748B;
                font-size: 11px;
                font-weight: 600;
                border: none;
                border-bottom: 1px solid #E2E8F0;
                padding: 5px 4px;
            }
            QTableWidget::item {
                border-bottom: 1px solid #F1F5F9;
                padding: 4px 4px;
            }
            QScrollBar:horizontal {
                height: 6px;
                background: transparent;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #CBD5E1;
                border-radius: 3px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #94A3B8;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
                height: 0px;
            }
        """)
        b_hdr_view = self.broker_table.horizontalHeader()
        for i in range(6):
            b_hdr_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        b_hdr_view.setStretchLastSection(False)
        self.broker_table.cellClicked.connect(self._on_broker_row_clicked)
        b_layout.addWidget(self.broker_table, 1)

        # 兼容旧代码与自动化测试中对 broker_donut.item_clicked 的访问
        class _DonutCompat(QObject):
            item_clicked = Signal(str)

            def set_data(self, *args, **kwargs):
                pass

        self.broker_donut = _DonutCompat(self)
        self.broker_donut.item_clicked.connect(self.broker_clicked.emit)

        layout.addWidget(self.broker_card, 39)

        # 2. 持仓分布（按类型）环形图 (约占 25%)
        self.industry_card = QFrame()
        self.industry_card.setObjectName("openChartCard")
        self.industry_card.setMinimumHeight(265)
        self.industry_card.setStyleSheet(card_qss)
        i_layout = QVBoxLayout(self.industry_card)
        i_layout.setContentsMargins(14, 10, 14, 10)
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
        layout.addWidget(self.industry_card, 25)

        # 3. 持仓市值趋势 (约占 36%)
        self.trend_card = QFrame()
        self.trend_card.setObjectName("openChartCard")
        self.trend_card.setMinimumHeight(265)
        self.trend_card.setStyleSheet(card_qss)
        t_layout = QVBoxLayout(self.trend_card)
        t_layout.setContentsMargins(14, 10, 14, 10)
        t_layout.setSpacing(4)

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

        # 收益指标与累计模式紧凑切换栏
        sub_ctrl = QHBoxLayout()
        sub_ctrl.setContentsMargins(0, 0, 0, 0)
        sub_ctrl.setSpacing(4)
        lbl_pm = QLabel("收益指标")
        lbl_pm.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600;")
        sub_ctrl.addWidget(lbl_pm)

        pill_qss = """
            QPushButton {
                background: #F1F5F9; color: #64748B; border: none; border-radius: 5px;
                padding: 2px 8px; font-size: 11px; font-weight: 500;
            }
            QPushButton:checked { background: #2563EB; color: #FFFFFF; font-weight: 700; }
            QPushButton:hover:!checked { background: #E2E8F0; color: #0F172A; }
        """
        self.metric_buttons: dict[str, QPushButton] = {}
        for lbl, key in (("累计盈亏", "cum"), ("每日盈亏", "daily")):
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(key == "cum")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(pill_qss)
            b.clicked.connect(lambda _=False, k=key: self._on_pnl_metric_clicked(k))
            self.metric_buttons[key] = b
            sub_ctrl.addWidget(b)

        self.cum_mode_sep = QLabel("|")
        self.cum_mode_sep.setStyleSheet("color: #CBD5E1; font-size: 11px; padding: 0 2px;")
        sub_ctrl.addWidget(self.cum_mode_sep)

        self.cum_mode_lbl = QLabel("累计模式")
        self.cum_mode_lbl.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600;")
        sub_ctrl.addWidget(self.cum_mode_lbl)

        self.cum_mode_buttons: dict[str, QPushButton] = {}
        for lbl, key in (("真实累计", "real"), ("区间归零", "zeroed")):
            b = QPushButton(lbl)
            b.setCheckable(True)
            b.setChecked(key == "real")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(pill_qss)
            b.clicked.connect(lambda _=False, k=key: self._on_cum_mode_clicked(k))
            self.cum_mode_buttons[key] = b
            sub_ctrl.addWidget(b)
        sub_ctrl.addStretch(1)
        t_layout.addLayout(sub_ctrl)

        self.trend_chart = OpenMvTrendChart(self.trend_card)
        t_layout.addWidget(self.trend_chart, 1)

        # 底部可点击交互图例
        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(0, 0, 0, 0)
        legend_row.setSpacing(10)
        legend_row.addStretch(1)
        self.mv_legend_btn = QPushButton("●  持仓市值 (左轴)")
        self.mv_legend_btn.setCheckable(True)
        self.mv_legend_btn.setChecked(True)
        self.mv_legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mv_legend_btn.clicked.connect(self._on_trend_legend_toggled)
        legend_row.addWidget(self.mv_legend_btn)

        self.pnl_legend_btn = QPushButton("●  累计盈亏 (右轴)")
        self.pnl_legend_btn.setCheckable(True)
        self.pnl_legend_btn.setChecked(True)
        self.pnl_legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pnl_legend_btn.clicked.connect(self._on_trend_legend_toggled)
        legend_row.addWidget(self.pnl_legend_btn)
        legend_row.addStretch(1)
        t_layout.addLayout(legend_row)
        self._sync_trend_legend_styles()

        layout.addWidget(self.trend_card, 36)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_broker_table_layout()

    def _adjust_broker_table_layout(self):
        """根据当前表格文本内容与可用视口宽度动态分配列宽，确保金额与百分比绝不截断。"""
        if not hasattr(self, "broker_table") or self.broker_table.columnCount() < 6:
            return

        hdr_view = self.broker_table.horizontalHeader()
        hdr_fm = QFontMetrics(hdr_view.font())
        bold_fm = QFontMetrics(numeric_font(11, bold=True))
        num_fm = QFontMetrics(numeric_font(11))

        # 1. 计算各列根据内容所需的基础安全最小宽度
        w0 = hdr_fm.horizontalAdvance("券商渠道") + 16
        for r in range(self.broker_table.rowCount()):
            it = self.broker_table.item(r, 0)
            if it and it.text():
                w0 = max(w0, QFontMetrics(it.font()).horizontalAdvance(it.text()) + 18)
        col0_min = max(64, w0)

        # Col 1: 市值占比（进度条允许缩短，给金额列更多空间，基础最小预留约 76px）
        col1_min = 76

        # Col 2: 持仓成本
        w2 = hdr_fm.horizontalAdvance("持仓成本") + 16
        for r in range(self.broker_table.rowCount()):
            it = self.broker_table.item(r, 2)
            if it and it.text():
                w2 = max(w2, QFontMetrics(it.font()).horizontalAdvance(it.text()) + 16)
        col2_min = max(76, w2)

        # Col 3: 持仓市值
        w3 = hdr_fm.horizontalAdvance("持仓市值") + 16
        for r in range(self.broker_table.rowCount()):
            it = self.broker_table.item(r, 3)
            if it and it.text():
                w3 = max(w3, QFontMetrics(it.font()).horizontalAdvance(it.text()) + 16)
        col3_min = max(76, w3)

        # Col 4: 浮动盈亏（包含正负号）
        w4 = hdr_fm.horizontalAdvance("浮动盈亏") + 16
        for r in range(self.broker_table.rowCount()):
            it = self.broker_table.item(r, 4)
            if it and it.text():
                w4 = max(w4, QFontMetrics(it.font()).horizontalAdvance(it.text()) + 16)
        col4_min = max(76, w4)

        # Col 5: 持仓数量
        w5 = hdr_fm.horizontalAdvance("持仓数量") + 14
        for r in range(self.broker_table.rowCount()):
            it = self.broker_table.item(r, 5)
            if it and it.text():
                w5 = max(w5, QFontMetrics(it.font()).horizontalAdvance(it.text()) + 14)
        col5_min = max(52, w5)

        total_min = col0_min + col1_min + col2_min + col3_min + col4_min + col5_min

        # 2. 获取表格视口可用宽度
        viewport_w = self.broker_table.viewport().width()
        if viewport_w <= 10:
            viewport_w = max(self.broker_card.width() - 30, total_min)

        # 3. 动态分配：
        # 视口充裕时金额列留有舒适右边距，剩余空间分配给占比进度条；
        # 视口紧凑时优先压缩占比进度条，金额列严格保留最小安全宽度（绝不压缩截断重要财务金额），允许横向滚动
        if viewport_w > total_min:
            extra = viewport_w - total_min
            extra_money = extra * 0.12
            c2_w = int(col2_min + extra_money)
            c3_w = int(col3_min + extra_money)
            c4_w = int(col4_min + extra_money)
            c1_w = int(viewport_w - (col0_min + c2_w + c3_w + c4_w + col5_min))
            c1_w = max(col1_min, c1_w)

            self.broker_table.setColumnWidth(0, int(col0_min))
            self.broker_table.setColumnWidth(1, int(c1_w))
            self.broker_table.setColumnWidth(2, int(c2_w))
            self.broker_table.setColumnWidth(3, int(c3_w))
            self.broker_table.setColumnWidth(4, int(c4_w))
            self.broker_table.setColumnWidth(5, int(col5_min))
        else:
            self.broker_table.setColumnWidth(0, int(col0_min))
            self.broker_table.setColumnWidth(1, int(col1_min))
            self.broker_table.setColumnWidth(2, int(col2_min))
            self.broker_table.setColumnWidth(3, int(col3_min))
            self.broker_table.setColumnWidth(4, int(col4_min))
            self.broker_table.setColumnWidth(5, int(col5_min))

    def _on_broker_row_clicked(self, row: int, _col: int):
        item = self.broker_table.item(row, 0)
        if not item:
            return
        ch = item.data(Qt.ItemDataRole.UserRole) or item.text() or ""
        if not ch or ch == "合计":
            self.broker_clicked.emit("")
        elif self._active_broker and str(self._active_broker).strip().casefold() == str(ch).strip().casefold():
            self.broker_clicked.emit("")
        else:
            self.broker_clicked.emit(ch)

    def _on_pnl_metric_clicked(self, key: str):
        for k, btn in self.metric_buttons.items():
            btn.setChecked(k == key)
        is_cum = key == "cum"
        self.cum_mode_sep.setVisible(is_cum)
        self.cum_mode_lbl.setVisible(is_cum)
        for btn in self.cum_mode_buttons.values():
            btn.setVisible(is_cum)
        self.trend_chart.set_mode(pnl_metric=key)
        self._sync_trend_legend_styles()

    def _on_cum_mode_clicked(self, key: str):
        for k, btn in self.cum_mode_buttons.items():
            btn.setChecked(k == key)
        self.trend_chart.set_mode(cum_mode=key)

    def _on_trend_legend_toggled(self):
        self._sync_trend_legend_styles()
        self.trend_chart.set_visibility(
            show_mv=self.mv_legend_btn.isChecked(),
            show_pnl=self.pnl_legend_btn.isChecked(),
        )

    def _sync_trend_legend_styles(self):
        pnl_is_daily = self.trend_chart.pnl_metric == "daily" if hasattr(self, "trend_chart") else False
        specs = (
            (self.mv_legend_btn, "持仓市值 (左轴)", "#2563EB", "●"),
            (
                self.pnl_legend_btn,
                "每日盈亏 (右轴)" if pnl_is_daily else "累计盈亏 (右轴)",
                "#16A34A" if pnl_is_daily else "#F59E0B",
                "■" if pnl_is_daily else "●",
            ),
        )
        for btn, label, color, glyph in specs:
            active = btn.isChecked()
            btn.setText(f"{glyph}  {label}" if active else f"○  {label}（已隐藏）")
            if active:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #F8FAFC; color: {color}; border: 1px solid #CBD5E1; border-radius: 11px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}"
                    f"QPushButton:hover {{ border-color: {color}; background: #EFF6FF; }}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #F1F5F9; color: #94A3B8; border: 1px dashed #CBD5E1; border-radius: 11px; padding: 2px 10px; font-size: 11px; font-weight: 500; }"
                    "QPushButton:hover { background: #E2E8F0; color: #475569; }"
                )

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
        total_cost = float(cards.get("total_cost_amount") or 0.0)
        total_pnl = float(cards.get("floating_pnl") or 0.0)
        total_stocks = int(cards.get("stock_count") or 0)
        self._active_broker = active_broker or ""

        # -----------------------------------------------------------------
        # 1. 填充券商渠道持仓汇总列表（默认按持仓市值从高到低排序）
        # -----------------------------------------------------------------
        raw_brokers = list(payload.get("broker_distribution") or [])
        sorted_brokers = sorted(
            raw_brokers,
            key=lambda x: -float(x.get("market_value") or 0.0),
        )

        has_data = len(sorted_brokers) > 0 or total_mv > 0
        total_rows = len(sorted_brokers) + (1 if has_data else 0)
        self.broker_table.setRowCount(total_rows)

        num_font = numeric_font(11)
        bold_num_font = numeric_font(11, bold=True)

        for r_idx, b in enumerate(sorted_brokers):
            ch_name = str(b.get("channel") or b.get("name") or "")
            b_cost = float(b.get("cost_amount") or 0.0)
            b_mv = float(b.get("market_value") or 0.0)
            b_pnl = float(b.get("floating_pnl") if b.get("floating_pnl") is not None else (b_mv - b_cost))
            b_ratio = float(b.get("ratio") or (b_mv / total_mv if total_mv > 0 else 0.0))
            b_percent = float(b.get("percent") or (b_ratio * 100.0))
            b_stocks = int(b.get("stock_count") or 0)

            is_selected = bool(
                self._active_broker
                and self._active_broker.strip().casefold() == ch_name.strip().casefold()
            )
            bg = QColor("#EFF6FF") if is_selected else QColor("#FFFFFF")
            fg = "#2563EB" if is_selected else "#0F172A"

            tip = (
                f"券商渠道：{ch_name}\n"
                f"持仓成本：{b_cost:,.2f} USD\n"
                f"持仓市值：{b_mv:,.2f} USD\n"
                f"浮动盈亏：{b_pnl:+,.2f} USD\n"
                f"市值占比：{b_percent:.1f}%\n"
                f"持仓数量：{b_stocks} 只"
            )

            # Col 0: 券商渠道（居中）
            it0 = QTableWidgetItem(ch_name)
            it0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it0.setData(Qt.ItemDataRole.UserRole, ch_name)
            it0.setBackground(bg)
            it0.setForeground(QColor(fg))
            it0.setToolTip(tip)
            self.broker_table.setItem(r_idx, 0, it0)

            # Col 1: 市值占比（居中紧凑进度条 + 百分比，底层item不填文字避免阴影重影）
            it1 = QTableWidgetItem("")
            it1.setData(Qt.ItemDataRole.UserRole, f"{b_percent:.1f}%")
            it1.setBackground(bg)
            it1.setToolTip(tip)
            self.broker_table.setItem(r_idx, 1, it1)
            p_cell = RatioProgressBarCell(
                b_ratio,
                f"{b_percent:.1f}%",
                is_total=False,
                is_selected=is_selected,
                tooltip=tip,
                parent=self.broker_table,
            )
            self.broker_table.setCellWidget(r_idx, 1, p_cell)

            # Col 2: 持仓成本（右对齐）
            it2 = QTableWidgetItem(f"{b_cost:,.2f}")
            it2.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it2.setFont(num_font)
            it2.setBackground(bg)
            it2.setForeground(QColor(fg))
            it2.setToolTip(tip)
            self.broker_table.setItem(r_idx, 2, it2)

            # Col 3: 持仓市值（右对齐）
            it3 = QTableWidgetItem(f"{b_mv:,.2f}")
            it3.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it3.setFont(num_font)
            it3.setBackground(bg)
            it3.setForeground(QColor(fg))
            it3.setToolTip(tip)
            self.broker_table.setItem(r_idx, 3, it3)

            # Col 4: 浮动盈亏（右对齐，带正负号与盈亏颜色）
            pnl_fg = _pnl_color(b_pnl)
            it4 = QTableWidgetItem(f"{b_pnl:+,.2f}")
            it4.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it4.setFont(num_font)
            it4.setBackground(bg)
            it4.setForeground(QColor(pnl_fg))
            it4.setToolTip(tip)
            self.broker_table.setItem(r_idx, 4, it4)

            # Col 5: 持仓数量（居中）
            it5 = QTableWidgetItem(str(b_stocks))
            it5.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it5.setFont(num_font)
            it5.setBackground(bg)
            it5.setForeground(QColor(fg))
            it5.setToolTip(tip)
            self.broker_table.setItem(r_idx, 5, it5)

            self.broker_table.setRowHeight(r_idx, 27)

        # 底部合计行（保持与当前筛选范围内的顶部 KPI 一致）
        if has_data:
            tot_idx = len(sorted_brokers)
            tot_bg = QColor("#F8FAFC")
            tot_tip = (
                f"总持仓成本：{total_cost:,.2f} USD\n"
                f"总持仓市值：{total_mv:,.2f} USD\n"
                f"总浮动盈亏：{total_pnl:+,.2f} USD\n"
                f"总持仓数量：{total_stocks} 只"
            )

            it0 = QTableWidgetItem("合计")
            it0.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it0.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold))
            it0.setData(Qt.ItemDataRole.UserRole, "合计")
            it0.setBackground(tot_bg)
            it0.setForeground(QColor("#0F172A"))
            it0.setToolTip(tot_tip)
            self.broker_table.setItem(tot_idx, 0, it0)

            # 验证当前渠道市值是否完整覆盖有效总市值；有有效且完整数据时显示 100.0%，无有效市值时显示 —，不能无条件硬编码
            sum_broker_mv = sum(float(b.get("market_value") or 0.0) for b in sorted_brokers)
            if total_mv > 0 and len(sorted_brokers) > 0 and sum_broker_mv > 0:
                coverage = sum_broker_mv / total_mv
                if coverage >= 0.999:
                    tot_percent_text = "100.0%"
                    tot_ratio = 1.0
                else:
                    tot_percent_text = f"{coverage * 100.0:.1f}%"
                    tot_ratio = min(1.0, max(0.0, coverage))
            else:
                tot_percent_text = "—"
                tot_ratio = 0.0

            it1 = QTableWidgetItem("")
            it1.setData(Qt.ItemDataRole.UserRole, tot_percent_text)
            it1.setBackground(tot_bg)
            it1.setToolTip(tot_tip)
            self.broker_table.setItem(tot_idx, 1, it1)
            tot_p_cell = RatioProgressBarCell(
                tot_ratio,
                tot_percent_text,
                is_total=True,
                is_selected=False,
                tooltip=tot_tip,
                parent=self.broker_table,
            )
            self.broker_table.setCellWidget(tot_idx, 1, tot_p_cell)

            it2 = QTableWidgetItem(f"{total_cost:,.2f}")
            it2.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it2.setFont(bold_num_font)
            it2.setBackground(tot_bg)
            it2.setForeground(QColor("#0F172A"))
            it2.setToolTip(tot_tip)
            self.broker_table.setItem(tot_idx, 2, it2)

            it3 = QTableWidgetItem(f"{total_mv:,.2f}")
            it3.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it3.setFont(bold_num_font)
            it3.setBackground(tot_bg)
            it3.setForeground(QColor("#0F172A"))
            it3.setToolTip(tot_tip)
            self.broker_table.setItem(tot_idx, 3, it3)

            tot_pnl_fg = _pnl_color(total_pnl)
            it4 = QTableWidgetItem(f"{total_pnl:+,.2f}")
            it4.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it4.setFont(bold_num_font)
            it4.setBackground(tot_bg)
            it4.setForeground(QColor(tot_pnl_fg))
            it4.setToolTip(tot_tip)
            self.broker_table.setItem(tot_idx, 4, it4)

            it5 = QTableWidgetItem(str(total_stocks))
            it5.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it5.setFont(bold_num_font)
            it5.setBackground(tot_bg)
            it5.setForeground(QColor("#0F172A"))
            it5.setToolTip(tot_tip)
            self.broker_table.setItem(tot_idx, 5, it5)

            self.broker_table.setRowHeight(tot_idx, 27)

        # 保持旧兼容组件数据同步
        self.broker_donut.set_data(
            payload.get("broker_distribution", []),
            total_mv,
            active_key=active_broker,
        )

        # -----------------------------------------------------------------
        # 2. 股票类型分布环形图
        # -----------------------------------------------------------------
        self.industry_donut.set_data(
            payload.get("industry_distribution", []),
            total_mv,
            active_key=active_industry,
        )

        # -----------------------------------------------------------------
        # 3. 持仓市值与收益趋势图
        # -----------------------------------------------------------------
        win_key = (trend_window or "ytd").strip().lower()
        for k, btn in self.window_buttons.items():
            blocked = btn.blockSignals(True)
            btn.setChecked(k == win_key)
            btn.blockSignals(blocked)

        mv_trend = payload.get("mv_trend", {})
        self.trend_chart.set_data(mv_trend.get("series") or mv_trend.get("points") or [])
        self.incomplete_lbl.setText("")
        self.incomplete_lbl.hide()
        self._adjust_broker_table_layout()


class OpenTop10Chart(QWidget):
    """以 0 为中心的横向发散条形图（持有中股票浮动盈亏贡献：左侧亏损 Top N + 中央发散条形 + 右侧盈利 Top N）。"""

    ticker_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.winners: list[dict[str, Any]] = []
        self.losers: list[dict[str, Any]] = []
        self.active_ticker: str = ""
        self._hit_rects: list[tuple[QRectF, dict[str, Any]]] = []
        self._hover_ticker: str = ""
        self.setMinimumHeight(148)

    def set_data(self, top10: dict[str, Any], *, active_ticker: str = ""):
        self.winners = list(top10.get("winners") or ())
        self.losers = list(top10.get("losers") or ())
        self.active_ticker = (active_ticker or "").strip().upper()
        rows = max(len(self.winners), len(self.losers), 1)
        self.setFixedHeight(max(76, rows * 26 + 30))
        self.update()

    def _hit_item(self, pos: QPointF) -> dict[str, Any] | None:
        for rect, item in self._hit_rects:
            if rect.contains(pos):
                return item
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self._hit_item(event.position())
            if item:
                tk = str(item.get("ticker") or "").upper()
                self._on_bar_clicked(tk)
                return
        super().mousePressEvent(event)

    def _on_bar_clicked(self, ticker: str):
        tk = (ticker or "").strip().upper()
        self.ticker_clicked.emit("" if tk == self.active_ticker else tk)

    def mouseMoveEvent(self, event):
        item = self._hit_item(event.position())
        if item:
            tk = str(item.get("ticker") or "")
            if tk != self._hover_ticker:
                self._hover_ticker = tk
                self.update()
            pnl = item.get("floating_pnl") if item.get("floating_pnl") is not None else item.get("realized_pnl")
            mv = item.get("market_value")
            qty = item.get("quantity")
            chs = "、".join(item.get("channels") or ()) or "—"
            lines = [f"股票代码：{tk}", f"浮动盈亏：{_fmt_usd(pnl, signed=True)} USD"]
            if mv is not None:
                lines.append(f"持仓市值：{_fmt_usd(mv)} USD")
            if qty is not None:
                lines.append(f"持仓数量：{qty}")
            lines.append(f"涉及券商：{chs}")
            QToolTip.showText(
                event.globalPosition().toPoint(),
                "\n".join(lines),
                self,
            )
        else:
            if self._hover_ticker:
                self._hover_ticker = ""
                self.update()
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_ticker:
            self._hover_ticker = ""
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hit_rects.clear()
        w, h = float(self.width()), float(self.height())

        if not self.winners and not self.losers:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "当前筛选范围暂无非零浮动盈亏的持仓股票")
            return

        rows = max(len(self.winners), len(self.losers), 1)
        side_w = max(145.0, min(215.0, w * 0.22))
        bar_area_x = side_w + 10.0
        bar_area_w = max(80.0, w - 2.0 * side_w - 20.0)
        center_x = bar_area_x + bar_area_w / 2.0
        half_w = bar_area_w / 2.0 - 8.0

        top_y = 20.0
        row_h = 26.0
        bottom_axis_y = top_y + rows * row_h + 2.0

        all_pnls = [
            abs(float(x.get("floating_pnl") if x.get("floating_pnl") is not None else (x.get("realized_pnl") or 0.0)))
            for x in (*self.winners, *self.losers)
        ]
        max_abs = max(all_pnls) if all_pnls else 100.0
        if max_abs <= 0:
            max_abs = 100.0

        # 中央 0 USD 虚线
        center_pen = QPen(QColor("#CBD5E1"), 1, Qt.PenStyle.DashLine)
        painter.setPen(center_pen)
        painter.drawLine(QPointF(center_x, top_y - 8), QPointF(center_x, bottom_axis_y))

        # 底部基准刻度
        painter.setPen(QPen(QColor("#E2E8F0"), 1))
        painter.drawLine(QPointF(bar_area_x, bottom_axis_y), QPointF(bar_area_x + bar_area_w, bottom_axis_y))
        painter.setPen(QColor("#94A3B8"))
        painter.setFont(numeric_font(9))
        painter.drawText(QRectF(center_x - 30, bottom_axis_y + 3, 60, 16), Qt.AlignmentFlag.AlignCenter, "0")
        for frac in (0.33, 0.66, 1.0):
            val_txt = f"{max_abs * frac:,.0f}"
            lx = center_x - half_w * frac
            painter.drawText(QRectF(lx - 40, bottom_axis_y + 3, 80, 16), Qt.AlignmentFlag.AlignCenter, f"-{val_txt}")
            rx = center_x + half_w * frac
            painter.drawText(QRectF(rx - 40, bottom_axis_y + 3, 80, 16), Qt.AlignmentFlag.AlignCenter, val_txt)

        # 逐行绘制
        l_start = 8.0
        r_start = w - side_w - 8.0

        for idx in range(rows):
            ry = top_y + idx * row_h

            # 左侧亏损行
            if idx < len(self.losers):
                item = self.losers[idx]
                tk = str(item["ticker"])
                val = float(item.get("floating_pnl") if item.get("floating_pnl") is not None else (item.get("realized_pnl") or 0.0))
                is_sel = self.active_ticker == tk.upper()
                is_hov = self._hover_ticker == tk
                bar_len = max(4.0, (abs(val) / max_abs) * half_w)

                row_rect = QRectF(l_start, ry + 1, side_w - 2, row_h - 2)
                bar_rect = QRectF(center_x - bar_len, ry + 5, bar_len, row_h - 10)
                self._hit_rects.append((row_rect, item))
                self._hit_rects.append((bar_rect, item))

                if is_sel or is_hov:
                    painter.setPen(QPen(QColor("#FCA5A5" if is_sel else "#FEE2E2"), 1))
                    painter.setBrush(QColor("#FEF2F2" if is_sel else "#FFF5F5"))
                    painter.drawRoundedRect(row_rect, 4, 4)

                painter.setPen(QColor("#64748B"))
                painter.setFont(numeric_font(10))
                painter.drawText(QRectF(l_start + 4, ry, 24, row_h), Qt.AlignmentFlag.AlignCenter, str(item.get("rank", idx + 1)))

                tk_w = 82 if (len(tk) >= 8 and "-" in tk) else 64
                painter.setPen(QColor("#2563EB" if is_sel else "#0F172A"))
                painter.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold if is_sel else QFont.Weight.DemiBold))
                painter.drawText(QRectF(l_start + 30, ry, tk_w, row_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tk)

                amt_x = l_start + 30 + tk_w
                painter.setPen(QColor("#DC2626"))
                painter.setFont(numeric_font(11, bold=True))
                painter.drawText(QRectF(amt_x, ry, max(40.0, side_w - (amt_x - l_start) - 4), row_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _fmt_usd(val, signed=True))

                bar_col = QColor("#EF4444")
                if self.active_ticker and not is_sel:
                    bar_col.setAlpha(75)
                elif is_sel or is_hov:
                    bar_col = QColor("#DC2626")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bar_col)
                painter.drawRoundedRect(bar_rect, 3, 3)

            # 右侧盈利行
            if idx < len(self.winners):
                item = self.winners[idx]
                tk = str(item["ticker"])
                val = float(item.get("floating_pnl") if item.get("floating_pnl") is not None else (item.get("realized_pnl") or 0.0))
                is_sel = self.active_ticker == tk.upper()
                is_hov = self._hover_ticker == tk
                bar_len = max(4.0, (abs(val) / max_abs) * half_w)

                row_rect = QRectF(r_start, ry + 1, side_w - 2, row_h - 2)
                bar_rect = QRectF(center_x, ry + 5, bar_len, row_h - 10)
                self._hit_rects.append((row_rect, item))
                self._hit_rects.append((bar_rect, item))

                if is_sel or is_hov:
                    painter.setPen(QPen(QColor("#93C5FD" if is_sel else "#E2E8F0"), 1))
                    painter.setBrush(QColor("#EFF6FF" if is_sel else "#F8FAFC"))
                    painter.drawRoundedRect(row_rect, 4, 4)

                painter.setPen(QColor("#64748B"))
                painter.setFont(numeric_font(10))
                painter.drawText(QRectF(r_start + 4, ry, 24, row_h), Qt.AlignmentFlag.AlignCenter, str(item.get("rank", idx + 1)))

                tk_w = 82 if (len(tk) >= 8 and "-" in tk) else 64
                painter.setPen(QColor("#2563EB" if is_sel else "#0F172A"))
                painter.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold if is_sel else QFont.Weight.DemiBold))
                painter.drawText(QRectF(r_start + 30, ry, tk_w, row_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tk)

                amt_x = r_start + 30 + tk_w
                painter.setPen(QColor("#16A34A"))
                painter.setFont(numeric_font(11, bold=True))
                painter.drawText(QRectF(amt_x, ry, max(40.0, side_w - (amt_x - r_start) - 4), row_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _fmt_usd(val, signed=True))

                bar_col = QColor("#22C55E")
                if self.active_ticker and not is_sel:
                    bar_col.setAlpha(75)
                elif is_sel or is_hov:
                    bar_col = QColor("#16A34A")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bar_col)
                painter.drawRoundedRect(bar_rect, 3, 3)


class OpenTop10Card(QFrame):
    """可折叠的「持有中股票盈亏贡献 Top N」横向发散条形图卡片。"""

    ticker_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("openPanelCard")
        self.setStyleSheet("""
            QFrame#openPanelCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QFrame#openPanelCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        self._raw_top10_data: dict[str, Any] = {}
        self._trend_points: list[dict[str, Any]] = []
        self._active_ticker: str = ""
        self._dim_mode: str = "stock"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        self.title_prefix = QLabel("持有中股票盈亏贡献 Top")
        self.title_prefix.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        hdr.addWidget(self.title_prefix)

        self.top_n_spin = QSpinBox()
        self.top_n_spin.setRange(1, 50)
        self.top_n_spin.setValue(5)
        self.top_n_spin.setFixedWidth(54)
        self.top_n_spin.setFixedHeight(22)
        self.top_n_spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.top_n_spin.setToolTip("输入要展示的股票数量（例如填 5 则展示 5 个最多收益与 5 个最多亏损股票）")
        self.top_n_spin.setStyleSheet("""
            QSpinBox {
                background: #FFFFFF;
                color: #2563EB;
                border: 1px solid #CBD5E1;
                border-radius: 4px;
                padding: 1px 2px;
                font-size: 12px;
                font-weight: 700;
            }
            QSpinBox:hover { border-color: #93C5FD; }
            QSpinBox:focus { border-color: #2563EB; }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 14px;
                border: none;
                background: #F1F5F9;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #E2E8F0;
            }
        """)
        self.top_n_spin.valueChanged.connect(self._on_top_n_changed)
        hdr.addWidget(self.top_n_spin)

        self.sub_lbl = QLabel("按浮动盈亏排序，可点击联动下方明细")
        self.sub_lbl.setStyleSheet("font-size: 11px; color: #94A3B8; margin-left: 2px;")
        hdr.addWidget(self.sub_lbl)

        dim_wrap = QFrame()
        dim_wrap.setStyleSheet("background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 6px;")
        dim_lay = QHBoxLayout(dim_wrap)
        dim_lay.setContentsMargins(2, 2, 2, 2)
        dim_lay.setSpacing(2)
        self.dim_group = QButtonGroup(self)
        self.dim_stock_btn = QPushButton("股票盈亏")
        self.dim_daily_btn = QPushButton("每日盈亏")
        for idx, btn in enumerate((self.dim_stock_btn, self.dim_daily_btn)):
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(20)
            btn.setStyleSheet("""
                QPushButton { background: transparent; border: none; border-radius: 4px; color: #64748B; font-size: 11px; font-weight: 600; padding: 0 8px; }
                QPushButton:checked { background: #2563EB; color: #FFFFFF; }
            """)
            self.dim_group.addButton(btn, idx)
            dim_lay.addWidget(btn)
        self.dim_stock_btn.setChecked(True)
        self.dim_group.idClicked.connect(self._on_dim_changed)
        hdr.addWidget(dim_wrap)

        hdr.addStretch()

        self.toggle_btn = QPushButton("⌄")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setFixedSize(26, 24)
        self.toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_btn.setToolTip("展开 / 收起 Top 贡献图")
        self.toggle_btn.setStyleSheet("""
            QPushButton { background: #F8FAFC; color: #475569; border: 1px solid #E2E8F0; border-radius: 5px; font-size: 13px; font-weight: 700; }
            QPushButton:hover { background: #EFF6FF; color: #2563EB; border-color: #93C5FD; }
        """)
        self.toggle_btn.clicked.connect(self._on_toggle)
        hdr.addWidget(self.toggle_btn)
        layout.addLayout(hdr)

        self.body_widget = QWidget(self)
        self.content_widget = self.body_widget
        b_layout = QVBoxLayout(self.body_widget)
        b_layout.setContentsMargins(0, 2, 0, 0)
        b_layout.setSpacing(6)

        # 顶部汇总提示（明确标注为当前筛选范围全部持仓记录汇总，非 Top 小计）
        sum_row = QHBoxLayout()
        self.loss_pill = QLabel("亏损贡献 (Top 5)  |  当前筛选全部亏损合计 —")
        self.loss_pill.setStyleSheet(
            "background: #FEF2F2; color: #B91C1C; border: 1px solid #FECACA; border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 600;"
        )
        sum_row.addWidget(self.loss_pill)
        sum_row.addStretch()
        self.win_pill = QLabel("盈利贡献 (Top 5)  |  当前筛选全部盈利合计 —")
        self.win_pill.setStyleSheet(
            "background: #F0FDF4; color: #15803D; border: 1px solid #BBF7D0; border-radius: 6px; padding: 3px 10px; font-size: 11px; font-weight: 600;"
        )
        sum_row.addWidget(self.win_pill)
        b_layout.addLayout(sum_row)

        self.chart = OpenTop10Chart(self.body_widget)
        self.chart.ticker_clicked.connect(self._on_chart_clicked)
        b_layout.addWidget(self.chart)

        layout.addWidget(self.body_widget)

    def _on_chart_clicked(self, key: str):
        if self._dim_mode == "stock":
            self.ticker_clicked.emit(key)

    def _on_dim_changed(self, btn_id: int):
        self._dim_mode = "daily" if btn_id == 1 else "stock"
        self._refresh_display()

    def _on_toggle(self):
        expanded = self.toggle_btn.isChecked()
        self.body_widget.setVisible(expanded)
        self.toggle_btn.setText("⌄" if expanded else "›")

    def _on_top_n_changed(self, _val: int):
        self._refresh_display()

    def _refresh_display(self):
        n = max(1, self.top_n_spin.value())
        if self._dim_mode == "daily":
            self.title_prefix.setText("每日盈亏极值 Top")
            self.sub_lbl.setText("按单日盈亏金额排序，展示区间最爽/最惨交易日")
            day_rows = []
            for p in self._trend_points:
                dp = p.get("daily_pnl")
                d_str = str(p.get("date") or "")
                if dp is not None and d_str:
                    day_rows.append({"ticker": d_str, "floating_pnl": float(dp)})
            wins = sorted([r for r in day_rows if r["floating_pnl"] > 0], key=lambda x: -x["floating_pnl"])
            losses = sorted([r for r in day_rows if r["floating_pnl"] < 0], key=lambda x: x["floating_pnl"])
            tot_win = sum(r["floating_pnl"] for r in wins)
            tot_loss = sum(r["floating_pnl"] for r in losses)
            top_w = [{**r, "rank": idx} for idx, r in enumerate(wins[:n], start=1)]
            top_l = [{**r, "rank": idx} for idx, r in enumerate(losses[:n], start=1)]
            self.loss_pill.setText(f"最惨交易日 (Top {n})  |  区间全部亏损日合计 {_fmt_usd(tot_loss, signed=True)} USD")
            self.win_pill.setText(f"最爽交易日 (Top {n})  |  区间全部盈利日合计 {_fmt_usd(tot_win, signed=True)} USD")
            self.chart.set_data({"winners": top_w, "losers": top_l}, active_ticker="")
            return

        self.title_prefix.setText("持有中股票盈亏贡献 Top")
        self.sub_lbl.setText("按浮动盈亏排序，可点击联动下方明细")
        if not hasattr(self, "_raw_top10_data") or not self._raw_top10_data:
            self.chart.set_data({}, active_ticker="")
            return
        tot_loss = self._raw_top10_data.get("total_floating_loss") if self._raw_top10_data.get("total_floating_loss") is not None else self._raw_top10_data.get("total_realized_loss")
        tot_win = self._raw_top10_data.get("total_floating_profit") if self._raw_top10_data.get("total_floating_profit") is not None else self._raw_top10_data.get("total_realized_profit")
        self.loss_pill.setText(f"亏损贡献 (Top {n})  |  当前筛选全部亏损合计 {_fmt_usd(tot_loss, signed=True)} USD")
        self.win_pill.setText(f"盈利贡献 (Top {n})  |  当前筛选全部盈利合计 {_fmt_usd(tot_win, signed=True)} USD")

        all_w = self._raw_top10_data.get("all_winners")
        if all_w is None:
            all_w = self._raw_top10_data.get("winners") or []
        all_l = self._raw_top10_data.get("all_losers")
        if all_l is None:
            all_l = self._raw_top10_data.get("losers") or []

        sliced_top10 = {
            **self._raw_top10_data,
            "winners": all_w[:n],
            "losers": all_l[:n],
        }
        self.chart.set_data(sliced_top10, active_ticker=getattr(self, "_active_ticker", ""))

    def set_data(self, top10: dict[str, Any], *, active_ticker: str = "", trend_points: list[dict[str, Any]] | None = None):
        self._raw_top10_data = dict(top10 or {})
        if trend_points is not None:
            self._trend_points = list(trend_points)
        self._active_ticker = (active_ticker or "").strip().upper()
        self._refresh_display()
