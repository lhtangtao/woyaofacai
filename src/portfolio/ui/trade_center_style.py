"""Presentation components scoped to the trade query center."""
import json
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QColor,QPainter,QFont
from PySide6.QtWidgets import (QPushButton,QLabel,QVBoxLayout,QHBoxLayout,QGridLayout,
    QWidget,QScrollArea,QFrame,QStyledItemDelegate,QStyle,QStyleOptionViewItem,QToolButton,QPlainTextEdit)

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
QWidget#tradeCenter QPushButton#statCard { padding:0; min-height:102px; max-height:102px; border:1px solid #E5EBF4; border-radius:12px; background:white; text-align:left; }
QWidget#tradeCenter QPushButton#statCard:hover { border-color:#BFDBFE; background:#FBFDFF; }
QWidget#tradeCenter QPushButton#statCard:checked { border:1px solid #2563EB; background:#EFF6FF; }
QWidget#tradeCenter QLabel#statLabel { color:#64748B; font-size:12px; }
QWidget#tradeCenter QLabel#statValue { color:#1E293B; font-size:26px; font-weight:700; }
QWidget#tradeCenter QLabel#statHint { color:#64748B; font-size:11px; }
QWidget#tradeCenter QPushButton#filterChip { background:#EBF2FF; border:0; color:#2563EB; padding:4px 8px; min-height:16px; font-size:11px; }
QWidget#tradeCenter QTableView { background:white; alternate-background-color:#FAFCFF; border:0; gridline-color:#F1F5F9; selection-background-color:#EAF2FF; selection-color:#1E293B; font-size:13px; }
QWidget#tradeCenter QTableView::item { padding:0 10px; border-bottom:1px solid #F0F3F8; }
QWidget#tradeCenter QTableView::item:hover { background:#F0F6FF; }
QWidget#tradeCenter QHeaderView::section { background:#F1F5FB; color:#64748B; border:0; border-bottom:1px solid #E6EDF7; padding:0 10px; font-size:12px; }
QWidget#tradeCenter QTabWidget::pane { border:0; background:white; }
QWidget#tradeCenter QTabBar::tab { padding:10px 9px; color:#64748B; background:#F5F7FB; border:0; }
QWidget#tradeCenter QTabBar::tab:selected { color:#2563EB; background:#EFF6FF; border-bottom:2px solid #2563EB; }
QWidget#tradeCenter QScrollArea { border:0; background:white; }
QWidget#tradeCenter QFrame#detailGroup { background:#FFFFFF; border:1px solid #E5EBF4; border-radius:9px; }
QWidget#tradeCenter QLabel#detailKey { color:#64748B; font-size:12px; }
QWidget#tradeCenter QLabel#detailValue { color:#1E293B; font-size:13px; }
'''


class StatCard(QPushButton):
    def __init__(self,title,icon,accent,parent=None):
        super().__init__(parent);self.setObjectName('statCard')
        layout=QGridLayout(self);layout.setContentsMargins(16,12,16,12);layout.setSpacing(4)
        self.label=QLabel(title);self.label.setObjectName('statLabel')
        self.value=QLabel('—');self.value.setObjectName('statValue')
        self.hint=QLabel('全部匹配记录');self.hint.setObjectName('statHint')
        glyph=QLabel(icon);glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        glyph.setStyleSheet('color:'+accent+';font-size:22px;background:#F4F7FC;border-radius:9px;');glyph.setFixedSize(34,34)
        layout.addWidget(self.label,0,0);layout.addWidget(self.value,1,0);layout.addWidget(self.hint,2,0);layout.addWidget(glyph,0,1,2,1)
        layout.setColumnStretch(0,1)
        for child in (self.label,self.value,self.hint,glyph):child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setCheckable(True)
    def setText(self,text):
        lines=text.split('\n');self.label.setText(lines[0]);self.value.setText(lines[1] if len(lines)>1 else '—');self.hint.setText(lines[2] if len(lines)>2 else '全部匹配记录')
        self.value.setToolTip(self.value.text());self.hint.setToolTip(self.hint.text())
        # Multi-currency totals can be longer; wrap without clipping or losing a currency.
        self.value.setWordWrap(True);self.hint.setWordWrap(True)
        self.value.setStyleSheet('font-size:18px;' if len(self.value.text())>16 else '')


GROUPS={
'交易概览':(('基本信息',('交易时间','订单编号','交易记录ID','人员','券商/账户','Ticker','分类','方向/类型','划转对象','微操标记')),
          ('成交与行情',('当前价','报价时间','行情状态','成交价','数量','成交金额','手续费','币种'))),
'成本变化':(('持仓与成本变化',('成交前持仓','成交后持仓','成交前成本价','成交后成本价','每股成本变化','成交后净投入','本笔已实现盈亏')),
          ('核算状态',('成本状态','计算说明','已实现盈亏说明'))),
'来源追溯':(('来源记录',('数据来源','原始记录ID','订单编号','成交编号','导入批次','来源文件','工作表','原始行号')),
          ('核对状态',('归属关联状态','费用来源','费用核实状态','解析状态','记录状态','账务角色','解析/审计说明')))}


class DetailPanel(QScrollArea):
    def __init__(self):
        super().__init__();self.setWidgetResizable(True)
    def setPlainText(self,text):
        box=QWidget();layout=QVBoxLayout(box);label=QLabel(text);label.setWordWrap(True);layout.addWidget(label);layout.addStretch();self.setWidget(box)
    def show_fields(self,section,fields,format_value):
        box=QWidget();layout=QVBoxLayout(box);layout.setContentsMargins(2,12,2,12);layout.setSpacing(12)
        groups=list(GROUPS.get(section,()));used={key for _,keys in groups for key in keys}
        groups.append(('补充信息',tuple(key for key in fields if key not in used)))
        for title,keys in groups:
            present=[key for key in keys if key in fields]
            if not present:continue
            card=QFrame();card.setObjectName('detailGroup');grid=QGridLayout(card);grid.setContentsMargins(14,12,14,12);grid.setHorizontalSpacing(14);grid.setVerticalSpacing(9)
            heading=QLabel(title);heading.setObjectName('sectionTitle');grid.addWidget(heading,0,0,1,2)
            for row,key in enumerate(present,1):
                value=fields[key]
                if isinstance(value,(dict,list)):
                    toggle=QToolButton();toggle.setText(key+' · 展开原始证据');toggle.setCheckable(True)
                    raw=QPlainTextEdit(json.dumps(value,ensure_ascii=False,indent=2,default=str));raw.setReadOnly(True);raw.setMinimumHeight(180);raw.hide();toggle.toggled.connect(raw.setVisible)
                    grid.addWidget(toggle,row*2-1,0,1,2);grid.addWidget(raw,row*2,0,1,2);continue
                label=QLabel(key);label.setObjectName('detailKey');label.setWordWrap(True)
                text=QLabel(format_value(key,value));text.setObjectName('detailValue');text.setWordWrap(True);text.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                if key in ('成交金额','当前价','成交后成本价','每股成本变化'):text.setStyleSheet('font-weight:600;')
                grid.addWidget(label,row*2-1,0);grid.addWidget(text,row*2-1,1)
            grid.setColumnStretch(1,1);layout.addWidget(card)
        layout.addStretch();self.setWidget(box)


class TradeCellDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        from portfolio.services.trade_center import KEYS
        key=KEYS[index.column()];row=index.model().rows[index.row()]
        if key!='side':return super().paint(painter,option,index)
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);text=opt.text;opt.text=''
        style=opt.widget.style();style.drawControl(QStyle.ControlElement.CE_ItemViewItem,opt,painter,opt.widget)
        side=row.get('side_code');color='#15803D' if side=='BUY' else '#DC2626' if side=='SELL' else '#2563EB'
        fill='#ECFDF3' if side=='BUY' else '#FFF1F2' if side=='SELL' else '#EFF6FF'
        width=min(option.rect.width()-10,option.fontMetrics.horizontalAdvance(text)+18)
        rect=QRectF(option.rect.x()+5,option.rect.center().y()-11,width,22)
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(fill));painter.drawRoundedRect(rect,6,6);painter.setPen(QColor(color));painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text);painter.restore()
