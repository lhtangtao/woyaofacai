"""Local trade-center controls; all query and financial work runs in workers."""
from datetime import date
from decimal import Decimal,localcontext,ROUND_HALF_UP
from pathlib import Path
import re
from PySide6.QtCore import QDate,Qt,QEvent,QSize
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,QComboBox,QLineEdit,QDateEdit,QCheckBox,QButtonGroup,QListWidget,QListWidgetItem,QTabWidget,QFileDialog,QMenu,QAbstractItemView,QTableView,QApplication,QFrame,QSizePolicy)
from portfolio.domain.models import QueryScope,Ledger,exact_text
from portfolio.services.trade_center import TradeCenterService,COLUMNS,KEYS,SIDES,KINDS
from portfolio.ui.workers import Worker
from portfolio.ui.finance_table import FinanceModel,FinanceTable
from portfolio.ui.trade_center_style import STYLE,StatCard,DetailPanel,TradeCellDelegate,side_tone
from PySide6.QtGui import QColor,QFont,QKeySequence,QShortcut
from portfolio.ui.theme import numeric_font,tokens
from portfolio.ui.components import FlowLayout


MONEY_KEYS={'current_price','price','amount','fees','post_cost','pre_cost','post_net','realized_pnl'}
PRESETS={
    '日常查看': ('trade_date','broker','person_name','symbol','category','side','current_price','price',
               'quantity','amount','fees','cost_change'),
    '成本分析': ('trade_date','person_name','broker','symbol','side','quantity','post_quantity','post_cost','cost_change'),
    '完整字段': KEYS,
}
NUMERIC_KEYS=MONEY_KEYS | {'quantity','post_quantity','cost_change'}
DAILY_CENTER_KEYS={'symbol','side','current_price'}
OPTION_LABELS={
    **SIDES, **KINDS, 'LINKED':'已关联','LEGACY_UNLINKED':'历史未关联','REVIEW_REQUIRED':'待核查',
    'NOT_APPLICABLE':'不适用','EXCEL':'Excel 导入','CSV':'CSV 导入','API':'接口同步','MANUAL':'手工录入',
    'LONGBRIDGE':'长桥','SCHWAB':'嘉信','FIRSTRADE':'第一证券',
}
MONEY_LABELS={'当前价','成交价','成交金额','手续费','成交前成本价','成交后成本价','成交后净投入','本笔已实现盈亏'}


def money_text(value, precision=2):
    if value in (None,''):return '—'
    with localcontext() as context:
        context.prec=100
        text=format(Decimal(str(value)).quantize(Decimal(1).scaleb(-precision),rounding=ROUND_HALF_UP),f',.{precision}f')
    return '0.00' if text=='-0.00' else text


def time_text(value, *, precise=None):
    if not value:
        return '—'
    if not precise and re.fullmatch(r'\d{4}-\d{2}-\d{2}[ T]00:00(?::00(?:\.0+)?)?',value):
        return value[:10]
    return value


def quote_text(row):
    return (row.get('quote_status') or '缺少有效行情')+' · '+(row.get('quote_time') or '无报价时间')


def cell_text(row,key):
    if key=='trade_date':
        return time_text(row.get(key))
    if key=='cost_change' and row.get('cost_change') in ('建仓','清仓'):
        return row['cost_change']
    if key=='cost_change' and row.get('cost_diff') is not None:
        delta=row['cost_diff']
        if not delta:
            return '不变'
        magnitude=delta.copy_abs()
        value=money_text(magnitude)
        if value=='0.00':
            value=money_text(magnitude,precision=4)
        value=exact_text(magnitude) if value=='0.0000' else value.rstrip('0').rstrip('.')
        return ('-' if delta<0 else '+')+value+'/股'
    value=row.get(key)
    return money_text(value) if key in MONEY_KEYS else '—' if value in ('',None) else exact_text(value)


