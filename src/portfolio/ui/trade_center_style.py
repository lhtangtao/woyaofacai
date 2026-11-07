"""Presentation components scoped to the trade query center."""
import json
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QPainter,QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QPushButton,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,
    QWidget,QScrollArea,QFrame,QStyledItemDelegate,QStyle,QStyleOptionViewItem,QToolButton,QPlainTextEdit)

from portfolio.services.trade_center import KEYS
from portfolio.ui.theme import tokens

STYLE = '''
QWidget#tradeCenter { background:#F5F7FB; color:#334155; font-size:13px; }
QWidget#tradeCenter QFrame#surface, QWidget#tradeCenter QWidget#filterCard,
QWidget#tradeCenter QWidget#tradeDrawer { background:white; border:1px solid #E8EDF5; border-radius:12px; }
QWidget#tradeCenter QLabel { background:transparent; border:0; }
QWidget#tradeCenter QLabel#fieldLabel { color:#64748B; font-size:12px; }
QWidget#tradeCenter QLabel#sectionTitle { color:#1E293B; font-size:15px; font-weight:600; }
QWidget#tradeCenter QLabel#pageHeading { color:#1E293B; font-size:22px; font-weight:700; }
QWidget#tradeCenter QPushButton { min-height:18px; padding:7px 12px; border:1px solid #E2E8F0; border-radius:7px; background:white; color:#334155; }
QWidget#tradeCenter QPushButton:hover { background:#EFF6FF; border-color:#BFDBFE; }
QWidget#tradeCenter QPushButton#primary { background:#2563EB; color:white; border-color:#2563EB; font-weight:600; }
QWidget#tradeCenter QPushButton#textButton { border:0; background:transparent; color:#2563EB; }
QWidget#tradeCenter QLineEdit, QWidget#tradeCenter QComboBox, QWidget#tradeCenter QDateEdit { min-height:20px; padding:6px 9px; border:1px solid #E2E8F0; border-radius:7px; background:#FFFFFF; }
QWidget#tradeCenter QLineEdit:focus, QWidget#tradeCenter QComboBox:focus { border-color:#93B4F5; }
QWidget#tradeCenter QPushButton#statCard, QWidget#tradeCenter QPushButton#staticStatCard { padding:0; min-height:102px; max-height:102px; border:1px solid #E5EBF4; border-radius:12px; background:white; text-align:left; }
QWidget#tradeCenter QPushButton#statCard:hover { border-color:#BFDBFE; background:#FBFDFF; }
QWidget#tradeCenter QPushButton#statCard:checked { border:1px solid #2563EB; background:#EFF6FF; }
QWidget#tradeCenter QLabel#statLabel { color:#334155; font-size:12px; font-weight:600; }
QWidget#tradeCenter QLabel#statValue { color:#1E293B; font-size:26px; font-weight:700; }
QWidget#tradeCenter QLabel#statHint { color:#64748B; font-size:11px; }
QWidget#tradeCenter QPushButton#filterChip { background:#EBF2FF; border:0; color:#2563EB; padding:4px 8px; min-height:16px; font-size:11px; }
QWidget#tradeCenter QTableView { background:white; alternate-background-color:#FAFCFF; border:0; gridline-color:#F1F5F9; selection-background-color:#EAF2FF; selection-color:#1E293B; font-size:13px; }
QWidget#tradeCenter QTableView::item { padding:0 10px; border-bottom:1px solid #F0F3F8; }
QWidget#tradeCenter QTableView::item:selected { background:#EAF2FF; color:#1E293B; }
QWidget#tradeCenter QHeaderView::section { background:#F1F5FB; color:#64748B; border:0; border-bottom:1px solid #E6EDF7; padding:0 10px; font-size:12px; }
QWidget#tradeCenter QTabWidget::pane { border:0; background:white; }
QWidget#tradeCenter QTabBar::tab { padding:10px 9px; color:#64748B; background:#F5F7FB; border:0; }
QWidget#tradeCenter QTabBar::tab:selected { color:#2563EB; background:#EFF6FF; border-bottom:2px solid #2563EB; }
QWidget#tradeCenter QScrollArea { border:0; background:white; }
QWidget#tradeCenter QFrame#detailGroup { background:#FFFFFF; border:1px solid #E5EBF4; border-radius:9px; }
QWidget#tradeCenter QLabel#detailKey { color:#64748B; font-size:12px; }
QWidget#tradeCenter QLabel#detailValue { color:#1E293B; font-size:13px; }
QWidget#tradeCenter QWidget#tradeSegments { background:#EFF4FB; border-radius:8px; }
QWidget#tradeCenter QPushButton#tradeSegment { background:transparent; border:0; min-height:22px; padding:4px 9px; }
QWidget#tradeCenter QPushButton#tradeSegment:checked { background:#2563EB; color:white; font-weight:600; }
QWidget#tradeCenter QLabel#tradeTicker { color:#2563EB; font-size:22px; font-weight:700; }
QWidget#tradeCenter QLabel#costBadge { background:#F1F5FB; border-radius:6px; padding:4px 7px; font-size:12px; }
QWidget#tradeCenter QLabel#compareHeader { background:#F1F5FB; padding:6px; color:#334155; font-weight:600; }
QWidget#tradeCenter QFrame#costCallout { background:#EFF6FF; border-radius:10px; }
QWidget#tradeCenter QListWidget#relatedList { background:white; border:0; }
QWidget#tradeCenter QListWidget#relatedList::item { background:#FAFCFF; border:1px solid #E8EDF5; border-radius:8px; padding:6px 9px; }
QWidget#tradeCenter QListWidget#relatedList::item:hover, QWidget#tradeCenter QListWidget#relatedList::item:selected { background:#EFF6FF; color:#1E293B; }
QWidget#tradeCenter QToolButton { color:#2563EB; background:#F1F5FB; border:0; padding:6px; border-radius:6px; }

'''


