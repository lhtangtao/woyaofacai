"""持仓管理右侧抽屉专用：「收益分析」双轴组合图（金额大图 + 收益率小图）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
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
    QVBoxLayout,
    QWidget,
)

BLUE = "#2563EB"
ORANGE = "#F59E0B"
PROFIT_GREEN = "#16A34A"
LOSS_RED = "#DC2626"
GRID_COLOR = "#E2E8F0"
ZERO_LINE_COLOR = "#94A3B8"
TEXT_MUTED = "#64748B"
TEXT_HEADING = "#0F172A"
CARD_BG = "#FFFFFF"


def _nice_step(span: float, steps: int = 4) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(1, steps)
    exp = math.floor(math.log10(raw))
    base = 10 ** exp
    frac = raw / base
    for mult in (1.0, 1.5, 2.0, 2.5, 4.0, 5.0, 8.0, 10.0):
        if frac <= mult + 1e-9:
            return mult * base
    return 10.0 * base


def _compute_signed_bounds(values: list[float | None], *, default_span: float = 10.0) -> tuple[float, float]:
    valid = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not valid:
        return -default_span, default_span
    raw_min = min(0.0, min(valid))
    raw_max = max(0.0, max(valid))
    if raw_min == 0.0 and raw_max == 0.0:
        return -default_span, default_span
    pad_up = raw_max * 1.12 if raw_max > 0 else 0.0
    pad_dn = raw_min * 1.12 if raw_min < 0 else 0.0
    step = _nice_step(pad_up - pad_dn, steps=4)
    nice_max = math.ceil(pad_up / step) * step if pad_up > 0 else 0.0
    nice_min = math.floor(pad_dn / step) * step if pad_dn < 0 else 0.0
    if nice_max == nice_min:
        nice_max = step
    return nice_min, nice_max


def _align_zero_bounds(
    left_bounds: tuple[float, float],
    right_bounds: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """在不裁切任何真实数据的前提下，尽量使左右双轴的 0 基准线处于同一水平高度。"""
    l_min, l_max = left_bounds
    r_min, r_max = right_bounds
    l_span = max(1e-9, l_max - l_min)
    r_span = max(1e-9, r_max - r_min)

    l_neg_frac = -l_min / l_span
    r_neg_frac = -r_min / r_span
    target_neg_frac = max(l_neg_frac, r_neg_frac)
    target_pos_frac = max(1.0 - l_neg_frac, 1.0 - r_neg_frac)

    # 若两侧都存在正负值或均包含0，按公共比例扩展较小一侧使0线对齐
    def expand_to_match(b_min: float, b_max: float) -> tuple[float, float]:
        if target_neg_frac <= 0.0:
            return 0.0, max(b_max, 1e-6)
        if target_pos_frac <= 0.0:
            return min(b_min, -1e-6), 0.0
        cur_span = max(1e-9, b_max - b_min)
        cur_neg = -b_min / cur_span
        if cur_neg < target_neg_frac:
            # 向下扩展 b_min
            new_min = -b_max * (target_neg_frac / max(1e-9, 1.0 - target_neg_frac))
            return min(b_min, new_min), b_max
        if cur_neg > target_neg_frac:
            # 向上扩展 b_max
            new_max = -b_min * ((1.0 - target_neg_frac) / max(1e-9, target_neg_frac))
            return b_min, max(b_max, new_max)
        return b_min, b_max

    return expand_to_match(l_min, l_max), expand_to_match(r_min, r_max)


def _fmt_usd_axis(val: float) -> str:
    if abs(val) < 1e-6:
        return "0"
    if abs(val) >= 1000:
        return f"{val:,.0f}"
    if abs(val - round(val)) < 1e-3:
        return f"{int(round(val)):,}"
    return f"{val:,.1f}"


def _fmt_pct_axis(val: float) -> str:
    pct = val * 100.0
    if abs(pct) < 1e-4:
        return "0.0%"
    if abs(pct) >= 20:
        return f"{pct:.0f}%"
    return f"{pct:.1f}%"


def _fmt_usd_tip(val: float | None) -> str:
    if val is None:
        return "—"
    v = float(val)
    if abs(v) < 0.005:
        return "$0.00"
    return f"{'+' if v > 0 else '-'}${abs(v):,.2f}"


def _fmt_pct_tip(val: float | None) -> str:
    if val is None:
        return "—"
    pct = float(val) * 100.0
    if abs(pct) < 0.005:
        return "0.00%"
    return f"{pct:+.2f}%"


class _PnlComboCanvas(QWidget):
    """柱状图（左轴） + 双折线（右轴）组合画布，支持联动悬停与独立隐藏系列。"""

    hoverIndexChanged = Signal(object)

    def __init__(self, *, is_rate: bool = False, fill_cumulative: bool = True, parent=None):
        super().__init__(parent)
        self.is_rate = is_rate
        self.fill_cumulative = fill_cumulative
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._dates: list[str] = []
        self._ordinals: list[int] = []
        self._bar_vals: list[float | None] = []
        self._cum_vals: list[float | None] = []
        self._flt_vals: list[float | None] = []
        self._spans_years: bool = False

        self._left_bounds: tuple[float, float] = (-10.0, 10.0)
        self._right_bounds: tuple[float, float] = (-10.0, 10.0)

        self.show_bar: bool = True
        self.show_cum: bool = True
        self.show_flt: bool = True

        self._hover_idx: int | None = None
        self._show_tooltip: bool = False
        self._last_plot_rect = QRectF()
        self._point_xs: list[float] = []

        self.left_title = "当日收益率" if is_rate else "当日盈亏 (USD)"
        self.right_title = "累计 / 浮动收益率" if is_rate else "累计 / 浮动盈亏 (USD)"
        self.bar_label = "当日收益率" if is_rate else "当日盈亏"
        self.cum_label = "累计收益率" if is_rate else "累计盈亏"
        self.flt_label = "持仓浮动收益率" if is_rate else "持仓浮动盈亏"

    def set_series(
        self,
        dates: list[str],
        bars: list[float | None],
        cums: list[float | None],
        flts: list[float | None],
    ):
        self._dates = list(dates)
        self._ordinals = []
        for d_str in self._dates:
            try:
                self._ordinals.append(datetime.strptime(d_str[:10], "%Y-%m-%d").date().toordinal())
            except ValueError:
                self._ordinals.append(len(self._ordinals))
        self._bar_vals = [float(v) if v is not None else None for v in bars]
        self._cum_vals = [float(v) if v is not None else None for v in cums]
        self._flt_vals = [float(v) if v is not None else None for v in flts]
        self._spans_years = len({d[:4] for d in self._dates}) > 1

        default_span = 0.05 if self.is_rate else 100.0
        raw_left = _compute_signed_bounds(self._bar_vals, default_span=default_span)
        raw_right = _compute_signed_bounds(self._cum_vals + self._flt_vals, default_span=default_span)
        self._left_bounds, self._right_bounds = _align_zero_bounds(raw_left, raw_right)
        self._hover_idx = None
        self._show_tooltip = False
        self.update()

    def set_visibility(self, *, show_bar: bool, show_cum: bool, show_flt: bool):
        self.show_bar = bool(show_bar)
        self.show_cum = bool(show_cum)
        self.show_flt = bool(show_flt)
        self.update()

    def set_external_hover(self, idx: int | None):
        if idx == self._hover_idx and not self._show_tooltip:
            return
        self._hover_idx = idx
        self._show_tooltip = False
        self.update()

    def leaveEvent(self, event):
        if self._hover_idx is not None or self._show_tooltip:
            self._hover_idx = None
            self._show_tooltip = False
            self.hoverIndexChanged.emit(None)
            self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if not self._dates or not self._point_xs or self._last_plot_rect.isEmpty():
            return
        pos = event.position()
        if not self._last_plot_rect.adjusted(-14, -10, 14, 16).contains(pos):
            if self._hover_idx is not None or self._show_tooltip:
                self._hover_idx = None
                self._show_tooltip = False
                self.hoverIndexChanged.emit(None)
                self.update()
            return
        px = pos.x()
        best_idx = min(range(len(self._point_xs)), key=lambda i: abs(self._point_xs[i] - px))
        if best_idx != self._hover_idx or not self._show_tooltip:
            self._hover_idx = best_idx
            self._show_tooltip = True
            self.hoverIndexChanged.emit(best_idx)
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
        pad = min(8.0, plot.width() * 0.025)
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

        min_ord = self._ordinals[0]
        max_ord = self._ordinals[-1]
        chosen: list[int] = [0]
        if max_ticks > 2 and n > 2:
            for step in range(1, max_ticks - 1):
                target_ord = min_ord + (max_ord - min_ord) * (step / (max_ticks - 1))
                nearest = min(range(n), key=lambda i: abs(self._ordinals[i] - target_ord))
                if nearest not in chosen and xs[nearest] - xs[chosen[-1]] >= min_gap:
                    chosen.append(nearest)
        if n - 1 not in chosen:
            if xs[n - 1] - xs[chosen[-1]] >= min_gap:
                chosen.append(n - 1)
            elif len(chosen) == 1:
                chosen.append(n - 1)
            elif xs[n - 1] - xs[chosen[-2]] >= min_gap:
                chosen[-1] = n - 1
        return [(i, xs[i], fmt_label(self._dates[i])) for i in chosen]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())
        h = float(self.height())
        painter.fillRect(self.rect(), QColor(CARD_BG))

        if not self._dates:
            painter.setPen(QColor(TEXT_MUTED))
            painter.setFont(QFont(".AppleSystemUIFont", 11))
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "暂无历史收益数据")
            self._last_plot_rect = QRectF()
            self._point_xs = []
            return

        axis_font = QFont(".AppleSystemUIFont", 10)
        painter.setFont(axis_font)
        fm = QFontMetrics(axis_font)
        fmt_axis = _fmt_pct_axis if self.is_rate else _fmt_usd_axis

        l_min, l_max = self._left_bounds
        r_min, r_max = self._right_bounds

        left_pad = max(48.0, fm.horizontalAdvance(fmt_axis(l_max)) + 12.0, fm.horizontalAdvance(fmt_axis(l_min)) + 12.0)
        right_pad = max(52.0, fm.horizontalAdvance(fmt_axis(r_max)) + 12.0, fm.horizontalAdvance(fmt_axis(r_min)) + 12.0)
        top_pad = 24.0
        bottom_pad = 26.0
        plot = QRectF(left_pad, top_pad, max(40.0, w - left_pad - right_pad), max(40.0, h - top_pad - bottom_pad))
        self._last_plot_rect = plot

        # 顶部双轴单位标题
        hdr_font = QFont(".AppleSystemUIFont", 10, QFont.Weight.DemiBold)
        painter.setFont(hdr_font)
        if self.show_bar:
            painter.setPen(QColor(BLUE))
            painter.drawText(QRectF(4, 2, left_pad + 100, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.left_title)
        if self.show_cum or self.show_flt:
            painter.setPen(QColor(ORANGE))
            painter.drawText(QRectF(w - right_pad - 120, 2, right_pad + 116, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.right_title)

        def y_left(val: float) -> float:
            return plot.bottom() - ((val - l_min) / max(1e-9, l_max - l_min)) * plot.height()

        def y_right(val: float) -> float:
            return plot.bottom() - ((val - r_min) / max(1e-9, r_max - r_min)) * plot.height()

        # 5 条水平网格线与左右 Y 轴刻度
        painter.setFont(axis_font)
        steps = 4
        for i in range(steps + 1):
            frac = i / steps
            y = plot.bottom() - frac * plot.height()
            pen = QPen(QColor(GRID_COLOR), 1.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

            if self.show_bar:
                painter.setPen(QColor(BLUE))
                lv = l_min + frac * (l_max - l_min)
                painter.drawText(QRectF(2, y - 9, left_pad - 8, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, fmt_axis(lv))
            if self.show_cum or self.show_flt:
                painter.setPen(QColor(ORANGE))
                rv = r_min + frac * (r_max - r_min)
                painter.drawText(QRectF(plot.right() + 6, y - 9, right_pad - 8, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fmt_axis(rv))

        # 0 基准线（左右轴对齐同一水平线）
        y0_left = y_left(0.0)
        painter.setPen(QPen(QColor(ZERO_LINE_COLOR), 1.0))
        painter.drawLine(QPointF(plot.left(), y0_left), QPointF(plot.right(), y0_left))

        xs = self._compute_x_positions(plot)
        self._point_xs = xs

        # X 轴日期刻度
        painter.setPen(QColor(TEXT_MUTED))
        for _, tx, label in self._pick_x_ticks(xs, plot, fm):
            tw = fm.horizontalAdvance(label) + 6.0
            rx = max(plot.left() - 6.0, min(plot.right() + 6.0 - tw, tx - tw / 2.0))
            painter.drawText(QRectF(rx, plot.bottom() + 5, tw, 18), Qt.AlignmentFlag.AlignCenter, label)

        if not (self.show_bar or self.show_cum or self.show_flt):
            painter.setPen(QColor(TEXT_MUTED))
            painter.setFont(QFont(".AppleSystemUIFont", 11))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "已隐藏全部系列，点击下方图例可恢复显示")
            return

        y0_right = y_right(0.0)

        # 1. 先绘制累计盈亏面积淡色填充（置于底层，不遮挡柱状图与折线）
        if self.fill_cumulative and self.show_cum:
            cum_pts = [(xs[i], self._cum_vals[i], y_right(self._cum_vals[i])) for i in range(len(xs)) if self._cum_vals[i] is not None]
            if len(cum_pts) >= 2:
                for k in range(len(cum_pts) - 1):
                    x1, v1, y1 = cum_pts[k]
                    x2, v2, y2 = cum_pts[k + 1]
                    if (v1 >= 0 and v2 >= 0) or (v1 <= 0 and v2 <= 0):
                        poly = QPainterPath(QPointF(x1, y0_right))
                        poly.lineTo(x1, y1)
                        poly.lineTo(x2, y2)
                        poly.lineTo(x2, y0_right)
                        poly.closeSubpath()
                        fill_col = QColor(22, 163, 74, 32) if (v1 + v2) >= 0 else QColor(220, 38, 38, 32)
                        painter.fillPath(poly, fill_col)
                    else:
                        t = abs(v1) / max(1e-9, abs(v1) + abs(v2))
                        xm = x1 + (x2 - x1) * t
                        for sx1, sx2, sy1, sy2, pos in (
                            (x1, xm, y1, y0_right, v1 >= 0),
                            (xm, x2, y0_right, y2, v2 >= 0),
                        ):
                            poly = QPainterPath(QPointF(sx1, y0_right))
                            poly.lineTo(sx1, sy1)
                            poly.lineTo(sx2, sy2)
                            poly.lineTo(sx2, y0_right)
                            poly.closeSubpath()
                            painter.fillPath(poly, QColor(22, 163, 74, 32) if pos else QColor(220, 38, 38, 32))

        # 2. 绘制当日柱状图（左轴）
        if self.show_bar:
            n = len(xs)
            bar_w = max(2.0, min(8.0, (plot.width() / max(1, n)) * 0.58))
            for i, val in enumerate(self._bar_vals):
                if val is None or abs(val) < 1e-12:
                    continue
                yv = y_left(val)
                top_y = min(y0_left, yv)
                bh = max(1.0, abs(yv - y0_left))
                col = QColor(22, 163, 74, 185) if val >= 0 else QColor(220, 38, 38, 185)
                painter.fillRect(QRectF(xs[i] - bar_w / 2.0, top_y, bar_w, bh), col)

        # Helper: draw polyline broken cleanly on None gaps
        def draw_line_series(vals: list[float | None], color_hex: str):
            segs: list[list[tuple[float, float]]] = []
            cur: list[tuple[float, float]] = []
            for i, v in enumerate(vals):
                if v is None:
                    if cur:
                        segs.append(cur)
                        cur = []
                else:
                    cur.append((xs[i], y_right(v)))
            if cur:
                segs.append(cur)
            painter.setPen(QPen(QColor(color_hex), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for seg in segs:
                if len(seg) == 1:
                    painter.setBrush(QColor(color_hex))
                    painter.drawEllipse(QPointF(seg[0][0], seg[0][1]), 3.5, 3.5)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                elif len(seg) >= 2:
                    path = QPainterPath(QPointF(seg[0][0], seg[0][1]))
                    for px, py in seg[1:]:
                        path.lineTo(px, py)
                    painter.drawPath(path)

        # 3. 绘制橙色浮动线 & 蓝色累计线（右轴）
        if self.show_flt:
            draw_line_series(self._flt_vals, ORANGE)
        if self.show_cum:
            draw_line_series(self._cum_vals, BLUE)

        # 4. 悬停辅助线、数据点高亮及 Tooltip
        if self._hover_idx is not None and 0 <= self._hover_idx < len(self._dates):
            hi = self._hover_idx
            hx = xs[hi]
            painter.setPen(QPen(QColor("#64748B"), 1.0, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(hx, plot.top()), QPointF(hx, plot.bottom()))

            vb = self._bar_vals[hi]
            vc = self._cum_vals[hi]
            vf = self._flt_vals[hi]

            if self.show_flt and vf is not None:
                painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
                painter.setBrush(QColor(ORANGE))
                painter.drawEllipse(QPointF(hx, y_right(vf)), 4.5, 4.5)
            if self.show_cum and vc is not None:
                painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
                painter.setBrush(QColor(BLUE))
                painter.drawEllipse(QPointF(hx, y_right(vc)), 4.5, 4.5)

            if self._show_tooltip:
                fmt_tip = _fmt_pct_tip if self.is_rate else _fmt_usd_tip
                lines: list[tuple[str, str]] = [("", self._dates[hi])]
                if self.show_bar:
                    bar_dot = PROFIT_GREEN if (vb is not None and vb >= 0) else LOSS_RED if vb is not None else TEXT_MUTED
                    lines.append((bar_dot, f"{self.bar_label}：{fmt_tip(vb)}"))
                if self.show_cum:
                    lines.append((BLUE, f"{self.cum_label}：{fmt_tip(vc)}"))
                if self.show_flt:
                    lines.append((ORANGE, f"{self.flt_label}：{fmt_tip(vf)}"))

                tip_font = QFont(".AppleSystemUIFont", 11)
                tip_bold = QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold)
                tfm = QFontMetrics(tip_bold)
                tip_w = max(tfm.horizontalAdvance(t) for _, t in lines) + 28.0
                tip_h = 14.0 + len(lines) * 19.0
                tx = hx + 12.0
                if tx + tip_w > w - 6.0:
                    tx = hx - tip_w - 12.0
                tx = max(6.0, min(w - tip_w - 6.0, tx))
                ty = max(4.0, min(plot.bottom() - tip_h - 4.0, plot.top() + 6.0))

                painter.setPen(QPen(QColor("#CBD5E1"), 1.0))
                painter.setBrush(QColor(255, 255, 255, 245))
                painter.drawRoundedRect(QRectF(tx, ty, tip_w, tip_h), 8.0, 8.0)

                for li, (dot_col, text) in enumerate(lines):
                    ly = ty + 7.0 + li * 19.0
                    painter.setFont(tip_bold if li == 0 else tip_font)
                    text_x = tx + 10.0
                    if dot_col:
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.setBrush(QColor(dot_col))
                        painter.drawEllipse(QPointF(tx + 14.0, ly + 9.0), 3.5, 3.5)
                        text_x = tx + 22.0
                    painter.setPen(QColor(TEXT_HEADING))
                    painter.drawText(QRectF(text_x, ly, tip_w - (text_x - tx) - 6.0, 18.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class PnlAnalysisChartsWidget(QWidget):
    """收益分析双轴组合图面板：共享时间范围 + 历史盈亏金额大图 (60%) + 历史收益率小图 (40%)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._payload: dict[str, Any] = {}
        self._window_key: str = "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 顶部公共时间范围切换栏
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(6)

        self.note_lbl = QLabel("展示每日盈亏、累计盈亏与持仓浮动盈亏走势")
        self.note_lbl.setStyleSheet("color: #64748B; font-size: 11px;")
        self.note_lbl.setWordWrap(True)
        top_bar.addWidget(self.note_lbl, 1)

        self._win_btns: dict[str, QPushButton] = {}
        for key, label in (("1m", "近1月"), ("3m", "近3月"), ("6m", "近6月"), ("ytd", "今年"), ("all", "全部")):
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setChecked(key == "all")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self.set_time_window(k))
            self._win_btns[key] = btn
            top_bar.addWidget(btn)
        self._sync_window_styles()
        layout.addLayout(top_bar)

        # 图一：历史盈亏金额走势 (约 60% 高度)
        amount_card = QFrame(self)
        amount_card.setObjectName("pnlChartCard")
        amount_card.setStyleSheet("QFrame#pnlChartCard { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; }")
        ac_layout = QVBoxLayout(amount_card)
        ac_layout.setContentsMargins(12, 10, 12, 8)
        ac_layout.setSpacing(6)

        ac_title = QLabel("历史盈亏金额走势 (USD)")
        ac_title.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        ac_layout.addWidget(ac_title)

        self.amount_canvas = _PnlComboCanvas(is_rate=False, fill_cumulative=True, parent=amount_card)
        self.amount_canvas.setMinimumHeight(230)
        ac_layout.addWidget(self.amount_canvas, 1)

        self.amt_bar_btn, self.amt_cum_btn, self.amt_flt_btn = self._build_legend_row(
            ac_layout,
            ("当日盈亏 (左轴)", PROFIT_GREEN, True),
            ("累计盈亏 (右轴)", BLUE, False),
            ("持仓浮动盈亏 (右轴)", ORANGE, False),
            self._sync_amount_visibility,
        )
        layout.addWidget(amount_card, 3)

        # 图二：历史收益率走势 (约 40% 高度)
        rate_card = QFrame(self)
        rate_card.setObjectName("pnlRateCard")
        rate_card.setStyleSheet("QFrame#pnlRateCard { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; }")
        rc_layout = QVBoxLayout(rate_card)
        rc_layout.setContentsMargins(12, 10, 12, 8)
        rc_layout.setSpacing(6)

        rc_title = QLabel("历史收益率走势 (%)")
        rc_title.setStyleSheet("color: #0F172A; font-size: 13px; font-weight: 700; border: none; background: transparent;")
        rc_layout.addWidget(rc_title)

        self.rate_canvas = _PnlComboCanvas(is_rate=True, fill_cumulative=False, parent=rate_card)
        self.rate_canvas.setMinimumHeight(180)
        rc_layout.addWidget(self.rate_canvas, 1)

        self.rate_bar_btn, self.rate_cum_btn, self.rate_flt_btn = self._build_legend_row(
            rc_layout,
            ("当日收益率 (左轴)", PROFIT_GREEN, True),
            ("累计收益率 (右轴)", BLUE, False),
            ("持仓浮动收益率 (右轴)", ORANGE, False),
            self._sync_rate_visibility,
        )
        layout.addWidget(rate_card, 2)

        # 两张图鼠标悬停日期联动
        self.amount_canvas.hoverIndexChanged.connect(self.rate_canvas.set_external_hover)
        self.rate_canvas.hoverIndexChanged.connect(self.amount_canvas.set_external_hover)

    def _build_legend_row(self, parent_layout, spec1, spec2, spec3, callback):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addStretch(1)
        buttons = []
        for label, color, is_bar in (spec1, spec2, spec3):
            btn = QPushButton(f"{'■' if is_bar else '●'}  {label}", self)
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("legend_label", label)
            btn.setProperty("legend_color", color)
            btn.setProperty("legend_bar", is_bar)
            btn.clicked.connect(callback)
            row.addWidget(btn)
            buttons.append(btn)
        row.addStretch(1)
        parent_layout.addLayout(row)
        self._style_legend_buttons(buttons)
        return buttons

    def _style_legend_buttons(self, buttons):
        for btn in buttons:
            active = btn.isChecked()
            label = btn.property("legend_label")
            color = btn.property("legend_color")
            is_bar = btn.property("legend_bar")
            glyph = ("■" if is_bar else "●") if active else "○"
            btn.setText(f"{glyph}  {label}" if active else f"{glyph}  {label}（已隐藏）")
            if active:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #F8FAFC; color: {color}; border: 1px solid #CBD5E1;
                        border-radius: 12px; padding: 3px 10px; font-size: 11px; font-weight: 600;
                    }}
                    QPushButton:hover {{ border-color: {color}; background: #EFF6FF; }}
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #F1F5F9; color: #94A3B8; border: 1px dashed #CBD5E1;
                        border-radius: 12px; padding: 3px 10px; font-size: 11px; font-weight: 500;
                    }
                    QPushButton:hover { background: #E2E8F0; color: #475569; }
                """)

    def _sync_amount_visibility(self):
        self._style_legend_buttons((self.amt_bar_btn, self.amt_cum_btn, self.amt_flt_btn))
        self.amount_canvas.set_visibility(
            show_bar=self.amt_bar_btn.isChecked(),
            show_cum=self.amt_cum_btn.isChecked(),
            show_flt=self.amt_flt_btn.isChecked(),
        )

    def _sync_rate_visibility(self):
        self._style_legend_buttons((self.rate_bar_btn, self.rate_cum_btn, self.rate_flt_btn))
        self.rate_canvas.set_visibility(
            show_bar=self.rate_bar_btn.isChecked(),
            show_cum=self.rate_cum_btn.isChecked(),
            show_flt=self.rate_flt_btn.isChecked(),
        )

    def _sync_window_styles(self):
        for key, btn in self._win_btns.items():
            active = key == self._window_key
            btn.setChecked(active)
            if active:
                btn.setStyleSheet("QPushButton { background: #2563EB; color: #FFFFFF; border: none; border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 600; }")
            else:
                btn.setStyleSheet("QPushButton { background: #F1F5F9; color: #475569; border: 1px solid #E2E8F0; border-radius: 6px; padding: 4px 10px; font-size: 11px; } QPushButton:hover { background: #E2E8F0; color: #0F172A; }")

    def set_time_window(self, key: str):
        if key not in self._win_btns:
            return
        self._window_key = key
        self._sync_window_styles()
        self._render_window()

    def set_data(self, pnl_data: dict[str, Any] | None):
        self._payload = pnl_data or {}
        note = self._payload.get("valuation_note") or "展示每日盈亏、累计盈亏与持仓浮动盈亏走势"
        self.note_lbl.setText(note)
        self._render_window()

    def _render_window(self):
        dates = list(self._payload.get("chart_dates") or ())
        d_pnls = list(self._payload.get("daily_pnls") or ())
        c_pnls = list(self._payload.get("cumulative_pnls") or ())
        f_pnls = list(self._payload.get("floating_pnls") or ())
        d_rates = list(self._payload.get("daily_rates") or ())
        c_rates = list(self._payload.get("cumulative_rates") or ())
        f_rates = list(self._payload.get("floating_rates") or ())

        if dates and self._window_key != "all":
            try:
                end_d = datetime.strptime(dates[-1][:10], "%Y-%m-%d").date()
            except ValueError:
                end_d = date.today()
            if self._window_key == "1m":
                start_d = end_d - timedelta(days=30)
            elif self._window_key == "3m":
                start_d = end_d - timedelta(days=90)
            elif self._window_key == "6m":
                start_d = end_d - timedelta(days=182)
            elif self._window_key == "ytd":
                start_d = date(end_d.year, 1, 1)
            else:
                start_d = date.min
            start_iso = start_d.isoformat()
            indices = [i for i, d in enumerate(dates) if d[:10] >= start_iso]
        else:
            indices = list(range(len(dates)))

        def slice_vals(arr):
            return [arr[i] if i < len(arr) else None for i in indices]

        s_dates = slice_vals(dates)
        self.amount_canvas.set_series(s_dates, slice_vals(d_pnls), slice_vals(c_pnls), slice_vals(f_pnls))
        self.rate_canvas.set_series(s_dates, slice_vals(d_rates), slice_vals(c_rates), slice_vals(f_rates))
