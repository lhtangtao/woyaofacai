"""Small native vector line icons, drawn at the device's resolution."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap

from portfolio.ui.theme import tokens


class LineIcon(QIconEngine):
    def __init__(self, kind, widget=None):
        super().__init__()
        self.kind, self.widget = kind, widget

    def clone(self):
        return LineIcon(self.kind, self.widget)

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, pixmap.rect(), mode, state)
        painter.end()
        return pixmap

    def scaledPixmap(self, size, mode, state, scale):
        pixmap = self.pixmap(size * scale, mode, state)
        pixmap.setDevicePixelRatio(scale)
        return pixmap

    def paint(self, painter, rect, mode, state):
        t = tokens(self.widget)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        color = t.brand if mode == QIcon.Mode.Selected or state == QIcon.State.On else t.subtle if mode == QIcon.Mode.Disabled else t.muted
        if self.widget and self.widget.objectName() == "primary" and mode != QIcon.Mode.Disabled:
            color = t.on_brand
        painter.setPen(QPen(QColor(color), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        def line(a, b, c, d):
            painter.drawLine(QPointF(a, b), QPointF(c, d))
        def box(x, y, w, h, radius=2):
            painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
        kind = self.kind
        if kind == "grid":
            for x, y in ((4, 4), (14, 4), (4, 14), (14, 14)):
                box(x, y, 6, 6, 1.2)
        elif kind in ("table", "calendar"):
            box(3, 5, 18, 16)
            line(3, 10, 21, 10)
            line(9, 10, 9, 21)
            line(15, 10, 15, 21)
            line(3, 15, 21, 15)
            if kind == "calendar":
                line(7, 3, 7, 7)
                line(17, 3, 17, 7)
        elif kind == "people":
            painter.drawEllipse(QRectF(5, 3, 6, 6))
            painter.drawEllipse(QRectF(14, 5, 5, 5))
            path = QPainterPath(QPointF(2, 20))
            path.cubicTo(2, 10, 14, 10, 14, 20)
            painter.drawPath(path)
            path = QPainterPath(QPointF(14, 13))
            path.cubicTo(20, 11, 22, 15, 22, 20)
            painter.drawPath(path)
        elif kind == "bank":
            line(3, 8, 12, 3)
            line(12, 3, 21, 8)
            line(3, 8, 21, 8)
            for x in (6, 12, 18):
                line(x, 11, x, 18)
            line(3, 21, 21, 21)
        elif kind == "pie":
            painter.drawEllipse(QRectF(4, 4, 16, 16))
            line(12, 12, 12, 4)
            line(12, 12, 20, 12)
        elif kind in ("chart", "wallet"):
            if kind == "wallet":
                box(3, 5, 18, 15)
                box(15, 11, 6, 5, 1)
            else:
                line(4, 4, 4, 20)
                line(4, 20, 21, 20)
                for a, b, c, d in ((7, 15, 11, 10), (11, 10, 15, 13), (15, 13, 20, 5)):
                    line(a, b, c, d)
        elif kind in ("trade", "back", "forward"):
            if kind == "trade":
                line(4, 8, 19, 8)
                line(16, 5, 19, 8)
                line(19, 8, 16, 11)
                line(20, 17, 5, 17)
                line(5, 17, 8, 14)
                line(5, 17, 8, 20)
            else:
                sign = 1 if kind == "back" else -1
                line(12 - sign * 4, 12, 12 + sign * 3, 5)
                line(12 - sign * 4, 12, 12 + sign * 3, 19)
        elif kind == "database":
            painter.drawEllipse(QRectF(4, 3, 16, 6))
            line(4, 6, 4, 19)
            line(20, 6, 20, 19)
            painter.drawArc(QRectF(4, 10, 16, 6), 180 * 16, 180 * 16)
            painter.drawArc(QRectF(4, 16, 16, 6), 180 * 16, 180 * 16)
        elif kind == "search":
            painter.drawEllipse(QRectF(4, 3, 12, 12))
            line(14, 14, 21, 21)
        elif kind == "refresh":
            painter.drawArc(QRectF(4, 4, 16, 16), 40 * 16, 290 * 16)
            line(20, 3, 20, 8)
            line(15, 8, 20, 8)
        elif kind in ("robot", "ai"):
            # 精致矢量 AI 机器人图标
            box(5, 7, 14, 11, 2.5)
            line(12, 3, 12, 7)
            painter.drawEllipse(QRectF(11, 2, 2, 2))
            painter.drawEllipse(QRectF(8, 10, 2, 2))
            painter.drawEllipse(QRectF(14, 10, 2, 2))
            line(3, 11, 5, 11)
            line(19, 11, 21, 11)
            line(9, 14.5, 15, 14.5)
        elif kind == "filter":
            for y, start, end in ((6, 3, 21), (12, 6, 18), (18, 9, 15)):
                line(start, y, end, y)
        elif kind == "download":
            line(12, 3, 12, 15)
            line(7, 10, 12, 15)
            line(12, 15, 17, 10)
            line(4, 16, 4, 21)
            line(4, 21, 20, 21)
            line(20, 21, 20, 16)
        elif kind in ("close", "menu", "more"):
            if kind == "close":
                line(6, 6, 18, 18)
                line(6, 18, 18, 6)
            elif kind == "menu":
                for y in (6, 12, 18):
                    line(4, y, 20, y)
            else:
                for x in (5, 12, 19):
                    painter.drawEllipse(QRectF(x - 1, 11, 2, 2))
        else:
            painter.drawEllipse(QRectF(5, 5, 14, 14))
            line(12, 8, 12, 13)
            line(12, 16, 12, 16.2)
        painter.restore()


def icon(kind, widget=None):
    return QIcon(LineIcon(kind, widget))


def app_icon():
    from pathlib import Path
    res_dir = Path(__file__).resolve().parents[1] / "resources"
    for name in ("app_icon.icns", "app_icon.png"):
        path = res_dir / name
        if path.is_file():
            return QIcon(str(path))
    return QIcon()
