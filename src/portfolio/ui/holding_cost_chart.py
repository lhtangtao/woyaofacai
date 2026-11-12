"""持仓管理右侧抽屉专用：双 Y 轴交互式「持仓与成本轨迹」图表组件。"""
from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget


BLUE = "#2563EB"
ORANGE = "#F59E0B"
GRID_COLOR = "#E2E8F0"
TEXT_MUTED = "#64748B"
TEXT_HEADING = "#0F172A"
CARD_BG = "#FFFFFF"


def _nice_upper(max_val: float, *, default: float = 10.0) -> float:
    """将最大值向上取整到美观刻度上限。"""
    if max_val <= 0 or not math.isfinite(max_val):
        return default
    exp = math.floor(math.log10(max_val))
    base = 10 ** exp
    frac = max_val / base
    for mult in (1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0):
        if frac <= mult + 1e-9:
            return mult * base
    return 10.0 * base


def _fmt_qty_tick(val: float) -> str:
    if abs(val) < 1e-6:
        return "0"
    if abs(val - round(val)) < 1e-4:
        return f"{int(round(val)):,}"
    return f"{val:,.1f}"


def _fmt_cost_tick(val: float) -> str:
    if abs(val) < 1e-6:
        return "0"
    if val >= 100 or abs(val - round(val)) < 1e-4:
        return f"{int(round(val)):,}"
    return f"{val:,.2f}"


def _fmt_qty_value(val: float | None) -> str:
    if val is None:
        return "—"
    if abs(val - round(val)) < 1e-6:
        return f"{int(round(val)):,} 股"
    return f"{val:,.2f} 股"


def _fmt_cost_value(val: float | None) -> str:
    if val is None:
        return "—"
    return f"${val:,.2f}/股"


