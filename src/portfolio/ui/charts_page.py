import math
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import ZERO
from portfolio.services.charts_service import build_charts, rank_chart
from portfolio.ui.theme import tokens


def chart_font(size, *, bold=False):
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(size)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0)
    font.setWordSpacing(0)
    font.setStretch(100)
    font.setKerning(True)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


class SparklineWidget(QWidget):
    """迷你走势折线图，用于在 KPI 卡片右侧展示紧凑的资产趋势"""

    def __init__(self, parent=None, color="#2563EB", fill=True):
        super().__init__(parent)
        self.color = QColor(color)
        self.fill = fill
        self.points_data = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedHeight(44)
        self.setMinimumWidth(80)

    def set_data(self, points):
        self.points_data = [float(v) for v in points if v is not None]
        self.update()

    def paintEvent(self, event):
        if len(self.points_data) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        pad_x, pad_y = 4.0, 4.0
        draw_w = w - pad_x * 2
        draw_h = h - pad_y * 2

        low = min(self.points_data)
        high = max(self.points_data)
        span = high - low or 1.0

        pts = []
        step = draw_w / (len(self.points_data) - 1)
        for i, val in enumerate(self.points_data):
            x = pad_x + i * step
            y = pad_y + draw_h - ((val - low) / span) * draw_h
            pts.append(QPointF(x, y))

        if self.fill:
            path_fill = QPainterPath()
            path_fill.moveTo(pts[0].x(), h)
            for pt in pts:
                path_fill.lineTo(pt)
            path_fill.lineTo(pts[-1].x(), h)
            path_fill.closeSubpath()

            grad = QLinearGradient(0, 0, 0, h)
            c_top = QColor(self.color)
            c_top.setAlpha(40)
            c_bottom = QColor(self.color)
            c_bottom.setAlpha(0)
            grad.setColorAt(0.0, c_top)
            grad.setColorAt(1.0, c_bottom)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawPath(path_fill)

        pen = QPen(self.color, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for i in range(len(pts) - 1):
            painter.drawLine(pts[i], pts[i + 1])

        painter.setBrush(self.color)
        painter.drawEllipse(pts[-1], 2.2, 2.2)


class CardIconWidget(QWidget):
    """KPI 卡片左侧矢量图标容器"""

    def __init__(self, icon_type="stock", parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.setFixedSize(36, 36)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        bg_color = (
            QColor("#EFF6FF")
            if self.icon_type in ("stock", "invest")
            else QColor("#ECFDF5")
        )
        fg_color = (
            QColor("#2563EB")
            if self.icon_type in ("stock", "invest")
            else QColor("#16A34A")
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect, 8, 8)

        painter.setPen(QPen(fg_color, 1.8, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        cx, cy = rect.center().x(), rect.center().y()
        if self.icon_type == "stock":
            # 图层堆叠图标
            painter.drawRoundedRect(QRectF(cx - 8, cy - 8, 16, 6), 2, 2)
            painter.drawLine(QPointF(cx - 7, cy + 2), QPointF(cx + 7, cy + 2))
            painter.drawLine(QPointF(cx - 7, cy + 7), QPointF(cx + 7, cy + 7))
        elif self.icon_type == "invest":
            # 入金/下箭头买入图标
            painter.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx - 4, cy), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx + 4, cy), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx - 6, cy + 8), QPointF(cx + 6, cy + 8))
        else:
            # 柱状图/资产增长图标
            painter.drawLine(QPointF(cx - 6, cy + 7), QPointF(cx - 6, cy + 1))
            painter.drawLine(QPointF(cx - 1, cy + 7), QPointF(cx - 1, cy - 3))
            painter.drawLine(QPointF(cx + 4, cy + 7), QPointF(cx + 4, cy - 7))


class AssetKpiCard(QFrame):
    """顶部三个关键指标卡片：股票市值、累计净买入、市值差额"""

    def __init__(self, title, icon_type="stock", parent=None):
        super().__init__(parent)
        self.setObjectName("AssetKpiCard")
        self.setStyleSheet(
            """
            QFrame#AssetKpiCard {
                background: #FFFFFF;
                border: 1px solid #E5EAF1;
                border-radius: 10px;
            }
        """
        )
        self.setFixedHeight(84)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(12)

        self.icon_widget = CardIconWidget(icon_type, self)
        main_layout.addWidget(self.icon_widget)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.lbl_title = QLabel(title)
        self.lbl_title.setStyleSheet("font-size: 13px; color: #64748B; font-weight: 500;")
        top_row.addWidget(self.lbl_title)

        self.badge = QLabel()
        self.badge.setFixedHeight(18)
        self.badge.setStyleSheet(
            "font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 4px;"
        )
        self.badge.hide()
        top_row.addWidget(self.badge)
        top_row.addStretch()

        info_layout.addLayout(top_row)

        self.lbl_value = QLabel("—")
        self.lbl_value.setStyleSheet(
            "font-size: 22px; font-weight: 700; color: #17253D; font-family: monospace, -apple-system;"
        )
        info_layout.addWidget(self.lbl_value)

        main_layout.addLayout(info_layout, 1)

        self.sparkline = SparklineWidget(
            self, color="#2563EB" if icon_type != "pnl" else "#16A34A"
        )
        main_layout.addWidget(self.sparkline)

    def set_value(self, text, is_gain=None, badge_text=None, history=None):
        self.lbl_value.setText(text)
        if is_gain is not None:
            color = "#16A34A" if is_gain else "#DC2626"
            self.lbl_value.setStyleSheet(
                f"font-size: 22px; font-weight: 700; color: {color}; font-family: monospace, -apple-system;"
            )
        else:
            self.lbl_value.setStyleSheet(
                "font-size: 22px; font-weight: 700; color: #17253D; font-family: monospace, -apple-system;"
            )

        if badge_text:
            self.badge.setText(badge_text)
            if is_gain is not None:
                bg = "#DCFCE7" if is_gain else "#FEE2E2"
                fg = "#16A34A" if is_gain else "#DC2626"
                self.badge.setStyleSheet(
                    f"font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 4px; background: {bg}; color: {fg};"
                )
            self.badge.show()
        else:
            self.badge.hide()

        if history:
            self.sparkline.set_data(history)
            if is_gain is not None:
                self.sparkline.color = QColor("#16A34A" if is_gain else "#DC2626")
                self.sparkline.update()


