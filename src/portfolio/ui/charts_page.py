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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from portfolio.domain.analytics import ZERO, strict_sum
from portfolio.services.charts_service import Chart, Series, build_charts, rank_chart
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
        self.setFixedHeight(48)
        self.setMinimumWidth(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_data(self, points):
        self.points_data = [float(v) for v in points if v is not None]
        self.update()

    def paintEvent(self, event):
        if len(self.points_data) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        pad_x, pad_y = 6.0, 5.0
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
            painter.drawRoundedRect(QRectF(cx - 8, cy - 8, 16, 6), 2, 2)
            painter.drawLine(QPointF(cx - 7, cy + 2), QPointF(cx + 7, cy + 2))
            painter.drawLine(QPointF(cx - 7, cy + 7), QPointF(cx + 7, cy + 7))
        elif self.icon_type == "invest":
            painter.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx - 4, cy), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx + 4, cy), QPointF(cx, cy + 4))
            painter.drawLine(QPointF(cx - 6, cy + 8), QPointF(cx + 6, cy + 8))
        else:
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

        main_layout.addLayout(info_layout)

        self.sparkline = SparklineWidget(
            self, color="#2563EB" if icon_type != "pnl" else "#16A34A"
        )
        main_layout.addWidget(self.sparkline, 1)

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
    """独立白色圆角筛选卡片，支持渠道、代码、日期及 Chips 标签与全局筛选双向联动"""

    filter_changed = Signal()

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
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

        # 第一行：筛选器控件（已移除高级筛选和重复的人员下拉框）
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(10)

        row1.addWidget(QLabel("渠道"))
        self.combo_channel = QComboBox()
        self.combo_channel.setMinimumWidth(120)
        self.combo_channel.addItem("全部渠道", None)
        row1.addWidget(self.combo_channel)

        self.input_ticker = QLineEdit()
        self.input_ticker.setPlaceholderText("🔍 输入 Ticker")
        self.input_ticker.setMaximumWidth(130)
        row1.addWidget(self.input_ticker)

        self.btn_date_range = QPushButton("2025-01-01 — 2026-11-09 ∨")
        row1.addWidget(self.btn_date_range)

        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("🔍 搜索代码、订单")
        self.input_search.setMaximumWidth(160)
        row1.addWidget(self.input_search)

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
        self.chip_channel = self.create_chip("全部渠道 ×")
        self.chip_channel.hide()
        self.chip_currency = self.create_chip("USD ×")
        self.chip_scope = self.create_chip("股票市值口径 ×")

        self.chips_row.addWidget(self.chip_person)
        self.chips_row.addWidget(self.chip_channel)
        self.chips_row.addWidget(self.chip_currency)
        self.chips_row.addWidget(self.chip_scope)
        self.chips_row.addStretch()

        main_layout.addLayout(self.chips_row)

        self.btn_reset.clicked.connect(self.reset_filters)
        self.combo_channel.currentIndexChanged.connect(self.on_channel_changed)
        self.input_ticker.returnPressed.connect(self.on_ticker_filter)

        # 点击人员 Chip 上的 ×，恢复为全部人员
        self.chip_person.clicked.connect(self.clear_person_filter)
        # 点击渠道 Chip 上的 ×，恢复为全部渠道
        self.chip_channel.clicked.connect(self.clear_channel_filter)

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
        return btn

    def set_available_channels(self, channels):
        current = self.combo_channel.currentText()
        self.combo_channel.blockSignals(True)
        self.combo_channel.clear()
        self.combo_channel.addItem("全部渠道", None)
        for ch in channels:
            if ch:
                self.combo_channel.addItem(str(ch), ch)
        idx = self.combo_channel.findText(current)
        if idx >= 0:
            self.combo_channel.setCurrentIndex(idx)
        self.combo_channel.blockSignals(False)

    def sync_broker(self, broker_name):
        self.combo_channel.blockSignals(True)
        if not broker_name:
            self.combo_channel.setCurrentIndex(0)
            self.chip_channel.hide()
        else:
            idx = self.combo_channel.findText(str(broker_name))
            if idx >= 0:
                self.combo_channel.setCurrentIndex(idx)
            self.chip_channel.setText(f"{broker_name} ×")
            self.chip_channel.show()
        self.combo_channel.blockSignals(False)

    def sync_person(self, person_text):
        if not person_text or "全部" in person_text:
            self.chip_person.setText("全部人员 ×")
        else:
            self.chip_person.setText(f"{person_text} ×")
        self.chip_person.show()

    def on_channel_changed(self):
        ch = self.combo_channel.currentText()
        if ch == "全部渠道":
            self.chip_channel.hide()
            if self.main_window and hasattr(self.main_window, "accounts"):
                self.main_window.accounts.reset()
        else:
            self.chip_channel.setText(f"{ch} ×")
            self.chip_channel.show()
            if self.main_window and hasattr(self.main_window, "accounts"):
                self.main_window.accounts.set_broker_name(ch)
        self.filter_changed.emit()

    def on_ticker_filter(self):
        ticker = self.input_ticker.text().strip().upper()
        if self.main_window and hasattr(self.main_window, "stock"):
            if not ticker:
                self.main_window.stock.setCurrentIndex(0)
            else:
                for idx in range(self.main_window.stock.count()):
                    if ticker in self.main_window.stock.itemText(idx).upper():
                        self.main_window.stock.setCurrentIndex(idx)
                        break
        self.filter_changed.emit()

    def clear_person_filter(self):
        if self.main_window and hasattr(self.main_window, "person"):
            self.main_window.person.setCurrentIndex(0)
        self.chip_person.setText("全部人员 ×")
        self.filter_changed.emit()

    def clear_channel_filter(self):
        self.combo_channel.setCurrentIndex(0)
        self.chip_channel.hide()
        if self.main_window and hasattr(self.main_window, "accounts"):
            self.main_window.accounts.reset()
        self.filter_changed.emit()

    def reset_filters(self):
        self.combo_channel.setCurrentIndex(0)
        self.input_ticker.clear()
        self.input_search.clear()
        self.chip_channel.hide()
        if self.main_window:
            if hasattr(self.main_window, "reset_filters"):
                self.main_window.reset_filters()
            elif hasattr(self.main_window, "person"):
                self.main_window.person.setCurrentIndex(0)
                if hasattr(self.main_window, "accounts"):
                    self.main_window.accounts.reset()
        self.filter_changed.emit()