class CenterModel(FinanceModel):
    preset='日常查看'

    def columnCount(self,parent=None):return len(COLUMNS)

    def column_title(self,index):
        key=KEYS[index]
        if key=='category':
            return '股票分类'
        if key=='cost_change' and self.preset=='成本分析':
            return '每股成本变化'
        return COLUMNS[index]

    def alignment(self,key):
        if self.preset=='日常查看' and key in DAILY_CENTER_KEYS:
            return Qt.AlignmentFlag.AlignCenter
        horizontal=Qt.AlignmentFlag.AlignRight if key in NUMERIC_KEYS else Qt.AlignmentFlag.AlignLeft
        return Qt.AlignmentFlag.AlignVCenter | horizontal

    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if orientation==Qt.Orientation.Horizontal:
            if role==Qt.ItemDataRole.DisplayRole:
                return self.column_title(section)
            if role==Qt.ItemDataRole.TextAlignmentRole:
                return int(self.alignment(KEYS[section]))
        elif role==Qt.ItemDataRole.DisplayRole:
            return str(section+1)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        row=self.rows[index.row()];key=KEYS[index.column()];value=row.get(key)
        if role==Qt.ItemDataRole.DisplayRole:
            return cell_text(row,key)
        if role==Qt.ItemDataRole.ToolTipRole:
            if key=='trade_date' and len(time_text(value))==10:
                return '账务日期；实际成交时刻请查看交易详情。'
            if key=='current_price':
                return quote_text(row)
            if key=='fees' and value is None:
                if row.get('fee_status')=='PENDING_SETTLEMENT':
                    return '手续费待券商次日结算，将在后续同步中自动补齐与分摊'
                return '手续费尚未核实'
            if key in ('post_cost','cost_change','post_quantity'):
                return row.get('cost_note')
            return cell_text(row,key)
        if role==Qt.ItemDataRole.FontRole and key in NUMERIC_KEYS:
            return numeric_font()
        if role==Qt.ItemDataRole.FontRole and key in ('symbol','trade_date'):
            font=QFont()
            font.setWeight(QFont.Weight.Bold if key=='symbol' else QFont.Weight.Medium)
            return font
        if role==Qt.ItemDataRole.ForegroundRole and key=='cost_change' and row.get('cost_diff'):
            theme=tokens(self.parent())
            return QColor(theme.profit if row['cost_diff']<0 else theme.loss)
        if role==Qt.ItemDataRole.TextAlignmentRole:
            return int(self.alignment(key))


