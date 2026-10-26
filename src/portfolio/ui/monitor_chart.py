"""Native, theme-aware daily candle chart and aligned technical subpanels."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from portfolio.ui.theme import tokens


class MonitorChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(300, 330)
        self.data = None
        self.count = 126
        self.mas = {20, 50, 200}
        self.panel = "成交量"
        self.show_boll = False

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens(self)
        p.fillRect(self.rect(), QColor(t.panel))
        data = self.data
        if not data or not data.get("bars"):
            p.setPen(QColor(t.muted))
            p.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无已完成日K\n配置行情后点击刷新；缺失值不会填零"
            )
            return
        allbars = data["bars"]
        start = max(0, len(allbars) - self.count) if self.count else 0
        bars = allbars[start:]
        main = QRectF(8, 12, self.width() - 66, self.height() * 0.66 - 22)
        sub = QRectF(8, self.height() * 0.79, self.width() - 66, self.height() * 0.16)

        def x(i):
            return main.left() + (i + 0.5) * main.width() / len(bars)

        values = [float(v) for b in bars for v in (b.low, b.high)]
        for n in self.mas:
            values += [float(v) for v in data["series"][f"MA{n}"][start:] if v is not None]
        if self.show_boll and "BOLL_UP" in data["series"]:
            for k in ("BOLL_UP", "BOLL_LOW"):
                values += [float(v) for v in data["series"].get(k, [])[start:] if v is not None]
        lo, hi = min(values), max(values)
        span = max(hi - lo, hi * 0.02)
        lo, hi = lo - span * 0.06, hi + span * 0.06

        def y(v):
            return main.bottom() - (float(v) - lo) / (hi - lo) * main.height()

        p.setPen(QColor(t.border))
        for i in range(5):
            yy = main.top() + main.height() * i / 4
            p.drawLine(QPointF(main.left(), yy), QPointF(main.right(), yy))
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(main.right() + 5, yy - 8, 55, 18), f"{hi - (hi - lo) * i / 4:.2f}")
            p.setPen(QColor(t.border))
        for i, b in enumerate(bars):
            color = QColor(t.profit if b.close >= b.open else t.loss)
            p.setPen(QPen(color, 1))
            p.setBrush(color)
            p.drawLine(QPointF(x(i), y(b.high)), QPointF(x(i), y(b.low)))
            p.drawRect(
                QRectF(
                    x(i) - max(1, main.width() / len(bars) * 0.3),
                    min(y(b.open), y(b.close)),
                    max(2, main.width() / len(bars) * 0.6),
                    max(1, abs(y(b.open) - y(b.close))),
                )
            )
        colors = (t.brand, t.warning, t.violet, t.teal, t.muted, t.profit, t.loss, t.subtle)

        def line(values, color, yy=y):
            path = QPainterPath()
            active = False
            for i, v in enumerate(values):
                if v is None:
                    active = False
                    continue
                point = QPointF(x(i), yy(v))
                if active:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                    active = True
            p.setPen(QPen(QColor(color), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        for n, color in zip((5, 10, 20, 50, 60, 120, 200, 250), colors):
            if n in self.mas:
                line(data["series"][f"MA{n}"][start:], color)
        if self.show_boll and "BOLL_UP" in data["series"]:
            line(data["series"]["BOLL_UP"][start:], t.teal)
            line(data["series"]["BOLL_MID"][start:], t.subtle)
            line(data["series"]["BOLL_LOW"][start:], t.teal)
        p.setPen(QColor(t.muted))
        for i in sorted({0, len(bars) // 2, len(bars) - 1}):
            xx = min(max(main.left(), x(i) - 32), main.right() - 64)
            p.drawText(QRectF(xx, main.bottom() + 5, 80, 18), bars[i].day.strftime("%Y-%m-%d"))
        p.drawText(QRectF(8, sub.top() - 18, main.width(), 18), self.panel + " · 与主图相同日期区间")
        if self.panel == "成交量":
            maximum = max(float(b.volume) for b in bars) or 1

            def sy(v):
                return sub.bottom() - float(v) / maximum * sub.height()

            for i, b in enumerate(bars):
                p.fillRect(
                    QRectF(
                        x(i) - 2,
                        sy(b.volume),
                        max(2, main.width() / len(bars) * 0.6),
                        sub.bottom() - sy(b.volume),
                    ),
                    QColor(t.profit if b.close >= b.open else t.loss),
                )
            for key, color in (("VMA5", t.warning), ("VMA20", t.violet)):
                line(data["series"][key][start:], color, sy)
        elif self.panel == "RSI14":

            def sy(v):
                return sub.bottom() - float(v) / 100 * sub.height()

            for level in (30, 70):
                p.setPen(QPen(QColor(t.border), 1, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(sub.left(), sy(level)), QPointF(sub.right(), sy(level)))
            line(data["series"]["RSI14"][start:], t.violet, sy)
        else:
            keys = ("DIF", "DEA", "MACD")
            extent = (
                max(
                    [abs(float(v)) for key in keys for v in data["series"][key][start:] if v is not None]
                    or [1]
                )
                or 1
            )

            def sy(v):
                return sub.center().y() - float(v) / extent * sub.height() / 2

            p.setPen(QColor(t.border))
            p.drawLine(QPointF(sub.left(), sy(0)), QPointF(sub.right(), sy(0)))
            for i, v in enumerate(data["series"]["MACD"][start:]):
                if v is not None:
                    p.fillRect(
                        QRectF(x(i) - 1, min(sy(v), sy(0)), 2, max(1, abs(sy(v) - sy(0)))),
                        QColor(t.profit if v >= 0 else t.loss),
                    )
            line(data["series"]["DIF"][start:], t.brand, sy)
            line(data["series"]["DEA"][start:], t.warning, sy)
        p.end()
