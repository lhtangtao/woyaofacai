"""Compact channel P/L bars and current security asset totals."""
from decimal import Decimal

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from portfolio.domain.analytics import strict_sum
from portfolio.engine.today_holdings import channel_summary, today_summary
from portfolio.ui.theme import tokens


class ChannelOverview(QWidget):
    channel_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = ()
        self.rates = {}
        self.unit = 'USD'
        self.selected_channel = None
        self.asset_hits = {}
        self.pnl_hits = {}
        self.setFixedHeight(340)
        self.setMouseTracking(True)
        self.setToolTip('点击任一渠道盈亏柱或资产柱，左右同步高亮，再次点击恢复全部渠道。收益率按渠道汇总盈亏及基数计算；资产不含现金，缺失数据保持未知。')

    def set_rows(self, rows, unit='USD'):
        rows = tuple(rows)
        self.channels, self.unit = channel_summary(rows), unit
        self.rates = {name: today_summary(tuple(r for r in rows if r.values[0] == name))[1]
                      for name, _, _ in self.channels}
        self.update()

    def paintEvent(self, event):
        self.asset_hits = {}
        self.pnl_hits = {}
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens(self)
        p.setPen(QPen(QColor(t.border)))
        p.setBrush(QColor(t.panel))
        p.drawRoundedRect(QRectF(1, 1, self.width()-2, self.height()-2), 12, 12)
        font = QFont(self.font())
        font.setPixelSize(13)
        p.setFont(font)
        split = self.width() * .58
        p.setPen(QColor(t.heading))
        p.drawText(QRectF(16, 12, split-24, 24), '各渠道当日盈亏（' + self.unit + '）')
        total = strict_sum(c[2] for c in self.channels if self.selected_channel is None or c[0] == self.selected_channel)
        total_text = f'{total:,.2f}' if total is not None else '— 数据不完整'
        p.drawText(QRectF(split+16, 12, self.width()-split-32, 24), (self.selected_channel or '全部渠道') + '持仓资产  ' + total_text + ' ' + self.unit)
        p.setPen(QColor(t.muted))
        p.drawText(QRectF(split+16, 37, self.width()-split-32, 22), '持仓市值 · 不含现金 · 点击渠道筛选，再点恢复全部')
        if not self.channels:
            p.drawText(QRectF(16, 75, self.width()-32, 40), '当前筛选暂无持仓')
            return
        top, bottom = 65, self.height()-51
        values = [c[1] for c in self.channels if c[1] is not None]
        high = max(values + [Decimal(0)])
        low = min(values + [Decimal(0)])
        span = high - low or Decimal(1)
        plot_top, plot_bottom = top+46, bottom-23
        scale = (plot_bottom-plot_top) / float(span)
        baseline = plot_top + float(high) * scale
        left, right = 55, split-18
        p.setPen(QPen(QColor(t.muted), 1.6))
        p.drawLine(int(left-20), int(baseline), int(right), int(baseline))
        p.drawText(QRectF(8, baseline-10, 30, 20), Qt.AlignmentFlag.AlignRight, '0')
        step = (right-left)/len(self.channels)
        asset_max = max((abs(c[2]) for c in self.channels if c[2] is not None), default=Decimal(0)) or Decimal(1)
        for index, (channel, pnl, asset) in enumerate(self.channels):
            x = left + step*index
            selected = self.selected_channel == channel
            p.setOpacity(1 if self.selected_channel is None or selected else .3)
            color = t.profit if pnl is not None and pnl > 0 else t.loss if pnl is not None and pnl < 0 else t.muted
            self.pnl_hits[channel] = QRectF(x, top, step, self.height()-top)
            text = (f'{"+" if pnl > 0 else "−" if pnl < 0 else ""}{"$" if self.unit == "USD" else "¥"}{abs(pnl):,.2f}'
                    if pnl is not None else '—')
            height = float(abs(pnl)) * scale if pnl is not None else 0
            y = baseline-height if pnl is not None and pnl >= 0 else baseline
            p.setPen(QPen(QColor(t.brand), 2) if selected else Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRect(QRectF(x+step*.25, y, step*.5, max(height, 1) if pnl is not None else 0))
            p.setPen(QColor(color))
            label_y = y-42
            p.drawText(QRectF(x-8, label_y, step+16, 20), Qt.AlignmentFlag.AlignCenter, text)
            rate = self.rates.get(channel)
            p.setPen(QColor(t.profit if rate is not None and rate > 0 else t.loss if rate is not None and rate < 0 else t.muted))
            p.drawText(QRectF(x-8, label_y+20, step+16, 20), Qt.AlignmentFlag.AlignCenter,
                       f'{rate:+.2%}' if rate is not None else '—')
            p.setPen(QColor(t.text))
            p.drawText(QRectF(x, self.height()-24, step, 20), Qt.AlignmentFlag.AlignCenter, channel)
            row_y = 76 + index * min(58, 230/max(1, len(self.channels)))
            bar_left, bar_right = split+104, self.width()-112
            bar_width = max(0, bar_right-bar_left)
            p.drawText(QRectF(split+16, row_y, 84, 28), Qt.AlignmentFlag.AlignVCenter, channel)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.alternate))
            p.drawRoundedRect(QRectF(bar_left, row_y+3, bar_width, 24), 3, 3)
            p.setPen(QPen(QColor(t.heading), 2) if selected else Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.brand))
            width = float(abs(asset)/asset_max) * bar_width if asset is not None else 0
            p.drawRoundedRect(QRectF(bar_left, row_y+3, max(2, width) if asset is not None else 0, 24), 3, 3)
            p.setPen(QColor(t.text))
            p.drawText(QRectF(self.width()-108, row_y, 92, 28), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f'{asset:,.2f}' if asset is not None else '— 数据不完整')
            self.asset_hits[channel] = QRectF(split+12, row_y, self.width()-split-24, 31)
        p.setOpacity(1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for channel, rect in (*self.asset_hits.items(), *self.pnl_hits.items()):
                if rect.contains(event.position()):
                    self.selected_channel = None if self.selected_channel == channel else channel
                    self.update()
                    self.channel_clicked.emit()
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor if any(r.contains(event.position()) for r in (*self.asset_hits.values(), *self.pnl_hits.values())) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)
