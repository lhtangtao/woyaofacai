from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from portfolio.services.charts_service import build_charts
from portfolio.ui.charts_page import ChartCanvas


class HoldingsDistributionPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.charts = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        # Header bar: 基准月份 and 导出 PNG
        row = QHBoxLayout()
        self.month_label = QLabel("基准月份")
        row.addWidget(self.month_label)
        self.month = QComboBox()
        self.month.setMinimumWidth(160)
        self.month.addItem("当前 / 最新", None)
        self.month.setToolTip("选择历史月份后更新右侧/下方月末持仓快照；当前/最新恢复最新数据。")
        self.month.currentTextChanged.connect(self.update_month)
        row.addWidget(self.month)

        row.addStretch()

        export_btn = QPushButton("导出 PNG…")
        export_btn.clicked.connect(self.export)
        row.addWidget(export_btn)
        layout.addLayout(row)

        # Scroll area containing the 6 pie charts in a grid
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(4, 4, 4, 12)
        self.grid.setSpacing(16)

        self.canvases = []
        for _ in range(6):
            canvas = ChartCanvas(card_mode=True)
            canvas.setMinimumSize(420, 290)
            canvas.selected.connect(self.drill)
            self.canvases.append(canvas)

        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)
        self._last_cols = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width())

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout(self.width())

    def _relayout(self, width):
        cols = 3 if width >= 1440 else 2
        if cols == self._last_cols:
            return
        self._last_cols = cols

        while self.grid.count():
            self.grid.takeAt(0)

        if cols == 3:
            # 3 columns x 2 rows
            # Row 0: 0 (当前股票), 1 (当前渠道), 2 (当前分类)
            # Row 1: 3 (月末股票), 4 (月末渠道), 5 (月末分类)
            for i in range(3):
                self.grid.addWidget(self.canvases[i], 0, i)
                self.grid.addWidget(self.canvases[i + 3], 1, i)
        else:
            # 2 columns x 3 rows:
            # Row 0: 当前股票 | 月末股票
            # Row 1: 当前渠道 | 月末渠道
            # Row 2: 当前分类 | 月末分类
            self.grid.addWidget(self.canvases[0], 0, 0)
            self.grid.addWidget(self.canvases[3], 0, 1)
            self.grid.addWidget(self.canvases[1], 1, 0)
            self.grid.addWidget(self.canvases[4], 1, 1)
            self.grid.addWidget(self.canvases[2], 2, 0)
            self.grid.addWidget(self.canvases[5], 2, 1)

    def clear(self):
        self.analytics = None
        self.charts = ()
        for c in self.canvases:
            c.chart = None
            c.update()

    def set_analytics(self, analytics):
        self.analytics = analytics
        selected_month = self.month.currentData()
        self.month.blockSignals(True)
        self.month.clear()
        self.month.addItem("当前 / 最新", None)
        if analytics and "每月持仓收益" in analytics.reports:
            for m in sorted({r.values[0] for r in analytics.reports["每月持仓收益"].rows}, reverse=True):
                self.month.addItem(m, m)
        idx = max(0, self.month.findData(selected_month))
        self.month.setCurrentIndex(idx)
        self.month.blockSignals(False)
        self.update_month()

    def update_month(self):
        if not self.analytics:
            self.clear()
            return
        self.charts = build_charts(self.analytics, selected_month=self.month.currentData())
        for i in range(min(6, len(self.charts))):
            self.canvases[i].chart = self.charts[i]
            self.canvases[i].update()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出持仓分布图表", "持仓分布.png", "PNG (*.png)")
        if path:
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not self.container.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except Exception:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