class ChartCanvas(QWidget):
    selected = Signal(str, str)
    selected_detail = Signal(int, str, str)
    drill_month = Signal(str)
    back_to_monthly = Signal()

    def __init__(self, parent=None, *, card_mode=False):
        super().__init__(parent)
        self.card_mode = card_mode
        self.chart, self.hits = None, []
        self.point_coords = {}
        self.setMinimumSize(360, 260)
        self.setMouseTracking(True)
        self.hover_index = None
        self.categories_x = []
        self.back_button_rect = None
        self.detail_mode = False
        self.detail_month = ""
        self.series_colors = {}

    def detail_hit(self, pos):
        return next((hit for hit in reversed(self.hits) if hit[0].contains(pos)), None)

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
        if self.detail_mode and self.chart and self.chart.kind != "capital_assets":
            hit = self.detail_hit(event.position())
            if hit:
                _, month, value, name = hit
                if self.chart.id == 11:
                    shares = ((series.name, dict(series.points).get(month)) for series in self.chart.series)
                    self.setToolTip("\n".join([month, *(f"{label}  {share:.2%}" if share is not None else f"{label}  —"
                                                         for label, share in shares)]))
                else:
                    amount = f"{value:.2%}" if self.chart.ratio else f"{value:,.0f} 只" if self.chart.id == 14 else f"{value:,.2f}"
                    self.setToolTip(f"{month} · {name} · {amount}")
            else:
                self.setToolTip("")
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
        self.point_coords = {}
        self.back_button_rect = None
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

        stacked_area = chart.kind == "stacked_area"
        left, top, width, height = 75, 76, self.width() - 105, self.height() - 198
        if stacked_area:
            top, height = 52, self.height() - 161
        low, high = min(Decimal(0), min(v for _, _, v, _ in data)), max(
            Decimal(0), max(v for _, _, v, _ in data)
        )
        if stacked_area:
            low = ZERO
            if chart.ratio:
                high = Decimal(1)
            else:
                totals = {}
                for _, month, value, _ in data:
                    totals[month] = totals.get(month, ZERO) + value
                high = max(totals.values(), default=ZERO)
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
        fixed_axis = bool(chart.x_ticks) or (stacked_area and len(categories) > 1)
        step = width / max(len(categories) - (1 if fixed_axis else 0), 1)
        intervals = 5 if stacked_area else 4
        for mark in range(intervals + 1):
            value = low + span * Decimal(mark) / intervals
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
        if stacked_area:
            self.paint_stacked_areas(painter, chart, categories, left, step, fixed_axis, ys)
        for index, series in enumerate(() if stacked_area else chart.series):
            color = QColor(self.series_colors.get(series.name, colors[index % len(colors)]))
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
                pt = QPointF(x, y)
                self.point_coords[(series.name, key)] = pt
                self.point_coords.setdefault(key, pt)
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
                hit = QRectF(x - step / 2, top, step, height)
                if self.detail_mode and chart.kind == "line":
                    hit = QRectF(x - 9, y - 9, 18, 18)
                if self.detail_mode and (chart.id == 14 or key == self.detail_month or chart.id == 13 and key == categories[-1]):
                    painter.setPen(QColor(t.brand))
                    painter.setFont(chart_font(11, bold=True))
                    label = f"{value:.2%}" if chart.ratio else f"{value:,.0f}"
                    painter.drawText(QRectF(x - 50, y - 25, 100, 20), Qt.AlignmentFlag.AlignCenter, label)
                self.hits.append((hit, key, value, series.name))

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
                label_x = left + index * step - 20
                label_width = step + 40
                if stacked_area:
                    center = left + (index + (0 if fixed_axis else 0.5)) * step
                    label_x, label_width = center - 37, 74
                painter.drawText(
                    QRectF(label_x, top + height + 8, label_width, 22),
                    Qt.AlignmentFlag.AlignCenter,
                    chart.x_labels.get(key, key),
                )

        if getattr(chart, "hide_legend", False) or getattr(self, "hide_legend", False):
            return

        painter.save()
        painter.setFont(chart_font(12, bold=len(chart.series) > 1))
        legend_x = float(left)
        legend_y = float(self.height() - (75 if stacked_area else 83))
        for s_idx, s in enumerate(chart.series):
            s_color = QColor(self.series_colors.get(s.name, colors[s_idx % len(colors)]))
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

    def paint_stacked_areas(self, painter, chart, months, left, step, fixed_axis, y_at):
        values = [dict(series.points) for series in chart.series]
        # Flush the polygons at each missing month. The axis stays at 0–100%
        # when a category is hidden, so remaining bands keep their real shares.
        runs = [[] for _ in chart.series]
        for index, month in enumerate([*months, None]):
            shares = [series.get(month) for series in values]
            if month is None or any(value is None for value in shares):
                for series, band in zip(chart.series, runs):
                    if not band:
                        continue
                    color = QColor(self.series_colors[series.name])
                    color.setAlpha(145)
                    if len(band) == 1:
                        x, upper, lower = band[0]
                        painter.fillRect(QRectF(x - 2, upper, 4, lower - upper), color)
                    else:
                        path = QPainterPath(QPointF(band[0][0], band[0][1]))
                        for x, upper, _ in band[1:]:
                            path.lineTo(x, upper)
                        for x, _, lower in reversed(band):
                            path.lineTo(x, lower)
                        path.closeSubpath()
                        painter.fillPath(path, color)
                    band.clear()
                continue
            x = left + (index + (0 if fixed_axis else 0.5)) * step
            total = ZERO
            for series, band, share in zip(chart.series, runs, shares):
                lower = y_at(total)
                total += share
                upper = y_at(total)
                band.append((x, upper, lower))
                if upper < lower:
                    hit_width = step
                    self.hits.append((QRectF(x - hit_width / 2, upper, hit_width, lower - upper),
                                      month, share, series.name))

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
        """专业柱状图与股票市值折线组合图（累计净买入基柱 + 市值高于/低于净买入 + 股票市值折线 + Tooltip交互 + 每日走势下钻支持）"""
        palette = ("#B8CBD7", "#70AD47", "#C00000", "#4472C4")
        series = chart.series
        categories = list(dict.fromkeys(key for item in series for key, _ in item.points))
        points = [dict(item.points) for item in series]

        extra = getattr(chart, "extra", {})
        monthly_inv = extra.get("monthly_investments", {})
        latest_profit_day = extra.get("latest_profit_day", "")
        currency = extra.get("reporting_currency", "USD")
        is_daily_drill = extra.get("is_daily_drill", False)
        drill_month = extra.get("drill_month", "")

        bounds = [Decimal(0)]
        for key in categories:
            segments = [data.get(key) for data in points[:3]]
            if all(value is not None for value in segments):
                if self.detail_mode:
                    bounds.extend(sum(segments[:end], ZERO) for end in range(1, 4))
                else:
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
        span = high - low or Decimal(1)
        step = width / max(len(categories), 1)

        def ys(value):
            return top + height - float((value - low) / span) * height

        # 1. 顶部 Header 与图例
        painter.setFont(chart_font(15, bold=True))
        painter.setPen(QColor(theme.heading))
        chart_title_text = (
            f"{drill_month} · 每日走势明细"
            if is_daily_drill
            else "累计净买入与股票市值变化"
        )
        painter.drawText(
            QRectF(left, 16, 260, 24),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            chart_title_text,
        )

        title_w = painter.fontMetrics().horizontalAdvance(chart_title_text)

        # 若在每日下钻模式，展示「✕ 返回月度总览」胶囊按钮
        if is_daily_drill:
            back_btn_rect = QRectF(left + title_w + 14, 16, 120, 24)
            self.back_button_rect = back_btn_rect
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2563EB"))
            painter.drawRoundedRect(back_btn_rect, 12, 12)
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(chart_font(11, bold=True))
            painter.drawText(
                back_btn_rect, Qt.AlignmentFlag.AlignCenter, "✕ 返回月度总览"
            )
            self.hits.append((back_btn_rect, "__back__", None, "__back__"))
        else:
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
        legend_x = max(
            left + title_w + (150 if is_daily_drill else 205),
            (self.width() - sum(legend_widths)) / 2,
        )
        for index, lbl in enumerate(legend_labels):
            painter.setPen(QPen(QColor(palette[index]), 2.5))
            if index == 3:
                painter.drawLine(QPointF(legend_x, 28), QPointF(legend_x + 22, 28))
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
        if latest_profit_day and not is_daily_drill:
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
                    start = sum(segments[:segment_index], ZERO) if self.detail_mode else positive if val >= 0 else negative
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
                painter.setBrush(QColor(palette[3]))
                radius = 6 if self.detail_mode and key == self.detail_month else 3.5
                painter.drawEllipse(pt, radius, radius)
                self.hits.append(
                    (QRectF(x - 9, pt.y() - 9, 18, 18), key, asset, series[3].name)
                )

            # X 轴标签
            painter.setPen(QColor(theme.muted))
            if is_daily_drill:
                # 每日模式下显示 MM-DD
                label = key[-5:] if len(key) >= 10 else key
            else:
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

            v_pen = QPen(QColor(156, 163, 175, 180), 1, Qt.PenStyle.DashLine)
            painter.setPen(v_pen)
            painter.drawLine(QPointF(hx, top), QPointF(hx, top + height))

            h_asset = points[3].get(h_key)
            if h_asset is not None:
                hy = ys(h_asset)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(68, 114, 196, 60))
                painter.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
                painter.setPen(QPen(QColor("#FFFFFF"), 2))
                painter.setBrush(QColor(palette[3]))
                painter.drawEllipse(QPointF(hx, hy), 4.0, 4.0)

            v0 = points[0].get(h_key) or ZERO
            v2 = points[2].get(h_key) or ZERO
            m_val = points[3].get(h_key) or ZERO
            c_val = v0 + v2 if v2 > 0 else v0
            diff = m_val - c_val
            inv_val = monthly_inv.get(h_key)
            if inv_val is None:
                inv_val = c_val

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
            painter.setPen(QPen(QColor("#E2E8F0"), 1))
            painter.setBrush(QColor(255, 255, 255, 245))
            painter.drawRoundedRect(tip_rect, 8, 8)

            if is_daily_drill:
                y_title = f"{h_key[:4]}年{h_key[5:7]}月{h_key[8:10]}日"
            else:
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

            painter.setFont(chart_font(11))
            diff_sign = "+" if diff >= 0 else "-"
            inv_sign = "+" if inv_val >= 0 else "-"
            rows_data = [
                ("股票市值", f"${m_val:,.2f}", palette[3], "#1E293B"),
                ("累计净买入", f"${c_val:,.2f}", palette[0], "#475569"),
                (
                    "市值差额",
                    f"{diff_sign}${abs(diff):,.2f}",
                    None,
                    "#16A34A" if diff >= 0 else "#DC2626",
                ),
                (
                    "当日净买入" if is_daily_drill else "当月净买入",
                    f"{inv_sign}${abs(inv_val):,.2f}",
                    None,
                    "#16A34A" if inv_val >= 0 else "#DC2626",
                ),
            ]
            if self.detail_mode:
                net_buy = extra.get("principal", {}).get(h_key)
                difference = h_asset - net_buy if h_asset is not None and net_buy is not None else None
                rows_data = [
                    (label, "—" if value is None else f"{value:,.2f} {currency}", dot, "#1E293B")
                    for label, value, dot in (
                        ("股票市值", h_asset, palette[3]),
                        ("累计净买入", net_buy, palette[0]),
                        ("市值差额", difference, None),
                    )
                ]

            cur_y = tip_y + 38
            for label_name, val_str, dot_col, text_col in rows_data:
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
        pos = event.position()
        if self.detail_mode and self.chart:
            hit = self.detail_hit(pos)
            if hit:
                self.selected_detail.emit(self.chart.id, hit[1], hit[3])
            return
        if self.chart:
            # 优先检测「返回月度总览」按钮
            if self.back_button_rect and self.back_button_rect.contains(pos):
                self.back_to_monthly.emit()
                return

            for rect, key, _, name in reversed(self.hits):
                if rect.contains(pos):
                    if key == "__back__":
                        self.back_to_monthly.emit()
                        return
                    # 若当前在资产走势图且未在日度下钻中，点击月度柱状展示当月每日走势
                    if self.chart.kind == "capital_assets" and not getattr(
                        self.chart, "extra", {}
                    ).get("is_daily_drill", False):
                        if len(key) == 7 and key.startswith("20"):
                            self.drill_month.emit(key)
                            return
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

    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.analytics = None
        self.charts = ()
        self.monthly_chart_18 = None  # 缓存全月度 Chart 18

        self.page_layout = QVBoxLayout(self)
        self.page_layout.setContentsMargins(16, 12, 16, 12)
        self.page_layout.setSpacing(10)

        # 1. 资产走势图专属 Header（已彻底移除多余的数据源哈希标签）
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

        self.page_subtitle = QLabel("查看投资组合累计净买入、股票市值及投资表现")
        self.page_subtitle.setStyleSheet("font-size: 12px; color: #64748B;")
        title_col.addWidget(self.page_subtitle)

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
        self.btn_refresh.clicked.connect(self.on_refresh_clicked)
        h_layout.addWidget(self.btn_refresh)

        self.btn_export_png = QPushButton("📥 导出 PNG")
        self.btn_export_png.setFixedHeight(28)
        self.btn_export_png.setStyleSheet(
            "QPushButton { background: #2563EB; border: none; border-radius: 6px; padding: 0 12px; font-size: 12px; font-weight: 600; color: #FFFFFF; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self.btn_export_png.clicked.connect(self.export)
        h_layout.addWidget(self.btn_export_png)

        # 小型图表切换下拉框（在资产走势图专属视图中隐藏）
        self.selector = QComboBox()
        self.selector.setMinimumWidth(180)
        self.selector.setStyleSheet(
            "QComboBox { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px; padding: 2px 6px; font-size: 12px; }"
        )
        self.selector.hide()
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

        # 3. 独立白色筛选卡片（与全局筛选双向联动）
        self.filter_card = AssetFilterCard(main_window=self.main_window, parent=self)
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
        self.canvas.drill_month.connect(self.drill_down_month)
        self.canvas.back_to_monthly.connect(self.restore_monthly_chart)
        self.page_layout.addWidget(self.canvas, 1)

        self.selector.currentIndexChanged.connect(self.show_chart)
        self.month.currentTextChanged.connect(self.update_month)
        self.offset.currentIndexChanged.connect(self.show_chart)
        self.rank_order.currentIndexChanged.connect(self.change_rank_order)

    def on_refresh_clicked(self):
        if self.main_window and hasattr(self.main_window, "refresh"):
            self.main_window.refresh()
        else:
            self.update_month()

    def sync_person_from_main(self, person_id):
        """主窗口全局人员变动时同步更新 Chips 标签"""
        person_name = "全部人员"
        if self.main_window and hasattr(self.main_window, "person"):
            person_name = self.main_window.person.currentText()
        elif person_id:
            person_name = str(person_id)
        self.filter_card.sync_person(person_name)

    def sync_broker_from_main(self, broker_name):
        """主窗口全局渠道变动时同步更新内部渠道筛选器"""
        self.filter_card.sync_broker(broker_name)

    def change_rank_order(self):
        self.offset.blockSignals(True)
        self.offset.setCurrentIndex(0)
        self.offset.blockSignals(False)
        self.show_chart()

    def clear(self):
        self.analytics = None
        self.charts = ()
        self.monthly_chart_18 = None
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
        self.monthly_chart_18 = None
        selected = self.selector.currentIndex()
        selected_month = self.month.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([f"{c.id:02}  {c.title}" for c in self.charts])
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

        # 动态同步可用渠道列表到内部筛选卡片
        channels = sorted({r.values[1] for r in analytics.reports["每月持仓收益"].rows if r.values[1]})
        self.filter_card.set_available_channels(channels)

        # 同步当前人员与渠道名称
        if self.main_window and hasattr(self.main_window, "person"):
            self.sync_person_from_main(self.main_window.person.currentData())
        if self.main_window and hasattr(self.main_window, "accounts"):
            self.sync_broker_from_main(self.main_window.accounts.current_broker_name())

        self.update_month()

    def update_month(self):
        if self.analytics:
            self.charts = build_charts(
                self.analytics, selected_month=self.month.currentData()
            )
            self.monthly_chart_18 = None
            self.show_chart()

    def show_chart(self, *_):
        if not self.charts:
            self.offset.hide()
            self.rank_order.hide()
            self.month_label.hide()
            self.month.hide()
            self.selector.hide()
            return
        chart = self.charts[self.selector.currentIndex()]
        is_capital = chart.id == 18

        self.kpi_container.setVisible(is_capital)
        self.filter_card.setVisible(is_capital)
        self.selector.setVisible(not is_capital)

        if is_capital:
            self.monthly_chart_18 = chart
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

    def drill_down_month(self, month_key):
        """点击月度柱状时，展示当月每天的走势（每日基柱 + 每日溢价/缺额 + 每日股票市值折线）"""
        if not self.analytics or "每日持仓收益" not in self.analytics.reports:
            return
        daily_rows = self.analytics.reports["每日持仓收益"].rows
        subset = [r for r in daily_rows if r.values and r.values[0].startswith(month_key)]
        if not subset:
            return

        days = sorted(set(r.values[0] for r in subset))
        if not days:
            return

        # 计算每天的股票市值与累计净投入
        m_by_day = {
            d: strict_sum(r.values[11] for r in subset if r.values[0] == d)
            for d in days
        }
        c_by_day = {
            d: strict_sum(r.values[22] for r in subset if r.values[0] == d)
            for d in days
        }
        # 当日买卖差额（当日净买入）
        b_by_day = {
            d: strict_sum(r.values[16] for r in subset if r.values[0] == d)
            for d in days
        }
        s_by_day = {
            d: strict_sum(r.values[17] for r in subset if r.values[0] == d)
            for d in days
        }
        inv_by_day = {
            d: (b_by_day.get(d) or ZERO) - (s_by_day.get(d) or ZERO)
            for d in days
        }

        # 缓存当前的月度图表
        if not self.monthly_chart_18 and self.canvas.chart:
            self.monthly_chart_18 = self.canvas.chart

        # 构造每日走势 Chart
        base_series = Series(
            "累计本金基柱",
            tuple(
                (
                    d,
                    min(c_by_day[d], m_by_day[d])
                    if c_by_day[d] is not None and m_by_day[d] is not None
                    else None,
                )
                for d in days
            ),
        )
        gain_series = Series(
            "资产高于本金",
            tuple(
                (
                    d,
                    max(m_by_day[d] - c_by_day[d], ZERO)
                    if c_by_day[d] is not None and m_by_day[d] is not None
                    else None,
                )
                for d in days
            ),
        )
        loss_series = Series(
            "资产低于本金",
            tuple(
                (
                    d,
                    max(c_by_day[d] - m_by_day[d], ZERO)
                    if c_by_day[d] is not None and m_by_day[d] is not None
                    else None,
                )
                for d in days
            ),
        )
        asset_series = Series(
            "总资产", tuple((d, m_by_day[d]) for d in days)
        )

        currency = self.analytics.scope.reporting_currency
        as_of_str = str(self.analytics.latest_profit_day or self.analytics.scope.as_of)
        note = f"Excel兼容口径 · 币种 {currency} · 统计月份 {month_key} · 每日明细走势"

        daily_chart = Chart(
            18,
            f"{month_key} 每日走势明细",
            "capital_assets",
            (base_series, gain_series, loss_series, asset_series),
            note,
            extra={
                "is_daily_drill": True,
                "drill_month": month_key,
                "monthly_investments": inv_by_day,
                "reporting_currency": currency,
            },
        )

        self.canvas.chart = daily_chart
        self.update_kpi_cards(daily_chart)
        self.canvas.update()

    def restore_monthly_chart(self):
        """从每日走势平滑切回完整的月度全景走势图"""
        if self.monthly_chart_18:
            self.canvas.chart = self.monthly_chart_18
            self.update_kpi_cards(self.monthly_chart_18)
            self.canvas.update()

    def update_kpi_cards(self, chart):
        """根据当前图表（月度或日度）的真实财务数据刷新顶部三个核心指标卡片"""
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

        valid_indices = [i for i, k in enumerate(categories) if points[3].get(k) is not None]
        if valid_indices:
            last_idx = valid_indices[-1]
            latest_m = points[3].get(categories[last_idx]) or ZERO
            latest_c = c_history[last_idx]
            m_spark = [points[3].get(categories[i]) for i in valid_indices]
            c_spark = [c_history[i] for i in valid_indices]
            diff_spark = [diff_history[i] for i in valid_indices]
        else:
            latest_m = ZERO
            latest_c = ZERO
            m_spark = []
            c_spark = []
            diff_spark = []
        latest_diff = latest_m - latest_c

        # 卡片 1：股票市值
        self.card_market_val.set_value(f"${latest_m:,.0f}", history=m_spark)

        # 卡片 2：累计净买入
        self.card_net_buy.set_value(f"${latest_c:,.0f}", history=c_spark)

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
            history=diff_spark,
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