STAT_ICONS={
    'layers':'M12 3 3 8l9 5 9-5-9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',
    'buy':'M6 18 18 6M6 6h12v12',
    'sell':'M6 6l12 12M6 18h12V6',
    'fees':'M19 6c0 2-3 3-7 3S5 8 5 6s3-3 7-3 7 1 7 3ZM5 6v12c0 2 3 3 7 3s7-1 7-3V6M5 12c0 2 3 3 7 3s7-1 7-3',
    'review':'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 7v5l3 2',
}


class StatCard(QPushButton):
    def __init__(self,title,icon,accent,*,interactive):
        super().__init__()
        self.setObjectName('statCard' if interactive else 'staticStatCard')
        self.setCheckable(interactive)
        if not interactive:
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName(title)
        layout=QGridLayout(self)
        layout.setContentsMargins(13,9,13,9)
        layout.setSpacing(3)
        self.label=QLabel(title)
        self.label.setObjectName('statLabel')
        self.value=detail_label('—','statValue')
        self.hint=detail_label('','statHint')
        self.hint.setWordWrap(False)
        self.value.setWordWrap(False)
        glyph=QLabel()
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fill={'#16A34A':'#ECFDF3','#DC2626':'#FFF1F2'}.get(accent,'#EFF6FF')
        glyph.setStyleSheet(f'background:{fill};border-radius:8px;')
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="{STAT_ICONS[icon]}" fill="none" stroke="{accent}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        scale=self.devicePixelRatioF()
        pixels=round(18*scale)
        pixmap=QPixmap(pixels,pixels)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pixmap)
        QSvgRenderer(svg.encode()).render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        glyph.setPixmap(pixmap)
        glyph.setFixedSize(27,27)
        layout.addWidget(self.label,0,0)
        layout.addWidget(glyph,0,1,Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.value,1,0,1,2)
        layout.addWidget(self.hint,2,0,1,2)
        layout.setColumnStretch(0,1)
        for child in (self.label,self.value,self.hint,glyph):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.display=['—','']

    def setText(self,text):
        title,_,body=text.partition('\n')
        value,_,hint=body.partition('\n')
        self.label.setText(title)
        self.display=[value or '—',hint]
        self.setAccessibleName(title+' '+value+' '+hint)
        self.setToolTip(title+'\n'+value+'\n'+hint)
        self.value.setStyleSheet('font-size:18px;' if len(value)>16 else '')
        self.value.setToolTip(value)
        self.hint.setToolTip(hint)
        self.fit_text()

    def fit_text(self):
        for label,text in zip((self.value,self.hint),self.display):
            label.setText(label.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(1,self.width()-26)))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fit_text()


