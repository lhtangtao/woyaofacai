import math
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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


class ChartCanvas(QWidget):
    selected = Signal(str, str)

    def __init__(self, parent=None, *, card_mode=False):
        super().__init__(parent)
        self.card_mode = card_mode
        self.chart, self.hits = None, []
        self.setMinimumSize(360, 260)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        t = tokens(self)
        colors = (t.brand, t.warning, t.teal, t.violet, "#EC4899", "#0284C7", "#EA580C", "#4F46E5", t.loss, t.muted)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.card_mode:
            painter.setPen(QPen(QColor(t.border), 1))
            painter.setBrush(QColor(t.panel))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        else:
            painter.fillRect(self.rect(), QColor(t.panel))
        self.hits = []
        chart = self.chart
        painter.setPen(QColor(t.heading))
        painter.setFont(chart_font(15 if self.card_mode else 17, bold=True))
        painter.drawText(QRectF(20 if self.card_mode else 24, 14, self.width() - 40, 32), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, chart.title if chart else "等待计算结果")
        if not chart:
            return
        painter.setFont(chart_font(11))
        painter.setPen(QColor(t.muted))
        painter.drawText(QRectF(20 if self.card_mode else 24, self.height() - 44, self.width() - 40, 38), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, chart.note)
        painter.setFont(chart_font(13))
        data = [(series.name, key, value, index) for index, series in enumerate(chart.series) for key, value in series.points if value is not None and key.strip()]
        if not data:
            painter.drawText(QRectF(30, 90, self.width() - 60, 160), Qt.AlignmentFlag.AlignCenter, "该筛选组合暂无完整数据\n请检查行情日期")
            return
        if chart.kind == "capital_assets":
            self.paint_capital_assets(painter, chart, t)
            return
        if chart.kind == "pie":
            positive = [(s, k, v, i) for s, k, v, i in data if v > 0]
            total = sum((v for _, _, v, _ in positive), Decimal(0))
            if not total:
                painter.drawText(QRectF(30, 90, self.width() - 60, 160), Qt.AlignmentFlag.AlignCenter, "暂无有效正数占比数据")
                return
            size = min(self.height() - 130, self.width() // 2 - 25)
            box = QRectF(25 if self.card_mode else 35, 62 if self.card_mode else 72, size, size)
            angle = 90 * 16
            for index, (series, label, val, _) in enumerate(positive):
                span = float(val / total) * 360 * 16
                painter.setPen(QPen(QColor(t.panel), 3))
                painter.setBrush(QColor(colors[index % len(colors)]))
                painter.drawPie(box, int(angle), int(span))
                angle += span
                target = QRectF(box.right() + (20 if self.card_mode else 30), (62 if self.card_mode else 74) + index * 34, min(420, self.width() - box.right() - (35 if self.card_mode else 55)), 30)
                painter.setPen(QColor(t.heading))
                painter.fillRect(QRectF(target.x(), target.y() + 9, 10, 10), QColor(colors[index % len(colors)]))
                text_x, text_width = target.x() + 18, target.width() - 18
                name_width, amount_width = text_width * .34, text_width * .40
                name_box = QRectF(text_x, target.y(), name_width, target.height())
                amount_box = QRectF(text_x + name_width, target.y(), amount_width, target.height())
                share_box = QRectF(amount_box.right() + 10, target.y(), max(35.0, text_width * .26 - 10), target.height())
                name = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, int(name_width - 6))
                painter.drawText(name_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
                painter.drawText(amount_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f'{val:,.2f}')
                painter.drawText(share_box, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f'{val / total:.2%}')
                self.hits.append((target, label, val, series))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.panel))
            painter.drawEllipse(box.adjusted(size * .30, size * .30, -size * .30, -size * .30))
            return
        if chart.kind == "horizontal":
            # Labels stay at the left edge; bar area has the remaining width.
            # Scroll/rank pagination is handled by the chart page's row offset.
            data = data[:max(24, len(data))]
            point_labels = getattr(chart, "point_labels", None) or {}

            def get_text(lbl, val):
                if lbl in point_labels:
                    return point_labels[lbl]
                return f"{val:+.2%}" if chart.ratio else f"{val:+,.2f}"

            label_width = min(210, max(80, max((painter.fontMetrics().horizontalAdvance(label[:30]) for _, label, _, _ in data), default=80)))
            max_val_width = max((painter.fontMetrics().horizontalAdvance(get_text(lbl, val)) for _, lbl, val, _ in data), default=80)
            val_margin = max(95, max_val_width + 12)
            left, top, bottom = label_width + max(120, val_margin + 10), 74, self.height() - 67
            low, high = min(Decimal(0), min(v for _, _, v, _ in data)), max(Decimal(0), max(v for _, _, v, _ in data))
            width = max(1, self.width() - left - max(105, val_margin + 10))
            span = high - low or Decimal(1)
            def x(value):
                return left + float((value - low) / span) * width
            painter.setPen(QPen(QColor(t.border), 1))
            painter.drawLine(QPointF(x(Decimal(0)), top - 6), QPointF(x(Decimal(0)), bottom))
            h = (bottom - top) / max(len(data), 1)
            for index, (series, label, value, color) in enumerate(data):
                y = top + index * h
                painter.setPen(QColor(t.text))
                text = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, label_width)
                painter.drawText(QRectF(10, y, label_width, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
                a, b = x(Decimal(0)), x(value)
                rect = QRectF(min(a, b), y + h * .2, max(abs(b - a), 2), h * .58)
                painter.fillRect(rect, QColor(t.profit if value >= 0 else t.loss))
                val_text = get_text(label, value)
                val_w = painter.fontMetrics().horizontalAdvance(val_text) + 8
                val_rect = QRectF(b + 5, y, val_w, h) if value >= 0 else QRectF(b - val_w - 5, y, val_w, h)
                painter.drawText(val_rect, Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if value >= 0 else Qt.AlignmentFlag.AlignRight), val_text)
                self.hits.append((QRectF(0, y, self.width(), h), label, value, series))
            self.draw_zero(painter, QPointF(x(Decimal(0)), top - 6), QPointF(x(Decimal(0)), bottom),
                           QRectF(x(Decimal(0)) - 20, top - 31, 40, 22), t)
            return
        left, top, width, height = 75, 76, self.width() - 105, self.height() - 198
        low, high = min(Decimal(0), min(v for _, _, v, _ in data)), max(Decimal(0), max(v for _, _, v, _ in data))
        span = high - low or Decimal(1)
        if getattr(chart, "highlight_extremes", False):
            # 为极值点标注预留顶部与底部净空，防止曲线触顶及文字被线条遮挡
            y_pad = span * Decimal("0.15")
            high += y_pad
            low -= y_pad
            span = high - low or Decimal(1)
        def ys(value):
            return top + height - float((value - low) / span) * height
        categories = list(dict.fromkeys(key for series in chart.series for key, _ in series.points))
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
            painter.drawText(QRectF(2, y - 9, left - 10, 20), Qt.AlignmentFlag.AlignRight, f"{value:.0%}" if chart.ratio else f"{value:,.0f}")

        # 绘制时段分界竖线与时段标签（夜盘 / 盘前 / 盘中 / 盘后）
        vertical_lines = getattr(chart, "vertical_lines", ())
        if vertical_lines:
            painter.save()
            vpen_dash = QPen(QColor(t.border), 1, Qt.PenStyle.DashLine)
            vpen_dot = QPen(QColor(t.border), 0.8, Qt.PenStyle.DotLine)
            vpen_solid = QPen(QColor(t.border), 1.2, Qt.PenStyle.SolidLine)
            painter.setFont(chart_font(10, bold=True))
            for v_item in vertical_lines:
                v_key = v_item[0]
                v_label = v_item[1] if len(v_item) > 1 else ""
                v_style = v_item[2] if len(v_item) > 2 else "dash"
                if v_key not in category_positions:
                    continue
                vx = left + (category_positions[v_key] + (0 if fixed_axis else .5)) * step
                # 绘制细虚线（不在左右最边缘绘制重合边框）
                if vx > left + 4 and vx < left + width - 4:
                    pen = vpen_dot if v_style == "dot" else (vpen_solid if v_style == "solid" else vpen_dash)
                    painter.setPen(pen)
                    painter.drawLine(QPointF(vx, top), QPointF(vx, top + height))
                if v_label:
                    painter.setPen(QColor(t.muted))
                    lw = painter.fontMetrics().horizontalAdvance(v_label) + 8
                    tx = min(left + width - lw, max(left + 2, vx + 4 if vx <= left + width - lw else vx - lw - 4))
                    painter.drawText(QRectF(tx, top + 4, lw, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, v_label)
            painter.restore()

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
                x, y = left + (category_positions[key] + (0 if fixed_axis else .5)) * step, ys(value)
                painter.setPen(QPen(color, getattr(chart,'line_width',2.0)))
                if chart.kind == "line":
                    if last and not series.name.startswith(("B ", "S ", "◇")):
                        painter.drawLine(last, QPointF(x, y))
                    if show_points:
                        painter.setBrush(color)
                        if series.name.startswith("◇"):
                            from PySide6.QtGui import QPolygonF
                            painter.drawPolygon(QPolygonF([QPointF(x, y - 6), QPointF(x + 6, y), QPointF(x, y + 6), QPointF(x - 6, y)]))
                        else:
                            radius = 5 if series.name.startswith(("B ", "S ")) else 3
                            painter.drawEllipse(QPointF(x, y), radius, radius)
                    last = QPointF(x, y)
                else:
                    barwidth = min(40, step * .72 / max(len(chart.series), 1))
                    painter.fillRect(QRectF(x - step * .35 + index * barwidth, min(ys(Decimal(0)), y), max(1, barwidth - 1), abs(y - ys(Decimal(0))) or 1), color)
                self.hits.append((QRectF(x - step / 2, top, step, height), key, value, series.name))

        # 标记最高点与最低点（高亮圆点及金额标签）
        if getattr(chart, "highlight_extremes", False):
            painter.save()
            multi_series = len(chart.series) > 1
            for s_idx, series in enumerate(chart.series):
                valid_pts = [(k, v) for k, v in series.points if v is not None]
                if not valid_pts:
                    continue
                max_item = max(valid_pts, key=lambda p: p[1])
                min_item = min(valid_pts, key=lambda p: p[1])
                extremes = [("max", max_item)]
                if min_item[0] != max_item[0]:
                    extremes.append(("min", min_item))

                for kind, (ekey, evalue) in extremes:
                    if ekey not in categories:
                        continue
                    ex = left + (category_positions[ekey] + (0 if fixed_axis else .5)) * step
                    ey = ys(evalue)
                    is_max = kind == "max"
                    if multi_series:
                        dot_color = QColor(colors[s_idx % len(colors)])
                    else:
                        dot_color = QColor(t.profit if evalue >= 0 else t.loss) if is_max else QColor(t.loss if evalue < 0 else t.profit)

                    # 画端点圆点（双层高亮圈）
                    painter.setPen(QPen(dot_color, 2))
                    painter.setBrush(QColor(t.panel))
                    painter.drawEllipse(QPointF(ex, ey), 4.5, 4.5)
                    painter.setBrush(dot_color)
                    painter.drawEllipse(QPointF(ex, ey), 2.5, 2.5)

                    # 标注金额文字与半透明胶囊背景
                    prefix = f"{series.name} " if multi_series else ""
                    text = f"{prefix}最高 {evalue:+,.2f}" if is_max else f"{prefix}最低 {evalue:+,.2f}"
                    painter.setFont(chart_font(11, bold=True))
                    tw = painter.fontMetrics().horizontalAdvance(text) + 12
                    th = 20
                    tx = min(left + width - tw - 4, max(left + 4, ex - tw / 2))
                    ty = ey - th - 6 if is_max else ey + 6
                    if is_max and ty < top + 2:
                        ty = top + 2
                    elif not is_max and ty + th > top + height - 2:
                        ty = top + height - th - 2

                    badge_rect = QRectF(tx, ty, tw, th)

                    # 1. 先用卡片背景色填充，彻底遮挡任何背景折线或网格，防止文字被线条穿透
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(t.panel))
                    painter.drawRoundedRect(badge_rect, 4, 4)

                    # 2. 绘制半透明色块与边框
                    bg_color = QColor(dot_color)
                    bg_color.setAlpha(30)
                    painter.setPen(QPen(dot_color, 1.2))
                    painter.setBrush(bg_color)
                    painter.drawRoundedRect(badge_rect, 4, 4)

                    # 3. 绘制文字
                    painter.setPen(dot_color)
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()

        zero_y = ys(Decimal(0))
        self.draw_zero(painter, QPointF(left, zero_y), QPointF(left + width, zero_y),
                       QRectF(2, zero_y - 11, left - 12, 22), t, ratio=chart.ratio)
        painter.setFont(chart_font(10 if fixed_axis else 12))
        shown_ticks = set()
        if fixed_axis:
            # 绘制半小时短刻度线与整点长刻度线
            painter.save()
            for index, key in enumerate(categories):
                if key in chart.x_ticks:
                    tx = left + (index + (0 if fixed_axis else .5)) * step
                    lbl = chart.x_labels.get(key, "")
                    is_major = "\n" in lbl or lbl.endswith(":00") or lbl.endswith("08:00")
                    painter.setPen(QPen(QColor(t.muted if is_major else t.border), 1))
                    painter.drawLine(QPointF(tx, top + height), QPointF(tx, top + height + (5 if is_major else 3)))
            painter.restore()

            last_right = 0
            for index, key in enumerate(categories):
                if key not in chart.x_ticks:
                    continue
                lbl = chart.x_labels.get(key, key)
                if not lbl:
                    continue
                lines = lbl.split("\n")
                lw = max(26, max(painter.fontMetrics().horizontalAdvance(l) for l in lines) + 2)
                label_x = min(left + width - lw / 2, max(0, left + index * step - lw / 2))
                is_end = key == chart.x_ticks[-1]
                if is_end or (label_x >= last_right + 3):
                    shown_ticks.add(key)
                    last_right = label_x + lw
        for index, key in enumerate(categories):
            visible = key in shown_ticks if fixed_axis else index % max(1, math.ceil(len(categories) / 12)) == 0
            if visible:
                painter.setPen(QColor(t.muted))
                if fixed_axis:
                    lbl = chart.x_labels.get(key, key)
                    lines = lbl.split("\n")
                    lw = max(26, max(painter.fontMetrics().horizontalAdvance(l) for l in lines) + 2)
                    label_x = min(left + width - lw / 2, max(0, left + index * step - lw / 2))
                    th = 30 if len(lines) > 1 else 20
                    painter.drawText(QRectF(label_x, top + height + 5, lw, th), Qt.AlignmentFlag.AlignCenter, lbl)
                else:
                    painter.drawText(QRectF(left + index * step - 20, top + height + 8, step + 40, 22), Qt.AlignmentFlag.AlignCenter, chart.x_labels.get(key, key))
        painter.save()
        painter.setFont(chart_font(12, bold=len(chart.series) > 1))
        legend_x = float(left)
        legend_y = float(self.height() - 83)
        for s_idx, s in enumerate(chart.series):
            s_color = QColor(colors[s_idx % len(colors)])
            # 绘制图例色条与圆点
            painter.setPen(QPen(s_color, 2.5))
            painter.drawLine(QPointF(legend_x, legend_y + 12.5), QPointF(legend_x + 16, legend_y + 12.5))
            painter.setBrush(s_color)
            painter.drawEllipse(QPointF(legend_x + 8, legend_y + 12.5), 3.0, 3.0)
            legend_x += 22
            s_label = s.name
            if len(chart.series) > 1:
                s_valid = [v for _, v in s.points if v is not None]
                if s_valid:
                    s_label = f"{s.name} ({s_valid[-1]:+,.2f})"
            painter.setPen(s_color if len(chart.series) > 1 else QColor(t.text))
            tw_lbl = painter.fontMetrics().horizontalAdvance(s_label) + 4
            painter.drawText(QRectF(legend_x, legend_y, tw_lbl, 25), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, s_label)
            legend_x += tw_lbl + 18
        painter.restore()

    def draw_zero(self, painter, start, end, label_rect, theme, *, ratio=False):
        painter.save()
        painter.setPen(QPen(QColor(theme.text), 2, Qt.PenStyle.SolidLine))
        painter.drawLine(start, end)
        painter.setFont(chart_font(13, bold=True))
        alignment = Qt.AlignmentFlag.AlignHCenter if start.x() == end.x() else Qt.AlignmentFlag.AlignRight
        painter.drawText(label_rect, alignment | Qt.AlignmentFlag.AlignVCenter, '0%' if ratio else '0')
        painter.restore()

    def paint_capital_assets(self, painter, chart, theme):
        """Three stacked bar segments and an independent total-assets line."""
        palette = ("#B8CBD7", "#70AD47", "#C00000", "#4472C4")
        series = chart.series
        categories = list(dict.fromkeys(key for item in series for key, _ in item.points))
        points = [dict(item.points) for item in series]
        # Include the full stacked height, rather than scaling to each segment.
        bounds = [Decimal(0)]
        for key in categories:
            segments = [data.get(key) for data in points[:3]]
            if all(value is not None for value in segments):
                bounds.extend((sum((v for v in segments if v >= 0), Decimal(0)),
                               sum((v for v in segments if v < 0), Decimal(0))))
            if points[3].get(key) is not None:
                bounds.append(points[3][key])
        low, high = min(bounds), max(bounds)
        raw_step = (high - low) / 6 or Decimal(1)
        magnitude = Decimal(10) ** raw_step.adjusted()
        tick = next(n * magnitude for n in map(Decimal, (1, 2, 5, 10)) if n * magnitude >= raw_step)
        low = (low // tick - (1 if low % tick else 0)) * tick
        high = ((high // tick) + 1) * tick
        left, top = 86, 105
        width, height = self.width() - left - 30, self.height() - top - 99
        span = high - low
        step = width / max(len(categories), 1)

        def ys(value):
            return top + height - float((value - low) / span) * height

        painter.setFont(chart_font(12))
        value = low
        while value <= high:
            y = ys(value)
            if value == 0:
                value += tick
                continue
            painter.setPen(QPen(QColor(theme.border), 1))
            painter.drawLine(QPointF(left, y), QPointF(left + width, y))
            painter.setPen(QColor(theme.muted))
            painter.drawText(QRectF(0, y - 10, left - 10, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{value:,.0f}")
            value += tick
        # Legend uses actual swatches; it is above the plot like the Excel chart.
        legend_widths = [painter.fontMetrics().horizontalAdvance(item.name) + 36 for item in series]
        legend_x = max(left, (self.width() - sum(legend_widths)) / 2)
        for index, item in enumerate(series):
            painter.setPen(QPen(QColor(palette[index]), 2.5))
            if index == 3:
                painter.drawLine(QPointF(legend_x, 70), QPointF(legend_x + 22, 70))
            else:
                painter.fillRect(QRectF(legend_x, 65, 22, 10), QColor(palette[index]))
            painter.setPen(QColor(theme.text))
            painter.drawText(QRectF(legend_x + 27, 58, legend_widths[index] - 27, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, item.name)
            legend_x += legend_widths[index]
        last = None
        label_skip = max(1, math.ceil(len(categories) * 42 / width))
        for index, key in enumerate(categories):
            x = left + (index + .5) * step
            segments = [data.get(key) for data in points[:3]]
            positive = negative = Decimal(0)
            bar_width = min(42, step * .62)
            if all(value is not None for value in segments):
                for segment_index, value in enumerate(segments):
                    start = positive if value >= 0 else negative
                    end = start + value
                    if value >= 0:
                        positive = end
                    else:
                        negative = end
                    rect = QRectF(x - bar_width / 2, min(ys(start), ys(end)), bar_width, abs(ys(start) - ys(end)))
                    painter.fillRect(rect, QColor(palette[segment_index]))
                    if value:
                        self.hits.append((rect, key, value, series[segment_index].name))
            asset = points[3].get(key)
            if asset is None:
                last = None
            else:
                point = QPointF(x, ys(asset))
                painter.setPen(QPen(QColor(palette[3]), 2.5))
                if last is not None:
                    painter.drawLine(last, point)
                last = point
                # A small hit area keeps segment tooltips accessible below the line.
                self.hits.append((QRectF(x - 7, point.y() - 7, 14, 14), key, asset, series[3].name))
            if index % label_skip == 0 or index == len(categories) - 1:
                painter.setPen(QColor(theme.muted))
                label = key[2:] if len(key) == 7 else key
                painter.drawText(QRectF(x - 25, top + height + 8, 50, 22), Qt.AlignmentFlag.AlignCenter, label)
        zero_y = ys(Decimal(0))
        self.draw_zero(painter, QPointF(left, zero_y), QPointF(left + width, zero_y),
                       QRectF(0, zero_y - 11, left - 10, 22), theme)
        painter.setPen(QColor(theme.muted))
        painter.drawText(QRectF(left, top - 24, width, 20), Qt.AlignmentFlag.AlignLeft, "金额（" + (chart.note.split(" · ")[1] if " · " in chart.note else "报告币种") + "）")

    def mouseMoveEvent(self, event):
        # 移除图表悬浮框，避免冗余提示
        pass

    def mousePressEvent(self, event):
        if self.chart:
            for rect, key, _, _ in reversed(self.hits):
                if rect.contains(event.position()):
                    self.selected.emit(self.chart.link, key)
                    return

    def export_png(self, path):
        # Exclusive file creation prevents overwriting an existing PNG/source.
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
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.setMinimumWidth(300)
        row.addWidget(self.selector)
        self.month_label = QLabel('查看月份')
        row.addWidget(self.month_label)
        self.month = QComboBox()
        self.month.setMinimumWidth(160)
        self.month_label.hide()
        self.month.hide()
        row.addWidget(self.month)
        self.offset = QComboBox()
        self.offset.addItems(["排行榜 1–24", "排行榜 25–48", "排行榜 49–72", "排行榜 73–96"])
        self.offset.hide()
        row.addWidget(self.offset)
        self.rank_order = QComboBox()
        for label, value in (('盈利最多', 'profit'), ('亏损最多', 'loss'), ('盈亏幅度最大', 'magnitude')):
            self.rank_order.addItem(label, value)
        self.rank_order.hide()
        row.addWidget(self.rank_order)
        row.addStretch()
        button = QPushButton("导出 PNG…")
        button.clicked.connect(self.export)
        row.addWidget(button)
        layout.addLayout(row)
        self.canvas = ChartCanvas()
        self.canvas.selected.connect(self.drill)
        layout.addWidget(self.canvas, 1)
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
        self.canvas.chart = None
        self.canvas.update()

    def set_analytics(self, analytics):
        self.analytics, self.charts = analytics, analytics.charts
        selected = self.selector.currentIndex()
        selected_month = self.month.currentData()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems([f"{c.id:02}  {c.title}" for c in self.charts])
        self.selector.setCurrentIndex(max(0, selected))
        self.selector.blockSignals(False)
        self.month.blockSignals(True)
        self.month.clear()
        self.month.addItem('当前 / 最新', None)
        for month in sorted({r.values[0] for r in analytics.reports["每月持仓收益"].rows}, reverse=True):
            self.month.addItem(month, month)
        self.month.setCurrentIndex(max(0, self.month.findData(selected_month)))
        self.month.blockSignals(False)
        self.update_month()

    def update_month(self):
        if self.analytics:
            self.charts = build_charts(self.analytics, selected_month=self.month.currentData())
            self.show_chart()

    def show_chart(self, *_):
        if not self.charts:
            self.offset.hide()
            self.rank_order.hide()
            self.month_label.hide()
            self.month.hide()
            return
        chart = self.charts[self.selector.currentIndex()]
        monthly = chart.id in (1, 2, 3, 4, 5, 6, 21)
        self.month_label.setVisible(monthly)
        self.month.setVisible(monthly)
        self.month_label.setText('基准月份' if chart.id in (4, 5, 6) else '收益月份' if chart.id == 21 else '查看月份')
        self.month.setToolTip('显示所选基准月份的上一个月末快照。' if chart.id in (4, 5, 6) else '选择历史月份后立即更新图表；当前 / 最新恢复最新数据。')
        ranked = chart.kind == "horizontal" and chart.id in (15, 16, 17)
        self.offset.setVisible(ranked)
        self.rank_order.setVisible(ranked)
        if ranked:
            count = len({key for s in chart.series for key, _ in s.points})
            selected = self.offset.currentIndex()
            self.offset.blockSignals(True)
            self.offset.clear()
            self.offset.addItems([f'排行榜 {start+1}–{min(start+24, count)}' for start in range(0, count, 24)] or ['排行榜 0条'])
            self.offset.setCurrentIndex(selected if 0 <= selected < self.offset.count() else 0)
            self.offset.blockSignals(False)
            start = self.offset.currentIndex() * 24
            chart = rank_chart(chart, self.rank_order.currentData(), start)
        self.canvas.chart = chart
        self.canvas.update()

    def export(self):
        if not self.canvas.chart:
            return
        path, _ = QFileDialog.getSaveFileName(self, "图表导出（含口径和日期）", self.canvas.chart.title + ".png", "PNG (*.png)")
        if path:
            try:
                self.canvas.export_png(path)
            except (OSError, ValueError):
                self.error.emit("导出未完成：目标已存在或不可写。请选择新文件名。")
