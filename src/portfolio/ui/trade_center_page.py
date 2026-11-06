"""Local trade-center controls; all query and financial work runs in workers."""
from datetime import date
from decimal import Decimal,localcontext,ROUND_HALF_UP
from pathlib import Path
import json
from PySide6.QtCore import QDate,Qt,QEvent
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,QComboBox,QLineEdit,QDateEdit,QCheckBox,QSplitter,QTableWidget,QTableWidgetItem,QTabWidget,QPlainTextEdit,QFileDialog,QMenu,QAbstractItemView,QTableView,QApplication)
from portfolio.domain.models import QueryScope,Ledger,exact_text
from portfolio.services.trade_center import TradeCenterService,COLUMNS,KEYS,SIDES,KINDS
from portfolio.ui.workers import Worker
from portfolio.ui.finance_table import FinanceModel,FinanceTable


MONEY_KEYS={'current_price','price','amount','fees','post_cost','pre_cost','post_net','realized_pnl'}
MONEY_LABELS={'当前价','成交价','成交金额','手续费','成交前成本价','成交后成本价','本笔已实现盈亏'}


def money_text(value):
    if value is None:return '—'
    with localcontext() as context:
        context.prec=100
        text=format(Decimal(str(value)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'.2f')
    return '0.00' if text=='-0.00' else text


def cell_text(row,key):
    if key=='cost_change' and row.get('cost_diff') is not None and row['cost_diff']!=0:
        delta=row['cost_diff']
        return ('降低' if delta<0 else '提高')+' '+money_text(delta.copy_abs())+'/股'
    return money_text(row.get(key)) if key in MONEY_KEYS else exact_text(row.get(key))


class CenterModel(FinanceModel):
    def columnCount(self,parent=None):return len(COLUMNS)
    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.DisplayRole:return COLUMNS[section] if orientation==Qt.Orientation.Horizontal else str(section+1)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        row=self.rows[index.row()];key=KEYS[index.column()];value=row.get(key)
        if role==Qt.ItemDataRole.DisplayRole:
            text=cell_text(row,key)
            return text
        if role==Qt.ItemDataRole.ToolTipRole:
            return (row.get("quote_status","")+" · "+(row.get("quote_time") or "无报价时间")) if key=="current_price" else row.get("cost_note") if key in ("post_cost","cost_change","post_quantity") else exact_text(value)
        if role==Qt.ItemDataRole.TextAlignmentRole:return int(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if isinstance(value,Decimal) else Qt.AlignmentFlag.AlignLeft))


class CenterTable(FinanceTable):
    def viewportEvent(self,event):
        if event.type()==QEvent.Type.ToolTip:return QTableView.viewportEvent(self,event)
        return super().viewportEvent(event)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.ToolTip:return QTableView.eventFilter(self,watched,event)
        return super().eventFilter(watched,event)
    def copy_selection(self):
        indexes=self.selectionModel().selectedIndexes()
        rows=sorted({i.row() for i in indexes});cols=sorted({i.column() for i in indexes})
        QApplication.clipboard().setText("\n".join("\t".join(exact_text(self.finance_model.rows[r].get(KEYS[c])) for c in cols) for r in rows))

LABELS={'broker':'券商渠道','symbol':'Ticker','category':'股票分类','side':'交易方向','kind':'交易类型','search':'关键词 / 订单号 / 记录ID','source_kind':'数据来源','source_file':'来源文件','link_status':'交易关联状态','quantity_min':'成交数量 ≥','quantity_max':'成交数量 ≤','price_min':'成交价格 ≥','price_max':'成交价格 ≤','amount_min':'成交金额 ≥','amount_max':'成交金额 ≤','cost_min':'每股成本变化 ≥','cost_max':'每股成本变化 ≤'}


class TradeCenterPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.jobs={};self.path=None;self.data=None;self.applied={};self.page=1;self.size=200;self.sort='trade_time';self.direction='desc';self.selected=None
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(10)
        stats=QHBoxLayout();self.cards=[]
        for i,title in enumerate(('匹配交易总数','买入记录及金额','卖出记录及金额','手续费合计','待核查记录')):
            card=QPushButton(title+'\n—');card.setMinimumHeight(78);card.clicked.connect(lambda _,index=i:self.quick(index));stats.addWidget(card,1);self.cards.append(card)
        layout.addLayout(stats)
        form=QWidget();grid=QGridLayout(form);grid.setContentsMargins(8,4,8,4);self.fields={}
        self.person=QComboBox();self.person.addItem('全部人员',None);grid.addWidget(QLabel('归属人员'),0,0);grid.addWidget(self.person,1,0)
        for n,key in enumerate(('broker','symbol','category','side','kind','search'),1):
            widget=QLineEdit() if key in ('symbol','search') else QComboBox()
            if isinstance(widget,QComboBox):widget.addItem('全部','')
            else:widget.returnPressed.connect(self.query)
            self.fields[key]=widget;grid.addWidget(QLabel(LABELS[key]),(n//4)*2,n%4);grid.addWidget(widget,(n//4)*2+1,n%4)
        self.local_search=self.fields['search']
        self.start=QDateEdit(QDate(2025,1,1));self.end=QDateEdit(QDate.currentDate())
        for w in (self.start,self.end):w.setCalendarPopup(True);w.setDisplayFormat('yyyy-MM-dd')
        dates=QHBoxLayout();dates.addWidget(QLabel('交易日期（账务日期；表内时间为北京时间）'));dates.addWidget(self.start);dates.addWidget(self.end)
        grid.addLayout(dates,4,0,1,4)
        actions=QHBoxLayout()
        for text,slot in (('查询',self.query),('重置筛选',self.reset),('高级筛选',self.toggle_advanced),('刷新',lambda:self.activate(True)),('导出全部匹配',self.export),('显示列',self.column_menu)):
            button=QPushButton(text);button.clicked.connect(slot);actions.addWidget(button)
        grid.addLayout(actions,5,0,1,4);layout.addWidget(form)
        self.advanced=QWidget();advanced=QGridLayout(self.advanced)
        for i,key in enumerate(('quantity_min','quantity_max','price_min','price_max','amount_min','amount_max','cost_min','cost_max','source_kind','source_file','link_status')):
            w=QComboBox() if key in ('source_kind','link_status') else QLineEdit()
            if isinstance(w,QComboBox):w.addItem('全部','')
            else:w.returnPressed.connect(self.query)
            self.fields[key]=w;advanced.addWidget(QLabel(LABELS[key]),(i//4)*2,i%4);advanced.addWidget(w,(i//4)*2+1,i%4)
        for i,key in enumerate(('review_only','affects_only')):
            w=QCheckBox('仅异常/待核查' if i==0 else '仅影响持仓数量');self.fields[key]=w;advanced.addWidget(w,6,i)
        advanced.addWidget(QLabel('微操逐笔标记、本笔已实现盈亏：缺少可靠逐笔证据，不推算。'),7,0,1,4);self.advanced.hide();layout.addWidget(self.advanced)
        self.chips=QWidget();self.chip_layout=QHBoxLayout(self.chips);self.chip_layout.setContentsMargins(0,0,0,0);layout.addWidget(self.chips)
        self.status=QLabel('等待连接数据库');self.status.setWordWrap(True);layout.addWidget(self.status)
        split=QSplitter(Qt.Orientation.Horizontal);layout.addWidget(split,1)
        self.table=CenterTable();self.table.finance_model=CenterModel(self.table);self.table.setModel(self.table.finance_model);self.table.frozen.setModel(self.table.finance_model);self.table.frozen.setSelectionModel(self.table.selectionModel());self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.table.frozen.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.table.horizontalHeader().sectionClicked.connect(self.sort_column);self.table.frozen.horizontalHeader().sectionClicked.connect(self.sort_column);self.table.clicked.connect(lambda i:self.open_row(i.row(),i.column()));self.table.frozen.clicked.connect(lambda i:self.open_row(i.row(),i.column()));split.addWidget(self.table)
        self.drawer=QWidget();drawer=QVBoxLayout(self.drawer);head=QHBoxLayout();self.detail_title=QLabel('交易详情');self.detail_title.setWordWrap(True);head.addWidget(self.detail_title,1);close=QPushButton('关闭');close.clicked.connect(self.close_drawer);head.addWidget(close);drawer.addLayout(head)
        self.tabs=QTabWidget();self.texts={}
        for label in ('交易概览','成本变化','来源追溯'):
            w=QPlainTextEdit();w.setReadOnly(True);self.tabs.addTab(w,label);self.texts[label]=w
        self.related=QTableWidget(0,3);self.related.setHorizontalHeaderLabels(('交易时间','Ticker / 人员 / 方向','记录ID'));self.related.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.related.cellClicked.connect(self.open_related);self.tabs.addTab(self.related,'关联记录');drawer.addWidget(self.tabs)
        nav=QHBoxLayout();self.prev=QPushButton('上一笔');self.next=QPushButton('下一笔');self.nav_label=QLabel();self.prev.clicked.connect(lambda:self.open_detail(self.navigation.get('previous_id')));self.next.clicked.connect(lambda:self.open_detail(self.navigation.get('next_id')));nav.addWidget(self.prev);nav.addWidget(self.nav_label,1);nav.addWidget(self.next);drawer.addLayout(nav);self.navigation={};split.addWidget(self.drawer);split.setStretchFactor(0,2);split.setStretchFactor(1,1);split.setSizes([1000,480]);self.drawer.hide()
        footer=QHBoxLayout();self.count=QLabel();footer.addWidget(self.count,1);self.per_page=QComboBox()
        for n in (25,50,100,200,500,1000):self.per_page.addItem(f'每页 {n} 条',n)
        self.per_page.setCurrentIndex(3);self.per_page.currentIndexChanged.connect(self.change_size);footer.addWidget(self.per_page)
        self.previous_page=QPushButton('上一页');self.next_page=QPushButton('下一页');self.previous_page.clicked.connect(lambda:self.turn(-1));self.next_page.clicked.connect(lambda:self.turn(1));footer.addWidget(self.previous_page);footer.addWidget(self.next_page);layout.addLayout(footer)
        self.note=QLabel();self.note.setWordWrap(True);layout.addWidget(self.note)

    def submit(self,name,task,ready):
        previous=self.jobs.get(name)
        if previous:previous.cancel.set()
        w=Worker(task);self.jobs[name]=w
        w.signals.done.connect(lambda result:ready(result) if self.jobs.get(name) is w and not w.cancel.is_set() else None,Qt.ConnectionType.QueuedConnection)
        w.signals.error.connect(lambda message:self.failed(name,message) if self.jobs.get(name) is w and not w.cancel.is_set() else None,Qt.ConnectionType.QueuedConnection)
        self.host.pool.start(w)

    def failed(self,name,message):
        if name=='detail':self.detail_title.setText('详情读取失败：'+message)
        else:self.status.setText('查询/导出失败：'+message)
        self.previous_page.setEnabled(True);self.next_page.setEnabled(True)

    def cancel_queries(self):
        for worker in self.jobs.values():worker.cancel.set()
        self.status.setText('查询已取消。可调整条件后重新查询。')
        self.previous_page.setEnabled(True);self.next_page.setEnabled(True)

    def invalidate(self):
        self.cancel_queries();self.path=None;self.data=None;self.close_drawer();self.table.finance_model.replace(())
        for card,title in zip(self.cards,('匹配交易总数','买入记录及金额','卖出记录及金额','手续费合计','待核查记录')):card.setText(title+'\n—')
        self.count.clear();self.note.clear();self.status.setText('正在连接新数据源；旧结果已清除。')

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
    def query(self):self.applied=self.values();self.page=1;self.close_drawer();self.render_chips();self.fetch()
    def fetch(self):
        if self.path is None:return
        try:scope=self.scope()
        except ValueError as exc:self.status.setText(str(exc));return
        service=TradeCenterService(self.path);params=self.params();self.status.setText('正在读取全部匹配统计与本页历史成本…');self.previous_page.setEnabled(False);self.next_page.setEnabled(False)
        self.submit('list',lambda cancel:service.page(scope,params,cancel=cancel),self.show_page)

    def show_page(self,data):
        self.data=data;self.page=data['page'];items=data['items'];self.table.finance_model.replace(tuple(items))
        self.table.resizeColumnsToContents()
        for col in range(len(KEYS)):self.table.setColumnWidth(col,min(250,max(85,self.table.columnWidth(col))))
        self.table._frozen_geometry()
        titles=('匹配交易总数','买入记录及金额','卖出记录及金额','手续费合计','待核查记录');s=data['stats']
        amounts=lambda key:' / '.join((c['currency']+'：' if len(s['currencies'])>1 else '')+(money_text(c[key]) if c[key] is not None else '—（未完整核实）') for c in s['currencies']) or '—'
        values=(str(s['matched']),str(s['buys'])+' 笔\n'+amounts('buy_amount'),str(s['sells'])+' 笔\n'+amounts('sell_amount'),amounts('fees'),str(s['review']))
        for card,title,value in zip(self.cards,titles,values):card.setText(title+'\n'+value)
        for key,column in (('broker','broker_code'),('category','category'),('kind','kind'),('side','side'),('source_kind','source_kind')):
            w=self.fields[key];saved=w.currentData();w.blockSignals(True);w.clear();w.addItem('全部','')
            for v in sorted({r[column] for r in data['options'] if r[column]}):w.addItem((SIDES if key=='side' else KINDS if key=='kind' else {}).get(v,v),v)
            w.setCurrentIndex(max(0,w.findData(saved)));w.blockSignals(False)
        w=self.fields['link_status']
        if w.count()==1:
            for value in ('LINKED','LEGACY_UNLINKED','REVIEW_REQUIRED','NOT_APPLICABLE'):w.addItem(value,value)
        self.status.setText('只读数据源：'+data['source']);self.note.setText(data['note']);self.count.setText(f"共 {data['total']} 条 · 第 {self.page} / {data['total_pages']} 页");self.previous_page.setEnabled(self.page>1);self.next_page.setEnabled(self.page<data['total_pages'])
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.frozen.horizontalHeader().setSortIndicatorShown(True)
        mapped=['trade_time' if k=='trade_date' else 'cost_diff' if k=='cost_change' else k for k in KEYS]
        for header in (self.table.horizontalHeader(),self.table.frozen.horizontalHeader()):header.setSortIndicator(mapped.index(self.sort),Qt.SortOrder.DescendingOrder if self.direction=='desc' else Qt.SortOrder.AscendingOrder)

    def render_chips(self):
        while self.chip_layout.count():
            item=self.chip_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
        for key,value in self.applied.items():
            label='人员' if key=='person_id' else LABELS.get(key,key);text=self.person.currentText() if key=='person_id' else str(value)
            b=QPushButton(label+': '+text+' ×');b.clicked.connect(lambda _,k=key:self.remove_filter(k));self.chip_layout.addWidget(b)
        self.chip_layout.addStretch()

    def remove_filter(self,key):
        if key=='person_id':self.person.setCurrentIndex(0)
        elif key in ('start_date','end_date'):
            (self.start if key=='start_date' else self.end).setDate(QDate(2025,1,1) if key=='start_date' else QDate.currentDate())
        else:
            w=self.fields[key]
            if isinstance(w,QComboBox):w.setCurrentIndex(0)
            elif isinstance(w,QCheckBox):w.setChecked(False)
            else:w.clear()
        self.query()
    def reset(self):
        self.person.setCurrentIndex(0);self.start.setDate(QDate(2025,1,1));self.end.setDate(QDate.currentDate())
        for w in self.fields.values():
            if isinstance(w,QComboBox):w.setCurrentIndex(0)
            elif isinstance(w,QCheckBox):w.setChecked(False)
            else:w.clear()
        self.query()
    def quick(self,index):
        if index in (1,2):
            w=self.fields['side'];v='BUY' if index==1 else 'SELL';w.setCurrentIndex(0 if w.currentData()==v else max(0,w.findData(v)))
        elif index==4:self.fields['review_only'].setChecked(not self.fields['review_only'].isChecked())
        else:return
        if index in (1,2):
            w=self.fields['kind'];w.setCurrentIndex(max(0,w.findData('MARKET_TRADE')))
        self.query()
    def toggle_advanced(self):self.advanced.setVisible(not self.advanced.isVisible())
    def sort_column(self,col):
        key=KEYS[col]
        if key=='action':return
        key='trade_time' if key=='trade_date' else 'cost_diff' if key=='cost_change' else key
        self.direction='asc' if self.sort==key and self.direction=='desc' else 'desc';self.sort=key;self.page=1;self.fetch()
    def turn(self,delta):self.page=max(1,self.page+delta);self.fetch()
    def change_size(self):self.size=self.per_page.currentData();self.page=1;self.fetch()
    def column_menu(self):
        menu=QMenu(self)
        for i,title in enumerate(COLUMNS):
            a=menu.addAction(title);a.setCheckable(True);a.setChecked(not self.table.isColumnHidden(i));a.toggled.connect(lambda checked,col=i:self.set_column(col,checked))
        menu.exec(self.mapToGlobal(self.rect().center()))
    def set_column(self,col,checked):
        self.table.setColumnHidden(col,not checked)
        if col==0:self.table.frozen.setVisible(checked);self.table._frozen_geometry()
    def open_row(self,row,_):
        if self.data and row<len(self.data['items']):self.open_detail(self.data['items'][row]['record_id'])
    def open_related(self,row,_):self.open_detail(int(self.related.item(row,2).text()))
    def open_detail(self,record_id):
        if record_id is None:return
        self.selected=record_id
        if self.data:
            for index,row in enumerate(self.data['items']):
                if row['record_id']==record_id:self.table.selectRow(index);break
        self.drawer.show();self.detail_title.setText('正在读取交易详情…');self.prev.setEnabled(False);self.next.setEnabled(False)
        for w in self.texts.values():w.setPlainText('正在读取该笔记录…')
        self.related.setRowCount(0)
        service=TradeCenterService(self.path);scope=self.scope();params=self.params();self.submit('detail',lambda cancel:service.detail(scope,params,record_id,cancel=cancel),self.show_detail)
    def show_detail(self,result):
        item=result['item'];self.detail_title.setText(item['symbol']+' · '+item['side']+'\n'+item['trade_date'])
        for key,w in self.texts.items():w.setPlainText('\n\n'.join(name+'：'+(json.dumps(value,ensure_ascii=False,indent=2,default=exact_text) if isinstance(value,(dict,list)) else money_text(value) if name in MONEY_LABELS else cell_text(item,'cost_change') if name=='每股成本变化' else exact_text(value) if value is not None else '—') for name,value in result['sections'][key].items()))
        self.navigation=result['navigation'];self.prev.setEnabled(bool(self.navigation.get('previous_id')));self.next.setEnabled(bool(self.navigation.get('next_id')));self.nav_label.setText(f"{self.navigation.get('position','关联')} / {self.navigation.get('matched','—')}")
        self.related.setRowCount(len(result['related']))
        for i,r in enumerate(result['related']):
            for col,value in enumerate((r['trade_date'],r['symbol']+' / '+(r['person_name'] or '')+' / '+r['side'],str(r['record_id']))):self.related.setItem(i,col,QTableWidgetItem(value))
        self.related.resizeColumnsToContents()
        if not result['related']:self.related.setToolTip('没有可靠关联记录。')
    def close_drawer(self):
        self.drawer.hide();self.selected=None
        if self.jobs.get('detail'):self.jobs['detail'].cancel.set()
    def export(self):
        if self.path is None:return
        path,_=QFileDialog.getSaveFileName(self,'导出全部匹配交易（不覆盖已有文件）','交易明细.csv','CSV (*.csv)')
        if not path:return
        service=TradeCenterService(self.path);scope=self.scope();params=self.params();self.status.setText('正在分批导出全部匹配记录…')
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
        self.submit('export',task,lambda p:self.status.setText('已导出全部匹配记录：'+p))
