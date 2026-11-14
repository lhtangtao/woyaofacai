"""国内资产每日与每月持仓收益页面（参考美股每日/每月持仓收益结构与交互）。"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from portfolio.domestic.performance import DomesticPerformanceData, build_domestic_performance
from portfolio.ui.performance_metrics import UnifiedPerformanceMetrics, _fmt_money, _fmt_signed_money


def _cny_font(size: int = 12, *, bold: bool = False) -> QFont:
    f = QFont(".AppleSystemUIFont", size)
    f.setBold(bold)
    return f


def _fmt_cny(val: float | None) -> str:
    if val is None:
        return "—"
    return f"¥{val:,.2f}"


def _fmt_signed_cny(val: float | None) -> str:
    if val is None:
        return "—"
    if abs(val) < 1e-9:
        return "¥0.00"
    sign = "+" if val > 0 else "-"
    return f"{sign}¥{abs(val):,.2f}"


class _PerfWorker(QObject):
    finished = Signal(object, str)

    def run(self):
        try:
            data = build_domestic_performance()
            self.finished.emit(data, "")
        except Exception as exc:  # noqa: BLE001
            self.finished.emit(None, str(exc))


class DomesticTrendCanvas(QWidget):
    """参考美股每日/每月趋势画布：支持柱状图（红绿/蓝橙）与面积折线图，带悬停十字线与白色圆角卡片。"""

    periodClicked = Signal(str)

    def __init__(self, *, unit: str = "day", parent=None):
        super().__init__(parent)
        self.unit = unit
        self.setMouseTracking(True)
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._series: list[dict[str, Any]] = []
        self._mode: str = "pnl"  # pnl | cum_pnl | flow | cum_flow
        self._hover_idx: int = -1
        self._selected_key: str = ""

    def set_data(self, series: list[dict[str, Any]], *, mode: str, selected_key: str = ""):
        self._series = series
        self._mode = mode
        self._selected_key = selected_key
        self._hover_idx = -1
        self.update()

    def _value_for(self, item: dict[str, Any]) -> float:
        if self._mode == "pnl":
            return float(item["pnl"])
        if self._mode == "cum_pnl":
            return float(item["cum_pnl"])
        if self._mode == "flow":
            return float(item["net_cash_in"])
        return float(item["cum_net_cash_in"])

    def _index_at(self, x: float) -> int:
        n = len(self._series)
        if n == 0:
            return -1
        left_pad = 58.0
        right_pad = 16.0
        plot_w = max(10.0, self.width() - left_pad - right_pad)
        if x < left_pad or x > left_pad + plot_w:
            return -1
        ratio = (x - left_pad) / plot_w
        idx = int(ratio * n) if self._mode in ("pnl", "flow") else round(ratio * max(1, n - 1))
        return max(0, min(n - 1, idx))

    def mouseMoveEvent(self, event):
        idx = self._index_at(event.position().x())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_idx != -1:
            self._hover_idx = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._index_at(event.position().x())
            if 0 <= idx < len(self._series):
                self.periodClicked.emit(self._series[idx]["key"])
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        h = float(self.height())
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        n = len(self._series)
        if n == 0:
            painter.setPen(QColor("#94A3B8"))
            painter.setFont(_cny_font(12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "当前筛选条件下暂无趋势数据")
            return

        left_pad, right_pad, top_pad, bot_pad = 58.0, 16.0, 14.0, 26.0
        plot_w = max(10.0, w - left_pad - right_pad)
        plot_h = max(10.0, h - top_pad - bot_pad)
        vals = [self._value_for(item) for item in self._series]
        vmin = min(0.0, min(vals))
        vmax = max(0.0, max(vals))
        if abs(vmax - vmin) < 1e-9:
            vmax += 100.0
            vmin -= 100.0
        span = vmax - vmin
        vmin -= span * 0.08
        vmax += span * 0.08

        def y_of(v: float) -> float:
            return top_pad + plot_h * (1.0 - (v - vmin) / (vmax - vmin))

        # 网格线与 Y 轴刻度
        painter.setFont(_cny_font(10))
        for step_i in range(5):
            gv = vmin + (vmax - vmin) * step_i / 4.0
            gy = y_of(gv)
            painter.setPen(QPen(QColor("#F1F5F9"), 1))
            painter.drawLine(QPointF(left_pad, gy), QPointF(left_pad + plot_w, gy))
            painter.setPen(QColor("#64748B"))
            lbl = f"{gv/1000:.0f}k" if abs(gv) >= 10000 else f"{gv:,.0f}"
            painter.drawText(QRectF(2.0, gy - 8.0, left_pad - 8.0, 16.0),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, lbl)

        zero_y = y_of(0.0)
        painter.setPen(QPen(QColor("#CBD5E1"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(left_pad, zero_y), QPointF(left_pad + plot_w, zero_y))

        xs: list[float] = []
        is_bar = self._mode in ("pnl", "flow")
        if is_bar:
            slot = plot_w / n
            bar_w = max(2.0, min(26.0, slot * 0.65))
            for i, item in enumerate(self._series):
                cx = left_pad + (i + 0.5) * slot
                xs.append(cx)
                v = vals[i]
                yv = y_of(v)
                top_y = min(zero_y, yv)
                bh = max(1.5, abs(yv - zero_y))
                if self._mode == "pnl":
                    color = QColor("#10B981" if v >= 0 else "#EF4444")
                else:
                    color = QColor("#2563EB" if v >= 0 else "#F59E0B")
                if item["key"] == self._selected_key:
                    painter.setPen(QPen(QColor("#0F172A"), 1.5))
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(cx - bar_w / 2.0, top_y, bar_w, bh), 2.0, 2.0)
        else:
            path = QPainterPath()
            fill_path = QPainterPath()
            for i, v in enumerate(vals):
                cx = left_pad + (plot_w * i / max(1, n - 1)) if n > 1 else left_pad + plot_w / 2.0
                xs.append(cx)
                cy = y_of(v)
                if i == 0:
                    path.moveTo(cx, cy)
                    fill_path.moveTo(cx, zero_y)
                    fill_path.lineTo(cx, cy)
                else:
                    path.lineTo(cx, cy)
                    fill_path.lineTo(cx, cy)
            fill_path.lineTo(xs[-1], zero_y)
            fill_path.closeSubpath()
            line_color = QColor("#2563EB") if self._mode == "cum_flow" else QColor("#0EA5E9")
            grad = QLinearGradient(0, top_pad, 0, top_pad + plot_h)
            c_top = QColor(line_color)
            c_top.setAlpha(45)
            c_bot = QColor(line_color)
            c_bot.setAlpha(4)
            grad.setColorAt(0.0, c_top)
            grad.setColorAt(1.0, c_bot)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(grad)
            painter.drawPath(fill_path)
            painter.setPen(QPen(line_color, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        # X 轴标签
        painter.setPen(QColor("#64748B"))
        painter.setFont(_cny_font(10))
        tick_indices = sorted({0, n // 2, n - 1}) if n >= 3 else list(range(n))
        for ti in tick_indices:
            lbl = self._series[ti]["key"]
            tx = max(left_pad, min(left_pad + plot_w - 64.0, xs[ti] - 32.0))
            painter.drawText(QRectF(tx, top_pad + plot_h + 4.0, 64.0, 18.0),
                             Qt.AlignmentFlag.AlignCenter, lbl)

        # 悬停虚线 + 高亮点 + 白底圆角卡片
        if 0 <= self._hover_idx < n:
            hi = self._hover_idx
            hx = xs[hi]
            item = self._series[hi]
            v = vals[hi]
            hy = y_of(v)
            painter.setPen(QPen(QColor("#94A3B8"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(hx, top_pad), QPointF(hx, top_pad + plot_h))

            dot_color = (
                ("#10B981" if v >= 0 else "#EF4444") if self._mode == "pnl"
                else ("#0EA5E9" if self._mode == "cum_pnl" else "#2563EB")
            )
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor(dot_color))
            painter.drawEllipse(QPointF(hx, hy), 4.5, 4.5)

            lbl_name = {
                "pnl": "当日盈亏" if self.unit == "day" else "当月盈亏",
                "cum_pnl": "累计盈亏",
                "flow": "当日净投入" if self.unit == "day" else "当月净投入",
                "cum_flow": "累计净投入",
            }[self._mode]
            lines = [
                ("", item["key"]),
                (dot_color, f"{lbl_name}：{_fmt_signed_cny(v)}"),
                ("#64748B", f"期末市值：{_fmt_cny(item['market_value'])}"),
                ("", f"操作笔数：{item['trade_count']} 笔"),
            ]
            tip_font = _cny_font(10)
            tip_bold = _cny_font(10, bold=True)
            painter.setFont(tip_bold)
            fm = painter.fontMetrics()
            tip_w = max(fm.horizontalAdvance(t) for _, t in lines) + 28.0
            tip_h = 10.0 + len(lines) * 17.0
            tx = hx + 10.0
            if tx + tip_w > w - 4.0:
                tx = hx - tip_w - 10.0
            tx = max(4.0, min(w - tip_w - 4.0, tx))
            ty = max(4.0, min(top_pad + plot_h - tip_h - 4.0, top_pad + 4.0))

            painter.setPen(QPen(QColor("#CBD5E1"), 1))
            painter.setBrush(QColor(255, 255, 255, 245))
            painter.drawRoundedRect(QRectF(tx, ty, tip_w, tip_h), 7.0, 7.0)
            for li, (dc, txt) in enumerate(lines):
                ly = ty + 5.0 + li * 17.0
                painter.setFont(tip_bold if li == 0 else tip_font)
                tx_pos = tx + 8.0
                if dc:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(dc))
                    painter.drawEllipse(QPointF(tx + 12.0, ly + 8.0), 3.0, 3.0)
                    tx_pos = tx + 19.0
                painter.setPen(QColor("#0F172A"))
                painter.drawText(
                    QRectF(tx_pos, ly, tip_w - (tx_pos - tx) - 4.0, 16.0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    txt,
                )


class DomesticStructureDonut(QWidget):
    """单日/单月盈亏结构环形图。"""

    def __init__(self, *, unit: str = "day", parent=None):
        super().__init__(parent)
        self.unit = unit
        self.setMinimumSize(130, 130)
        self.setFixedSize(136, 136)
        self._profit_sum = 0.0
        self._loss_sum = 0.0
        self._net_sum = 0.0

    def set_data(self, profit_sum: float, loss_sum: float, net_sum: float):
        self._profit_sum = max(0.0, profit_sum)
        self._loss_sum = abs(min(0.0, loss_sum))
        self._net_sum = net_sum
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(12.0, 12.0, self.width() - 24.0, self.height() - 24.0)
        total = self._profit_sum + self._loss_sum

        pen_bg = QPen(QColor("#E2E8F0"), 14, Qt.PenStyle.SolidLine, Qt.CapStyle.FlatCap)
        painter.setPen(pen_bg)
        painter.drawArc(rect, 0, 360 * 16)

        if total > 0:
            p_span = int(round((self._profit_sum / total) * 360 * 16))
            l_span = 360 * 16 - p_span
            if p_span > 0:
                painter.setPen(QPen(QColor("#10B981"), 14, Qt.PenStyle.SolidLine, Qt.CapStyle.FlatCap))
                painter.drawArc(rect, 90 * 16, -p_span)
            if l_span > 0:
                painter.setPen(QPen(QColor("#EF4444"), 14, Qt.PenStyle.SolidLine, Qt.CapStyle.FlatCap))
                painter.drawArc(rect, 90 * 16 - p_span, -l_span)

        painter.setPen(QColor("#10B981" if self._net_sum >= 0 else "#EF4444"))
        painter.setFont(_cny_font(12, bold=True))
        painter.drawText(QRectF(16, 46, self.width() - 32, 22),
                         Qt.AlignmentFlag.AlignCenter, f"{self._net_sum:+,.0f}")
        painter.setPen(QColor("#64748B"))
        painter.setFont(_cny_font(10))
        painter.drawText(QRectF(16, 68, self.width() - 32, 18),
                         Qt.AlignmentFlag.AlignCenter, "当日净收益" if self.unit == "day" else "当月净收益")


class DomesticPerfTableModel(QAbstractTableModel):
    """高性能表格模型，支持排序与红绿收益着色。"""

    def __init__(self, columns: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.columns = columns  # [(key, label, fmt)]
        self._rows: list[dict[str, Any]] = []
        self._sort_col: int = -1
        self._sort_order: Qt.SortOrder = Qt.SortOrder.DescendingOrder

    def set_rows(self, rows: list[dict[str, Any]]):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()
        if 0 <= self._sort_col < len(self.columns):
            self.sort(self._sort_col, self._sort_order)

    def row_at(self, row_idx: int) -> dict[str, Any] | None:
        return self._rows[row_idx] if 0 <= row_idx < len(self._rows) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][1]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.DescendingOrder):
        if not (0 <= column < len(self.columns)):
            return
        self._sort_col = column
        self._sort_order = order
        key, _, fmt = self.columns[column]
        self.layoutAboutToBeChanged.emit()
        self._rows.sort(
            key=lambda r: (float(r.get(key) or 0.0) if fmt in ("money", "signed", "int") else str(r.get(key) or "")),
            reverse=(order == Qt.SortOrder.DescendingOrder),
        )
        self.layoutChanged.emit()

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        key, _, fmt = self.columns[index.column()]
        val = row.get(key)
        if role == Qt.ItemDataRole.DisplayRole:
            if fmt == "signed":
                return _fmt_signed_money(float(val) if val is not None else None)
            if fmt == "money":
                return _fmt_money(float(val) if val is not None else None)
            if fmt == "int":
                return str(int(val or 0))
            return str(val or "—")
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if fmt in ("money", "signed", "int"):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and fmt == "signed" and val is not None:
            v = float(val)
            if v > 0:
                return QColor("#16A34A")
            if v < 0:
                return QColor("#DC2626")
        if role == Qt.ItemDataRole.FontRole and fmt in ("money", "signed", "int"):
            return _cny_font(12, bold=(fmt == "signed"))
        return None


class DomesticPerformanceBasePage(QWidget):
    """国内资产每日/每月持仓收益通用页面骨架。"""

    def __init__(self, *, unit: str, parent=None):
        super().__init__(parent)
        self.unit = unit
        self.is_day = unit == "day"
        self.period_key = "date" if self.is_day else "month"
        self.pnl_key = "daily_pnl" if self.is_day else "month_pnl"
        self.mv_key = "market_value" if self.is_day else "end_mv"

        self._raw_data: DomesticPerformanceData | None = None
        self._loading = False
        self._worker_thread: QThread | None = None
        self._filtered_rows: list[dict[str, Any]] = []
        self._period_series: list[dict[str, Any]] = []
        self._selected_period: str = ""
        self._selected_platform: str = ""
        self._drill_metric_key: str = ""

        self._build_ui()

    def _build_ui(self):
        self.setObjectName("domesticPerfPage")
        self.setStyleSheet("""
            QWidget#domesticPerfPage { background: #F8FAFC; }
            QFrame#perfCard { background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 10px; }
            QComboBox, QLineEdit {
                min-height: 28px; padding: 2px 8px; border: 1px solid #CBD5E1;
                border-radius: 6px; background: #FFFFFF; color: #0F172A; font-size: 12px;
            }
            QPushButton {
                min-height: 28px; padding: 4px 12px; border: 1px solid #CBD5E1;
                border-radius: 6px; background: #FFFFFF; color: #334155; font-size: 12px; font-weight: 600;
            }
            QPushButton:hover { background: #F1F5F9; }
            QPushButton#primaryBtn {
                background: #2563EB; border-color: #2563EB; color: #FFFFFF;
            }
            QPushButton#primaryBtn:hover { background: #1D4ED8; }
            QPushButton#tabBtn {
                border: none; background: transparent; color: #64748B; padding: 6px 10px; border-radius: 6px;
            }
            QPushButton#tabBtn:checked {
                background: #EFF6FF; color: #2563EB; font-weight: 700;
            }
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(12)

        # 1. 右上角状态与操作工具栏（主标题由主窗口 heading 统一展示）
        header = QHBoxLayout()
        self.as_of_lbl = QLabel("数据截至：—")
        self.as_of_lbl.setStyleSheet("color: #64748B; font-size: 12px; padding: 4px 10px; background: #FFFFFF; border: 1px solid #E2E8F0; border-radius: 6px;")
        header.addWidget(self.as_of_lbl)
        header.addStretch(1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self.reload(force=True))
        header.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.clicked.connect(self.export_csv)
        header.addWidget(self.export_btn)
        root.addLayout(header)

        # 2. 筛选栏
        filter_card = QFrame(objectName="perfCard")
        fl = QHBoxLayout(filter_card)
        fl.setContentsMargins(12, 10, 12, 10)
        fl.setSpacing(8)

        self.start_combo = QComboBox()
        self.start_combo.setMinimumWidth(115)
        self.end_combo = QComboBox()
        self.end_combo.setMinimumWidth(115)
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("全部平台", "")
        self.type_combo = QComboBox()
        self.type_combo.addItem("全部基金类型", "")
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索基金代码 / 名称...")
        self.search_edit.returnPressed.connect(self._apply_filters)

        query_btn = QPushButton("查询", objectName="primaryBtn")
        query_btn.clicked.connect(self._apply_filters)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset_filters)

        fl.addWidget(QLabel("区间："))
        fl.addWidget(self.start_combo)
        fl.addWidget(QLabel("至"))
        fl.addWidget(self.end_combo)
        fl.addWidget(self.platform_combo)
        fl.addWidget(self.type_combo)
        fl.addWidget(self.search_edit, 1)
        fl.addWidget(query_btn)
        fl.addWidget(reset_btn)
        root.addWidget(filter_card)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #64748B; font-size: 12px;")
        self.status_lbl.hide()
        root.addWidget(self.status_lbl)

        # 3. 统一 KPI 指标区
        self.metrics_widget = UnifiedPerformanceMetrics(period_unit=self.unit, parent=self)
        self.metrics_widget.cardClicked.connect(self._on_kpi_clicked)
        root.addWidget(self.metrics_widget)

        # 4. 中部左右双栏图表区
        mid_row = QHBoxLayout()
        mid_row.setSpacing(12)

        # 左：趋势画布
        trend_card = QFrame(objectName="perfCard")
        tl = QVBoxLayout(trend_card)
        tl.setContentsMargins(14, 12, 14, 12)
        tl.setSpacing(8)
        t_tabs = QHBoxLayout()
        self.chart_tab_group = QButtonGroup(self)
        chart_modes = (
            ("pnl", "日度收益走势" if self.is_day else "月度收益走势"),
            ("cum_pnl", "累计收益走势"),
            ("flow", "每日净投入" if self.is_day else "每月净投入"),
            ("cum_flow", "累计净投入"),
        )
        self._chart_mode_keys = [k for k, _ in chart_modes]
        for idx, (_k, lbl) in enumerate(chart_modes):
            btn = QPushButton(lbl, objectName="tabBtn", checkable=True)
            self.chart_tab_group.addButton(btn, idx)
            t_tabs.addWidget(btn)
            if idx == 0:
                btn.setChecked(True)
        t_tabs.addStretch(1)
        self.chart_tab_group.idClicked.connect(lambda _: self._refresh_chart())
        tl.addLayout(t_tabs)

        self.trend_canvas = DomesticTrendCanvas(unit=self.unit, parent=trend_card)
        self.trend_canvas.periodClicked.connect(self._on_period_selected)
        tl.addWidget(self.trend_canvas)
        mid_row.addWidget(trend_card, 62)

        # 右：渠道收益贡献
        plat_card = QFrame(objectName="perfCard")
        pl = QVBoxLayout(plat_card)
        pl.setContentsMargins(14, 12, 14, 12)
        pl.setSpacing(8)
        p_head = QHBoxLayout()
        p_title = QLabel("渠道收益贡献（期间累计）")
        p_title.setFont(_cny_font(13, bold=True))
        p_head.addWidget(p_title, 1)
        p_tip = QLabel("点击行可筛选平台")
        p_tip.setStyleSheet("color: #94A3B8; font-size: 11px;")
        p_head.addWidget(p_tip)
        pl.addLayout(p_head)
        self.platform_list_layout = QVBoxLayout()
        self.platform_list_layout.setSpacing(6)
        pl.addLayout(self.platform_list_layout)
        pl.addStretch(1)
        mid_row.addWidget(plat_card, 38)
        root.addLayout(mid_row)

        # 5. 单日 / 单月收益归因区
        attr_card = QFrame(objectName="perfCard")
        al = QVBoxLayout(attr_card)
        al.setContentsMargins(14, 12, 14, 12)
        al.setSpacing(10)

        a_head = QHBoxLayout()
        self.attr_title_lbl = QLabel("单日收益归因" if self.is_day else "单月收益归因")
        self.attr_title_lbl.setFont(_cny_font(14, bold=True))
        a_head.addWidget(self.attr_title_lbl)
        badge = QLabel("点击联动")
        badge.setStyleSheet("background: #EFF6FF; color: #2563EB; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px;")
        a_head.addWidget(badge)
        a_head.addStretch(1)
        self.period_picker = QComboBox()
        self.period_picker.setMinimumWidth(120)
        self.period_picker.currentIndexChanged.connect(self._on_picker_changed)
        self.prev_period_btn = QPushButton("◀ 上一日" if self.is_day else "◀ 上一月")
        self.next_period_btn = QPushButton("下一日 ▶" if self.is_day else "下一月 ▶")
        self.prev_period_btn.clicked.connect(lambda: self._step_period(-1))
        self.next_period_btn.clicked.connect(lambda: self._step_period(1))
        a_head.addWidget(self.period_picker)
        a_head.addWidget(self.prev_period_btn)
        a_head.addWidget(self.next_period_btn)
        al.addLayout(a_head)

        a_body = QHBoxLayout()
        a_body.setSpacing(12)

        # 左：盈亏结构环形图
        struct_box = QFrame()
        struct_box.setStyleSheet("QFrame { background: #F8FAFC; border: 1px solid #F1F5F9; border-radius: 8px; }")
        sl = QVBoxLayout(struct_box)
        sl.setContentsMargins(12, 10, 12, 10)
        s_lbl = QLabel("当日盈亏结构" if self.is_day else "当月盈亏结构")
        s_lbl.setFont(_cny_font(12, bold=True))
        sl.addWidget(s_lbl)
        s_inner = QHBoxLayout()
        self.donut = DomesticStructureDonut(unit=self.unit, parent=struct_box)
        s_inner.addWidget(self.donut)
        s_stats = QVBoxLayout()
        self.struct_win_lbl = QLabel("● 盈利基金：—")
        self.struct_win_lbl.setStyleSheet("color: #0F172A; font-size: 12px; border: none;")
        self.struct_loss_lbl = QLabel("● 亏损基金：—")
        self.struct_loss_lbl.setStyleSheet("color: #0F172A; font-size: 12px; border: none;")
        s_stats.addStretch(1)
        s_stats.addWidget(self.struct_win_lbl)
        s_stats.addWidget(self.struct_loss_lbl)
        s_stats.addStretch(1)
        s_inner.addLayout(s_stats, 1)
        sl.addLayout(s_inner)
        a_body.addWidget(struct_box, 30)

        # 中：亏损 Top5
        loss_box = QFrame()
        loss_box.setStyleSheet("QFrame { background: #F8FAFC; border: 1px solid #F1F5F9; border-radius: 8px; }")
        ll = QVBoxLayout(loss_box)
        ll.setContentsMargins(12, 10, 12, 10)
        l_head = QLabel("↓ 亏损贡献排行 Top 5")
        l_head.setFont(_cny_font(12, bold=True))
        l_head.setStyleSheet("color: #DC2626; border: none;")
        ll.addWidget(l_head)
        self.loss_top_layout = QVBoxLayout()
        self.loss_top_layout.setSpacing(4)
        ll.addLayout(self.loss_top_layout)
        ll.addStretch(1)
        a_body.addWidget(loss_box, 35)

        # 右：盈利 Top5
        win_box = QFrame()
        win_box.setStyleSheet("QFrame { background: #F8FAFC; border: 1px solid #F1F5F9; border-radius: 8px; }")
        wl = QVBoxLayout(win_box)
        wl.setContentsMargins(12, 10, 12, 10)
        w_head = QLabel("↑ 盈利贡献排行 Top 5")
        w_head.setFont(_cny_font(12, bold=True))
        w_head.setStyleSheet("color: #16A34A; border: none;")
        wl.addWidget(w_head)
        self.win_top_layout = QVBoxLayout()
        self.win_top_layout.setSpacing(4)
        wl.addLayout(self.win_top_layout)
        wl.addStretch(1)
        a_body.addWidget(win_box, 35)

        al.addLayout(a_body)
        root.addWidget(attr_card)

        # 6. 底部明细表（3 个 Tab）
        table_card = QFrame(objectName="perfCard")
        tbl_l = QVBoxLayout(table_card)
        tbl_l.setContentsMargins(14, 12, 14, 12)
        tbl_l.setSpacing(8)

        tab_bar = QHBoxLayout()
        self.table_tab_group = QButtonGroup(self)
        t_names = (
            "单日基金明细" if self.is_day else "单月基金明细",
            "按平台汇总",
            "按日总览" if self.is_day else "按月总览",
        )
        for idx, tname in enumerate(t_names):
            btn = QPushButton(tname, objectName="tabBtn", checkable=True)
            self.table_tab_group.addButton(btn, idx)
            tab_bar.addWidget(btn)
            if idx == 0:
                btn.setChecked(True)
        tab_bar.addStretch(1)
        self.table_count_lbl = QLabel("0 条")
        self.table_count_lbl.setStyleSheet("color: #64748B; font-size: 12px;")
        tab_bar.addWidget(self.table_count_lbl)
        self.table_tab_group.idClicked.connect(lambda _: self._refresh_table())
        tbl_l.addLayout(tab_bar)

        self.table_view = QTableView()
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setSortingEnabled(True)
        self.table_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_view.verticalHeader().hide()
        self.table_view.setMinimumHeight(320)
        self.table_view.doubleClicked.connect(self._on_table_double_clicked)
        tbl_l.addWidget(self.table_view)
        root.addWidget(table_card)

        self._models = {
            0: DomesticPerfTableModel([
                ("fund_name", "基金名称", "text"),
                ("fund_code", "代码", "text"),
                ("platform", "平台", "text"),
                ("fund_type", "基金类型", "text"),
                (self.mv_key, "期末市值(CNY)", "money"),
                (self.pnl_key, "当日收益(CNY)" if self.is_day else "当月收益(CNY)", "signed"),
                ("cum_pnl", "累计收益(CNY)", "signed"),
                ("buy_amount", "买入(CNY)", "money"),
                ("sell_amount", "卖出(CNY)", "money"),
                ("net_cash_in", "净投入(CNY)", "signed"),
                ("trade_count", "笔数", "int"),
            ]),
            1: DomesticPerfTableModel([
                (self.period_key, "日期" if self.is_day else "月份", "text"),
                ("platform", "平台", "text"),
                (self.mv_key, "期末市值(CNY)", "money"),
                (self.pnl_key, "当期收益(CNY)", "signed"),
                ("cum_pnl", "累计收益(CNY)", "signed"),
                ("buy_amount", "买入(CNY)", "money"),
                ("sell_amount", "卖出(CNY)", "money"),
                ("net_cash_in", "净投入(CNY)", "signed"),
                ("trade_count", "笔数", "int"),
            ]),
            2: DomesticPerfTableModel([
                ("key", "日期" if self.is_day else "月份", "text"),
                ("market_value", "期末总市值(CNY)", "money"),
                ("pnl", "当期总收益(CNY)", "signed"),
                ("cum_pnl", "累计总收益(CNY)", "signed"),
                ("buy_amount", "买入(CNY)", "money"),
                ("sell_amount", "卖出(CNY)", "money"),
                ("net_cash_in", "净投入(CNY)", "signed"),
                ("cum_net_cash_in", "累计净投入(CNY)", "money"),
                ("trade_count", "笔数", "int"),
            ]),
        }
        self.table_view.setModel(self._models[0])

    def reload(self, *, force: bool = False):
        if self._loading:
            return
        if self._raw_data is not None and not force:
            return
        self._loading = True
        self.status_lbl.setText("正在加载国内资产历史持仓收益数据…")
        self.status_lbl.show()
        self.refresh_btn.setEnabled(False)

        self._worker_thread = QThread(self)
        self._worker = _PerfWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_loaded)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        self._worker_thread.start()

    def _on_loaded(self, data: DomesticPerformanceData | None, error: str):
        self._loading = False
        self.refresh_btn.setEnabled(True)
        if error or data is None:
            self.status_lbl.setText(f"加载国内持仓收益失败：{error or '未知错误'}")
            return
        self.status_lbl.hide()
        self._raw_data = data
        self.as_of_lbl.setText(f"数据截至：{data.as_of or '—'}")
        self._populate_filter_options(data)
        self._apply_filters()

    def _populate_filter_options(self, data: DomesticPerformanceData):
        rows = data.daily_rows if self.is_day else data.monthly_rows
        periods = sorted({r[self.period_key] for r in rows})
        for combo, default_idx in ((self.start_combo, 0), (self.end_combo, max(0, len(periods) - 1))):
            combo.blockSignals(True)
            combo.clear()
            for p in periods:
                combo.addItem(p, p)
            if periods:
                combo.setCurrentIndex(default_idx)
            combo.blockSignals(False)

        self.platform_combo.blockSignals(True)
        self.platform_combo.clear()
        self.platform_combo.addItem("全部平台", "")
        for p in data.platforms:
            self.platform_combo.addItem(p, p)
        self.platform_combo.blockSignals(False)

        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        self.type_combo.addItem("全部基金类型", "")
        for ft in data.fund_types:
            self.type_combo.addItem(ft, ft)
        self.type_combo.blockSignals(False)

    def _reset_filters(self):
        if self.start_combo.count() > 0:
            self.start_combo.setCurrentIndex(0)
        if self.end_combo.count() > 0:
            self.end_combo.setCurrentIndex(self.end_combo.count() - 1)
        self.platform_combo.setCurrentIndex(0)
        self.type_combo.setCurrentIndex(0)
        self.search_edit.clear()
        self._selected_platform = ""
        self._drill_metric_key = ""
        self._apply_filters()

    def _apply_filters(self):
        if self._raw_data is None:
            return
        rows = self._raw_data.daily_rows if self.is_day else self._raw_data.monthly_rows
        start_p = self.start_combo.currentData() or ""
        end_p = self.end_combo.currentData() or ""
        if start_p and end_p and start_p > end_p:
            start_p, end_p = end_p, start_p
        plat = self.platform_combo.currentData() or ""
        ftype = self.type_combo.currentData() or ""
        kw = self.search_edit.text().strip().lower()

        filtered = []
        for r in rows:
            p = r[self.period_key]
            if start_p and p < start_p:
                continue
            if end_p and p > end_p:
                continue
            if plat and r["platform"] != plat:
                continue
            if ftype and r["fund_type"] != ftype:
                continue
            if kw and kw not in r["fund_code"].lower() and kw not in r["fund_name"].lower():
                continue
            filtered.append(r)

        self._filtered_rows = filtered
        self._recompute_dashboard()

    def _recompute_dashboard(self):
        by_period: dict[str, dict[str, Any]] = {}
        for r in self._filtered_rows:
            pk = r[self.period_key]
            item = by_period.setdefault(pk, {
                "key": pk,
                "market_value": 0.0,
                "pnl": 0.0,
                "cum_pnl": 0.0,
                "buy_amount": 0.0,
                "sell_amount": 0.0,
                "net_cash_in": 0.0,
                "cum_net_cash_in": 0.0,
                "trade_count": 0,
            })
            item["market_value"] += float(r[self.mv_key])
            item["pnl"] += float(r[self.pnl_key])
            item["cum_pnl"] += float(r["cum_pnl"])
            item["buy_amount"] += float(r["buy_amount"])
            item["sell_amount"] += float(r["sell_amount"])
            item["net_cash_in"] += float(r["net_cash_in"])
            item["cum_net_cash_in"] += float(r["cum_net_cash_in"])
            item["trade_count"] += int(r["trade_count"])

        series = [by_period[k] for k in sorted(by_period)]
        if self._drill_metric_key == "up":
            series = [s for s in series if s["pnl"] > 0]
        elif self._drill_metric_key == "down":
            series = [s for s in series if s["pnl"] < 0]
        self._period_series = series

        # 更新 KPI
        pnls = [s["pnl"] for s in series]
        wins = [x for x in pnls if x > 0]
        losses = [x for x in pnls if x < 0]
        total_pnl = sum(pnls)
        buy_sum = sum(s["buy_amount"] for s in series)
        sell_sum = sum(s["sell_amount"] for s in series)
        net_sum = sum(s["net_cash_in"] for s in series)
        end_cum_net = series[-1]["cum_net_cash_in"] if series else 0.0
        trades_sum = sum(s["trade_count"] for s in series)
        self.metrics_widget.update_metrics(
            total_pnl=total_pnl if series else None,
            period_count=len(series),
            first_period=series[0]["key"] if series else None,
            last_period=series[-1]["key"] if series else None,
            up_count=len(wins),
            down_count=len(losses),
            up_pnl_sum=sum(wins),
            down_pnl_sum=sum(losses),
            buy_total=buy_sum,
            sell_total=sell_sum,
            net_inflow=net_sum,
            end_cum_net=end_cum_net,
            op_count=trades_sum,
        )

        # 选中期次默认取最新
        available_keys = [s["key"] for s in series]
        if self._selected_period not in available_keys:
            self._selected_period = available_keys[-1] if available_keys else ""

        self.period_picker.blockSignals(True)
        self.period_picker.clear()
        for k in reversed(available_keys):
            self.period_picker.addItem(k, k)
        if self._selected_period:
            idx = self.period_picker.findData(self._selected_period)
            if idx >= 0:
                self.period_picker.setCurrentIndex(idx)
        self.period_picker.blockSignals(False)

        self._refresh_chart()
        self._refresh_platform_contrib()
        self._refresh_attribution()
        self._refresh_table()

    def _on_kpi_clicked(self, key: str):
        if key in ("up", "down"):
            self._drill_metric_key = "" if self._drill_metric_key == key else key
            for k, card in self.metrics_widget.kpi_cards.items():
                card.set_selected(k == self._drill_metric_key)
            self._recompute_dashboard()

    def _refresh_chart(self):
        idx = self.chart_tab_group.checkedId()
        mode = self._chart_mode_keys[idx] if 0 <= idx < len(self._chart_mode_keys) else "pnl"
        self.trend_canvas.set_data(self._period_series, mode=mode, selected_key=self._selected_period)

    def _refresh_platform_contrib(self):
        while self.platform_list_layout.count():
            w = self.platform_list_layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()

        by_plat: dict[str, float] = defaultdict(float)
        for r in self._filtered_rows:
            by_plat[r["platform"]] += float(r[self.pnl_key])
        if not by_plat:
            self.platform_list_layout.addWidget(QLabel("暂无平台数据"))
            return
        total_abs = sum(abs(v) for v in by_plat.values()) or 1.0
        for plat, pnl in sorted(by_plat.items(), key=lambda kv: -kv[1]):
            row_frame = QFrame()
            row_frame.setCursor(Qt.CursorShape.PointingHandCursor)
            is_sel = (self.platform_combo.currentData() or "") == plat
            row_frame.setStyleSheet(
                "QFrame { background: #EFF6FF; border: 1px solid #93C5FD; border-radius: 6px; padding: 4px 6px; }"
                if is_sel else
                "QFrame { background: #F8FAFC; border: 1px solid #F1F5F9; border-radius: 6px; padding: 4px 6px; }"
            )
            rl = QHBoxLayout(row_frame)
            rl.setContentsMargins(6, 4, 6, 4)
            rl.setSpacing(6)
            lbl = QLabel(plat)
            lbl.setFixedWidth(72)
            lbl.setStyleSheet("font-size: 12px; font-weight: 600; color: #0F172A; border: none;")
            rl.addWidget(lbl)

            pct = abs(pnl) / total_abs * 100.0
            bar = QProgressBar()
            bar.setFixedHeight(8)
            bar.setTextVisible(False)
            bar.setRange(0, 1000)
            bar.setValue(int(max(10, min(1000, pct * 10))))
            bar.setStyleSheet(
                "QProgressBar { background: #E2E8F0; border: none; border-radius: 4px; }"
                "QProgressBar::chunk { background: #2563EB; border-radius: 4px; }"
            )
            rl.addWidget(bar, 1)

            pct_lbl = QLabel(f"{pct:.1f}%")
            pct_lbl.setFixedWidth(44)
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pct_lbl.setStyleSheet("font-size: 11px; color: #64748B; border: none;")
            rl.addWidget(pct_lbl)

            val_lbl = QLabel(_fmt_signed_cny(pnl))
            val_lbl.setFont(_cny_font(12, bold=True))
            val_lbl.setStyleSheet(f"color: {'#16A34A' if pnl >= 0 else '#DC2626'}; border: none;")
            val_lbl.setFixedWidth(96)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(val_lbl)

            row_frame.mousePressEvent = lambda _e, p=plat: self._toggle_platform(p)
            self.platform_list_layout.addWidget(row_frame)

    def _toggle_platform(self, plat: str):
        cur = self.platform_combo.currentData() or ""
        if cur == plat:
            self.platform_combo.setCurrentIndex(0)
        else:
            idx = self.platform_combo.findData(plat)
            if idx >= 0:
                self.platform_combo.setCurrentIndex(idx)
        self._apply_filters()

    def _on_period_selected(self, period_key: str):
        self._selected_period = period_key
        idx = self.period_picker.findData(period_key)
        if idx >= 0:
            self.period_picker.blockSignals(True)
            self.period_picker.setCurrentIndex(idx)
            self.period_picker.blockSignals(False)
        self._refresh_chart()
        self._refresh_attribution()
        if self.table_tab_group.checkedId() == 0:
            self._refresh_table()

    def _on_picker_changed(self):
        pk = self.period_picker.currentData()
        if pk and pk != self._selected_period:
            self._on_period_selected(pk)

    def _step_period(self, delta: int):
        keys = [s["key"] for s in self._period_series]
        if not keys or self._selected_period not in keys:
            return
        cur = keys.index(self._selected_period)
        nxt = max(0, min(len(keys) - 1, cur + delta))
        if nxt != cur:
            self._on_period_selected(keys[nxt])

    def _refresh_attribution(self):
        pk = self._selected_period
        prefix = "单日收益归因" if self.is_day else "单月收益归因"
        self.attr_title_lbl.setText(f"{prefix} ({pk})" if pk else prefix)

        p_rows = [r for r in self._filtered_rows if r[self.period_key] == pk] if pk else []
        wins = sorted([r for r in p_rows if float(r[self.pnl_key]) > 0], key=lambda r: -float(r[self.pnl_key]))
        losses = sorted([r for r in p_rows if float(r[self.pnl_key]) < 0], key=lambda r: float(r[self.pnl_key]))
        win_sum = sum(float(r[self.pnl_key]) for r in wins)
        loss_sum = sum(float(r[self.pnl_key]) for r in losses)
        net_sum = sum(float(r[self.pnl_key]) for r in p_rows)

        self.donut.set_data(win_sum, loss_sum, net_sum)
        self.struct_win_lbl.setText(f"● 盈利基金：{len(wins)} 只  {_fmt_signed_cny(win_sum)}")
        self.struct_loss_lbl.setText(f"● 亏损基金：{len(losses)} 只  {_fmt_signed_cny(loss_sum)}")

        self._fill_top_list(self.loss_top_layout, losses[:5], is_loss=True, denom=abs(loss_sum) or 1.0)
        self._fill_top_list(self.win_top_layout, wins[:5], is_loss=False, denom=abs(win_sum) or 1.0)

    def _fill_top_list(self, layout: QVBoxLayout, items: list[dict[str, Any]], *, is_loss: bool, denom: float):
        while layout.count():
            w = layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not items:
            empty = QLabel("当期无" + ("亏损" if is_loss else "盈利") + "基金")
            empty.setStyleSheet("color: #94A3B8; font-size: 12px; border: none;")
            layout.addWidget(empty)
            return
        color = "#DC2626" if is_loss else "#16A34A"
        for idx, r in enumerate(items, start=1):
            row_w = QWidget()
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.setSpacing(6)
            num = QLabel(str(idx))
            num.setFixedWidth(16)
            num.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 700; border: none;")
            rl.addWidget(num)

            name = QLabel(r["fund_name"])
            name.setStyleSheet("color: #0F172A; font-size: 12px; border: none;")
            name.setMinimumWidth(110)
            rl.addWidget(name, 1)

            plat = QLabel(r["platform"])
            plat.setStyleSheet("color: #64748B; font-size: 11px; border: none;")
            plat.setFixedWidth(52)
            rl.addWidget(plat)

            val = float(r[self.pnl_key])
            val_lbl = QLabel(_fmt_signed_cny(val))
            val_lbl.setFont(_cny_font(11, bold=True))
            val_lbl.setStyleSheet(f"color: {color}; border: none;")
            val_lbl.setFixedWidth(84)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(val_lbl)

            pct_lbl = QLabel(f"{abs(val)/denom*100:.1f}%")
            pct_lbl.setStyleSheet("color: #64748B; font-size: 11px; border: none;")
            pct_lbl.setFixedWidth(40)
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            rl.addWidget(pct_lbl)
            layout.addWidget(row_w)

    def _refresh_table(self):
        tab_idx = self.table_tab_group.checkedId()
        if tab_idx not in self._models:
            tab_idx = 0
        model = self._models[tab_idx]
        self.table_view.setModel(model)

        if tab_idx == 0:
            rows = [r for r in self._filtered_rows if r[self.period_key] == self._selected_period] if self._selected_period else []
            rows = sorted(rows, key=lambda r: -abs(float(r[self.pnl_key])))
        elif tab_idx == 1:
            grouped: dict[tuple[str, str], dict[str, Any]] = {}
            for r in self._filtered_rows:
                gk = (r[self.period_key], r["platform"])
                item = grouped.setdefault(gk, {
                    self.period_key: gk[0],
                    "platform": gk[1],
                    self.mv_key: 0.0,
                    self.pnl_key: 0.0,
                    "cum_pnl": 0.0,
                    "buy_amount": 0.0,
                    "sell_amount": 0.0,
                    "net_cash_in": 0.0,
                    "trade_count": 0,
                })
                item[self.mv_key] += float(r[self.mv_key])
                item[self.pnl_key] += float(r[self.pnl_key])
                item["cum_pnl"] += float(r["cum_pnl"])
                item["buy_amount"] += float(r["buy_amount"])
                item["sell_amount"] += float(r["sell_amount"])
                item["net_cash_in"] += float(r["net_cash_in"])
                item["trade_count"] += int(r["trade_count"])
            rows = sorted(grouped.values(), key=lambda r: (r[self.period_key], r["platform"]), reverse=True)
        else:
            rows = list(reversed(self._period_series))

        model.set_rows(rows)
        self.table_count_lbl.setText(f"{len(rows)} 条")
        header = self.table_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

    def _on_table_double_clicked(self, index: QModelIndex):
        tab_idx = self.table_tab_group.checkedId()
        model = self._models.get(tab_idx)
        if not model:
            return
        row = model.row_at(index.row())
        if not row:
            return
        pk = row.get("key") if tab_idx == 2 else row.get(self.period_key)
        if pk:
            self._on_period_selected(pk)

    def export_csv(self):
        if not self._filtered_rows:
            QMessageBox.information(self, "导出提示", "当前没有可导出的收益数据。")
            return
        default_name = f"国内资产_{'每日' if self.is_day else '每月'}持仓收益_{self._raw_data.as_of or 'export'}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出国内资产持仓收益", default_name, "CSV (*.csv)")
        if not path:
            return
        headers = [
            "日期" if self.is_day else "月份", "平台", "基金代码", "基金名称", "基金类型",
            "期末市值(CNY)", "当期收益(CNY)", "累计收益(CNY)", "买入(CNY)", "卖出(CNY)", "净投入(CNY)", "累计净投入(CNY)", "操作笔数",
        ]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for r in self._filtered_rows:
                writer.writerow([
                    r[self.period_key], r["platform"], r["fund_code"], r["fund_name"], r["fund_type"],
                    f"{r[self.mv_key]:.2f}", f"{r[self.pnl_key]:.2f}", f"{r['cum_pnl']:.2f}",
                    f"{r['buy_amount']:.2f}", f"{r['sell_amount']:.2f}", f"{r['net_cash_in']:.2f}",
                    f"{r['cum_net_cash_in']:.2f}", r["trade_count"],
                ])
        QMessageBox.information(self, "导出完成", f"已导出 {len(self._filtered_rows)} 条记录到：\n{path}")


class DomesticDailyPerformancePage(DomesticPerformanceBasePage):
    def __init__(self, parent=None):
        super().__init__(unit="day", parent=parent)


class DomesticMonthlyPerformancePage(DomesticPerformanceBasePage):
    def __init__(self, parent=None):
        super().__init__(unit="month", parent=parent)
