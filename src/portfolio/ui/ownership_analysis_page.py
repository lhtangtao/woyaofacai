"""Independent native ownership/cost workspace; all I/O runs in a worker."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from PySide6.QtCore import QDate,Qt,QTimer,Signal,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QFont
from zoneinfo import ZoneInfo
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFrame,QComboBox,QDateEdit,QLineEdit,QPushButton,QLabel,QTreeWidget,QTreeWidgetItem,QScrollArea,QFileDialog,QMessageBox)
from portfolio.domain.models import QueryScope
from portfolio.domain.live_quotes import UTC
from portfolio.services.ownership_analysis import analyze,choices,export_csv,COLUMNS,CLOSED_COLUMNS
from portfolio.ui.charts_page import ChartCanvas
from portfolio.services.charts_service import Chart,Series
from portfolio.ui.metric_card import MetricCard
from portfolio.ui.theme import tokens


class OwnershipBars(ChartCanvas):
    selected_name=None
    def paintEvent(self,event):
        super().paintEvent(event)
        if self.selected_name:
            painter=QPainter(self);painter.setPen(QPen(QColor(tokens(self).brand),2));painter.setBrush(Qt.BrushStyle.NoBrush)
            for rect,name,_,_ in self.hits:
                if name==self.selected_name:painter.drawRoundedRect(rect.adjusted(1,1,-1,-1),4,4)


class OwnershipAnalysisPage(QWidget):
    def __init__(self,parent):
        super().__init__(parent)
        self.bundle=None;self.data=None;self.identity=None;self.busy=None;self.expanded=set();self.selected=None
        self.sort_key='quantity';self.descending=True;self.holding_status='holding'
        outer=QVBoxLayout(self)
        controls=QHBoxLayout();outer.addLayout(controls)
        # 持仓状态切换按钮：持仓中（默认）/ 已清仓 / 全部
        self.status_frame=QFrame()
        self.status_frame.setStyleSheet('QFrame { background: #F1F5F9; border: 1px solid #E2E8F0; border-radius: 7px; }')
        status_layout=QHBoxLayout(self.status_frame);status_layout.setContentsMargins(2,2,2,2);status_layout.setSpacing(2)
        self.status_buttons={}
        for key,label in (('holding','持仓中'),('closed','已清仓'),('all','全部')):
            btn=QPushButton(label);btn.setCursor(Qt.CursorShape.PointingHandCursor);btn.setFixedHeight(26)
            btn.clicked.connect(lambda _=False,k=key:self.set_holding_status(k))
            status_layout.addWidget(btn);self.status_buttons[key]=btn
        controls.addWidget(self.status_frame)
        self._sync_status_buttons()

        self.stock=QComboBox();self.stock.setMinimumWidth(240);self.stock.setToolTip('选择要查看归属与成本的股票（可根据持仓中/已清仓/全部切换）')
        self.date=QDateEdit(QDate.currentDate());self.date.setCalendarPopup(True);self.date.setMaximumDate(QDate.currentDate());self.date.setDisplayFormat('yyyy-MM-dd')
        self.search=QLineEdit();self.search.setPlaceholderText('搜索人员')
        for w in (QLabel('股票'),self.stock,QLabel('统计截止日期'),self.date,self.search):controls.addWidget(w)
        for text,action in (('刷新',lambda:self.activate(True)),('重置筛选',self.reset),('导出报表',self.export)):
            b=QPushButton(text);b.clicked.connect(action);controls.addWidget(b)
        self.status=QLabel('请选择股票。');self.status.setWordWrap(True);outer.addWidget(self.status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        content=QWidget();scroll.setWidget(content);body=QVBoxLayout(content)
        row=QHBoxLayout();self.cards=[MetricCard('—') for _ in range(6)]
        for card in self.cards:row.addWidget(card,1)
        body.addLayout(row)
        self.check=QLabel();self.check.setWordWrap(True);body.addWidget(self.check)
        charts=QHBoxLayout()
        chart_column=QVBoxLayout();self.mode=QComboBox();self.mode.addItems(('持股数量','占比','累计盈亏'));chart_column.addWidget(self.mode)
        self.chart=OwnershipBars();self.chart.setMinimumHeight(250);chart_column.addWidget(self.chart);charts.addLayout(chart_column,2)
        self.cost=QLabel();self.cost.setWordWrap(True);charts.addWidget(self.cost,1);body.addLayout(charts)
        self.table=QTreeWidget();self.table.setColumnCount(10);self.table.setHeaderLabels(COLUMNS);self.table.setAlternatingRowColors(True);self.table.setMinimumHeight(330);self.table.setRootIsDecorated(True)
        self.table.header().setSectionsClickable(True);self.table.header().setSortIndicatorShown(True);self.table.header().setSortIndicator(1,Qt.SortOrder.DescendingOrder)
        body.addWidget(self.table)
        self.note=QLabel();self.note.setWordWrap(True);body.addWidget(self.note)
        self.stock.currentIndexChanged.connect(self.stock_changed);self.date.dateChanged.connect(lambda:self.activate(True));self.search.textChanged.connect(self.redraw)
        self.mode.currentIndexChanged.connect(self.redraw)
        self.chart.selected.connect(lambda link,name:self.select_person(name))
        self.table.itemExpanded.connect(lambda item:self.expanded.add(item.data(0,Qt.ItemDataRole.UserRole)))
        self.table.itemCollapsed.connect(lambda item:self.expanded.discard(item.data(0,Qt.ItemDataRole.UserRole)))
        self.table.itemClicked.connect(self.table_click);self.table.header().sectionClicked.connect(self.sort)
        self.timer=QTimer(self);self.timer.setInterval(10000);self.timer.timeout.connect(self.activate);self.timer.start()

    def _sync_status_buttons(self):
        active_qss='QPushButton { background: #2563EB; color: #FFFFFF; border: none; border-radius: 5px; font-size: 12px; font-weight: 600; padding: 0 10px; }'
        inactive_qss='QPushButton { background: transparent; color: #475569; border: none; border-radius: 5px; font-size: 12px; font-weight: 500; padding: 0 10px; } QPushButton:hover { color: #0F172A; background: #E2E8F0; }'
        for key,btn in self.status_buttons.items():
            btn.setStyleSheet(active_qss if key==self.holding_status else inactive_qss)

    def _populate_stock_combo(self,options,preferred_id=None):
        current=preferred_id if preferred_id is not None else self.stock.currentData()
        self.stock.blockSignals(True);self.stock.clear()
        for option in options:
            label=option.get('label') or option['symbol']
            self.stock.addItem(label,option['id'])
            idx=self.stock.count()-1
            role_label='参与人' if option.get('is_closed') else '持有人'
            self.stock.setItemData(idx,f"{option['symbol']} · {role_label}：{'、'.join(option.get('holders',[])) or '全渠道'}",Qt.ItemDataRole.ToolTipRole)
        index=self.stock.findData(current)
        if index<0 and options:index=0
        if index>=0:self.stock.setCurrentIndex(index)
        self.stock.blockSignals(False)

    def set_holding_status(self,status):
        if status not in ('holding','closed','all'):return
        changed=(self.holding_status!=status)
        self.holding_status=status
        self._sync_status_buttons()
        if not self.bundle:
            if changed:self.activate(True)
            return
        options=choices(self.bundle,status=self.holding_status)
        self._populate_stock_combo(options)
        if changed:
            self.selected=None;self.expanded.clear()
            self.sort_key='pnl' if status=='closed' else 'quantity'
            self.descending=True
            sort_col=8 if status=='closed' else 1
            self.table.header().setSortIndicator(sort_col,Qt.SortOrder.DescendingOrder)
        self.redraw()

    def activate(self,force=False):
        if not self.isVisible():return
        main=self.window()
        if not getattr(main,'analytics_service',None) or not getattr(main,'info',None):return
        identity=(str(main.info.path),self.date.date().toString('yyyy-MM-dd'))
        if self.busy:return
        prior=self.bundle if self.identity==identity and not force else None
        changed=self.identity!=identity
        if changed:self.bundle=None;self.data=None;self.expanded.clear();self.selected=None
        self.identity=identity;self.busy=identity;self.status.setText('正在读取全部历史人员、全部渠道的持仓与有效行情…')
        service=main.analytics_service;cutoff=self.date.date().toPython();instrument=None if changed else self.stock.currentData()
        status_filter=self.holding_status
        def task(cancel):
            from portfolio.db.connection import readonly_connection
            from portfolio.db.repositories.analytics import instrument_catalog
            from portfolio.domain.live_quotes import us_session,NY
            bundle=prior or service.calculate_people(QueryScope(cutoff,include_disabled=True,start_date=None),cancel=cancel)
            with readonly_connection(identity[0],cancel=cancel) as c:catalog=instrument_catalog(c)
            now=datetime.now(UTC);quotes,days,halves,covered=main.quote_store.load(now)
            session=us_session(now,days,halves) if cutoff==now.astimezone(ZoneInfo('Asia/Shanghai')).date() and covered and covered[0]<=now.astimezone(NY).date()<=covered[1] else None
            avail=choices(bundle,status=status_filter)
            target_inst=instrument if any(v['id']==instrument for v in avail) else None
            return bundle,catalog,quotes,session,now,analyze(bundle,target_inst,catalog,quotes,session,now,status=status_filter)
        def ready(value):
            if self.identity!=identity:return
            self.busy=None
            if self.date.date().toPython()!=cutoff:self.activate(True);return
            self.bundle,self.catalog,self.quotes,self.session,self.now,self.data=value
            self._populate_stock_combo(self.data['choices'],preferred_id=self.data['instrument_id'])
            self.redraw()
            status_desc={'holding':'持仓中','closed':'已清仓','all':'全部'}[self.holding_status]
            self.status.setText(f'数据源：{identity[0]} · 截止 {identity[1]} · {status_desc} · 全部历史人员／全部渠道 · '+('实时/缓存有效报价' if self.session else '历史收盘参考'))
        lane='ownership-analysis';main._submit(lane,task,ready)
        def failed(message):
            self.busy=None;self.status.setText('读取失败：'+message)
        main.jobs[lane].signals.error.connect(failed,Qt.ConnectionType.QueuedConnection)

    def stock_changed(self,*_):
        self.selected=None;self.expanded.clear();self.redraw()

    def redraw(self,*_):
        if not self.bundle:return
        try:self.data=analyze(self.bundle,self.stock.currentData(),self.catalog,self.quotes,self.session,self.now,self.search.text(),status=self.holding_status)
        except ValueError as exc:self.status.setText(str(exc));return
        d=self.data;t=tokens(self);is_closed=bool(d.get('is_closed'))
        def fmt(v,percent=False,quantity=False,price=False):
            if v is None:return '—'
            if percent:return f'{v:.2%}'
            if isinstance(v,Decimal):return f'{v:,.8f}'.rstrip('0').rstrip('.') if quantity or price else f'{v:,.2f}'
            return str(v)
        if not d.get('choices'):
            for card in self.cards:card.setText('—')
            self.check.setText('当前筛选条件下暂无符合条件的股票。')
            self.cost.setText('暂无数据')
            self.chart.chart=Chart(0,'人员分布','horizontal',(Series('人员',()),),'',ratio=False)
            self.chart.empty_message='当前筛选条件下没有可展示的股票。'
            self.chart.update()
            self.table.clear();self.note.setText('')
            return

        if is_closed:
            titles=('当前参考价','历史买入总额','历史卖出总额','总收益率','清仓总收益（人员）','参与人数')
            for card,title,key in zip(self.cards,titles,('price','buy_total','sell_total','rate','pnl','holders')):
                value=d['summary'].get(key)
                card.setText(title+'\n'+fmt(value,percent=key=='rate',price=key=='price'))
                color=(t.profit if value>0 else t.loss if value<0 else t.heading) if key in ('pnl','rate') and value is not None else t.heading
                card.setStyleSheet(f'QPushButton {{text-align:left;padding:10px;border:1px solid {t.border};border-radius:10px;background:{t.panel};color:{color};}}')
            rec=d['reconciliation']
            self.check.setText(f"核对：当前均已清仓（0 股） · 券商清仓收益 {fmt(rec.get('broker_pnl'))} · 全部人员清仓收益 {fmt(rec.get('assigned_pnl'))} · 归属差异 {fmt(rec.get('pnl_difference'))} · "+('一致' if rec['ok'] else '存在差异或缺失，请核对'))
            self.check.setStyleSheet('color:'+str(t.profit if rec['ok'] else t.loss))
            ca=d['cost_analysis']
            best=ca.get('best_pnl');worst=ca.get('worst_pnl')
            self.cost.setText('清仓收益摘要\n\n最高收益：'+(f"{best['name']} {best['pnl']:+,.2f}" if best and best.get('pnl') is not None else '—')+'\n最低收益：'+(f"{worst['name']} {worst['pnl']:+,.2f}" if worst and worst.get('pnl') is not None else '—')+'\n加权买入均价：'+fmt(ca.get('weighted'),price=True)+'\n加权卖出均价：'+fmt(ca.get('weighted_sell'),price=True))
            rows=d['rows']
            mode_idx=self.mode.currentIndex()
            if mode_idx==1:
                bars=tuple((r['person_name'],r['share']) for r in rows)
                self.chart.chart=Chart(0,'人员买入资金占比','horizontal',(Series('人员',bars),),'展示各人员历史买入总额占全部人员买入总额的比例。',ratio=True,
                                       point_labels={r['person_name']:f"{fmt(r['share'],True)} · 收益 {r['pnl']:+,.2f}" if r['pnl'] is not None else fmt(r['share'],True) for r in rows})
                self.chart.bar_color=t.brand
            else:
                bars=tuple((r['person_name'],r['pnl'] if r['pnl'] is not None else Decimal(0)) for r in rows)
                self.chart.chart=Chart(0,'人员清仓收益分布','horizontal',(Series('人员',bars),),'展示该股票历史已实现清仓收益在不同人员名下的归属分布（绿色盈利、红色亏损）。',ratio=False,
                                       point_labels={r['person_name']:f"{r['pnl']:+,.2f} USD · 买占 {fmt(r['share'],True)}" if r['pnl'] is not None else '—' for r in rows})
                self.chart.bar_color=None
            self.chart.selected_name=self.selected
            self.chart.empty_message='没有可绘制的已清仓收益记录。'
            self.chart.setFixedHeight(max(250,100+len(rows)*38));self.chart.update()
            self.table.setHeaderLabels(CLOSED_COLUMNS)
            sort_key=self.sort_key if self.sort_key in ('buy_quantity','share','buy_avg','buy_total','sell_avg','sell_total','rate','pnl') else 'pnl'
            rows=sorted(rows,key=lambda r:(r.get(sort_key) is None,-r[sort_key] if self.descending and r.get(sort_key) is not None else r.get(sort_key) or Decimal(0)))
            self.table.blockSignals(True);self.table.clear()
            keys=('buy_quantity','share','buy_avg','buy_total','sell_avg','sell_total','rate','pnl')
            for r in rows:
                item=QTreeWidgetItem([r['person_name'],*(fmt(r.get(k),k in ('share','rate'),k=='buy_quantity',k in ('buy_avg','sell_avg')) for k in keys),'查看券商明细'])
                item.setData(0,Qt.ItemDataRole.UserRole,r['person_id']);self.table.addTopLevelItem(item)
                item.setToolTip(0,r.get('warning',''))
                for col,key in ((7,'rate'),(8,'pnl')):
                    if r.get(key) is not None:item.setForeground(col,QColor(t.profit if r[key]>0 else t.loss if r[key]<0 else t.heading))
                for b in r['brokers']:
                    child=QTreeWidgetItem([b['channel'],fmt(b.get('buy_quantity'),quantity=True),'—',fmt(b.get('buy_avg'),price=True),fmt(b.get('buy_total')),fmt(b.get('sell_avg'),price=True),fmt(b.get('sell_total')),fmt(b.get('rate'),True),fmt(b.get('pnl')),''])
                    if b.get('pnl') is not None:child.setForeground(8,QColor(t.profit if b['pnl']>0 else t.loss if b['pnl']<0 else t.heading))
                    item.addChild(child)
                item.setExpanded(r['person_id'] in self.expanded)
                if self.selected==r['person_name']:self.table.setCurrentItem(item)
            total=QTreeWidgetItem(['合计',*(fmt(d['total'].get(k),k in ('share','rate'),k=='buy_quantity',k in ('buy_avg','sell_avg')) for k in keys),'—']);self.table.addTopLevelItem(total)
            for col in range(10):font=total.font(col);font.setBold(True);total.setFont(col,font);total.setBackground(col,QColor(t.subtle))
            self.table.blockSignals(False)
            for col in range(10):self.table.resizeColumnToContents(col)
            self.note.setText(d['symbol']+' · '+d['note']+'\n行情状态：'+d['summary']['quote_note'])
            return

        titles=('当前价','总股数（券商）','总成本总额','总市值（券商）','累计盈亏（人员）','持有人数')
        for card,title,key in zip(self.cards,titles,('price','broker_quantity','cost_total','market_value','pnl','holders')):
            value=d['summary'][key];card.setText(title+'\n'+fmt(value,quantity=key=='broker_quantity',price=key=='price'))
            color=t.profit if key=='pnl' and value is not None and value>0 else t.loss if key=='pnl' and value is not None and value<0 else t.heading
            card.setStyleSheet(f'QPushButton {{text-align:left;padding:10px;border:1px solid {t.border};border-radius:10px;background:{t.panel};color:{color};}}')
        rec=d['reconciliation'];self.check.setText(f"核对：券商 {fmt(rec['broker'],quantity=True)} 股 · 全部人员 {fmt(rec['assigned'],quantity=True)} 股 · 未分配／归属差异 {fmt(rec['difference'],quantity=True)} 股 · "+('一致' if rec['ok'] else '存在账户级差异或未知数量，请核对'))
        self.check.setStyleSheet('color:'+str(t.profit if rec['ok'] else t.loss))
        ca=d['cost_analysis'];self.cost.setText('成本分析摘要\n\n最低成本：'+(ca['lowest']['name']+' '+fmt(ca['lowest']['cost']) if ca['lowest'] else '—')+'\n最高成本：'+(ca['highest']['name']+' '+fmt(ca['highest']['cost']) if ca['highest'] else '—')+'\n加权平均成本：'+fmt(ca['weighted'])+('\n部分成本缺失，极值仅比较有效成本。' if not ca['complete'] else ''))
        rows=d['rows']
        mode_idx=self.mode.currentIndex()
        if mode_idx==2:
            bars=tuple((r['person_name'],r['pnl'] if r['pnl'] is not None else Decimal(0)) for r in rows)
            self.chart.chart=Chart(0,'人员累计盈亏分布','horizontal',(Series('人员',bars),),'展示该股票下各人员（含已清仓）的累计盈亏分布（绿色盈利、红色亏损）。',ratio=False,
                                   point_labels={r['person_name']:f"{r['pnl']:+,.2f} USD · {'已清仓' if r.get('is_closed') else fmt(r['quantity'],quantity=True)+' 股'}" if r['pnl'] is not None else '—' for r in rows})
            self.chart.bar_color=None
        else:
            bars=tuple((r['person_name'],r['quantity'] if mode_idx==0 else r['share']) for r in rows)
            self.chart.chart=Chart(0,'人员持股分布','horizontal',(Series('人员',bars),),'占比以券商总股数为分母，未归一化。',ratio=mode_idx==1,
                                   point_labels={r['person_name']:(f"0 股（已清仓 · 盈亏 {r['pnl']:+,.2f}）" if r.get('is_closed') and r['pnl'] is not None else fmt(r['quantity'],quantity=True)+' 股 · '+fmt(r['share'],True)) for r in rows})
            self.chart.bar_color=t.brand
        self.chart.selected_name=self.selected
        self.chart.empty_message='没有可绘制的有效股数或占比，请查看股数核对提示。'
        self.chart.setFixedHeight(max(250,100+len(rows)*38));self.chart.update()
        self.table.setHeaderLabels(COLUMNS)
        sort_key=self.sort_key if self.sort_key in ('quantity','share','cost','cost_total','market_value','rate','pnl') else 'quantity'
        rows=sorted(rows,key=lambda r:(r.get(sort_key) is None,-r[sort_key] if self.descending and r.get(sort_key) is not None else r.get(sort_key) or Decimal(0)))
        self.table.blockSignals(True);self.table.clear()
        keys=('quantity','share','cost','cost_total','price','market_value','rate','pnl')
        for r in rows:
            label_name=f"{r['person_name']}（已清仓）" if r.get('is_closed') else r['person_name']
            row_vals=[fmt(None if (r.get('is_closed') and k=='cost') else r[k],k in ('share','rate'),k=='quantity',k=='price') for k in keys]
            item=QTreeWidgetItem([label_name,*row_vals,'查看券商明细'])
            item.setData(0,Qt.ItemDataRole.UserRole,r['person_id']);self.table.addTopLevelItem(item)
            item.setToolTip(0,r.get('warning',''))
            item.setToolTip(5,r.get('quote_note',''))
            for col,key in ((7,'rate'),(8,'pnl')):
                if r[key] is not None:item.setForeground(col,QColor(t.profit if r[key]>0 else t.loss if r[key]<0 else t.heading))
            for b in r['brokers']:
                b_cost=fmt(None if b.get('is_closed') else b['cost'])
                b_name=f"{b['channel']}（已清仓）" if b.get('is_closed') else b['channel']
                child=QTreeWidgetItem([b_name,fmt(b['quantity'],quantity=True),'—',b_cost,fmt(b['cost_total']),fmt(b['price'],price=True),fmt(b['market_value']),fmt(b.get('rate'),True) if b.get('is_closed') else '—',fmt(b['pnl']),''])
                child.setToolTip(5,b.get('quote_note',''))
                if b['pnl'] is not None:child.setForeground(8,QColor(t.profit if b['pnl']>0 else t.loss if b['pnl']<0 else t.heading))
                item.addChild(child)
            item.setExpanded(r['person_id'] in self.expanded)
            if self.selected==r['person_name']:self.table.setCurrentItem(item)
        total=QTreeWidgetItem(['合计',*(fmt(d['total'][k],k in ('share','rate'),k=='quantity',k=='price') for k in keys),'—']);self.table.addTopLevelItem(total)
        for col in range(10):font=total.font(col);font.setBold(True);total.setFont(col,font);total.setBackground(col,QColor(t.subtle))
        self.table.blockSignals(False)
        for col in range(10):self.table.resizeColumnToContents(col)
        self.note.setText(d['symbol']+' · '+d['note']+'\n行情状态：'+d['summary']['quote_note'])

    def select_person(self,name,*_):self.selected=None if self.selected==name else name;self.redraw()
    def table_click(self,item,column):
        if item.parent() is None and item.data(0,Qt.ItemDataRole.UserRole) is not None:
            self.selected=item.text(0).replace('（已清仓）','')
            self.chart.selected_name=self.selected;self.chart.update()
            if column==9:item.setExpanded(not item.isExpanded())
    def sort(self,column):
        if self.data and self.data.get('is_closed'):
            keys={1:'buy_quantity',2:'share',3:'buy_avg',4:'buy_total',5:'sell_avg',6:'sell_total',7:'rate',8:'pnl'}
        else:
            keys={1:'quantity',2:'share',3:'cost',4:'cost_total',6:'market_value',7:'rate',8:'pnl'}
        if column not in keys:return
        self.descending=not self.descending if self.sort_key==keys[column] else True;self.sort_key=keys[column]
        self.table.header().setSortIndicator(column,Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder);self.redraw()
    def reset(self):
        self.holding_status='holding';self._sync_status_buttons()
        self.mode.setCurrentIndex(0)
        self.search.clear();self.selected=None;self.expanded.clear();self.sort_key='quantity';self.descending=True
        self.table.header().setSortIndicator(1,Qt.SortOrder.DescendingOrder)
        if self.bundle:self._populate_stock_combo(choices(self.bundle,status='holding'))
        elif self.stock.count():self.stock.setCurrentIndex(0)
        self.date.setDate(QDate.currentDate());self.activate(True)
    def export(self):
        if not self.data:return
        path,_=QFileDialog.getSaveFileName(self,'导出股票归属与成本分析','股票归属与成本分析.csv','CSV (*.csv)')
        if not path:return
        target=Path(path)
        if target.exists():target=target.with_name(target.stem+'-'+datetime.now().strftime('%Y%m%d%H%M%S%f')+target.suffix)
        try:
            with target.open('x',encoding='utf-8-sig',newline='') as stream:stream.write(export_csv(self.data))
        except OSError:QMessageBox.warning(self,'导出失败','无法写入所选位置，请重新选择。')
