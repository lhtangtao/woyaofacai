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
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

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
        return "#64748B"
    return "#16A34A" if float(val) > 0 else "#DC2626"


class ClosedKpiCard(QFrame):
    """已清仓看板 7 卡之一，紧凑金融风设计。"""

    def __init__(self, title: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("closedKpiCard")
        if tooltip:
            self.setToolTip(tooltip)
        self.setStyleSheet("""
            QFrame#closedKpiCard {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
            }
            QFrame#closedKpiCard QLabel {
                border: none;
                background: transparent;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(78)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 500;")
        layout.addWidget(self.title_lbl)

        self.val_lbl = QLabel("—")
        self.val_lbl.setFont(numeric_font(17, bold=True))
        self.val_lbl.setStyleSheet("color: #0F172A;")
        layout.addWidget(self.val_lbl)

    def set_value(self, text: str, color: str = "#0F172A"):
        self.val_lbl.setText(text)
        self.val_lbl.setStyleSheet(f"color: {color};")


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
    """每月已实现盈亏柱状图：绿盈红亏、零轴线、柱顶金额标注与鼠标悬停提示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self.items: list[dict[str, Any]] = []
        self._bar_rects: list[tuple[QRectF, dict[str, Any]]] = []
        self._hover_idx: int = -1

    def set_data(self, items: list[dict[str, Any]]):
        self.items = list(items or [])
        self._hover_idx = -1
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        hit_idx = -1
        for i, (rect, item) in enumerate(self._bar_rects):
            hit_box = QRectF(rect.x() - 4, 16, rect.width() + 8, self.height() - 32)
            if hit_box.contains(pos):
                hit_idx = i
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"月份：{item['month']}\n已实现盈亏：{_fmt_money(item['pnl'], signed=True)} USD\n卖出笔数：{item.get('sell_count', 0)} 笔",
                    self,
                )
                break
        if hit_idx != self._hover_idx:
            self._hover_idx = hit_idx
            self.update()
        if hit_idx < 0:
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
        w, h = self.width(), self.height()

        if not self.items:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(QFont(".AppleSystemUIFont", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无月度已实现盈亏记录")
            return

        left_pad = 64.0
        right_pad = 14.0
        top_pad = 24.0
        bottom_pad = 28.0
        plot_w = max(40.0, w - left_pad - right_pad)
        plot_h = max(40.0, h - top_pad - bottom_pad)

        vals = [float(x.get("pnl") or 0.0) for x in self.items]
        max_v = max(max(vals), 0.0)
        min_v = min(min(vals), 0.0)
        if max_v == 0.0 and min_v == 0.0:
            max_v, min_v = 100.0, -100.0
        else:
            pad = max(abs(max_v), abs(min_v)) * 0.16
            if max_v > 0:
                max_v += pad
            if min_v < 0:
                min_v -= pad
        span_v = max(1.0, max_v - min_v)

        def y_of(v: float) -> float:
            return top_pad + ((max_v - v) / span_v) * plot_h

        zero_y = y_of(0.0)

        # 网格线 & Y轴刻度
        painter.setFont(numeric_font(9))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            gv = min_v + frac * span_v
            gy = y_of(gv)
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            painter.drawLine(QPointF(left_pad, gy), QPointF(left_pad + plot_w, gy))
            painter.setPen(QColor("#64748B"))
            painter.drawText(QRectF(2, gy - 9, left_pad - 8, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{gv:,.0f}")

        # 清晰零轴
        painter.setPen(QPen(QColor("#94A3B8"), 1.2))
        painter.drawLine(QPointF(left_pad, zero_y), QPointF(left_pad + plot_w, zero_y))

        n = len(self.items)
        slot_w = plot_w / n
        bar_w = max(6.0, min(34.0, slot_w * 0.58))
        label_stride = max(1, math.ceil(n / 8))

        for i, item in enumerate(self.items):
            v = float(item.get("pnl") or 0.0)
            cx = left_pad + (i + 0.5) * slot_w
            bx = cx - bar_w / 2.0
            vy = y_of(v)
            by = min(zero_y, vy)
            bh = max(2.0, abs(vy - zero_y))
            rect = QRectF(bx, by, bar_w, bh)
            self._bar_rects.append((rect, item))

            is_pos = v >= 0
            base_color = QColor("#16A34A" if is_pos else "#DC2626")
            if self._hover_idx >= 0 and self._hover_idx != i:
                base_color.setAlpha(155)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base_color)
            painter.drawRoundedRect(rect, 3, 3)

            # 柱顶/柱底金额标注（数量较多时仅标注较大柱或悬停柱，避免重叠）
            if n <= 12 or i == self._hover_idx or abs(v) >= max(abs(max_v), abs(min_v)) * 0.35:
                painter.setPen(QColor("#15803D" if is_pos else "#B91C1C"))
                painter.setFont(numeric_font(9, bold=True))
                lbl = f"{v:+,.0f}"
                ly = (by - 15) if is_pos else (by + bh + 2)
                ly = max(2.0, min(h - bottom_pad - 14, ly))
                painter.drawText(QRectF(cx - 34, ly, 68, 14), Qt.AlignmentFlag.AlignCenter, lbl)

            # X轴月份标签
            if i % label_stride == 0 or i == n - 1:
                painter.setPen(QColor("#475569"))
                painter.setFont(numeric_font(9))
                painter.drawText(QRectF(cx - 26, top_pad + plot_h + 6, 52, 18), Qt.AlignmentFlag.AlignCenter, item["month"])


class ClosedDashboardWidget(QFrame):
    """美股已清仓收益分析看板主容器（位于筛选栏下方、已清仓明细表格上方）。"""

    broker_clicked = Signal(str)
    structure_clicked = Signal(str)
    trend_window_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("closedDashboard")
        self.setStyleSheet("#closedDashboard { background: transparent; border: none; }")
        self._active_broker = ""
        self._active_structure = ""
        self._active_window = "all"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 4)
        root.setSpacing(10)

        # ---------------------------------------------------------------------
        # 第一行：七张核心统计卡片
        # ---------------------------------------------------------------------
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)

        self.card_count = ClosedKpiCard("已清仓股票数", "按证券与账户维度统计已清仓标的记录数，不跨账户合并")
        self.card_buy = ClosedKpiCard("买入总额 (USD)", "当前筛选范围内已清仓股票的历史买入总金额")
        self.card_sell = ClosedKpiCard("卖出总额 (USD)", "当前筛选范围内已清仓股票的历史卖出总金额")
        self.card_profit = ClosedKpiCard("已实现盈利", "仅汇总已实现盈亏为正的股票")
        self.card_loss = ClosedKpiCard("已实现亏损", "仅汇总已实现盈亏为负的股票")
        self.card_net = ClosedKpiCard("净已实现盈亏", "净已实现盈亏 = 已实现盈利 + 已实现亏损")
        self.card_rate = ClosedKpiCard("已实现收益率", "净已实现盈亏 ÷ 买入总额 × 100%（与系统已清仓个股收益率口径一致）")

        for c in (
            self.card_count,
            self.card_buy,
            self.card_sell,
            self.card_profit,
            self.card_loss,
            self.card_net,
            self.card_rate,
        ):
            kpi_row.addWidget(c, 1)
        root.addLayout(kpi_row)

        # ---------------------------------------------------------------------
        # 第二行：左 渠道汇总 | 中 盈亏结构环形图 | 右 每月已实现盈亏趋势
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
        self.broker_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.broker_table.setStyleSheet("""
            QTableWidget { border: none; background: #FFFFFF; font-size: 11px; }
            QHeaderView::section {
                background: #F8FAFC; color: #64748B; font-size: 11px; font-weight: 600;
                border: none; border-bottom: 1px solid #E2E8F0; padding: 5px 4px;
            }
            QTableWidget::item { border-bottom: 1px solid #F1F5F9; padding: 4px 4px; }
        """)
        hdr = self.broker_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.broker_table.cellClicked.connect(self._on_broker_row_clicked)
        bc_layout.addWidget(self.broker_table, 1)
        mid_row.addWidget(broker_card, 43)

        # 2. 盈利 / 亏损结构环形图
        struct_card = self._make_panel_card("盈利 / 亏损结构", "点击扇区或图例联动明细")
        sc_layout = struct_card.layout()
        self.donut = ClosedStructureDonut()
        self.donut.category_clicked.connect(self.structure_clicked.emit)
        sc_layout.addWidget(self.donut, 1)
        mid_row.addWidget(struct_card, 23)

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
        mid_row.addWidget(trend_card, 34)

        root.addLayout(mid_row)

    @staticmethod
    def _make_panel_card(title: str, subtitle: str) -> QFrame:
        card = QFrame()
        card.setObjectName("closedPanelCard")
        card.setMinimumHeight(255)
        card.setStyleSheet("""
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
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

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

    def set_data(self, payload: dict[str, Any], *, active_broker: str = "", active_structure: str = "", trend_window: str = "all"):
        self._active_broker = active_broker or ""
        self._active_structure = active_structure or ""
        if trend_window in self.window_buttons:
            self._active_window = trend_window
            self._sync_window_buttons()

        cards = payload.get("cards") or {}
        self.card_count.set_value(f"{int(cards.get('closed_count') or 0)}", "#0F172A")
        self.card_buy.set_value(_fmt_money(cards.get("total_buy_amount")), "#0F172A")
        self.card_sell.set_value(_fmt_money(cards.get("total_sell_amount")), "#0F172A")
        self.card_profit.set_value(_fmt_money(cards.get("realized_profit"), signed=True), "#16A34A" if (cards.get("realized_profit") or 0) > 0 else "#64748B")
        self.card_loss.set_value(_fmt_money(cards.get("realized_loss"), signed=True), "#DC2626" if (cards.get("realized_loss") or 0) < 0 else "#64748B")

        net_v = cards.get("net_realized_pnl")
        self.card_net.set_value(_fmt_money(net_v, signed=True), _pnl_color(net_v))
        rate_v = cards.get("realized_pnl_rate")
        self.card_rate.set_value(_fmt_rate(rate_v), _pnl_color(rate_v))

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