class CenterTable(FinanceTable):
    def __init__(self):
        super().__init__()
        self.hovered_row=-1
        self.frozen.setMouseTracking(True)
        self.frozen.viewport().setMouseTracking(True)
        self.entered.connect(lambda index:self.set_hover(index.row()))
        self.frozen.entered.connect(lambda index:self.set_hover(index.row()))

    def set_hover(self,row):
        if row!=getattr(self,'hovered_row',-1):
            self.hovered_row=row
            self.viewport().update()
            self.frozen.viewport().update()

    def fit_columns(self):
        widths={'trade_date':156,'broker':82,'person_name':72,'symbol':64,'category':72,
                'side':76,'micro_tag':180,'current_price':72,'price':72,'quantity':76,
                'amount':98,'fees':68,'post_quantity':86,'post_cost':96,'post_net':106,
                'cost_change':108}
        micro_column=KEYS.index('micro_tag')
        if not self.isColumnHidden(micro_column):
            metrics=self.fontMetrics()
            longest=max((metrics.horizontalAdvance(row.get('micro_tag') or '') for row in self.finance_model.rows),default=0)
            widths['micro_tag']=max(widths['micro_tag'],min(280,longest+28))
        if self.finance_model.preset=='成本分析':
            widths['cost_change']=120
        visible=[(i,widths.get(key,90)) for i,key in enumerate(KEYS) if not self.isColumnHidden(i)]
        extra=max(0,self.viewport().width()-sum(width for _,width in visible))
        for position,(column,width) in enumerate(visible):
            self.setColumnWidth(column,width+extra//len(visible)+(position<extra%len(visible)))
        self._frozen_geometry()
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.model() is not None and self.model().columnCount()==len(KEYS):self.fit_columns()
    def viewportEvent(self,event):
        if event.type()==QEvent.Type.Leave:self.set_hover(-1)
        if event.type()==QEvent.Type.ToolTip:return QTableView.viewportEvent(self,event)
        return super().viewportEvent(event)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Leave and getattr(self,'frozen',None) and watched is self.frozen.viewport():self.set_hover(-1)
        if event.type()==QEvent.Type.ToolTip:return QTableView.eventFilter(self,watched,event)
        return super().eventFilter(watched,event)
    def copy_selection(self):
        indexes=self.selectionModel().selectedIndexes()
        rows=sorted({i.row() for i in indexes});cols=sorted({i.column() for i in indexes if not self.isColumnHidden(i)},key=self.horizontalHeader().visualIndex)
        QApplication.clipboard().setText("\n".join("\t".join(exact_text(self.finance_model.rows[r].get(KEYS[c])) for c in cols) for r in rows))

LABELS={'broker':'券商渠道','symbol':'Ticker','category':'股票分类','side':'交易方向','kind':'交易类型','search':'关键词 / 订单号 / 记录ID','source_kind':'数据来源','source_file':'来源文件','link_status':'交易关联状态','quantity_min':'成交数量 ≥','quantity_max':'成交数量 ≤','price_min':'成交价格 ≥','price_max':'成交价格 ≤','amount_min':'成交金额 ≥','amount_max':'成交金额 ≤','cost_min':'每股成本变化 ≥','cost_max':'每股成本变化 ≤'}


class TradeCenterPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.jobs={};self.path=None;self.data=None;self.applied={};self.page=1;self.size=50;self.sort='trade_time';self.direction='desc';self.selected=None
        self.setObjectName('tradeCenter');self.setStyleSheet(STYLE)
        self.body=QHBoxLayout(self)
        self.body.setContentsMargins(0,0,0,0)
        self.body.setSpacing(12)
        main=QWidget()
        self.body.addWidget(main,1)
        layout=QVBoxLayout(main)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(12)
        self.preset='日常查看'
        self.custom_columns={}
        stats=QHBoxLayout();stats.setSpacing(12);self.cards=[]
        for i,(title,icon,accent) in enumerate((('匹配交易总数','layers','#2563EB'),('买入记录','buy','#16A34A'),('卖出记录','sell','#DC2626'),('手续费合计','fees','#2563EB'),('待核查记录','review','#2563EB'))):
            card=StatCard(title,icon,accent,interactive=i in (1,2,4))
            card.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
            if card.isCheckable():
                card.clicked.connect(lambda _,index=i:self.quick(index))
            stats.addWidget(card,1)
            self.cards.append(card)
        form=QWidget();form.setObjectName('filterCard');form_layout=QVBoxLayout(form);form_layout.setContentsMargins(14,12,14,10);form_layout.setSpacing(8);self.fields={}
        def field(label,widget):
            box=QWidget();column=QVBoxLayout(box);column.setContentsMargins(0,0,0,0);column.setSpacing(5)
            name=QLabel(label);name.setObjectName('fieldLabel');column.addWidget(name);column.addWidget(widget);widget.setMinimumWidth(0);return box
        self.person=QComboBox();self.person.addItem('全部人员',None)
        for key in ('broker','symbol','category','side','kind','search'):
            widget=QLineEdit() if key in ('symbol','search') else QComboBox()
            if isinstance(widget,QComboBox):widget.addItem('全部','')
            else:widget.returnPressed.connect(self.query)
            self.fields[key]=widget
        self.fields['symbol'].setPlaceholderText('搜索 Ticker')
        self.fields['search'].setPlaceholderText('搜索订单号、交易 ID、来源')
        self.local_search=self.fields['search']
        self.start=QDateEdit(QDate(2025,1,1));self.end=QDateEdit(QDate.currentDate())
        for widget in (self.start,self.end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat('yyyy-MM-dd')
            widget.setFixedWidth(136)
        dates=QWidget()
        dates.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Preferred)
        date_layout=QHBoxLayout(dates)
        date_layout.setContentsMargins(0,0,0,0)
        date_layout.setSpacing(6)
        date_layout.addWidget(self.start)
        date_layout.addWidget(QLabel('至'))
        date_layout.addWidget(self.end)
        top=QHBoxLayout()
        top.setSpacing(12)
        for label,widget,stretch in (('归属人员',self.person,2),('券商渠道',self.fields['broker'],1),('Ticker',self.fields['symbol'],1),('日期范围 · 账务日期',dates,0),('关键词',self.fields['search'],3)):
            top.addWidget(field(label,widget),stretch)
        form_layout.addLayout(top)

        secondary=QHBoxLayout()
        secondary.setSpacing(10)
        self.chips=QWidget()
        self.chip_layout=FlowLayout(self.chips)
        secondary.addWidget(self.chips,1,Qt.AlignmentFlag.AlignVCenter)
        for title,slot,name in (('查询',self.query,'primary'),('重置',self.reset,''),('高级筛选 ▾',self.toggle_advanced,'textButton')):
            button=QPushButton(title)
            button.setObjectName(name)
            button.clicked.connect(slot)
            secondary.addWidget(button,0,Qt.AlignmentFlag.AlignVCenter)
        self.advanced_button=button
        form_layout.addLayout(secondary)
        layout.addLayout(stats);layout.addWidget(form)
        self.advanced=QWidget()
        advanced=QGridLayout(self.advanced)
        advanced.setContentsMargins(0,4,0,4)
        advanced.setSpacing(10)
        keys=('side','category','kind','quantity_min','quantity_max','price_min','price_max',
              'amount_min','amount_max','cost_min','cost_max','source_kind','source_file','link_status')
        for index,key in enumerate(keys):
            if key not in self.fields:
                widget=QComboBox() if key in ('source_kind','link_status') else QLineEdit()
                if isinstance(widget,QComboBox):
                    widget.addItem('全部','')
                else:
                    widget.returnPressed.connect(self.query)
                self.fields[key]=widget
            advanced.addWidget(field(LABELS[key],self.fields[key]),index//4,index%4)
        for index,key in enumerate(('review_only','affects_only')):
            widget=QCheckBox('仅待核查' if index==0 else '仅影响持仓')
            self.fields[key]=widget
            advanced.addWidget(widget,3,index+2)
        self.advanced.hide()
        form_layout.addWidget(self.advanced)
        self.status=QLabel('等待连接数据库');self.status.setWordWrap(True);layout.addWidget(self.status)
        table_surface=QFrame();table_surface.setObjectName('surface');table_layout=QVBoxLayout(table_surface);table_layout.setContentsMargins(0,0,0,0);table_layout.setSpacing(0);layout.addWidget(table_surface,1)
        table_head=QHBoxLayout()
        table_head.setContentsMargins(12,10,12,10)
        heading=QLabel('交易记录')
        heading.setObjectName('sectionTitle')
        table_head.addWidget(heading)
        self.table_count=QLabel()
        table_head.addWidget(self.table_count)
        table_head.addStretch()
        segments=QWidget()
        segments.setObjectName('tradeSegments')
        segment_layout=QHBoxLayout(segments)
        segment_layout.setContentsMargins(2,2,2,2)
        segment_layout.setSpacing(2)
        self.presets=QButtonGroup(self)
        for name in PRESETS:
            button=QPushButton(name)
            button.setObjectName('tradeSegment')
            button.setCheckable(True)
            button.setChecked(name==self.preset)
            button.clicked.connect(lambda _,name=name:self.set_preset(name))
            self.presets.addButton(button)
            segment_layout.addWidget(button)
        table_head.addWidget(segments)
        table_head.addStretch()
        self.columns=QPushButton('显示列')
        self.columns.clicked.connect(self.column_menu)
        table_head.addWidget(self.columns)
        table_layout.addLayout(table_head)

        self.table=CenterTable()
        self.table.finance_model=CenterModel(self.table)
        self.table.setModel(self.table.finance_model)
        for view in (self.table,self.table.frozen):
            view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            view.verticalHeader().setDefaultSectionSize(40)
            view.horizontalHeader().sectionClicked.connect(self.sort_column)
            view.clicked.connect(lambda index:self.open_row(index.row(),index.column()))
        self.table.activated.connect(lambda index:self.open_row(index.row(),index.column()))
        delegate=TradeCellDelegate(self.table)
        self.table.setItemDelegate(delegate)
        self.table.frozen.setItemDelegate(delegate)
        table_layout.addWidget(self.table,1)
        self.apply_columns()

        self.drawer=QWidget(self)
        self.drawer.setObjectName('tradeDrawer')
        self.drawer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        drawer=QVBoxLayout(self.drawer)
        drawer.setContentsMargins(12,10,12,10)
        head=QHBoxLayout()
        heading=QLabel('交易详情')
        heading.setObjectName('sectionTitle')
        head.addWidget(heading,1)
        close=QPushButton('×')
        close.setAccessibleName('关闭详情')
        close.clicked.connect(self.close_drawer)
        head.addWidget(close)
        drawer.addLayout(head)
        identity=QHBoxLayout()
        self.detail_title=QLabel()
        self.detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_title.setObjectName('tradeTicker')
        self.detail_side=QLabel()
        self.detail_side.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_side.setWordWrap(True)
        self.detail_cost=QLabel()
        self.detail_cost.setObjectName('costBadge')
        identity.addWidget(self.detail_title)
        identity.addWidget(self.detail_side)
        identity.addStretch()
        identity.addWidget(self.detail_cost)
        drawer.addLayout(identity)
        self.detail_meta=QLabel()
        self.detail_meta.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_meta.setObjectName('detailKey')
        self.detail_meta.setWordWrap(True)
        drawer.addWidget(self.detail_meta)

        self.tabs=QTabWidget()
        self.texts={}
        for label in ('交易概览','成本变化','来源追溯'):
            panel=DetailPanel()
            self.tabs.addTab(panel,label)
            self.texts[label]=panel
        related_panel=QWidget()
        related_layout=QVBoxLayout(related_panel)
        related_layout.setContentsMargins(2,10,2,0)
        self.related_status=QLabel()
        self.related_status.setTextFormat(Qt.TextFormat.PlainText)
        self.related_status.setWordWrap(True)
        related_layout.addWidget(self.related_status)
        self.related=QListWidget()
        self.related.setObjectName('relatedList')
        self.related.setSpacing(4)
        self.related.setWordWrap(True)
        self.related.itemClicked.connect(self.open_related)
        self.related.itemActivated.connect(self.open_related)
        related_layout.addWidget(self.related)
        self.tabs.addTab(related_panel,'关联记录')
        drawer.addWidget(self.tabs,1)
        nav=QHBoxLayout()
        self.prev=QPushButton('上一笔')
        self.next=QPushButton('下一笔')
        self.nav_label=QLabel()
        self.prev.clicked.connect(lambda:self.open_detail(self.navigation.get('previous_id')))
        self.next.clicked.connect(lambda:self.open_detail(self.navigation.get('next_id')))
        nav.addWidget(self.prev)
        nav.addWidget(self.nav_label,1,Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.next)
        drawer.addLayout(nav)
        self.navigation={}
        self.drawer.hide()
        escape=QShortcut(QKeySequence(Qt.Key.Key_Escape),self.drawer)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.close_drawer)
        footer=QHBoxLayout();self.count=QLabel();footer.addWidget(self.count,1);self.per_page=QComboBox()
        for n in (25,50,100,200,500,1000):self.per_page.addItem(f'每页 {n} 条',n)
        self.per_page.setCurrentIndex(1);self.per_page.currentIndexChanged.connect(self.change_size);footer.addWidget(self.per_page)
        self.previous_page=QPushButton('上一页');self.next_page=QPushButton('下一页');self.previous_page.clicked.connect(lambda:self.turn(-1));self.next_page.clicked.connect(lambda:self.turn(1));footer.addWidget(self.previous_page);footer.addWidget(self.next_page);footer.setContentsMargins(14,10,14,10);table_layout.addLayout(footer)
        self.note=QLabel();self.note.setWordWrap(True);layout.addWidget(self.note)

    def submit(self,name,task,ready):
        previous=self.jobs.get(name)
        if previous:previous.cancel.set()
        w=Worker(task);self.jobs[name]=w
        w.signals.done.connect(lambda result:ready(result) if self.jobs.get(name) is w and not w.cancel.is_set() else None,Qt.ConnectionType.QueuedConnection)
        w.signals.error.connect(lambda message:self.failed(name,message) if self.jobs.get(name) is w and not w.cancel.is_set() else None,Qt.ConnectionType.QueuedConnection)
        self.host.pool.start(w)

    def set_status(self,text):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def failed(self,name,message):
        if name=='detail':
            for panel in self.texts.values():
                panel.setPlainText('详情读取失败 · '+message)
            self.detail_meta.setText('详情读取失败')
            self.related_status.setText('详情读取失败 · '+message)
            return
        self.set_status('查询/导出失败：'+message)
        self.previous_page.setEnabled(True);self.next_page.setEnabled(True)

    def cancel_queries(self):
        for worker in self.jobs.values():worker.cancel.set()
        self.set_status('查询已取消。可调整条件后重新查询。')
        self.previous_page.setEnabled(True);self.next_page.setEnabled(True)

    def invalidate(self):
        self.cancel_queries();self.path=None;self.data=None;self.clear_detail();self.table.finance_model.replace(())
        for card,title in zip(self.cards,('匹配交易总数','买入记录','卖出记录','手续费合计','待核查记录')):card.setText(title+'\n—');card.setChecked(False)
        self.table_count.clear()
        self.count.clear();self.note.clear();self.set_status('正在连接新数据源；旧结果已清除。')

    def activate(self,force=False):
        if self.host.info is None or self.host.service is None:return
        path=self.host.service.path
        if self.path!=path:
            for w in self.jobs.values():w.cancel.set()
            self.data=None;self.path=path;self.person.blockSignals(True);self.person.clear();self.person.addItem('全部人员（含停用历史人员）',None)
            for p in self.host.info.persons:self.person.addItem(p['name']+('（停用）' if not p['enabled'] else ''),p['id'])
            self.person.blockSignals(False);self.reset();return
        if self.data is None or force:self.fetch()

    def values(self):
        result={'start_date':self.start.date().toString('yyyy-MM-dd'),'end_date':self.end.date().toString('yyyy-MM-dd')}
        if self.person.currentData() is not None:result['person_id']=self.person.currentData()
        for key,w in self.fields.items():
            v=w.isChecked() if isinstance(w,QCheckBox) else w.currentData() if isinstance(w,QComboBox) else w.text().strip()
            if v not in ('',None,False):result[key]=v
        return result

    def scope(self):
        end=date.fromisoformat(self.applied.get('end_date') or date.today().isoformat());start=date.fromisoformat(self.applied.get('start_date') or '2025-01-01')
        if start>end:raise ValueError('开始日期不可晚于结束日期。')
        p=self.applied.get('person_id')
        return QueryScope(as_of=end,ledger=Ledger.PERSON,person_ids=(p,) if p is not None else None,start_date=start,include_disabled=True,category=self.applied.get('category') or None,search=self.applied.get('search',''))

    def params(self):return {**self.applied,'page':self.page,'size':self.size,'sort_col':self.sort,'sort_dir':self.direction}
    def query(self):self.applied=self.values();self.page=1;self.clear_detail();self.render_chips();self.fetch()
    def fetch(self):
        if self.path is None:return
        try:scope=self.scope()
        except ValueError as exc:self.set_status(str(exc));return
        service=TradeCenterService(self.path);params=self.params();self.set_status('正在读取全部匹配统计与本页历史成本…');self.previous_page.setEnabled(False);self.next_page.setEnabled(False)
        self.table.setEnabled(False)
        self.submit('list',lambda cancel:service.page(scope,params,cancel=cancel),self.show_page)

    def show_page(self,data):
        self.data=data;self.page=data['page'];items=data['items'];self.table.finance_model.replace(tuple(items))
        self.table.setEnabled(True)
        self.table.fit_columns()
        self.mark_selected()
        titles=('匹配交易总数','买入记录','卖出记录','手续费合计','待核查记录');s=data['stats']
        amounts=lambda key:' / '.join((c['currency']+'：' if len(s['currencies'])>1 else '')+(money_text(c[key]) if c[key] is not None else money_text(c['known_fees']) if key=='fees' and c.get('known_fees') is not None else '—（未完整核实）') for c in s['currencies']) or '—'
        pending_fee_count=sum((c.get('unknown_fees') or 0) for c in s['currencies'])
        fee_hint=f'已核实汇总 · {pending_fee_count} 笔待核实/结算' if pending_fee_count else '按币种核实汇总'
        values=(format(s['matched'],',')+f"\n买入 {s['buys']:,} · 卖出 {s['sells']:,} · 其他 —",format(s['buys'],',')+'\n买入总额 '+amounts('buy_amount'),format(s['sells'],',')+'\n卖出总额 '+amounts('sell_amount'),amounts('fees')+'\n'+fee_hint,format(s['review'],',')+'\n关联、解析或导入异常')
        for card,title,value in zip(self.cards,titles,values):card.setText(title+'\n'+value)
        self.cards[0].setToolTip(values[0]+'\n汇总接口未提供其他交易类型笔数。')
        self.cards[1].setChecked(self.applied.get('side')=='BUY' and self.applied.get('kind')=='MARKET_TRADE');self.cards[2].setChecked(self.applied.get('side')=='SELL' and self.applied.get('kind')=='MARKET_TRADE');self.cards[4].setChecked(bool(self.applied.get('review_only')))
        self.table_count.setText(format(data['total'],',')+' 条匹配记录')
        for key,column in (('broker','broker_code'),('category','category'),('kind','kind'),('side','side'),('source_kind','source_kind')):
            w=self.fields[key];saved=w.currentData();w.blockSignals(True);w.clear();w.addItem('全部','')
            options={r[column] for r in data['options'] if r[column]}
            if saved:options.add(saved)
            for v in sorted(options):w.addItem(OPTION_LABELS.get(v,v),v)
            w.setCurrentIndex(max(0,w.findData(saved)));w.blockSignals(False)
        w=self.fields['link_status']
        if w.count()==1:
            for value in ('LINKED','LEGACY_UNLINKED','REVIEW_REQUIRED','NOT_APPLICABLE'):w.addItem(OPTION_LABELS[value],value)
        self.set_status('' if items else '没有符合查询条件的交易记录，请调整筛选条件。');self.note.setText(data['note']);self.count.setText(f"共 {data['total']:,} 条 · 第 {self.page} / {data['total_pages']} 页");self.previous_page.setEnabled(self.page>1);self.next_page.setEnabled(self.page<data['total_pages'])
        self.update_sort_indicator()

    def update_sort_indicator(self):
        mapped=['trade_time' if key=='trade_date' else 'cost_diff' if key=='cost_change' else key for key in KEYS]
        if self.sort in mapped:
            for header in (self.table.horizontalHeader(),self.table.frozen.horizontalHeader()):
                header.setSortIndicator(mapped.index(self.sort),Qt.SortOrder.DescendingOrder if self.direction=='desc' else Qt.SortOrder.AscendingOrder)
                header.setSortIndicatorShown(True)

    def render_chips(self):
        while self.chip_layout.count():
            item=self.chip_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        start=self.applied.get('start_date','不限');end=self.applied.get('end_date','不限')
        caption=QLabel('已选条件');caption.setObjectName('fieldLabel');self.chip_layout.addWidget(caption)
        dates=QPushButton('日期 '+start+' 至 '+end+' ×');dates.setToolTip('恢复默认日期范围');dates.setObjectName('filterChip');dates.clicked.connect(self.reset_dates);self.chip_layout.addWidget(dates)
        for key,value in self.applied.items():
            if key in ('start_date','end_date'):continue
            label='归属人员' if key=='person_id' else {'review_only':'待核查','affects_only':'影响持仓'}.get(key,LABELS.get(key,key))
            text=self.person.currentText() if key=='person_id' else self.fields[key].currentText() if isinstance(self.fields.get(key),QComboBox) else '是' if value is True else str(value)
            b=QPushButton(label+' '+text+' ×');b.setObjectName('filterChip');b.clicked.connect(lambda _,k=key:self.remove_filter(k));self.chip_layout.addWidget(b)

    def apply_filters(self,filters):
        self.person.setCurrentIndex(max(0,self.person.findData(filters.get('person_id'))))
        self.start.setDate(QDate.fromString(filters['start_date'],'yyyy-MM-dd'))
        self.end.setDate(QDate.fromString(filters['end_date'],'yyyy-MM-dd'))
        for key,widget in self.fields.items():
            value=filters.get(key)
            if isinstance(widget,QComboBox):
                if value and widget.findData(value)<0:
                    widget.addItem(OPTION_LABELS.get(value,value),value)
                widget.setCurrentIndex(max(0,widget.findData(value)))
            elif isinstance(widget,QCheckBox):
                widget.setChecked(bool(value))
            else:
                widget.setText(value or '')
        self.query()

    def default_dates(self):
        return {'start_date':'2025-01-01','end_date':QDate.currentDate().toString('yyyy-MM-dd')}

    def reset_dates(self):
        self.apply_filters({**self.applied,**self.default_dates()})

    def remove_filter(self,key):
        filters=self.applied.copy()
        filters.pop(key,None)
        self.apply_filters(filters)

    def reset(self):
        self.apply_filters(self.default_dates())

    def quick(self,index):
        if not self.data:
            return
        filters=self.applied.copy()
        if index in (1,2):
            side='BUY' if index==1 else 'SELL'
            active=filters.get('side')==side and filters.get('kind')=='MARKET_TRADE'
            filters.update(side='' if active else side,kind='MARKET_TRADE')
        elif index==4:
            filters['review_only']=not filters.get('review_only')
        self.apply_filters(filters)

    def toggle_advanced(self):
        expanded=self.advanced.isHidden()
        self.advanced.setVisible(expanded)
        self.advanced_button.setText('高级筛选 ▴' if expanded else '高级筛选 ▾')

    def sort_column(self,col):
        key=KEYS[col]
        if key=='micro_tag':
            return
        key='trade_time' if key=='trade_date' else 'cost_diff' if key=='cost_change' else key
        self.direction='asc' if self.sort==key and self.direction=='desc' else 'desc'
        self.sort=key
        self.page=1
        self.fetch()
        if self.drawer.isVisible():
            self.open_detail(self.selected)

    def turn(self,delta):self.page=max(1,self.page+delta);self.fetch()
    def change_size(self):self.size=self.per_page.currentData();self.page=1;self.fetch()

    def set_preset(self,preset):
        self.preset=preset
        self.apply_columns()

    def column_order(self):
        base=PRESETS[self.preset]
        return (*base,*(key for key in KEYS if key not in base))

    def column_menu(self):
        menu=QMenu(self)
        shown=self.custom_columns.get(self.preset,PRESETS[self.preset])
        for key in self.column_order():
            action=menu.addAction(self.table.finance_model.column_title(KEYS.index(key)))
            action.setCheckable(True)
            action.setChecked(key in shown)
            action.setEnabled(len(shown)>1 or key not in shown)
            action.toggled.connect(lambda checked,key=key:self.set_column(key,checked))
        if self.preset in self.custom_columns:
            menu.addSeparator()
            restore=menu.addAction('恢复方案列')
            restore.triggered.connect(self.restore_columns)
        menu.exec(self.columns.mapToGlobal(self.columns.rect().bottomLeft()))

    def restore_columns(self):
        self.custom_columns.pop(self.preset,None)
        self.apply_columns()

    def set_column(self,key,checked):
        shown=set(self.custom_columns.get(self.preset,PRESETS[self.preset]))
        if checked:
            shown.add(key)
        else:
            shown.discard(key)
        self.custom_columns[self.preset]=shown
        self.apply_columns()

    def apply_columns(self):
        shown=self.custom_columns.get(self.preset,PRESETS[self.preset])
        table=self.table
        table.finance_model.preset=self.preset
        header=table.horizontalHeader()
        for position,key in enumerate(self.column_order()):
            column=KEYS.index(key)
            header.moveSection(header.visualIndex(column),position)
            table.setColumnHidden(column,key not in shown)
        table.frozen.setVisible('trade_date' in shown)
        model=table.finance_model
        model.headerDataChanged.emit(Qt.Orientation.Horizontal,0,len(KEYS)-1)
        if model.rowCount():
            model.dataChanged.emit(model.index(0,0),model.index(model.rowCount()-1,len(KEYS)-1),[Qt.ItemDataRole.TextAlignmentRole])
        table.fit_columns()
        self.update_sort_indicator()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'drawer') and not self.drawer.isHidden():
            self.position_drawer()

    def position_drawer(self):
        # Keep a single drawer and table instance so resizing preserves both states.
        wide=self.width()>=1450
        docked=self.body.indexOf(self.drawer)>=0
        if wide:
            self.drawer.setFixedWidth(440)
            if not docked:
                self.body.addWidget(self.drawer)
        else:
            if docked:
                self.body.removeWidget(self.drawer)
            self.drawer.setFixedWidth(min(440,self.width()))
            self.drawer.setGeometry(self.width()-self.drawer.width(),0,self.drawer.width(),self.height())
            self.drawer.raise_()

    def mark_selected(self):
        for index,row in enumerate(self.table.finance_model.rows):
            if row['record_id']==self.selected:
                self.table.selectRow(index)
                return
        self.table.clearSelection()

    def open_row(self,row,_):
        if self.table.isEnabled() and self.data and row<len(self.data['items']):
            record_id=self.data['items'][row]['record_id']
            if self.drawer.isHidden() or record_id!=self.selected:
                self.open_detail(record_id)

    def open_related(self,item):
        record_id=item.data(Qt.ItemDataRole.UserRole)
        if record_id!=self.selected:
            self.open_detail(record_id)

    def open_detail(self,record_id):
        if record_id is None:return
        self.selected=record_id
        self.mark_selected()
        if self.drawer.isHidden():
            self.tabs.setCurrentIndex(0)
        self.position_drawer()
        was_hidden=self.drawer.isHidden()
        self.drawer.show()
        if was_hidden:
            self.tabs.tabBar().setFocus()
        self.detail_title.clear()
        self.detail_side.clear()
        self.detail_cost.clear()
        self.detail_cost.hide()
        self.detail_meta.setText('正在读取交易详情…')
        self.prev.setEnabled(False)
        self.next.setEnabled(False)
        self.nav_label.clear()
        self.navigation={}
        for panel in self.texts.values():
            panel.setPlainText('正在读取该笔记录…')
        self.related.clear()
        self.related.hide()
        self.related_status.setText('正在读取关联记录…')
        service=TradeCenterService(self.path)
        scope=self.scope()
        params=self.params()
        self.submit('detail',lambda cancel:service.detail(scope,params,record_id,cancel=cancel),self.show_detail)

    def show_detail(self,result):
        item=result['item']
        sections=result['sections']
        overview=sections['交易概览']
        time=time_text(item['trade_date'],precise=bool(overview.get('真实成交UTC')))
        change=cell_text(item,'cost_change')
        if change=='—':
            change='暂无可信计算结果'
        self.detail_title.setText(item['symbol'])
        self.detail_side.setText(item['side'])
        fill,color=side_tone(item)
        self.detail_side.setStyleSheet(f'background:{fill};color:{color};border-radius:7px;padding:4px 7px;')
        theme=tokens(self)
        delta=item.get('cost_diff')
        color=theme.profit if delta is not None and delta<0 else theme.loss if delta else theme.text
        self.detail_cost.setStyleSheet(f'color:{color};')
        self.detail_cost.setText(change)
        self.detail_cost.show()
        self.detail_meta.setText(' · '.join((time,item['person_name'],item['broker'])))

        def format_value(name,value):
            if name=='交易时间':
                return time
            if name=='每股成本变化':
                return change
            if name in MONEY_LABELS:
                return money_text(value)
            if name in ('数据来源','归属关联状态'):
                return OPTION_LABELS.get(value,exact_text(value))
            return '—' if value in (None,'') else exact_text(value)

        for key,panel in self.texts.items():
            fields=sections[key]
            if key=='来源追溯':
                fields={**fields,'交易记录ID':item['record_id']}
            panel.show_fields(key,fields,format_value,item,quote_text(item))
        self.navigation=result['navigation']
        self.prev.setEnabled(bool(self.navigation.get('previous_id')))
        self.next.setEnabled(bool(self.navigation.get('next_id')))
        position=self.navigation.get('position')
        self.nav_label.setText(f"{position} / {self.navigation['matched']}" if position else '关联记录')
        related=result['related']
        self.related.setVisible(bool(related))
        message='依据原始成交、同账户证券订单或人员划转来源关联。' if related else '没有可靠关联记录。'
        if result['related_truncated']:
            message+=' 仅列出前 200 条关联记录。'
        self.related_status.setText(message)
        for row in related:
            heading=f"{row['symbol']} · {row['side']}"
            meta=f"{time_text(row['trade_date'])} · {row['person_name'] or '—'} · #{row['record_id']}"
            entry=QListWidgetItem(heading+'\n'+meta)
            entry.setData(Qt.ItemDataRole.UserRole,row['record_id'])
            entry.setSizeHint(QSize(0,54))
            entry.setToolTip(heading+'\n'+meta)
            self.related.addItem(entry)

    def close_drawer(self):
        if self.drawer.isVisible():
            self.table.setFocus()
        self.drawer.hide()
        if self.jobs.get('detail'):
            self.jobs['detail'].cancel.set()

    def clear_detail(self):
        self.close_drawer()
        self.selected=None
        self.table.clearSelection()

    def export(self):
        if self.path is None:return
        path,_=QFileDialog.getSaveFileName(self,'导出全部匹配交易（不覆盖已有文件）','交易明细.csv','CSV (*.csv)')
        if not path:return
        service=TradeCenterService(self.path);scope=self.scope();params=self.params();self.set_status('正在分批导出全部匹配记录…')
        def task(cancel):
            created=False
            try:
                with Path(path).open('x',encoding='utf-8',newline='') as stream:
                    created=True
                    for chunk in service.export(scope,params,cancel=cancel):
                        if cancel.is_set():raise ValueError('导出已取消。')
                        stream.write(chunk)
            except FileExistsError as exc:
                raise ValueError('目标文件已存在，请选择新文件名；没有覆盖原文件。') from exc
            except Exception:
                if created:Path(path).unlink(missing_ok=True)
                raise
            return path
        self.submit('export',task,lambda p:self.set_status('已导出全部匹配记录：'+p))
