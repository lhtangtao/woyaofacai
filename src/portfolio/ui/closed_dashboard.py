"""美股已清仓收益分析看板专属组件 (Mac Desktop / PySide6)。

包含：
1. 顶部 7 张核心统计卡片
2. 左：按渠道汇总表（点击联动渠道筛选）
3. 中：盈利 / 亏损结构环形图（点击扇区/图例联动明细筛选）
4. 右：每月已实现盈亏趋势柱状图（支持 近6月 / 近12月 / 近24月 / 全部 切换与悬停提示）
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


def _fmt_money(val: float | None, *, signed: bool = False) -> str:
    if val is None:
        return "—"
    v = float(val)
    if abs(v) < 0.005:
        return "0.00"
    if signed:
        return f"{v:+,.2f}"
    return f"{v:,.2f}"


def _fmt_rate(val: float | None) -> str:
    if val is None:
        return "—"
    pct = float(val) * 100.0
    if abs(pct) < 0.005:
        return "0.00%"
    return f"{pct:+.2f}%"


def _pnl_color(val: float | None) -> str:
    if val is None or abs(float(val)) < 0.005:
        return "#0F172A"
    return "#16A34A" if float(val) > 0 else "#DC2626"


ClosedKpiCard = HoldingsKpiCard


class ClosedStructureDonut(QWidget):
    """盈利 / 持平 / 亏损结构环形图，支持扇区与图例点击联动明细表。"""

    category_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.total_count = 0
        self.items: list[dict[str, Any]] = []
        self.active_filter: str = ""
        self._slice_arcs: list[tuple[str, float, float]] = []
        self._legend_rects: list[tuple[str, QRectF]] = []
        self._center_pt = QPointF(0, 0)
        self._outer_r = 0.0
        self._inner_r = 0.0

    def set_data(self, structure: dict[str, Any], active_filter: str = ""):
        self.total_count = int(structure.get("total") or 0)
        self.active_filter = active_filter or ""
        self.items = [
            {
                "key": "win",
                "label": "盈利股票",
                "count": int(structure.get("win_count") or 0),
                "pct": float(structure.get("win_pct") or 0.0),
                "color": "#16A34A",
            },
            {
                "key": "flat",
                "label": "持平股票",
                "count": int(structure.get("flat_count") or 0),
                "pct": float(structure.get("flat_pct") or 0.0),
                "color": "#94A3B8",
            },
            {
                "key": "loss",
                "label": "亏损股票",
                "count": int(structure.get("loss_count") or 0),
                "pct": float(structure.get("loss_pct") or 0.0),
                "color": "#DC2626",
            },
        ]
        self.update()

    def _hit_category(self, pos: QPointF) -> str | None:
        for key, rect in self._legend_rects:
            if rect.contains(pos):
                return key
        dx = pos.x() - self._center_pt.x()
        dy = pos.y() - self._center_pt.y()
        dist = math.hypot(dx, dy)
        if self._inner_r <= dist <= self._outer_r and self._slice_arcs:
            # 0° 在右侧，逆时针递增（与 Qt startAngle=90° 对齐）
            deg = (math.degrees(math.atan2(-dy, dx)) + 360.0) % 360.0
            for key, start_deg, span_deg in self._slice_arcs:
                end_deg = start_deg + span_deg
                # 统一转到从 90° 逆时针偏移判断
                offset = (deg - start_deg + 360.0) % 360.0
                if offset <= span_deg:
                    return key
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._hit_category(event.position())
            if key:
                self.category_clicked.emit("" if self.active_filter == key else key)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        key = self._hit_category(event.position())
        if key:
            item = next((x for x in self.items if x["key"] == key), None)
            if item:
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{item['label']}：{item['count']} 只 ({item['pct']:.2f}%)\n点击筛选下方明细",
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

        if self.total_count <= 0:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无已清仓股票")
            return

        donut_d = min(max(110, h - 24), int(w * 0.44))
        cx = 16.0 + donut_d / 2.0
        cy = h / 2.0
        self._center_pt = QPointF(cx, cy)
        self._outer_r = donut_d / 2.0
        self._inner_r = self._outer_r * 0.56

        ring_rect = QRectF(cx - self._outer_r, cy - self._outer_r, donut_d, donut_d)
        start_angle = 90.0

        for item in self.items:
            cnt = item["count"]
            if cnt <= 0:
                continue
            span = (cnt / self.total_count) * 360.0
            color = QColor(item["color"])
            if self.active_filter and self.active_filter != item["key"]:
                color.setAlpha(75)
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(color)
            painter.drawPie(ring_rect, int(round(start_angle * 16)), int(round(span * 16)))
            self._slice_arcs.append((item["key"], start_angle % 360.0, span))
            start_angle += span

        # 镂空中心
        inner_d = self._inner_r * 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(cx - self._inner_r, cy - self._inner_r, inner_d, inner_d))

        # 中心数字
        painter.setPen(QColor("#0F172A"))
        painter.setFont(numeric_font(20, bold=True))
        painter.drawText(QRectF(cx - self._inner_r, cy - 22, inner_d, 26), Qt.AlignmentFlag.AlignCenter, str(self.total_count))
        painter.setPen(QColor("#64748B"))
        painter.setFont(QFont(".AppleSystemUIFont", 10))
        painter.drawText(QRectF(cx - self._inner_r, cy + 4, inner_d, 18), Qt.AlignmentFlag.AlignCenter, "清仓股票数")

        # 右侧图例
        leg_x = cx + self._outer_r + 16.0
        leg_w = max(80.0, w - leg_x - 8.0)
        row_h = 30.0
        start_y = cy - (len(self.items) * row_h) / 2.0

        for idx, item in enumerate(self.items):
            ry = start_y + idx * row_h
            rect = QRectF(leg_x, ry, leg_w, row_h - 4)
            self._legend_rects.append((item["key"], rect))

            is_active = self.active_filter == item["key"]
            is_dimmed = bool(self.active_filter and not is_active)

            if is_active:
                painter.setPen(QPen(QColor("#BFDBFE"), 1))
                painter.setBrush(QColor("#EFF6FF"))
                painter.drawRoundedRect(rect, 5, 5)

            dot_c = QColor(item["color"])
            if is_dimmed:
                dot_c.setAlpha(85)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(dot_c)
            painter.drawEllipse(QPointF(leg_x + 8, ry + 13), 4.0, 4.0)

            text_c = QColor("#0F172A" if not is_dimmed else "#94A3B8")
            painter.setPen(text_c)
            painter.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.DemiBold if is_active else QFont.Weight.Medium))
            painter.drawText(QRectF(leg_x + 18, ry, 72, row_h - 4), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, item["label"])

            painter.setFont(numeric_font(11, bold=True))
            val_txt = f"{item['count']} ({item['pct']:.2f}%)"
            painter.drawText(QRectF(leg_x + 78, ry, max(40.0, leg_w - 84), row_h - 4), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, val_txt)


class MonthlyRealizedBarChart(QWidget):
    """每月已实现盈亏双轴柱线图：左轴月度盈亏柱（绿盈红亏），右轴累计已实现盈亏折线（蓝色），支持图例独立显隐与悬停提示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(135)
        self.setMouseTracking(True)
        self.items: list[dict[str, Any]] = []
        self._bar_rects: list[tuple[QRectF, dict[str, Any]]] = []
        self._line_pts: list[tuple[QPointF, dict[str, Any]]] = []
        self._hover_idx: int = -1
        self.show_monthly_bars: bool = True
        self.show_cumulative_line: bool = True

    @property
    def _show_bars(self) -> bool:
        return self.show_monthly_bars

    @property
    def _show_line(self) -> bool:
        return self.show_cumulative_line

    def set_data(self, items: list[dict[str, Any]]):
        self.items = list(items or [])
        self._hover_idx = -1
        self.update()

    def set_series_visibility(self, *, show_bars: bool | None = None, show_line: bool | None = None):
        if show_bars is not None:
            self.show_monthly_bars = bool(show_bars)
        if show_line is not None:
            self.show_cumulative_line = bool(show_line)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        hit_idx = -1
        n = len(self.items)
        if n > 0 and self._bar_rects:
            for i, (rect, item) in enumerate(self._bar_rects):
                hit_box = QRectF(rect.x() - 4, 16, rect.width() + 8, self.height() - 32)
                if hit_box.contains(pos):
                    hit_idx = i
                    break
        elif n > 0 and self._line_pts:
            px = pos.x()
            hit_idx = min(range(len(self._line_pts)), key=lambda i: abs(self._line_pts[i][0].x() - px))

        if hit_idx != self._hover_idx:
            self._hover_idx = hit_idx
            self.update()

        if 0 <= hit_idx < n:
            item = self.items[hit_idx]
            lines = [f"月份：{item['month']}"]
            if self.show_monthly_bars:
                lines.append(f"月度已实现盈亏：{_fmt_money(item['pnl'], signed=True)} USD")
            if self.show_cumulative_line:
                lines.append(f"累计已实现盈亏：{_fmt_money(item.get('cumulative_pnl'), signed=True)} USD")
            lines.append(f"相关卖出笔数：{item.get('sell_count', 0)} 笔")
            QToolTip.showText(
                event.globalPosition().toPoint(),
                "\n".join(lines),
                self,
            )
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_idx >= 0:
            self._hover_idx = -1
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._bar_rects.clear()
        self._line_pts.clear()
        w, h = float(self.width()), float(self.height())

        if not self.items:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无月度已实现盈亏记录")
            return

        left_pad = 58.0
        right_pad = 58.0
        top_pad = 24.0
        bottom_pad = 22.0
        plot_w = max(40.0, w - left_pad - right_pad)
        plot_h = max(40.0, h - top_pad - bottom_pad)

        # 1. 计算左轴范围 (月度盈亏)
        m_vals = [float(x.get("pnl") or 0.0) for x in self.items]
        m_max = max(max(m_vals), 0.0)
        m_min = min(min(m_vals), 0.0)
        if m_max == 0.0 and m_min == 0.0:
            m_max, m_min = 100.0, -100.0
        else:
            pad = max(abs(m_max), abs(m_min)) * 0.16
            m_max += pad if m_max > 0 else 0.0
            m_min -= pad if m_min < 0 else 0.0
        m_span = max(1.0, m_max - m_min)

        def y_left(v: float) -> float:
            return top_pad + ((m_max - v) / m_span) * plot_h

        # 2. 计算右轴范围 (累计已实现盈亏)
        c_vals = [float(x.get("cumulative_pnl") or 0.0) for x in self.items if x.get("cumulative_pnl") is not None]
        if not c_vals:
            c_max, c_min = 100.0, -100.0
        else:
            c_max = max(max(c_vals), 0.0)
            c_min = min(min(c_vals), 0.0)
            if c_max == 0.0 and c_min == 0.0:
                c_max, c_min = 100.0, -100.0
            else:
                pad_c = max(abs(c_max), abs(c_min)) * 0.16
                c_max += pad_c if c_max > 0 else 0.0
                c_min -= pad_c if c_min < 0 else 0.0
        c_span = max(1.0, c_max - c_min)

        def y_right(v: float) -> float:
            return top_pad + ((c_max - v) / c_span) * plot_h

        zero_y_left = y_left(0.0)

        # 顶部双轴单位
        painter.setFont(QFont(".AppleSystemUIFont", 9, QFont.Weight.DemiBold))
        if self.show_monthly_bars:
            painter.setPen(QColor("#16A34A"))
            painter.drawText(QRectF(2, 2, 110, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "月度已实现 (USD)")
        if self.show_cumulative_line:
            painter.setPen(QColor("#2563EB"))
            painter.drawText(QRectF(w - 116, 2, 114, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "累计已实现 (USD)")

        # 绘制水平网格线 & 左右 Y 轴刻度
        painter.setFont(numeric_font(9))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            # 以左轴为主网格线
            gy = top_pad + (1.0 - frac) * plot_h
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            painter.drawLine(QPointF(left_pad, gy), QPointF(left_pad + plot_w, gy))

            if self.show_monthly_bars:
                gv = m_min + frac * m_span
                painter.setPen(QColor("#64748B"))
                painter.drawText(QRectF(2, gy - 9, left_pad - 6, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{gv:,.0f}")
            if self.show_cumulative_line:
                cv = c_min + frac * c_span
                painter.setPen(QColor("#2563EB"))
                painter.drawText(QRectF(left_pad + plot_w + 6, gy - 9, right_pad - 8, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, f"{cv:,.0f}")

        # 左轴清晰 0 基准线
        if self.show_monthly_bars:
            painter.setPen(QPen(QColor("#94A3B8"), 1.2))
            painter.drawLine(QPointF(left_pad, zero_y_left), QPointF(left_pad + plot_w, zero_y_left))

        n = len(self.items)
        slot_w = plot_w / n
        bar_w = max(4.0, min(30.0, slot_w * 0.52))
        label_stride = max(1, math.ceil(n / 8))

        # 3. 绘制月度盈亏柱状图 (左轴)
        if self.show_monthly_bars:
            for i, item in enumerate(self.items):
                v = float(item.get("pnl") or 0.0)
                cx = left_pad + (i + 0.5) * slot_w
                bx = cx - bar_w / 2.0
                vy = y_left(v)
                by = min(zero_y_left, vy)
                bh = max(2.0, abs(vy - zero_y_left))
                rect = QRectF(bx, by, bar_w, bh)
                self._bar_rects.append((rect, item))

                is_pos = v >= 0
                base_color = QColor("#16A34A" if is_pos else "#DC2626")
                if self._hover_idx >= 0 and self._hover_idx != i:
                    base_color.setAlpha(155)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(base_color)
                painter.drawRoundedRect(rect, 3, 3)

                # 柱顶金额标注
                if n <= 12 or i == self._hover_idx or abs(v) >= max(abs(m_max), abs(m_min)) * 0.35:
                    painter.setPen(QColor("#15803D" if is_pos else "#B91C1C"))
                    painter.setFont(numeric_font(9, bold=True))
                    lbl = f"{v:+,.0f}"
                    ly = (by - 14) if is_pos else (by + bh + 2)
                    ly = max(2.0, min(h - bottom_pad - 14, ly))
                    painter.drawText(QRectF(cx - 34, ly, 68, 14), Qt.AlignmentFlag.AlignCenter, lbl)

        # 4. 绘制累计已实现盈亏蓝色折线 (右轴)
        if self.show_cumulative_line:
            pts: list[QPointF] = []
            for i, item in enumerate(self.items):
                cx = left_pad + (i + 0.5) * slot_w
                cv = float(item.get("cumulative_pnl") or 0.0)
                cy = y_right(cv)
                pt = QPointF(cx, cy)
                pts.append(pt)
                self._line_pts.append((pt, item))

            if len(pts) >= 2:
                path = QPainterPath(pts[0])
                for pt in pts[1:]:
                    path.lineTo(pt)
                painter.setPen(QPen(QColor("#2563EB"), 2.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

            for pt in pts:
                painter.setPen(QPen(QColor("#FFFFFF"), 1.8))
                painter.setBrush(QColor("#2563EB"))
                painter.drawEllipse(pt, 3.5, 3.5)

        # 悬停指示
        if 0 <= self._hover_idx < n:
            hcx = left_pad + (self._hover_idx + 0.5) * slot_w
            painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(hcx, top_pad), QPointF(hcx, top_pad + plot_h))

        # 5. X 轴月份标签
        painter.setFont(numeric_font(9))
        for i, item in enumerate(self.items):
            if i % label_stride == 0 or i == n - 1:
                cx = left_pad + (i + 0.5) * slot_w
                painter.setPen(QColor("#475569"))
                painter.drawText(QRectF(cx - 26, top_pad + plot_h + 3, 52, 16), Qt.AlignmentFlag.AlignCenter, item["month"])

        if not self.show_monthly_bars and not self.show_cumulative_line:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 11))
            painter.drawText(QRectF(left_pad, top_pad, plot_w, plot_h), Qt.AlignmentFlag.AlignCenter, "已隐藏全部图表系列，点击下方图例可恢复显示")


class ClosedTop10Chart(QWidget):
    """以 0 为中心的横向发散条形图（左侧亏损 Top 5 + 中央发散条形 + 右侧盈利 Top 5）。"""

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
            chs = "、".join(item.get("channels") or ()) or "—"
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"股票代码：{tk}\n已实现盈亏：{_fmt_money(item.get('realized_pnl'), signed=True)} USD\n已清仓记录：{int(item.get('record_count') or 0)} 条\n涉及券商：{chs}",
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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "当前筛选范围暂无非零盈亏的已清仓股票")
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

        all_pnls = [abs(float(x.get("realized_pnl") or 0.0)) for x in (*self.winners, *self.losers)]
        max_abs = max(all_pnls) if all_pnls else 100.0
        if max_abs <= 0:
            max_abs = 100.0

        # 表头
        painter.setPen(QColor("#64748B"))
        painter.setFont(QFont(".AppleSystemUIFont", 10, QFont.Weight.DemiBold))
        painter.drawText(QRectF(4, 2, 24, 16), Qt.AlignmentFlag.AlignCenter, "#")
        painter.drawText(QRectF(30, 2, 64, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "股票")
        painter.drawText(QRectF(94, 2, side_w - 98, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "已实现盈亏 (USD)")

        r_start = w - side_w
        painter.drawText(QRectF(r_start + 4, 2, 24, 16), Qt.AlignmentFlag.AlignCenter, "#")
        painter.drawText(QRectF(r_start + 30, 2, 64, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "股票")
        painter.drawText(QRectF(r_start + 94, 2, side_w - 98, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "已实现盈亏 (USD)")

        # 中心 0 USD 基准线 & 底部刻度
        painter.setPen(QPen(QColor("#CBD5E1"), 1.0))
        painter.drawLine(QPointF(center_x, top_y), QPointF(center_x, bottom_axis_y))
        painter.drawLine(QPointF(bar_area_x, bottom_axis_y), QPointF(bar_area_x + bar_area_w, bottom_axis_y))

        painter.setFont(numeric_font(9))
        for frac in (-1.0, -0.5, 0.0, 0.5, 1.0):
            tx = center_x + frac * half_w
            painter.setPen(QPen(QColor("#E2E8F0"), 1.0, Qt.PenStyle.DashLine))
            if frac != 0.0:
                painter.drawLine(QPointF(tx, top_y), QPointF(tx, bottom_axis_y))
            painter.setPen(QColor("#64748B"))
            lbl_v = frac * max_abs
            lbl_str = "0" if frac == 0.0 else f"{lbl_v:,.0f}"
            painter.drawText(QRectF(tx - 32, bottom_axis_y + 2, 64, 16), Qt.AlignmentFlag.AlignCenter, lbl_str)

        for idx in range(rows):
            ry = top_y + idx * row_h

            # 左侧亏损行
            if idx < len(self.losers):
                item = self.losers[idx]
                tk = str(item["ticker"])
                val = float(item["realized_pnl"] or 0.0)
                is_sel = self.active_ticker == tk.upper()
                is_hov = self._hover_ticker == tk
                bar_len = max(4.0, (abs(val) / max_abs) * half_w)

                row_rect = QRectF(2, ry + 1, side_w - 2, row_h - 2)
                bar_rect = QRectF(center_x - bar_len, ry + 5, bar_len, row_h - 10)
                self._hit_rects.append((row_rect, item))
                self._hit_rects.append((bar_rect, item))

                if is_sel or is_hov:
                    painter.setPen(QPen(QColor("#93C5FD" if is_sel else "#E2E8F0"), 1))
                    painter.setBrush(QColor("#EFF6FF" if is_sel else "#F8FAFC"))
                    painter.drawRoundedRect(row_rect, 4, 4)

                painter.setPen(QColor("#64748B"))
                painter.setFont(numeric_font(10))
                painter.drawText(QRectF(4, ry, 24, row_h), Qt.AlignmentFlag.AlignCenter, str(item["rank"]))

                painter.setPen(QColor("#2563EB" if is_sel else "#0F172A"))
                painter.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold if is_sel else QFont.Weight.DemiBold))
                painter.drawText(QRectF(30, ry, 64, row_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tk)

                painter.setPen(QColor("#DC2626"))
                painter.setFont(numeric_font(11, bold=True))
                painter.drawText(QRectF(94, ry, side_w - 98, row_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _fmt_money(val, signed=True))

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
                val = float(item["realized_pnl"] or 0.0)
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
                painter.drawText(QRectF(r_start + 4, ry, 24, row_h), Qt.AlignmentFlag.AlignCenter, str(item["rank"]))

                painter.setPen(QColor("#2563EB" if is_sel else "#0F172A"))
                painter.setFont(QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold if is_sel else QFont.Weight.DemiBold))
                painter.drawText(QRectF(r_start + 30, ry, 64, row_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tk)

                painter.setPen(QColor("#16A34A"))
                painter.setFont(numeric_font(11, bold=True))
                painter.drawText(QRectF(r_start + 94, ry, side_w - 98, row_h), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _fmt_money(val, signed=True))

                bar_col = QColor("#22C55E")
                if self.active_ticker and not is_sel:
                    bar_col.setAlpha(75)
                elif is_sel or is_hov:
                    bar_col = QColor("#16A34A")
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(bar_col)
                painter.drawRoundedRect(bar_rect, 3, 3)


class ClosedTop10Card(QFrame):
    """可折叠的「已清仓股票盈亏贡献 Top 10」横向发散条形图卡片。"""

    ticker_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("closedPanelCard")
        self.setStyleSheet("""
            QFrame#closedPanelCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QFrame#closedPanelCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        self._raw_top10_data: dict[str, Any] = {}
        self._active_ticker: str = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        title_prefix = QLabel("已清仓股票盈亏贡献 Top")
        title_prefix.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        hdr.addWidget(title_prefix)

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

        sub = QLabel("按已实现盈亏排序，可点击联动下方明细")
        sub.setStyleSheet("font-size: 11px; color: #94A3B8; margin-left: 2px;")
        hdr.addWidget(sub)
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

        # 顶部汇总提示（明确标注为当前筛选范围全部已清仓记录汇总，非 Top 小计）
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

        self.chart = ClosedTop10Chart(self.body_widget)
        self.chart.ticker_clicked.connect(self.ticker_clicked.emit)
        b_layout.addWidget(self.chart)

        layout.addWidget(self.body_widget)

    def _on_toggle(self):
        expanded = self.toggle_btn.isChecked()
        self.body_widget.setVisible(expanded)
        self.toggle_btn.setText("⌄" if expanded else "›")

    def _on_top_n_changed(self, _val: int):
        self._refresh_display()

    def _refresh_display(self):
        if not hasattr(self, "_raw_top10_data") or not self._raw_top10_data:
            return
        n = max(1, self.top_n_spin.value())
        tot_loss = self._raw_top10_data.get("total_realized_loss")
        tot_win = self._raw_top10_data.get("total_realized_profit")
        self.loss_pill.setText(f"亏损贡献 (Top {n})  |  当前筛选全部亏损合计 {_fmt_money(tot_loss, signed=True)} USD")
        self.win_pill.setText(f"盈利贡献 (Top {n})  |  当前筛选全部盈利合计 {_fmt_money(tot_win, signed=True)} USD")

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

    def set_data(self, top10: dict[str, Any], *, active_ticker: str = ""):
        self._raw_top10_data = dict(top10 or {})
        self._active_ticker = (active_ticker or "").strip().upper()
        self._refresh_display()


class ClosedHoldingsKpiBar(QFrame):
    """美股已清仓页面顶部六张核心统计卡片栏（与持有中页面统一高度、风格、图标及间距）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("closedHoldingsKpiBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.card_sell = ClosedKpiCard("卖出总额 (USD)", "↗", "当前筛选范围内已清仓股票的历史卖出总金额")
        self.card_buy = ClosedKpiCard("买入总额 (USD)", "↘", "当前筛选范围内已清仓股票的历史买入总金额")
        self.card_net = ClosedKpiCard("净已实现盈亏 (USD)", "±", "当前筛选范围内的累计净已实现盈亏总额（已实现盈利 + 已实现亏损）")
        self.card_rate = ClosedKpiCard("已实现收益率", "%", "净已实现盈亏 ÷ 买入总额 × 100%（与系统已清仓个股收益率口径一致）")
        self.card_count = ClosedKpiCard("已清仓股票数量", "#", "按证券与账户维度统计已清仓标的记录数，不跨账户合并")
        self.card_win_ratio = ClosedKpiCard("盈利股票占比", "✓", "当前有效筛选范围内盈利已清仓记录数占全部已清仓记录数的比例（若单股票跨券商清仓，以记录笔数为统计单位）")

        for c in (
            self.card_sell,
            self.card_buy,
            self.card_net,
            self.card_rate,
            self.card_count,
            self.card_win_ratio,
        ):
            layout.addWidget(c, 1)

    def set_cards_data(self, payload: dict[str, Any]):
        cards = payload.get("cards") or {}
        struct = payload.get("structure") or {}

        # 1. 卖出总额 (USD)
        self.card_sell.set_value(_fmt_money(cards.get("total_sell_amount")), "#0F172A")

        # 2. 买入总额 (USD)
        self.card_buy.set_value(_fmt_money(cards.get("total_buy_amount")), "#0F172A")

        # 3. 净已实现盈亏 (USD)
        net_v = cards.get("net_realized_pnl")
        net_col = _pnl_color(net_v)
        net_bg = "#DCFCE7" if (net_v or 0) > 0 else "#FEE2E2" if (net_v or 0) < 0 else "#EFF6FF"
        net_fg = "#16A34A" if (net_v or 0) > 0 else "#DC2626" if (net_v or 0) < 0 else "#2563EB"
        self.card_net.set_value(_fmt_money(net_v, signed=True), net_col, badge_bg=net_bg, badge_fg=net_fg)

        # 4. 已实现收益率
        rate_v = cards.get("realized_pnl_rate")
        rate_col = _pnl_color(rate_v)
        rate_bg = "#DCFCE7" if (rate_v or 0) > 0 else "#FEE2E2" if (rate_v or 0) < 0 else "#EFF6FF"
        rate_fg = "#16A34A" if (rate_v or 0) > 0 else "#DC2626" if (rate_v or 0) < 0 else "#2563EB"
        self.card_rate.set_value(_fmt_rate(rate_v), rate_col, badge_bg=rate_bg, badge_fg=rate_fg)

        # 5. 已清仓股票数量
        cnt = int(cards.get("closed_count") or 0)
        self.card_count.set_value(f"{cnt}", "#0F172A")

        # 6. 盈利股票占比
        win_pct = cards.get("win_pct") if cards.get("win_pct") is not None else struct.get("win_pct")
        if cnt == 0 or win_pct is None:
            self.card_win_ratio.set_value("—", "#0F172A")
        else:
            self.card_win_ratio.set_value(f"{float(win_pct):.2f}%", "#0F172A")


class ClosedDashboardWidget(QFrame):
    """美股已清仓收益分析看板主容器（位于筛选栏下方、已清仓明细表格上方）。"""

    broker_clicked = Signal(str)
    structure_clicked = Signal(str)
    trend_window_changed = Signal(str)
    ticker_clicked = Signal(str)

    def __init__(self, parent=None, kpi_bar: ClosedHoldingsKpiBar | None = None):
        super().__init__(parent)
        self.setObjectName("closedDashboard")
        self.setStyleSheet("#closedDashboard { background: transparent; border: none; }")
        self._active_broker = ""
        self._active_structure = ""
        self._active_window = "all"

        self.kpi_bar = kpi_bar or ClosedHoldingsKpiBar(self)
        self.card_sell = self.kpi_bar.card_sell
        self.card_buy = self.kpi_bar.card_buy
        self.card_net = self.kpi_bar.card_net
        self.card_rate = self.kpi_bar.card_rate
        self.card_count = self.kpi_bar.card_count
        self.card_win_ratio = self.kpi_bar.card_win_ratio

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(10)

        # 若外部未单独挂载 kpi_bar，则放入看板顶部自身布局中
        if kpi_bar is None:
            root.addWidget(self.kpi_bar)

        # ---------------------------------------------------------------------
        # 分析图表行：左 渠道汇总 | 中 盈亏结构环形图 | 右 每月已实现盈亏趋势
        # ---------------------------------------------------------------------
        mid_row = QHBoxLayout()
        mid_row.setSpacing(10)

        # 1. 券商渠道收益汇总
        broker_card = self._make_panel_card("按渠道汇总", "点击行筛选券商，点击合计恢复")
        bc_layout = broker_card.layout()

        self.broker_table = QTableWidget(0, 8)
        self.broker_table.setHorizontalHeaderLabels([
            "渠道", "股票数", "买入总额", "卖出总额", "已实现盈利", "已实现亏损", "净已实现盈亏", "收益率"
        ])
        self.broker_table.verticalHeader().setVisible(False)
        self.broker_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.broker_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.broker_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.broker_table.setShowGrid(False)
        self.broker_table.setAlternatingRowColors(False)
        self.broker_table.setTextElideMode(Qt.TextElideMode.ElideRight)
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
        """)
        hdr = self.broker_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.broker_table.cellClicked.connect(self._on_broker_row_clicked)
        bc_layout.addWidget(self.broker_table, 1)
        mid_row.addWidget(broker_card, 39)

        # 2. 盈利 / 亏损结构环形图
        struct_card = self._make_panel_card("盈利 / 亏损结构", "点击扇区或图例联动明细")
        sc_layout = struct_card.layout()
        self.donut = ClosedStructureDonut()
        self.donut.category_clicked.connect(self.structure_clicked.emit)
        sc_layout.addWidget(self.donut, 1)
        mid_row.addWidget(struct_card, 25)

        # 3. 每月已实现盈亏趋势柱状图
        trend_card = self._make_panel_card("已实现盈亏趋势", "")
        tc_layout = trend_card.layout()
        # 在头部注入信息提示与时间范围切换按钮
        hdr_row = trend_card.findChild(QHBoxLayout, "panelHdr")
        self.trend_info_lbl = QLabel("ⓘ")
        self.trend_info_lbl.setStyleSheet("color: #64748B; font-size: 12px; padding: 0 4px;")
        hdr_row.insertWidget(1, self.trend_info_lbl)

        self.window_buttons: dict[str, QPushButton] = {}
        win_box = QFrame()
        win_box.setStyleSheet("background: #F1F5F9; border-radius: 6px;")
        wb_layout = QHBoxLayout(win_box)
        wb_layout.setContentsMargins(2, 2, 2, 2)
        wb_layout.setSpacing(2)
        for key, lbl in (("6m", "近6月"), ("12m", "近12月"), ("24m", "近24月"), ("all", "全部")):
            btn = QPushButton(lbl)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self._on_window_clicked(k))
            self.window_buttons[key] = btn
            wb_layout.addWidget(btn)
        hdr_row.addWidget(win_box)
        self._sync_window_buttons()

        self.bar_chart = MonthlyRealizedBarChart()
        tc_layout.addWidget(self.bar_chart, 1)

        # 图例：月度柱状图 & 累计折线独立显隐切换
        t_legend = QHBoxLayout()
        t_legend.setContentsMargins(0, 0, 0, 0)
        t_legend.setSpacing(8)
        t_legend.addStretch(1)
        self.bar_legend_btn = QPushButton("■  月度已实现盈亏")
        self.bar_legend_btn.setCheckable(True)
        self.bar_legend_btn.setChecked(True)
        self.bar_legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bar_legend_btn.clicked.connect(self._on_trend_legend_toggled)
        t_legend.addWidget(self.bar_legend_btn)

        self.cum_legend_btn = QPushButton("●  累计已实现盈亏")
        self.cum_legend_btn.setCheckable(True)
        self.cum_legend_btn.setChecked(True)
        self.cum_legend_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cum_legend_btn.clicked.connect(self._on_trend_legend_toggled)
        t_legend.addWidget(self.cum_legend_btn)
        t_legend.addStretch(1)
        tc_layout.addLayout(t_legend)
        self._sync_trend_legend_styles()

        mid_row.addWidget(trend_card, 36)

        root.addLayout(mid_row)

        # ---------------------------------------------------------------------
        # 第三行：已清仓股票盈亏贡献 Top 10（可折叠横向发散条形图）
        # ---------------------------------------------------------------------
        self.top10_card = ClosedTop10Card(self)
        self.top10_card.ticker_clicked.connect(self.ticker_clicked.emit)
        root.addWidget(self.top10_card)

    @staticmethod
    def _make_panel_card(title: str, subtitle: str) -> QFrame:
        card = QFrame()
        card.setObjectName("closedPanelCard")
        card.setMinimumHeight(265)
        card.setStyleSheet("""
            QFrame#closedPanelCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 10px;
            }
            QFrame#closedPanelCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setObjectName("panelHdr")
        hdr.setSpacing(4)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #0F172A;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet("font-size: 10px; color: #94A3B8;")
            hdr.addWidget(sub)
        layout.addLayout(hdr)
        return card

    def _sync_window_buttons(self):
        for key, btn in self.window_buttons.items():
            active = key == self._active_window
            btn.setStyleSheet(
                "QPushButton { border: none; border-radius: 4px; padding: 2px 7px; font-size: 10px; font-weight: 600; "
                + ("background: #2563EB; color: #FFFFFF; }" if active else "background: transparent; color: #64748B; }")
            )

    def _on_window_clicked(self, key: str):
        if key == self._active_window:
            return
        self._active_window = key
        self._sync_window_buttons()
        self.trend_window_changed.emit(key)

    def _on_broker_row_clicked(self, row: int, _col: int):
        item = self.broker_table.item(row, 0)
        if not item:
            return
        ch = item.data(Qt.ItemDataRole.UserRole) or ""
        if not ch or ch == "合计":
            self.broker_clicked.emit("")
        elif self._active_broker == ch:
            self.broker_clicked.emit("")
        else:
            self.broker_clicked.emit(ch)

    def _on_trend_legend_toggled(self):
        self._sync_trend_legend_styles()
        self.bar_chart.set_series_visibility(
            show_bars=self.bar_legend_btn.isChecked(),
            show_line=self.cum_legend_btn.isChecked(),
        )

    def _sync_trend_legend_styles(self):
        for btn, label, color, glyph in (
            (self.bar_legend_btn, "月度已实现盈亏", "#16A34A", "■"),
            (self.cum_legend_btn, "累计已实现盈亏", "#2563EB", "●"),
        ):
            active = btn.isChecked()
            btn.setText(f"{glyph}  {label}" if active else f"○  {label}（已隐藏）")
            if active:
                btn.setStyleSheet(
                    f"QPushButton {{ background: #F8FAFC; color: {color}; border: 1px solid #CBD5E1; border-radius: 10px; padding: 2px 9px; font-size: 10px; font-weight: 600; }}"
                    f"QPushButton:hover {{ border-color: {color}; background: #EFF6FF; }}"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #F1F5F9; color: #94A3B8; border: 1px dashed #CBD5E1; border-radius: 10px; padding: 2px 9px; font-size: 10px; font-weight: 500; }"
                    "QPushButton:hover { background: #E2E8F0; color: #475569; }"
                )

    def set_data(self, payload: dict[str, Any], *, active_broker: str = "", active_structure: str = "", active_ticker: str = "", trend_window: str = "all"):
        self._cached_payload = payload
        self._active_broker = active_broker or ""
        self._active_structure = active_structure or ""
        if trend_window in self.window_buttons:
            self._active_window = trend_window
            self._sync_window_buttons()

        self.kpi_bar.set_cards_data(payload)

        # 券商汇总表
        brokers = list(payload.get("broker_summary") or [])
        total_row = payload.get("broker_total")
        display_rows = brokers + ([total_row] if brokers and total_row else [])
        self.broker_table.setRowCount(len(display_rows))
        num_font = numeric_font(11)
        bold_num_font = numeric_font(11, bold=True)

        for r_idx, row in enumerate(display_rows):
            is_total = row.get("channel") == "合计"
            is_selected = bool(not is_total and self._active_broker and self._active_broker == row.get("channel"))
            bg = QColor("#EFF6FF") if is_selected else QColor("#F8FAFC") if is_total else QColor("#FFFFFF")

            cells = [
                (str(row.get("channel") or ""), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "#2563EB" if is_selected else "#0F172A", False),
                (str(int(row.get("stock_count") or 0)), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "#0F172A", True),
                (_fmt_money(row.get("buy_amount")), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "#0F172A", True),
                (_fmt_money(row.get("sell_amount")), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "#0F172A", True),
                (_fmt_money(row.get("realized_profit"), signed=True), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "#16A34A" if (row.get("realized_profit") or 0) > 0 else "#64748B", True),
                (_fmt_money(row.get("realized_loss"), signed=True), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "#DC2626" if (row.get("realized_loss") or 0) < 0 else "#64748B", True),
                (_fmt_money(row.get("net_pnl"), signed=True), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _pnl_color(row.get("net_pnl")), True),
                (_fmt_rate(row.get("pnl_rate")), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, _pnl_color(row.get("pnl_rate")), True),
            ]
            for c_idx, (txt, align, fg, is_num) in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setTextAlignment(align)
                it.setBackground(bg)
                it.setForeground(QColor(fg))
                if c_idx == 0:
                    it.setData(Qt.ItemDataRole.UserRole, row.get("channel"))
                if is_total:
                    f = bold_num_font if is_num else QFont(".AppleSystemUIFont", 11, QFont.Weight.Bold)
                    it.setFont(f)
                elif is_num:
                    it.setFont(num_font)
                self.broker_table.setItem(r_idx, c_idx, it)
            self.broker_table.setRowHeight(r_idx, 27)

        # 盈利/亏损结构环形图
        self.donut.set_data(payload.get("structure") or {}, active_filter=self._active_structure)

        # 每月已实现盈亏趋势
        self.trend_info_lbl.setToolTip(str(payload.get("attribution_note") or ""))
        self.bar_chart.set_data(payload.get("monthly_trend") or [])

        # 已清仓股票盈亏贡献 Top 10
        self.top10_card.set_data(payload.get("ticker_top10") or {}, active_ticker=active_ticker)
