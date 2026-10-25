from decimal import Decimal

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget

from portfolio.services.charts_service import Chart, Series
from portfolio.ui.charts_page import ChartCanvas
from portfolio.ui.report_page import ReportPage


class StockDetail(QWidget):
    drill = Signal(str, str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.label = QLabel("从持仓或排行榜双击证券，可查看价格、买卖点和成本轨迹。")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        split = QSplitter()
        from PySide6.QtCore import Qt
        split.setOrientation(Qt.Orientation.Vertical)
        self.canvas = ChartCanvas()
        self.canvas.setMinimumHeight(260)
        self.canvas.selected.connect(self.drill)
        split.addWidget(self.canvas)
        self.report = ReportPage(("交易回放", "每日持仓收益", "每月持仓收益", "微操配对证据", "人员资产", "行情状态"))
        split.addWidget(self.report)
        split.setSizes([300, 350])
        layout.addWidget(split)

    def clear(self):
        self.canvas.chart = None
        self.canvas.update()
        self.report.clear()

    def set_analytics(self, analytics):
        self.report.set_reports(analytics.reports)
        rows = analytics.reports["行情状态"].rows
        symbols = {r.values[0] for r in rows}
        if len(symbols) != 1:
            self.label.setText("请选择单只证券查看价格和买卖点；下表仍显示当前全局筛选的完整成本轨迹。")
            self.canvas.chart = None
            self.canvas.update()
            return
        symbol = next(iter(symbols))
        self.label.setText(f"{symbol} · 原币价格曲线与交易买卖点 · 划转独立标识 · 交易金额、人员、账户和来源见下表；微操收益不与组合收益相加。")
        closes = {r.values[1]: r.values[3] for r in rows if r.values[2] == "DAILY_CLOSE"}
        series = [Series("日收盘价 USD", tuple(sorted(closes.items())))]
        for sides, name in ((('买入', 'BUY'), "B 买入"), (('卖出', 'SELL'), "S 卖出")):
            points = []
            for row in analytics.reports["交易回放"].rows:
                if row.values[4] in sides and isinstance(row.values[6], Decimal):
                    points.append((row.values[0][:10], row.values[6]))
            series.append(Series(name, tuple(points)))
        transfers = [(r.values[0][:10], r.values[6]) for r in analytics.reports["交易回放"].rows if r.values[12] in ("人员划转", "权益划转") and r.values[6] is not None]
        series.append(Series("◇ 人员划转", tuple(transfers)))
        self.canvas.chart = Chart(28, symbol + " · 价格与操作", "line", tuple(series), f"USD 原始成交价格 · 基准 {analytics.scope.as_of} · 历史股本口径参见行情状态，不做二次复权", link="day")
        self.canvas.update()
