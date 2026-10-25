"""Donut and exact-value legend for stock ownership; no negative pie slices."""
from decimal import localcontext

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen


def shares_text(value):
    return '— 数据不完整' if value is None else format(value, ',.8f').rstrip('0').rstrip('.')+' 股'


def draw_ownership(painter, rect, rows, total, symbol, theme):
    p = painter
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    size = min(380, rect.height()-48, rect.width()*0.43)
    circle = QRectF(24, (rect.height()-size)/2, size, size)
    with localcontext() as ctx:
        ctx.prec = 50
        valid = bool(rows) and total is not None and total > 0 and all(r.values[1] is not None and r.values[1] >= 0 for r in rows)
        valid = valid and sum(r.values[1] for r in rows) == total
        palette = (theme.brand, theme.teal, theme.violet, '#D97706', '#DB2777', '#0891B2', '#65A30D', '#6366F1')
        colors = [theme.subtle if r.metadata['person_id'] == 'residual' else palette[i % len(palette)] for i, r in enumerate(rows)]
        if valid:
            start = 90*16
            used = 0
            p.setPen(QPen(QColor(theme.panel), 2))
            for i, row in enumerate(rows):
                span = 360*16-used if i == len(rows)-1 else int(row.values[1]/total*(360*16))
                p.setBrush(QColor(colors[i]))
                p.drawPie(circle, start, -span)
                start -= span
                used += span
            hole = circle.adjusted(size*0.28, size*0.28, -size*0.28, -size*0.28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.panel))
            p.drawEllipse(hole)
            p.setPen(QColor(theme.text))
            font = QFont(p.font())
            font.setBold(True)
            p.setFont(font)
            p.drawText(hole.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, symbol+'\n'+shares_text(total))
            font.setBold(False)
            p.setFont(font)
        else:
            p.setPen(QColor(theme.muted))
            message = '暂无持股数据' if not rows else '股数存在负值、缺失或未核对一致，\n请查看持股明细'
            p.drawText(circle, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
    left = circle.right()+48
    width = max(1, rect.width()-left-24)
    y = max(24, (rect.height()-(len(rows)+1)*36)/2)
    p.setPen(QColor(theme.muted))
    p.drawText(QRectF(left+24, y, width*0.4-24, 30), Qt.AlignmentFlag.AlignVCenter, '人员')
    p.drawText(QRectF(left+width*0.4, y, width*0.36, 30), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, '持有股数')
    p.drawText(QRectF(left+width*0.78, y, width*0.22, 30), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, '占比')
    for i, row in enumerate(rows):
        y += 36
        p.fillRect(QRectF(left, y+10, 12, 12), QColor(colors[i]))
        p.setPen(QColor(theme.text))
        name = p.fontMetrics().elidedText(row.values[0], Qt.TextElideMode.ElideRight, max(1, int(width*0.4-32)))
        p.drawText(QRectF(left+24, y, width*0.4-24, 32), Qt.AlignmentFlag.AlignVCenter, name)
        p.drawText(QRectF(left+width*0.4, y, width*0.36, 32), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, shares_text(row.values[1]))
        percent = '—' if row.values[2] is None else f'{row.values[2]:.2%}'
        p.drawText(QRectF(left+width*0.78, y, width*0.22, 32), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, percent)
