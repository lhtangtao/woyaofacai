from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from portfolio.services.charts_service import build_charts
from portfolio.ui.charts_page import ChartCanvas

# Core trend chart IDs:
# 7: 每月收益走势
# 8: 月度收益率走势（%）
# 10: 每月净投入走势
TREND_CHART_IDS = (7, 8, 10)


class TrendChartsPage(QWidget):
    drill = Signal(str, str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analytics = None
        self.charts = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)

        # Top bar: view selector and export button
        bar = QHBoxLayout()
        label = QLabel("走势视图")
        bar.addWidget(label)
        self.view_selector = QComboBox()
        self.view_selector.setMinimumWidth(240)
        self.view_selector.addItem("全部走势图 · 垂直平铺", -1)
        self.view_selector.addItem("07  每月收益走势", 7)
        self.view_selector.addItem("08  月度收益率走势（%）", 8)
        self.view_selector.addItem("10  每月净投入走势", 10)
        self.view_selector.currentIndexChanged.connect(self._change_view)
        bar.addWidget(self.view_selector)

        bar.addStretch()

        export_btn = QPushButton("导出 PNG…")
        export_btn.clicked.connect(self.export)
        bar.addWidget(export_btn)
        layout.addLayout(bar)

        # Scroll area containing the trend chart cards
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.container = QWidget()
        self.cards_layout = QVBoxLayout(self.container)
        self.cards_layout.setContentsMargins(4, 4, 4, 16)
        self.cards_layout.setSpacing(16)

        self.canvases = {}
        for cid in TREND_CHART_IDS:
            canvas = ChartCanvas(card_mode=True)
            canvas.setMinimumHeight(340)
            canvas.selected.connect(self.drill)
            self.cards_layout.addWidget(canvas)
            self.canvases[cid] = canvas

        self.scroll.setWidget(self.container)
        layout.addWidget(self.scroll, 1)

    def _change_view(self):
        target_id = self.view_selector.currentData()
        for cid, canvas in self.canvases.items():
            canvas.setVisible(target_id == -1 or target_id == cid)

    def clear(self):
        self.analytics = None
        self.charts = ()
        for canvas in self.canvases.values():
            canvas.chart = None
            canvas.update()

    def set_analytics(self, analytics):
        self.analytics = analytics
        self.update_charts()

    def update_charts(self):
        if not self.analytics:
            self.clear()
            return
        self.charts = build_charts(self.analytics)
        chart_map = {c.id: c for c in self.charts}
        for cid, canvas in self.canvases.items():
            if cid in chart_map:
                canvas.chart = chart_map[cid]
                canvas.update()

    def export(self):
        target_id = self.view_selector.currentData()
        if target_id != -1 and target_id in self.canvases:
            target_widget = self.canvases[target_id]
            title = target_widget.chart.title if target_widget.chart else "走势图"
        else:
            target_widget = self.container
            title = "走势图汇总"
        path, _ = QFileDialog.getSaveFileName(self, "导出走势图表", f"{title}.png", "PNG (*.png)")
        if path:
            try:
                buffer = QBuffer()
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if not target_widget.grab().save(buffer, "PNG"):
                    raise ValueError("PNG 编码失败")
                with Path(path).open("xb") as stream:
                    stream.write(bytes(buffer.data()))
            except Exception:
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