class _HoldingCostCanvas(QWidget):
    """内部绘图画布：双 Y 轴、真实时间比例 X 轴、阶梯股数、真实历史成本、悬停 Tooltip。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(210)

        self._dates: list[str] = []
        self._ordinals: list[int] = []
        self._quantities: list[float | None] = []
        self._costs: list[float | None] = []
        self._qty_max: float = 10.0
        self._cost_max: float = 10.0
        self._spans_years: bool = False

        self.show_qty: bool = True
        self.show_cost: bool = True
        self._hover_idx: int | None = None
        self._last_plot_rect = QRectF()
        self._point_xs: list[float] = []

    def set_series(
        self,
        dates: list[str],
        quantities: list[float | None],
        costs: list[float | None],
    ):
        parsed: list[tuple[str, int, float | None, float | None]] = []
        for idx, raw_d in enumerate(dates or ()):
            d_str = str(raw_d or "")[:10]
            try:
                d_obj = datetime.strptime(d_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            q = quantities[idx] if idx < len(quantities) else None
            c = costs[idx] if idx < len(costs) else None
            q_val = float(q) if q is not None else None
            c_val = float(c) if (c is not None and float(c) > 0) else None
            parsed.append((d_str, d_obj.toordinal(), q_val, c_val))

        # 按日期升序并去重（同日保留最后状态）
        by_day: dict[int, tuple[str, int, float | None, float | None]] = {}
        for item in parsed:
            by_day[item[1]] = item
        ordered = [by_day[k] for k in sorted(by_day.keys())]

        self._dates = [item[0] for item in ordered]
        self._ordinals = [item[1] for item in ordered]
        self._quantities = [item[2] for item in ordered]
        self._costs = [item[3] for item in ordered]

        years = {d[:4] for d in self._dates}
        self._spans_years = len(years) > 1

        max_q = max((q for q in self._quantities if q is not None and q > 0), default=0.0)
        max_c = max((c for c in self._costs if c is not None and c > 0), default=0.0)
        self._qty_max = _nice_upper(max_q * 1.08 if max_q > 0 else 0.0, default=10.0)
        self._cost_max = _nice_upper(max_c * 1.08 if max_c > 0 else 0.0, default=100.0)
        self._hover_idx = None
        self.update()

    def set_visibility(self, *, show_qty: bool, show_cost: bool):
        self.show_qty = bool(show_qty)
        self.show_cost = bool(show_cost)
        self.update()

    def leaveEvent(self, event):
        if self._hover_idx is not None:
            self._hover_idx = None
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if not self._dates or not self._point_xs or self._last_plot_rect.isEmpty():
            return
        pos = event.position()
        if not self._last_plot_rect.adjusted(-14, -10, 14, 16).contains(pos):
            if self._hover_idx is not None:
                self._hover_idx = None
                self.update()
            return
        px = pos.x()
        best_idx = min(range(len(self._point_xs)), key=lambda i: abs(self._point_xs[i] - px))
        if best_idx != self._hover_idx:
            self._hover_idx = best_idx
            self.update()

    def _compute_x_positions(self, plot: QRectF) -> list[float]:
        n = len(self._ordinals)
        if n == 0:
            return []
        if n == 1:
            return [plot.left() + plot.width() * 0.5]
        min_ord = self._ordinals[0]
        max_ord = self._ordinals[-1]
        span = max(1, max_ord - min_ord)
        pad = min(8.0, plot.width() * 0.03)
        usable = max(1.0, plot.width() - 2 * pad)
        return [plot.left() + pad + ((o - min_ord) / span) * usable for o in self._ordinals]

    def _pick_x_ticks(self, xs: list[float], plot: QRectF, fm: QFontMetrics) -> list[tuple[int, float, str]]:
        n = len(self._dates)
        if n == 0:
            return []

        def fmt_label(d_str: str) -> str:
            return d_str[2:] if self._spans_years else d_str[5:]

        if n == 1:
            return [(0, xs[0], fmt_label(self._dates[0]))]

        sample_w = fm.horizontalAdvance("26-12-31" if self._spans_years else "12-31")
        min_gap = max(56.0, sample_w + 18.0)
        max_ticks = max(2, min(7, int(plot.width() // min_gap) + 1))

        # 从真实时间等分点选取最接近的数据点索引，保证首尾尽量展示且不重叠
        min_ord = self._ordinals[0]
        max_ord = self._ordinals[-1]
        chosen_indices: list[int] = [0]
        if max_ticks > 2 and n > 2:
            for step in range(1, max_ticks - 1):
                target_ord = min_ord + (max_ord - min_ord) * (step / (max_ticks - 1))
                nearest = min(range(n), key=lambda i: abs(self._ordinals[i] - target_ord))
                if nearest not in chosen_indices and xs[nearest] - xs[chosen_indices[-1]] >= min_gap:
                    chosen_indices.append(nearest)
        if n - 1 not in chosen_indices:
            if xs[n - 1] - xs[chosen_indices[-1]] >= min_gap:
                chosen_indices.append(n - 1)
            elif len(chosen_indices) == 1:
                chosen_indices.append(n - 1)
            else:
                # 替换倒数第二个，确保尾日期始终可见
                if xs[n - 1] - xs[chosen_indices[-2]] >= min_gap:
                    chosen_indices[-1] = n - 1

        return [(i, xs[i], fmt_label(self._dates[i])) for i in chosen_indices]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        painter.fillRect(self.rect(), QColor(CARD_BG))

        if not self._dates:
            painter.setPen(QColor(TEXT_MUTED))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "暂无持仓与成本历史记录")
            self._last_plot_rect = QRectF()
            self._point_xs = []
            return

        axis_font = QFont(".AppleSystemUIFont", 10)
        painter.setFont(axis_font)
        fm = QFontMetrics(axis_font)

        left_pad = max(46.0, fm.horizontalAdvance(_fmt_qty_tick(self._qty_max)) + 14.0)
        right_pad = max(52.0, fm.horizontalAdvance(_fmt_cost_tick(self._cost_max)) + 14.0)
        top_pad = 26.0
        bottom_pad = 28.0
        plot = QRectF(left_pad, top_pad, max(40.0, w - left_pad - right_pad), max(40.0, h - top_pad - bottom_pad))
        self._last_plot_rect = plot

        # 顶部双轴单位标题
        hdr_font = QFont(".AppleSystemUIFont", 10, QFont.Weight.DemiBold)
        painter.setFont(hdr_font)
        if self.show_qty:
            painter.setPen(QColor(BLUE))
            painter.drawText(QRectF(4, 4, left_pad + 90, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "持仓股数 (股)")
        if self.show_cost:
            painter.setPen(QColor(ORANGE))
            painter.drawText(QRectF(w - right_pad - 110, 4, right_pad + 106, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "成本价 (USD/股)")

        # 5 条水平网格线与左右 Y 轴刻度（共用绘图区顶部与底部）
        painter.setFont(axis_font)
        steps = 4
        for i in range(steps + 1):
            ratio_y = i / steps  # 0 at bottom, 1 at top
            y = plot.bottom() - ratio_y * plot.height()
            pen = QPen(QColor("#CBD5E1" if i == 0 else GRID_COLOR), 1.0)
            if i > 0:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

            if self.show_qty:
                painter.setPen(QColor(BLUE))
                lbl = _fmt_qty_tick(self._qty_max * ratio_y)
                painter.drawText(QRectF(2, y - 9, left_pad - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lbl)

            if self.show_cost:
                painter.setPen(QColor(ORANGE))
                lbl = _fmt_cost_tick(self._cost_max * ratio_y)
                painter.drawText(QRectF(plot.right() + 6, y - 9, right_pad - 8, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, lbl)

        xs = self._compute_x_positions(plot)
        self._point_xs = xs

        # X 轴自适应日期刻度
        painter.setPen(QColor(TEXT_MUTED))
        ticks = self._pick_x_ticks(xs, plot, fm)
        for idx, tx, label in ticks:
            tw = fm.horizontalAdvance(label) + 6.0
            rx = max(plot.left() - 6.0, min(plot.right() + 6.0 - tw, tx - tw / 2.0))
            painter.drawText(QRectF(rx, plot.bottom() + 6, tw, 18), Qt.AlignmentFlag.AlignCenter, label)

        if not self.show_qty and not self.show_cost:
            painter.setPen(QColor(TEXT_MUTED))
            painter.setFont(QFont(".AppleSystemUIFont", 11))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "已隐藏全部曲线，点击下方图例可恢复显示")
            return

        def y_qty(q: float) -> float:
            clamped = max(0.0, min(self._qty_max, q))
            return plot.bottom() - (clamped / self._qty_max) * plot.height()

        def y_cost(c: float) -> float:
            clamped = max(0.0, min(self._cost_max, c))
            return plot.bottom() - (clamped / self._cost_max) * plot.height()

        # 1. 蓝色：持仓股数（阶梯折线 + 柔和填充）
        if self.show_qty:
            qty_pts = [(xs[i], y_qty(q)) for i, q in enumerate(self._quantities) if q is not None]
            if len(qty_pts) == 1:
                px, py = qty_pts[0]
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(BLUE))
                painter.drawEllipse(QPointF(px, py), 4.0, 4.0)
            elif len(qty_pts) >= 2:
                line_path = QPainterPath(QPointF(qty_pts[0][0], qty_pts[0][1]))
                for k in range(1, len(qty_pts)):
                    prev_x, prev_y = qty_pts[k - 1]
                    cur_x, cur_y = qty_pts[k]
                    line_path.lineTo(cur_x, prev_y)
                    line_path.lineTo(cur_x, cur_y)

                area_path = QPainterPath(line_path)
                area_path.lineTo(qty_pts[-1][0], plot.bottom())
                area_path.lineTo(qty_pts[0][0], plot.bottom())
                area_path.closeSubpath()

                grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
                grad.setColorAt(0.0, QColor(37, 99, 235, 42))
                grad.setColorAt(1.0, QColor(37, 99, 235, 4))
                painter.fillPath(area_path, grad)

                painter.setPen(QPen(QColor(BLUE), 2.0))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(line_path)

        # 2. 橙色：持仓成本价（阶梯折线，清仓归零时与蓝色持仓股数统一直线归零并保持连贯）
        if self.show_cost:
            cost_pts: list[tuple[float, float]] = []
            for i, c in enumerate(self._costs):
                if c is not None:
                    cost_pts.append((xs[i], y_cost(c)))
                elif self._quantities[i] is not None and self._quantities[i] <= 0:
                    cost_pts.append((xs[i], plot.bottom()))

            painter.setPen(QPen(QColor(ORANGE), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if len(cost_pts) == 1:
                painter.setBrush(QColor(ORANGE))
                painter.drawEllipse(QPointF(cost_pts[0][0], cost_pts[0][1]), 3.5, 3.5)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            elif len(cost_pts) >= 2:
                c_path = QPainterPath(QPointF(cost_pts[0][0], cost_pts[0][1]))
                for k in range(1, len(cost_pts)):
                    prev_x, prev_y = cost_pts[k - 1]
                    cur_x, cur_y = cost_pts[k]
                    c_path.lineTo(cur_x, prev_y)
                    c_path.lineTo(cur_x, cur_y)
                painter.drawPath(c_path)

        # 3. 鼠标悬停垂直辅助线、高亮数据点与 Tooltip
        if self._hover_idx is not None and 0 <= self._hover_idx < len(self._dates):
            hi = self._hover_idx
            hx = xs[hi]
            guide_pen = QPen(QColor("#94A3B8"), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(guide_pen)
            painter.drawLine(QPointF(hx, plot.top()), QPointF(hx, plot.bottom()))

            hv_qty = self._quantities[hi]
            hv_cost = self._costs[hi]

            if self.show_qty and hv_qty is not None:
                hy = y_qty(hv_qty)
                painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
                painter.setBrush(QColor(BLUE))
                painter.drawEllipse(QPointF(hx, hy), 4.5, 4.5)

            if self.show_cost and hv_cost is not None:
                hy = y_cost(hv_cost)
                painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
                painter.setBrush(QColor(ORANGE))
                painter.drawEllipse(QPointF(hx, hy), 4.5, 4.5)

            lines: list[tuple[str, str, str]] = [("", f"日期：{self._dates[hi]}", TEXT_HEADING)]
            if self.show_qty:
                lines.append((BLUE, f"持仓股数：{_fmt_qty_value(hv_qty)}", TEXT_HEADING))
            if self.show_cost:
                lines.append((ORANGE, f"持仓成本价：{_fmt_cost_value(hv_cost)}", TEXT_HEADING))

            tip_font = QFont(".AppleSystemUIFont", 11)
            tip_bold = QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold)
            tfm = QFontMetrics(tip_bold)
            tip_w = max(tfm.horizontalAdvance(text) for _, text, _ in lines) + 28.0
            tip_h = 14.0 + len(lines) * 20.0

            tx = hx + 12.0
            if tx + tip_w > w - 6.0:
                tx = hx - tip_w - 12.0
            tx = max(6.0, min(w - tip_w - 6.0, tx))
            ty = plot.top() + 8.0
            if ty + tip_h > plot.bottom() - 4.0:
                ty = max(4.0, plot.bottom() - tip_h - 4.0)

            tip_rect = QRectF(tx, ty, tip_w, tip_h)
            painter.setPen(QPen(QColor("#CBD5E1"), 1.0))
            painter.setBrush(QColor(255, 255, 255, 245))
            painter.drawRoundedRect(tip_rect, 8.0, 8.0)

            for line_i, (dot_col, text, fg) in enumerate(lines):
                ly = ty + 8.0 + line_i * 20.0
                painter.setFont(tip_bold if line_i == 0 else tip_font)
                text_x = tx + 10.0
                if dot_col:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(dot_col))
                    painter.drawEllipse(QPointF(tx + 14.0, ly + 9.0), 3.5, 3.5)
                    text_x = tx + 22.0
                painter.setPen(QColor(fg))
                painter.drawText(QRectF(text_x, ly, tip_w - (text_x - tx) - 6.0, 18.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class HoldingCostDualAxisChart(QWidget):
    """持仓与成本轨迹组件：包含双轴画布与底部可点击交互图例。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.canvas = _HoldingCostCanvas(self)
        layout.addWidget(self.canvas, 1)

        legend_row = QHBoxLayout()
        legend_row.setContentsMargins(0, 0, 0, 2)
        legend_row.setSpacing(12)
        legend_row.addStretch(1)

        self.qty_legend_btn = QPushButton("●  持仓股数")
        self.qty_legend_btn.setCheckable(True)
        self.qty_legend_btn.setChecked(True)
        self.qty_legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.qty_legend_btn.setToolTip("点击隐藏或恢复「持仓股数」曲线")
        self.qty_legend_btn.clicked.connect(self._on_legend_toggled)
        legend_row.addWidget(self.qty_legend_btn)

        self.cost_legend_btn = QPushButton("●  持仓成本价")
        self.cost_legend_btn.setCheckable(True)
        self.cost_legend_btn.setChecked(True)
        self.cost_legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cost_legend_btn.setToolTip("点击隐藏或恢复「持仓成本价」曲线")
        self.cost_legend_btn.clicked.connect(self._on_legend_toggled)
        legend_row.addWidget(self.cost_legend_btn)

        legend_row.addStretch(1)
        layout.addLayout(legend_row)
        self._sync_legend_styles()

    def set_data(self, holding_analysis: dict[str, Any] | None):
        data = holding_analysis or {}
        self.canvas.set_series(
            list(data.get("dates") or ()),
            list(data.get("quantities") or ()),
            list(data.get("costs") or ()),
        )

    def _on_legend_toggled(self):
        self._sync_legend_styles()
        self.canvas.set_visibility(
            show_qty=self.qty_legend_btn.isChecked(),
            show_cost=self.cost_legend_btn.isChecked(),
        )

    def _sync_legend_styles(self):
        for btn, label, color in (
            (self.qty_legend_btn, "持仓股数", BLUE),
            (self.cost_legend_btn, "持仓成本价", ORANGE),
        ):
            active = btn.isChecked()
            btn.setText(f"●  {label}" if active else f"○  {label}（已隐藏）")
            if active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #F8FAFC;
                        color: {color};
                        border: 1px solid #CBD5E1;
                        border-radius: 14px;
                        padding: 4px 14px;
                        font-size: 12px;
                        font-weight: 600;
                        min-height: 26px;
                    }}
                    QPushButton:hover {{
                        background: #EFF6FF;
                        border-color: {color};
                    }}
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #F1F5F9;
                        color: #94A3B8;
                        border: 1px dashed #CBD5E1;
                        border-radius: 14px;
                        padding: 4px 14px;
                        font-size: 12px;
                        font-weight: 500;
                        min-height: 26px;
                    }
                    QPushButton:hover {
                        background: #E2E8F0;
                        color: #475569;
                    }
                """)