class AssetFilterCard(QFrame):
    """独立白色圆角筛选卡片，支持人员、渠道、代码、日期及 Chips 标签交互"""

    filter_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AssetFilterCard")
        self.setStyleSheet(
            """
            QFrame#AssetFilterCard {
                background: #FFFFFF;
                border: 1px solid #E5EAF1;
                border-radius: 10px;
            }
            QLabel {
                font-size: 12px;
                color: #64748B;
                font-weight: 500;
            }
            QComboBox, QLineEdit {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                color: #1E293B;
                min-height: 20px;
            }
            QComboBox:hover, QLineEdit:focus {
                border-color: #2563EB;
            }
            QPushButton {
                background: #F8FAFC;
                border: 1px solid #E2E8F0;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                color: #475569;
                font-weight: 500;
                min-height: 20px;
            }
            QPushButton:hover {
                background: #F1F5F9;
                color: #1E293B;
            }
        """
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        # 第一行：筛选器控件
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(10)

        row1.addWidget(QLabel("人员"))
        self.combo_person = QComboBox()
        self.combo_person.setMinimumWidth(100)
        self.combo_person.addItem("个人", "个人")
        self.combo_person.addItem("家庭", "家庭")
        self.combo_person.addItem("全部人员", None)
        row1.addWidget(self.combo_person)

        row1.addWidget(QLabel("渠道"))
        self.combo_channel = QComboBox()
        self.combo_channel.setMinimumWidth(110)
        self.combo_channel.addItem("全部渠道", None)
        row1.addWidget(self.combo_channel)

        self.input_ticker = QLineEdit()
        self.input_ticker.setPlaceholderText("🔍 输入 Ticker")
        self.input_ticker.setMaximumWidth(120)
        row1.addWidget(self.input_ticker)

        self.btn_date_range = QPushButton("2025-01-01 — 2026-11-09 ∨")
        row1.addWidget(self.btn_date_range)

        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("🔍 搜索代码、订单")
        self.input_search.setMaximumWidth(150)
        row1.addWidget(self.input_search)

        self.btn_advanced = QPushButton("▽ 高级筛选")
        row1.addWidget(self.btn_advanced)

        self.btn_reset = QPushButton("↺ 重置")
        self.btn_reset.setStyleSheet(
            "QPushButton { background: #FFFFFF; border: 1px solid #E2E8F0; color: #64748B; }"
            "QPushButton:hover { background: #F8FAFC; color: #1E293B; }"
        )
        row1.addWidget(self.btn_reset)
        row1.addStretch()

        main_layout.addLayout(row1)

        # 第二行：生效过滤标签 Chips
        self.chips_row = QHBoxLayout()
        self.chips_row.setContentsMargins(0, 0, 0, 0)
        self.chips_row.setSpacing(8)

        self.chip_person = self.create_chip("个人 ×")
        self.chip_currency = self.create_chip("USD ×")
        self.chip_scope = self.create_chip("股票市值口径 ×")

        self.chips_row.addWidget(self.chip_person)
        self.chips_row.addWidget(self.chip_currency)
        self.chips_row.addWidget(self.chip_scope)
        self.chips_row.addStretch()

        main_layout.addLayout(self.chips_row)

        self.btn_reset.clicked.connect(self.reset_filters)
        self.combo_person.currentIndexChanged.connect(self.on_filter_changed)
        self.combo_channel.currentIndexChanged.connect(self.on_filter_changed)
        self.input_ticker.returnPressed.connect(self.on_filter_changed)

    def create_chip(self, text):
        btn = QPushButton(text)
        btn.setStyleSheet(
            """
            QPushButton {
                background: #EFF6FF;
                border: 1px solid #DBEAFE;
                color: #2563EB;
                border-radius: 12px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 500;
                min-height: 18px;
            }
            QPushButton:hover {
                background: #DBEAFE;
            }
        """
        )
        btn.clicked.connect(lambda: btn.hide())
        return btn

    def reset_filters(self):
        self.combo_person.setCurrentIndex(0)
        self.combo_channel.setCurrentIndex(0)
        self.input_ticker.clear()
        self.input_search.clear()
        self.chip_person.show()
        self.chip_currency.show()
        self.chip_scope.show()
        self.filter_changed.emit()

    def on_filter_changed(self):
        person_txt = self.combo_person.currentText()
        self.chip_person.setText(f"{person_txt} ×")
        self.chip_person.show()
        self.filter_changed.emit()


class ChartCanvas(QWidget):
    selected = Signal(str, str)

    def __init__(self, parent=None, *, card_mode=False):
        super().__init__(parent)
        self.card_mode = card_mode
        self.chart, self.hits = None, []
        self.setMinimumSize(360, 260)
        self.setMouseTracking(True)
        self.hover_index = None
        self.categories_x = []

    def set_loading(self, message="", completed=None, total=None):
        self.loading_message = message
        self.loading_progress = (completed, total)
        if not hasattr(self, "loading_timer"):
            self.loading_angle = 0
            self.loading_timer = QTimer(self)
            self.loading_timer.setInterval(80)

            def advance():
                self.loading_angle = (self.loading_angle + 24) % 360
                self.update()

            self.loading_timer.timeout.connect(advance)
        if message:
            self.loading_timer.start()
        else:
            self.loading_timer.stop()
        self.update()

    def mouseMoveEvent(self, event):
        if self.chart and self.chart.kind == "capital_assets":
            pos = event.position()
            if self.categories_x:
                best_idx = None
                min_dist = float("inf")
                for idx, cx in enumerate(self.categories_x):
                    dist = abs(cx - pos.x())
                    if dist < min_dist:
                        min_dist = dist
                        best_idx = idx
                # 只有在图表水平有效跨度内吸附
                if min_dist < 40:
                    if self.hover_index != best_idx:
                        self.hover_index = best_idx
                        self.update()
                else:
                    if self.hover_index is not None:
                        self.hover_index = None
                        self.update()
            return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hover_index is not None:
            self.hover_index = None
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        t = tokens(self)
        colors = (
            t.brand,
            t.warning,
            t.teal,
            t.violet,
            "#EC4899",
            "#0284C7",
            "#EA580C",
            "#4F46E5",
            t.loss,
            t.muted,
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.card_mode:
            painter.setPen(QPen(QColor(t.border), 1))
            painter.setBrush(QColor(t.panel))
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10
            )
        else:
            painter.fillRect(self.rect(), QColor(t.panel))
        self.hits = []
        chart = self.chart
        if getattr(self, "loading_message", ""):
            center = self.rect().center()
            painter.setPen(QPen(QColor(t.brand), 3))
            painter.drawArc(
                QRectF(center.x() - 14, center.y() - 50, 28, 28),
                self.loading_angle * 16,
                260 * 16,
            )
            painter.setPen(QColor(t.heading))
            painter.setFont(chart_font(14))
            painter.drawText(
                QRectF(30, center.y() - 10, self.width() - 60, 85),
                Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                self.loading_message,
            )
            done, total = self.loading_progress
            if total:
                fraction = max(0, min(1, (done or 0) / total))
                bar = QRectF(
                    center.x() - min(180, self.width() / 3),
                    center.y() + 80,
                    min(360, self.width() * 2 / 3),
                    8,
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(t.border))
                painter.drawRoundedRect(bar, 4, 4)
                filled = QRectF(bar)
                filled.setWidth(bar.width() * fraction)
                painter.setBrush(QColor(t.brand))
                painter.drawRoundedRect(filled, 4, 4)
                painter.setPen(QColor(t.muted))
                painter.setFont(chart_font(12))
                painter.drawText(
                    QRectF(30, center.y() + 98, self.width() - 60, 30),
                    Qt.AlignmentFlag.AlignHCenter,
                    f"{done or 0}/{total}（{int(fraction*100)}%）",
                )
            return
        if not chart:
            painter.setPen(QColor(t.heading))
            painter.setFont(chart_font(15 if self.card_mode else 17, bold=True))
            painter.drawText(
                QRectF(20 if self.card_mode else 24, 14, self.width() - 40, 32),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "等待计算结果",
            )
            return

        if chart.kind == "capital_assets":
            self.paint_capital_assets(painter, chart, t)
            return

        painter.setPen(QColor(t.heading))
        painter.setFont(chart_font(15 if self.card_mode else 17, bold=True))
        painter.drawText(
            QRectF(20 if self.card_mode else 24, 14, self.width() - 40, 32),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            chart.title,
        )

        painter.setFont(chart_font(11))
        painter.setPen(QColor(t.muted))
        painter.drawText(
            QRectF(
                20 if self.card_mode else 24,
                self.height() - 44,
                self.width() - 40,
                38,
            ),
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap,
            chart.note,
        )
        painter.setFont(chart_font(13))
        data = [
            (series.name, key, value, index)
            for index, series in enumerate(chart.series)
            for key, value in series.points
            if value is not None and key.strip()
        ]
        if not data:
            painter.drawText(
                QRectF(30, 90, self.width() - 60, 160),
                Qt.AlignmentFlag.AlignCenter,
                getattr(self, "empty_message", None)
                or "该筛选组合暂无完整数据\n请检查行情日期",
            )
            return

        if chart.kind == "pie":
            positive = [(s, k, v, i) for s, k, v, i in data if v > 0]
            total = sum((v for _, _, v, _ in positive), Decimal(0))
            if not total:
                painter.drawText(
                    QRectF(30, 90, self.width() - 60, 160),
                    Qt.AlignmentFlag.AlignCenter,
                    "暂无有效正数占比数据",
                )
                return
            size = min(self.height() - 130, self.width() // 2 - 25)
            box = QRectF(
                25 if self.card_mode else 35,
                62 if self.card_mode else 72,
                size,
                size,
            )
            angle = 90 * 16
            for index, (series, label, val, _) in enumerate(positive):
                span = float(val / total) * 360 * 16
                painter.setPen(QPen(QColor(t.panel), 3))
                painter.setBrush(QColor(colors[index % len(colors)]))
                painter.drawPie(box, int(angle), int(span))
                angle += span
                target = QRectF(
                    box.right() + (20 if self.card_mode else 30),
                    (62 if self.card_mode else 74) + index * 34,
                    min(420, self.width() - box.right() - (35 if self.card_mode else 55)),
                    30,
                )
                painter.setPen(QColor(t.heading))
                painter.fillRect(
                    QRectF(target.x(), target.y() + 9, 10, 10),
                    QColor(colors[index % len(colors)]),
                )
                text_x, text_width = target.x() + 18, target.width() - 18
                name_width, amount_width = text_width * 0.34, text_width * 0.40
                name_box = QRectF(text_x, target.y(), name_width, target.height())
                amount_box = QRectF(
                    text_x + name_width, target.y(), amount_width, target.height()
                )
                share_box = QRectF(
                    amount_box.right() + 10,
                    target.y(),
                    max(35.0, text_width * 0.26 - 10),
                    target.height(),
                )
                name = painter.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideRight, int(name_width - 6)
                )
                painter.drawText(
                    name_box,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    name,
                )
                painter.drawText(
                    amount_box,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    f"{val:,.2f}",
                )
                painter.drawText(
                    share_box,
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    f"{val / total:.2%}",
                )
                self.hits.append((target, label, val, series))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.panel))
            painter.drawEllipse(
                box.adjusted(size * 0.30, size * 0.30, -size * 0.30, -size * 0.30)
            )
            return

        if chart.kind == "horizontal":
            data = data[: max(24, len(data))]
            point_labels = getattr(chart, "point_labels", None) or {}

            def get_text(lbl, val):
                if lbl in point_labels:
                    return point_labels[lbl]
                return f"{val:+.2%}" if chart.ratio else f"{val:+,.2f}"

            label_width = min(
                210,
                max(
                    80,
                    max(
                        (
                            painter.fontMetrics().horizontalAdvance(label[:30])
                            for _, label, _, _ in data
                        ),
                        default=80,
                    ),
                ),
            )
            max_val_width = max(
                (
                    painter.fontMetrics().horizontalAdvance(get_text(lbl, val))
                    for _, lbl, val, _ in data
                ),
                default=80,
            )
            val_margin = max(95, max_val_width + 12)
            left, top, bottom = label_width + max(120, val_margin + 10), 74, self.height() - 67
            low, high = min(Decimal(0), min(v for _, _, v, _ in data)), max(
                Decimal(0), max(v for _, _, v, _ in data)
            )
            width = max(1, self.width() - left - max(105, val_margin + 10))
            span = high - low or Decimal(1)

            def x(value):
                return left + float((value - low) / span) * width

            painter.setPen(QPen(QColor(t.border), 1))
            painter.drawLine(
                QPointF(x(Decimal(0)), top - 6), QPointF(x(Decimal(0)), bottom)
            )
            h = (bottom - top) / max(len(data), 1)
            for index, (series, label, value, color) in enumerate(data):
                y = top + index * h
                painter.setPen(QColor(t.text))
                text = painter.fontMetrics().elidedText(
                    label, Qt.TextElideMode.ElideRight, label_width
                )
                painter.drawText(
                    QRectF(10, y, label_width, h),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    text,
                )
                a, b = x(Decimal(0)), x(value)
                rect = QRectF(min(a, b), y + h * 0.2, max(abs(b - a), 2), h * 0.58)
                painter.fillRect(
                    rect,
                    QColor(
                        getattr(self, "bar_color", None)
                        or (t.profit if value >= 0 else t.loss)
                    ),
                )
                val_text = get_text(label, value)
                val_w = painter.fontMetrics().horizontalAdvance(val_text) + 8
                val_rect = (
                    QRectF(b + 5, y, val_w, h)
                    if value >= 0
                    else QRectF(b - val_w - 5, y, val_w, h)
                )
                painter.drawText(
                    val_rect,
                    Qt.AlignmentFlag.AlignVCenter
                    | (
                        Qt.AlignmentFlag.AlignLeft
                        if value >= 0
                        else Qt.AlignmentFlag.AlignRight
                    ),
                    val_text,
                )
                self.hits.append((QRectF(0, y, self.width(), h), label, value, series))
            self.draw_zero(
                painter,
                QPointF(x(Decimal(0)), top - 6),
                QPointF(x(Decimal(0)), bottom),
                QRectF(x(Decimal(0)) - 20, top - 31, 40, 22),
                t,
            )
            return

        left, top, width, height = 75, 76, self.width() - 105, self.height() - 198
        low, high = min(Decimal(0), min(v for _, _, v, _ in data)), max(
            Decimal(0), max(v for _, _, v, _ in data)
        )
        span = high - low or Decimal(1)
        if getattr(chart, "highlight_extremes", False):
            y_pad = span * Decimal("0.15")
            high += y_pad
            low -= y_pad
            span = high - low or Decimal(1)

        def ys(value):
            return top + height - float((value - low) / span) * height

        categories = list(
            dict.fromkeys(key for series in chart.series for key, _ in series.points)
        )
        category_positions = {key: index for index, key in enumerate(categories)}
        fixed_axis = bool(chart.x_ticks)
        step = width / max(len(categories) - (1 if fixed_axis else 0), 1)
        for mark in range(5):
            value = low + span * Decimal(mark) / 4
            y = ys(value)
            if abs(y - ys(Decimal(0))) < 20:
                continue
            painter.setPen(QPen(QColor(t.border), 1))
            painter.drawLine(QPointF(left, y), QPointF(left + width, y))
            painter.setPen(QColor(t.muted))
            painter.drawText(
                QRectF(2, y - 9, left - 10, 20),
                Qt.AlignmentFlag.AlignRight,
                f"{value:.0%}" if chart.ratio else f"{value:,.0f}",
            )

        show_points = getattr(chart, "show_points", True)
        break_keys = set(getattr(chart, "break_keys", ()))
        for index, series in enumerate(chart.series):
            color = QColor(colors[index % len(colors)])
            last = None
            for key, value in series.points:
                if key in break_keys:
                    last = None
                if value is None:
                    last = None
                    continue
                x, y = (
                    left + (category_positions[key] + (0 if fixed_axis else 0.5)) * step,
                    ys(value),
                )
                painter.setPen(QPen(color, getattr(chart, "line_width", 2.0)))
                if chart.kind == "line":
                    if last and not series.name.startswith(("B ", "S ", "◇")):
                        painter.drawLine(last, QPointF(x, y))
                    if show_points:
                        painter.setBrush(color)
                        radius = 5 if series.name.startswith(("B ", "S ")) else 3
                        painter.drawEllipse(QPointF(x, y), radius, radius)
                    last = QPointF(x, y)
                else:
                    barwidth = min(40, step * 0.72 / max(len(chart.series), 1))
                    painter.fillRect(
                        QRectF(
                            x - step * 0.35 + index * barwidth,
                            min(ys(Decimal(0)), y),
                            max(1, barwidth - 1),
                            abs(y - ys(Decimal(0))) or 1,
                        ),
                        color,
                    )
                self.hits.append(
                    (QRectF(x - step / 2, top, step, height), key, value, series.name)
                )

        zero_y = ys(Decimal(0))
        self.draw_zero(
            painter,
            QPointF(left, zero_y),
            QPointF(left + width, zero_y),
            QRectF(2, zero_y - 11, left - 12, 22),
            t,
            ratio=chart.ratio,
        )
        painter.setFont(chart_font(12))
        for index, key in enumerate(categories):
            visible = index % max(1, math.ceil(len(categories) / 12)) == 0
            if visible:
                painter.setPen(QColor(t.muted))
                painter.drawText(
                    QRectF(left + index * step - 20, top + height + 8, step + 40, 22),
                    Qt.AlignmentFlag.AlignCenter,
                    chart.x_labels.get(key, key),
                )

        painter.save()
        painter.setFont(chart_font(12, bold=len(chart.series) > 1))
        legend_x = float(left)
        legend_y = float(self.height() - 83)
        for s_idx, s in enumerate(chart.series):
            s_color = QColor(colors[s_idx % len(colors)])
            painter.setPen(QPen(s_color, 2.5))
            painter.drawLine(
                QPointF(legend_x, legend_y + 12.5), QPointF(legend_x + 16, legend_y + 12.5)
            )
            painter.setBrush(s_color)
            painter.drawEllipse(QPointF(legend_x + 8, legend_y + 12.5), 3.0, 3.0)
            legend_x += 22
            s_label = s.name
            painter.setPen(s_color if len(chart.series) > 1 else QColor(t.text))
            tw_lbl = painter.fontMetrics().horizontalAdvance(s_label) + 4
            painter.drawText(
                QRectF(legend_x, legend_y, tw_lbl, 25),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                s_label,
            )
            legend_x += tw_lbl + 18
        painter.restore()

    def draw_zero(self, painter, start, end, label_rect, theme, *, ratio=False):
        painter.save()
        painter.setPen(QPen(QColor(theme.text), 2, Qt.PenStyle.SolidLine))
        painter.drawLine(start, end)
        painter.setFont(chart_font(13, bold=True))
        alignment = (
            Qt.AlignmentFlag.AlignHCenter
            if start.x() == end.x()
            else Qt.AlignmentFlag.AlignRight
        )
        painter.drawText(
            label_rect, alignment | Qt.AlignmentFlag.AlignVCenter, "0%" if ratio else "0"
        )
        painter.restore()

    def paint_capital_assets(self, painter, chart, theme):
        """专业柱状图与股票市值折线组合图（累计净买入基柱 + 市值高于/低于净买入 + 股票市值折线 + Tooltip交互）"""
        palette = ("#B8CBD7", "#70AD47", "#C00000", "#4472C4")
        series = chart.series
        categories = list(dict.fromkeys(key for item in series for key, _ in item.points))
        points = [dict(item.points) for item in series]

        extra = getattr(chart, "extra", {})
        monthly_inv = extra.get("monthly_investments", {})
        latest_profit_day = extra.get("latest_profit_day", "")
        currency = extra.get("reporting_currency", "USD")

        bounds = [Decimal(0)]
        for key in categories:
            segments = [data.get(key) for data in points[:3]]
            if all(value is not None for value in segments):
                bounds.extend(
                    (
                        sum((v for v in segments if v >= 0), Decimal(0)),
                        sum((v for v in segments if v < 0), Decimal(0)),
                    )
                )
            if points[3].get(key) is not None:
                bounds.append(points[3][key])
        low, high = min(bounds), max(bounds)
        raw_step = (high - low) / 6 or Decimal(1)
        magnitude = Decimal(10) ** raw_step.adjusted()
        tick = next(
            n * magnitude for n in map(Decimal, (1, 2, 5, 10)) if n * magnitude >= raw_step
        )
        low = (low // tick - (1 if low % tick else 0)) * tick
        high = ((high // tick) + 1) * tick

        left, top = 86, 75
        width, height = self.width() - left - 30, self.height() - top - 95
        span = high - low
        step = width / max(len(categories), 1)

        def ys(value):
            return top + height - float((value - low) / span) * height

        # 1. 顶部 Header 与图例
        # 左侧标题与胶囊
        painter.setFont(chart_font(15, bold=True))
        painter.setPen(QColor(theme.heading))
        chart_title_text = "累计净买入与股票市值变化"
        painter.drawText(
            QRectF(left, 16, 220, 24),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            chart_title_text,
        )

        title_w = painter.fontMetrics().horizontalAdvance(chart_title_text)
        badge_rect = QRectF(left + title_w + 14, 18, 175, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#EFF6FF"))
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QColor("#2563EB"))
        painter.setFont(chart_font(11, bold=True))
        painter.drawText(
            badge_rect,
            Qt.AlignmentFlag.AlignCenter,
            "保留柱状图，更利于月度对比",
        )

        # 居中图例
        legend_labels = (
            "累计净买入基柱",
            "市值高于净买入",
            "市值低于净买入",
            "股票市值",
        )
        legend_widths = [
            painter.fontMetrics().horizontalAdvance(lbl) + 36 for lbl in legend_labels
        ]
        legend_x = max(left + title_w + 205, (self.width() - sum(legend_widths)) / 2)
        for index, lbl in enumerate(legend_labels):
            painter.setPen(QPen(QColor(palette[index]), 2.5))
            if index == 3:
                painter.drawLine(
                    QPointF(legend_x, 28), QPointF(legend_x + 22, 28)
                )
                painter.setBrush(QColor(palette[3]))
                painter.drawEllipse(QPointF(legend_x + 11, 28), 3.0, 3.0)
            else:
                painter.fillRect(QRectF(legend_x, 23, 20, 10), QColor(palette[index]))
            painter.setPen(QColor(theme.text))
            painter.setFont(chart_font(12))
            painter.drawText(
                QRectF(legend_x + 26, 16, legend_widths[index] - 26, 24),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                lbl,
            )
            legend_x += legend_widths[index]

        # 右侧未完成月份胶囊提示
        if latest_profit_day:
            day_str = latest_profit_day[-5:]
            right_badge = f"当前月份 · 截至 {day_str}"
            rb_w = painter.fontMetrics().horizontalAdvance(right_badge) + 18
            rb_rect = QRectF(self.width() - 30 - rb_w, 18, rb_w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F3F4F6"))
            painter.drawRoundedRect(rb_rect, 4, 4)
            painter.setPen(QColor("#4B5563"))
            painter.setFont(chart_font(11, bold=True))
            painter.drawText(rb_rect, Qt.AlignmentFlag.AlignCenter, right_badge)

        # 2. 金额 Y 轴及水平网格线
        painter.setFont(chart_font(11))
        painter.setPen(QColor(theme.muted))
        painter.drawText(
            QRectF(left, top - 24, 150, 20),
            Qt.AlignmentFlag.AlignLeft,
            f"金额（{currency}）",
        )

        value = low
        while value <= high:
            y = ys(value)
            if value != 0:
                painter.setPen(QPen(QColor(theme.border), 1))
                painter.drawLine(QPointF(left, y), QPointF(left + width, y))
                painter.setPen(QColor(theme.muted))
                painter.drawText(
                    QRectF(0, y - 10, left - 10, 20),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    f"{value:,.0f}",
                )
            value += tick

        # 3. 柱状图与折线绘制
        last = None
        self.categories_x = []
        bar_width = min(42, step * 0.62)

        for index, key in enumerate(categories):
            x = left + (index + 0.5) * step
            self.categories_x.append(x)
            segments = [data.get(key) for data in points[:3]]
            positive = negative = Decimal(0)
            if all(val is not None for val in segments):
                for segment_index, val in enumerate(segments):
                    start = positive if val >= 0 else negative
                    end = start + val
                    if val >= 0:
                        positive = end
                    else:
                        negative = end
                    rect = QRectF(
                        x - bar_width / 2,
                        min(ys(start), ys(end)),
                        bar_width,
                        abs(ys(start) - ys(end)),
                    )
                    painter.fillRect(rect, QColor(palette[segment_index]))
                    if val:
                        self.hits.append(
                            (rect, key, val, series[segment_index].name)
                        )

            asset = points[3].get(key)
            if asset is None:
                last = None
            else:
                pt = QPointF(x, ys(asset))
                painter.setPen(QPen(QColor(palette[3]), 2.5))
                if last is not None:
                    painter.drawLine(last, pt)
                last = pt
                # 绘制折线数据点
                painter.setBrush(QColor(palette[3]))
                painter.drawEllipse(pt, 3.5, 3.5)
                self.hits.append(
                    (QRectF(x - 9, pt.y() - 9, 18, 18), key, asset, series[3].name)
                )

            # X 轴月份标签：当前未完成月份加星号
            painter.setPen(QColor(theme.muted))
            label = key[2:] if len(key) == 7 else key
            if index == len(categories) - 1 and latest_profit_day:
                if not latest_profit_day.endswith(("-30", "-31", "-28", "-29")):
                    label += "*"
            painter.drawText(
                QRectF(x - 25, top + height + 8, 50, 20),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        # 零轴
        zero_y = ys(Decimal(0))
        self.draw_zero(
            painter,
            QPointF(left, zero_y),
            QPointF(left + width, zero_y),
            QRectF(0, zero_y - 11, left - 10, 22),
            theme,
        )

        # 4. 鼠标悬停辅助虚线、折线高亮与浮动 Tooltip
        if self.hover_index is not None and 0 <= self.hover_index < len(categories):
            hx = self.categories_x[self.hover_index]
            h_key = categories[self.hover_index]

            # 垂直辅助虚线
            v_pen = QPen(QColor(156, 163, 175, 180), 1, Qt.PenStyle.DashLine)
            painter.setPen(v_pen)
            painter.drawLine(QPointF(hx, top), QPointF(hx, top + height))

            h_asset = points[3].get(h_key)
            if h_asset is not None:
                hy = ys(h_asset)
                # 高亮数据点双层同心圆
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(68, 114, 196, 60))
                painter.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
                painter.setBrush(QColor(palette[3]))
                painter.drawEllipse(QPointF(hx, hy), 4.0, 4.0)

            # 提取 4 项指标数值
            v0 = points[0].get(h_key) or ZERO
            v1 = points[1].get(h_key) or ZERO
            v2 = points[2].get(h_key) or ZERO
            m_val = points[3].get(h_key) or ZERO
            c_val = v0 + v2 if v2 > 0 else v0
            diff = m_val - c_val
            inv_val = monthly_inv.get(h_key)
            if inv_val is None:
                inv_val = c_val

            # 浮动 Tooltip 弹窗卡片
            tip_w, tip_h = 200, 126
            tip_x = hx + 14
            if tip_x + tip_w > self.width() - 10:
                tip_x = hx - 14 - tip_w
            tip_y = max(
                top + 6,
                min(
                    ys(m_val) - tip_h / 2 if h_asset is not None else top + 20,
                    top + height - tip_h - 10,
                ),
            )

            tip_rect = QRectF(tip_x, tip_y, tip_w, tip_h)

            # 半透明白色阴影背景
            painter.setPen(QPen(QColor("#E2E8F0"), 1))
            painter.setBrush(QColor(255, 255, 255, 245))
            painter.drawRoundedRect(tip_rect, 8, 8)

            # 标题：2026年05月 或 2026年11月 · 截至 11-09
            y_title = h_key.replace("-", "年") + "月"
            if self.hover_index == len(categories) - 1 and latest_profit_day:
                y_title += f" · 截至 {latest_profit_day[-5:]}"

            painter.setPen(QColor("#1E293B"))
            painter.setFont(chart_font(12, bold=True))
            painter.drawText(
                QRectF(tip_x + 12, tip_y + 8, tip_w - 24, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                y_title,
            )

            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            painter.drawLine(
                QPointF(tip_x + 12, tip_y + 32), QPointF(tip_x + tip_w - 12, tip_y + 32)
            )

            # 4 项指标逐行绘制
            painter.setFont(chart_font(11))
            rows_data = [
                ("股票市值", f"${m_val:,.2f}", palette[3], "#1E293B"),
                ("累计净买入", f"${c_val:,.2f}", palette[0], "#475569"),
                (
                    "市值差额",
                    f"{'+' if diff >= 0 else '-'}${abs(diff):,.2f}",
                    None,
                    "#16A34A" if diff >= 0 else "#DC2626",
                ),
                (
                    "当月净买入",
                    f"{'+' if inv_val >= 0 else '-'}${abs(inv_val):,.2f}",
                    None,
                    "#16A34A" if inv_val >= 0 else "#DC2626",
                ),
            ]

            cur_y = tip_y + 38
            for label_name, val_str, dot_col, text_col in rows_data:
                # 绘制圆点或缩进
                if dot_col:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(dot_col))
                    painter.drawEllipse(QPointF(tip_x + 16, cur_y + 8), 3.0, 3.0)
                    painter.setPen(QColor("#64748B"))
                    painter.drawText(
                        QRectF(tip_x + 24, cur_y, 75, 18),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        label_name,
                    )
                else:
                    painter.setPen(QColor("#64748B"))
                    painter.drawText(
                        QRectF(tip_x + 16, cur_y, 80, 18),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        label_name,
                    )

                painter.setPen(QColor(text_col))
                painter.setFont(chart_font(11, bold=True))
                painter.drawText(
                    QRectF(tip_x + 95, cur_y, tip_w - 107, 18),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    val_str,
                )
                painter.setFont(chart_font(11))
                cur_y += 20

        # 5. 底部口径说明文字
        painter.setFont(chart_font(11))
        painter.setPen(QColor(theme.muted))
        note_y = self.height() - 48
        painter.drawText(
            QRectF(left, note_y, width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"ⓘ {chart.note}",
        )
        painter.drawText(
            QRectF(left, note_y + 20, width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "说明：累计净买入基柱为累计投入；绿色为市值高于净买入（正差额）；红色为市值低于净买入（负差额）；蓝线为股票市值。股票市值仅表示股票持仓市值，不包含现金、融资及估值核对调整。",
        )

    def mousePressEvent(self, event):
        if self.chart:
            for rect, key, _, _ in reversed(self.hits):
                if rect.contains(event.position()):
                    self.selected.emit(self.chart.link, key)
                    return

    def export_png(self, path):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not self.grab().save(buffer, "PNG"):
            raise ValueError("图表 PNG 编码失败。")
        with Path(path).open("xb") as stream:
            stream.write(bytes(buffer.data()))


class ChartsPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.charts = ()

        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(16, 12, 16, 12)
        self.page_layout.setSpacing(10)

        # 1. 现代化资产走势图专属 Header
        self.asset_header = QWidget()
        h_layout = QHBoxLayout(self.asset_header)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(2)

        self.page_title = QLabel("资产走势图")
        self.page_title.setStyleSheet(
            "font-size: 18px; font-weight: 700; color: #17253D;"
        )
        title_col.addWidget(self.page_title)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(10)

        self.page_subtitle = QLabel("查看投资组合累计净买入、股票市值及投资表现")
        self.page_subtitle.setStyleSheet("font-size: 12px; color: #64748B;")
        sub_row.addWidget(self.page_subtitle)

        self.db_label = QLabel("🗄️ 数据源只读：money.sqlite3")
        self.db_label.setStyleSheet(
            "font-size: 11px; color: #94A3B8; background: #F1F5F9; border-radius: 4px; padding: 1px 6px;"
        )
        sub_row.addWidget(self.db_label)
        sub_row.addStretch()

        title_col.addLayout(sub_row)
        h_layout.addLayout(title_col)
        h_layout.addStretch()

        # 右侧操作按钮
        self.btn_prev = QPushButton("〈")
        self.btn_prev.setFixedSize(28, 28)
        self.btn_prev.setStyleSheet(
            "QPushButton { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; color: #64748B; }"
            "QPushButton:hover { background: #F8FAFC; color: #1E293B; }"
        )
        h_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton("〉")
        self.btn_next.setFixedSize(28, 28)
        self.btn_next.setStyleSheet(
            "QPushButton { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; color: #64748B; }"
            "QPushButton:hover { background: #F8FAFC; color: #1E293B; }"
        )
        h_layout.addWidget(self.btn_next)

        self.btn_refresh = QPushButton("↻ 刷新")
        self.btn_refresh.setFixedHeight(28)
        self.btn_refresh.setStyleSheet(
            "QPushButton { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; padding: 0 10px; font-size: 12px; color: #475569; }"
            "QPushButton:hover { background: #F8FAFC; color: #1E293B; }"
        )
        h_layout.addWidget(self.btn_refresh)

        self.btn_export_png = QPushButton("📥 导出 PNG")
        self.btn_export_png.setFixedHeight(28)
        self.btn_export_png.setStyleSheet(
            "QPushButton { background: #2563EB; border: none; border-radius: 6px; padding: 0 12px; font-size: 12px; font-weight: 600; color: #FFFFFF; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self.btn_export_png.clicked.connect(self.export)
        h_layout.addWidget(self.btn_export_png)

        # 小型图表切换下拉框，供需要查看其他26个图表时使用
        self.selector = QComboBox()
        self.selector.setMinimumWidth(180)
        self.selector.setStyleSheet(
            "QComboBox { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; padding: 2px 6px; font-size: 12px; }"
        )
        h_layout.addWidget(self.selector)

        self.page_layout.addWidget(self.asset_header)

        # 2. 顶部三个核心指标卡片
        self.kpi_container = QWidget()
        kpi_layout = QHBoxLayout(self.kpi_container)
        kpi_layout.setContentsMargins(0, 0, 0, 0)
        kpi_layout.setSpacing(12)

        self.card_market_val = AssetKpiCard("股票市值", "stock", self)
        self.card_net_buy = AssetKpiCard("累计净买入", "invest", self)
        self.card_diff = AssetKpiCard("市值差额", "pnl", self)

        kpi_layout.addWidget(self.card_market_val)
        kpi_layout.addWidget(self.card_net_buy)
        kpi_layout.addWidget(self.card_diff)

        self.page_layout.addWidget(self.kpi_container)

        # 3. 独立白色筛选卡片
        self.filter_card = AssetFilterCard(self)
        self.page_layout.addWidget(self.filter_card)

        # 传统控制行（在查看其他非资产走势图图表时显示）
        self.classic_row = QHBoxLayout()
        self.month_label = QLabel("查看月份")
        self.classic_row.addWidget(self.month_label)
        self.month = QComboBox()
        self.month.setMinimumWidth(160)
        self.month_label.hide()
        self.month.hide()
        self.classic_row.addWidget(self.month)

        self.offset = QComboBox()
        self.offset.addItems(
            ["排行榜 1–24", "排行榜 25–48", "排行榜 49–72", "排行榜 73–96"]
        )
        self.offset.hide()
        self.classic_row.addWidget(self.offset)

        self.rank_order = QComboBox()
        for label, value in (
            ("盈利最多", "profit"),
            ("亏损最多", "loss"),
            ("盈亏幅度最大", "magnitude"),
        ):
            self.rank_order.addItem(label, value)
        self.rank_order.hide()
        self.classic_row.addWidget(self.rank_order)
        self.classic_row.addStretch()

        self.page_layout.addLayout(self.classic_row)

        # 4. 图表画布容器
        self.canvas = ChartCanvas(card_mode=True)
        self.canvas.selected.connect(self.drill)
        self.page_layout.addWidget(self.canvas, 1)

        self.selector.currentIndexChanged.connect(self.show_chart)
        self.month.currentTextChanged.connect(self.update_month)
        self.offset.currentIndexChanged.connect(self.show_chart)
        self.rank_order.currentIndexChanged.connect(self.change_rank_order)

    def change_rank_order(self):
        self.offset.blockSignals(True)
        self.offset.setCurrentIndex(0)
        self.offset.blockSignals(False)
        self.show_chart()

    def clear(self):
        self.analytics = None
        self.charts = ()
        self.offset.hide()
        self.rank_order.hide()
        self.month_label.hide()
        self.month.hide()
        self.card_market_val.set_value("—")
        self.card_net_buy.set_value("—")
        self.card_diff.set_value("—")
        self.canvas.chart = None
        self.canvas.update()

    def set_analytics(self, analytics):
        self.analytics, self.charts = analytics, analytics.charts
        selected = self.selector.currentIndex()
        selected_month = self.month.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([f"{c.id:02}  {c.title}" for c in self.charts])
        # 如果未选择，默认展示 Chart 18（累计本金与总资产变化，即索引 17）
        target_idx = 17 if len(self.charts) > 17 and selected <= 0 else max(0, selected)
        self.selector.setCurrentIndex(target_idx)
        self.selector.blockSignals(False)

        self.month.blockSignals(True)
        self.month.clear()
        self.month.addItem("当前 / 最新", None)
        for month in sorted(
            {r.values[0] for r in analytics.reports["每月持仓收益"].rows},
            reverse=True,
        ):
            self.month.addItem(month, month)
        self.month.setCurrentIndex(max(0, self.month.findData(selected_month)))
        self.month.blockSignals(False)

        # 更新数据源名称
        if hasattr(analytics, "source_identity") and analytics.source_identity:
            src_name = Path(str(analytics.source_identity)).name
            self.db_label.setText(f"🗄️ 数据源只读：{src_name}")

        self.update_month()

    def update_month(self):
        if self.analytics:
            self.charts = build_charts(
                self.analytics, selected_month=self.month.currentData()
            )
            self.show_chart()

    def show_chart(self, *_):
        if not self.charts:
            self.offset.hide()
            self.rank_order.hide()
            self.month_label.hide()
            self.month.hide()
            return
        chart = self.charts[self.selector.currentIndex()]
        is_capital = chart.id == 18

        # 模式切换：当展示资产走势图时，展现现代化卡片布局
        self.kpi_container.setVisible(is_capital)
        self.filter_card.setVisible(is_capital)

        if is_capital:
            self.update_kpi_cards(chart)

        monthly = chart.id in (1, 2, 3, 4, 5, 6, 21)
        self.month_label.setVisible(monthly)
        self.month.setVisible(monthly)
        self.month_label.setText(
            "基准月份"
            if chart.id in (4, 5, 6)
            else "收益月份"
            if chart.id == 21
            else "查看月份"
        )
        self.month.setToolTip(
            "显示所选基准月份的上一个月末快照。"
            if chart.id in (4, 5, 6)
            else "选择历史月份后立即更新图表；当前 / 最新恢复最新数据。"
        )
        ranked = chart.kind == "horizontal" and chart.id in (15, 16, 17)
        self.offset.setVisible(ranked)
        self.rank_order.setVisible(ranked)
        if ranked:
            count = len({key for s in chart.series for key, _ in s.points})
            selected = self.offset.currentIndex()
            self.offset.blockSignals(True)
            self.offset.clear()
            self.offset.addItems(
                [
                    f"排行榜 {start+1}–{min(start+24, count)}"
                    for start in range(0, count, 24)
                ]
                or ["排行榜 0条"]
            )
            self.offset.setCurrentIndex(
                selected if 0 <= selected < self.offset.count() else 0
            )
            self.offset.blockSignals(False)
            start = self.offset.currentIndex() * 24
            chart = rank_chart(chart, self.rank_order.currentData(), start)

        self.canvas.chart = chart
        self.canvas.update()

    def update_kpi_cards(self, chart):
        """根据当前图表 18 的真实财务数据刷新顶部三个核心指标卡片"""
        if not chart or len(chart.series) < 4:
            return
        points = [dict(s.points) for s in chart.series]
        categories = [k for k, _ in chart.series[3].points if k]
        if not categories:
            self.card_market_val.set_value("—")
            self.card_net_buy.set_value("—")
            self.card_diff.set_value("—")
            return

        m_history = [points[3].get(k) for k in categories]
        c_history = []
        diff_history = []

        for k in categories:
            v0 = points[0].get(k) or ZERO
            v2 = points[2].get(k) or ZERO
            m = points[3].get(k) or ZERO
            c = v0 + v2 if v2 > 0 else v0
            c_history.append(c)
            diff_history.append(m - c)

        latest_m = m_history[-1] if m_history else ZERO
        latest_c = c_history[-1] if c_history else ZERO
        latest_diff = latest_m - latest_c

        # 卡片 1：股票市值
        self.card_market_val.set_value(f"${latest_m:,.0f}", history=m_history)

        # 卡片 2：累计净买入
        self.card_net_buy.set_value(f"${latest_c:,.0f}", history=c_history)

        # 卡片 3：市值差额
        is_gain = latest_diff >= ZERO
        sign = "+" if is_gain else "-"
        diff_text = f"{sign}${abs(latest_diff):,.0f}"

        badge_text = None
        if latest_c and latest_c != ZERO:
            ratio = (latest_diff / latest_c) * Decimal(100)
            badge_text = f"{'↗ +' if is_gain else '↘ '}{ratio:.1f}%"
        else:
            badge_text = "--"

        self.card_diff.set_value(
            diff_text,
            is_gain=is_gain,
            badge_text=badge_text,
            history=diff_history,
        )

    def export(self):
        if not self.canvas.chart:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "图表导出（含口径和日期）",
            self.canvas.chart.title + ".png",
            "PNG (*.png)",
        )
        if path:
            try:
                self.canvas.export_png(path)
            except (OSError, ValueError):
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