EXECUTION=('成交金额','数量','成交价','当前价','手续费','方向/类型')
IDENTITY=('人员','券商/账户','分类','交易时间','订单编号','交易记录ID')
SOURCE=('数据来源','订单编号','交易记录ID','原始记录ID','来源文件','导入批次','成交编号','工作表','原始行号')
VERIFICATION=('归属关联状态','费用来源','费用核实状态','解析状态','记录状态','账务角色','解析/审计说明')
COMPARISON=(
    ('持仓数量','成交前持仓','成交后持仓'),
    ('每股成本价','成交前成本价','成交后成本价'),
    ('净投入',None,'成交后净投入'),
)
FIELD_LABELS={'数量':'成交数量','方向/类型':'交易方向','人员':'归属人员','券商/账户':'券商渠道 / 账户','分类':'股票分类'}


def side_tone(row):
    if row.get('kind_code')=='MARKET_TRADE':
        if row.get('side_code')=='BUY':
            return '#ECFDF3','#15803D'
        if row.get('side_code')=='SELL':
            return '#FFF1F2','#B91C1C'
    return '#EFF6FF','#2563EB'


def detail_label(value,name='detailKey'):
    label=QLabel(value)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class DetailPanel(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)

    def setPlainText(self,text):
        box=QWidget()
        layout=QVBoxLayout(box)
        layout.addWidget(detail_label(text))
        layout.addStretch()
        self.setWidget(box)

    def show_fields(self,section,fields,format_value,item,quote_info):
        box=QWidget()
        layout=QVBoxLayout(box)
        layout.setContentsMargins(2,12,2,12)
        layout.setSpacing(12)

        def card(title,columns):
            frame=QFrame()
            frame.setObjectName('detailGroup')
            grid=QGridLayout(frame)
            grid.setContentsMargins(12,10,12,10)
            grid.setSpacing(9)
            grid.addWidget(detail_label(title,'sectionTitle'),0,0,1,columns)
            layout.addWidget(frame)
            return grid

        def add_fields(title,keys):
            present=[key for key in keys if key in fields]
            if not present:
                return
            grid=card(title,2)
            for index,key in enumerate(present,1):
                value=fields[key]
                if isinstance(value,(dict,list)):
                    toggle=QToolButton()
                    toggle.setText(key+' · 展开证据')
                    toggle.setCheckable(True)
                    raw=QPlainTextEdit(json.dumps(value,ensure_ascii=False,indent=2,default=str))
                    raw.setReadOnly(True)
                    raw.setMinimumHeight(180)
                    raw.hide()
                    toggle.toggled.connect(raw.setVisible)
                    grid.addWidget(toggle,index*2-1,0,1,2)
                    grid.addWidget(raw,index*2,0,1,2)
                    continue
                grid.addWidget(detail_label(FIELD_LABELS.get(key,key)),index*2-1,0)
                label=detail_label(format_value(key,value),'detailValue')
                label.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(label,index*2-1,1)
            grid.setColumnStretch(1,1)

        if section=='交易概览':
            grid=card('成交信息',3)
            for index,key in enumerate(EXECUTION):
                value=item['side'] if key=='方向/类型' else format_value(key,fields.get(key))
                detail=QWidget()
                cell=QVBoxLayout(detail)
                cell.setContentsMargins(0,0,0,0)
                cell.setSpacing(3)
                cell.addWidget(detail_label(FIELD_LABELS.get(key,key)))
                label=detail_label(value,'detailValue')
                cell.addWidget(label)
                if key=='成交金额':
                    label.setStyleSheet('font-size:24px;font-weight:700;')
                    if item.get('currency'):
                        cell.addWidget(detail_label(item['currency']))
                elif key=='当前价':
                    cell.addWidget(detail_label(quote_info))
                elif key=='手续费' and fields.get(key) is None:
                    cell.addWidget(detail_label('尚未核实'))
                elif key=='方向/类型':
                    fill,color=side_tone(item)
                    label.setStyleSheet(f'background:{fill};color:{color};border-radius:7px;padding:4px 7px;')
                cell.addStretch()
                grid.addWidget(detail,index//3+1,index%3)
                grid.setColumnStretch(index%3,1)
            add_fields('基本信息',IDENTITY)
            used=(*EXECUTION,*IDENTITY,'报价时间','行情状态')
            add_fields('补充信息',tuple(key for key in fields if key not in used))
        elif section=='成本变化':
            grid=card('持仓与成本',3)
            for column,title in enumerate(('指标','成交前','成交后')):
                heading=detail_label(title,'compareHeader')
                if column:
                    heading.setAlignment(Qt.AlignmentFlag.AlignRight)
                    grid.setColumnStretch(column,1)
                grid.addWidget(heading,1,column)
            for row,(title,before,after) in enumerate(COMPARISON,2):
                grid.addWidget(detail_label(title),row,0)
                for column,key in enumerate((before,after),1):
                    value=detail_label(format_value(key,fields.get(key)) if key else '—','detailValue')
                    value.setAlignment(Qt.AlignmentFlag.AlignRight)
                    grid.addWidget(value,row,column)
            callout=QFrame()
            callout.setObjectName('costCallout')
            content=QVBoxLayout(callout)
            heading=detail_label('成本变化 · '+format_value('每股成本变化',fields.get('每股成本变化')),'sectionTitle')
            delta=item.get('cost_diff')
            if delta:
                theme=tokens(self)
                heading.setStyleSheet(f'color:{theme.profit if delta<0 else theme.loss};')
            content.addWidget(heading)
            content.addWidget(detail_label(fields.get('计算说明') or '暂无可用的成本说明。','detailValue'))
            layout.addWidget(callout)
            used={key for _,before,after in COMPARISON for key in (before,after)} | {'每股成本变化','计算说明'}
            add_fields('核算信息',tuple(key for key in fields if key not in used))
        else:
            add_fields('来源记录',SOURCE)
            add_fields('核查状态',VERIFICATION)
            add_fields('来源证据',tuple(key for key in fields if key not in (*SOURCE,*VERIFICATION)))

        layout.addStretch()
        self.setWidget(box)


class TradeCellDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        table=self.parent()
        opt=QStyleOptionViewItem(option)
        self.initStyleOption(opt,index)
        selected=bool(opt.state & QStyle.StateFlag.State_Selected)
        if not selected and table.hovered_row==index.row():
            opt.backgroundBrush=QColor('#F0F6FF')
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        key=KEYS[index.column()]
        if key=='side':
            text=opt.text
            opt.text=''
        opt.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,opt,painter,opt.widget)
        if key=='side':
            fill,color=side_tone(index.model().rows[index.row()])
            width=min(opt.rect.width()-10,opt.fontMetrics.horizontalAdvance(text)+16)
            rect=QRectF(opt.rect.x()+8,opt.rect.center().y()-11,width,22)
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(rect,11,11)
            painter.setPen(QColor(color))
            label=opt.fontMetrics.elidedText(text,Qt.TextElideMode.ElideRight,int(width)-10)
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,label)
            painter.restore()
        header=table.horizontalHeader()
        first=next((header.logicalIndex(pos) for pos in range(header.count()) if not header.isSectionHidden(header.logicalIndex(pos))),None)
        if selected and index.column()==first:
            painter.fillRect(opt.rect.x(),opt.rect.y(),3,opt.rect.height(),QColor('#2563EB'))
