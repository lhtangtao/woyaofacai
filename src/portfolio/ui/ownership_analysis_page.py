"""Independent native ownership/cost workspace; all I/O runs in a worker."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from PySide6.QtCore import QDate,Qt,QTimer,Signal,QRectF
from PySide6.QtGui import QColor,QPainter,QPen,QFont
from zoneinfo import ZoneInfo
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QComboBox,QDateEdit,QLineEdit,QPushButton,QLabel,QTreeWidget,QTreeWidgetItem,QScrollArea,QFileDialog,QMessageBox)
from portfolio.domain.models import QueryScope
from portfolio.domain.live_quotes import UTC
from portfolio.services.ownership_analysis import analyze,export_csv,COLUMNS
from portfolio.ui.metric_card import MetricCard
from portfolio.ui.theme import tokens


class OwnershipPie(QWidget):
    selected = Signal(str, str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.total_qty = Decimal(0)
        self.symbol = ''
        self.selected_name = None
        self.hits = []
        self.setMouseTracking(True)
        self.setMinimumHeight(240)

    def set_data(self, symbol, rows, total_qty, selected_name=None):
        self.symbol = symbol
        self.rows = rows
        self.total_qty = total_qty
        self.selected_name = selected_name
        self.update()

    def mousePressEvent(self, event):
        pos = event.position()
        for rect, name in self.hits:
            if rect.contains(pos):
                self.selected.emit('', name)
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = tokens(self)
        self.hits = []
        w, h = self.width(), self.height()

        p.setPen(QColor(t.heading))
        font = p.font()
        font.setBold(True)
        font.setPointSize(12)
        p.setFont(font)
        p.drawText(QRectF(15, 10, 200, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, '人员持股分布')
        font.setBold(False)
        font.setPointSize(10)
        p.setFont(font)

        if not self.rows or not self.total_qty or self.total_qty <= 0:
            p.setPen(QColor(t.muted))
            p.drawText(QRectF(0, 36, w, h - 36), Qt.AlignmentFlag.AlignCenter, '当前无有效持股数据')
            return

        palette = ('#3B82F6', '#10B981', '#8B5CF6', '#F59E0B', '#EC4899', '#06B6D4', '#84CC16', '#6366F1', '#14B8A6')

        content_top = 38
        avail_h = h - content_top - 12
        donut_size = min(avail_h, 175)
        donut_rect = QRectF(25, content_top + (avail_h - donut_size) / 2, donut_size, donut_size)

        start_angle = 90 * 16
        used_angle = 0
        total_float = float(self.total_qty)

        for i, r in enumerate(self.rows):
            qty = float(r.get('quantity') or 0)
            if qty <= 0:
                continue
            if i == len(self.rows) - 1:
                span_angle = -(360 * 16 - used_angle)
            else:
                span_angle = -int((qty / total_float) * 360 * 16)

            color = QColor(palette[i % len(palette)])
            p.setPen(QPen(QColor(t.panel), 2))
            p.setBrush(color)

            if r['person_name'] == self.selected_name:
                p.setPen(QPen(QColor(t.heading), 2.5))

            p.drawPie(donut_rect, start_angle, span_angle)
            start_angle += span_angle
            used_angle += abs(span_angle)

        hole_size = donut_size * 0.58
        hole_rect = QRectF(donut_rect.center().x() - hole_size / 2, donut_rect.center().y() - hole_size / 2, hole_size, hole_size)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.panel))
        p.drawEllipse(hole_rect)

        p.setPen(QColor(t.heading))
        font.setBold(True)
        font.setPointSize(11)
        p.setFont(font)
        p.drawText(QRectF(hole_rect.x(), hole_rect.y() + hole_size * 0.22, hole_rect.width(), 20), Qt.AlignmentFlag.AlignCenter, self.symbol)

        p.setPen(QColor(t.muted))
        font.setBold(False)
        font.setPointSize(9)
        p.setFont(font)
        qty_str = f'{self.total_qty:,.8f}'.rstrip('0').rstrip('.') + ' 股'
        p.drawText(QRectF(hole_rect.x(), hole_rect.y() + hole_size * 0.54, hole_rect.width(), 18), Qt.AlignmentFlag.AlignCenter, qty_str)

        legend_left = donut_rect.right() + 30
        legend_w = w - legend_left - 15
        row_h = 32
        legend_y = content_top + max(0, (avail_h - len(self.rows) * row_h) / 2)

        for i, r in enumerate(self.rows):
            name = r['person_name']
            qty = r.get('quantity')
            share = r.get('share')
            color = palette[i % len(palette)]
            row_rect = QRectF(legend_left, legend_y, legend_w, row_h)
            self.hits.append((row_rect, name))

            if name == self.selected_name:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.selected))
                p.drawRoundedRect(row_rect.adjusted(0, 2, 0, -2), 6, 6)

            dot_y = legend_y + (row_h - 10) / 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawEllipse(QRectF(legend_left + 8, dot_y, 10, 10))

            p.setPen(QColor(t.heading))
            font.setPointSize(10)
            font.setBold(name == self.selected_name)
            p.setFont(font)
            p.drawText(QRectF(legend_left + 26, legend_y, legend_w * 0.35, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)

            p.setPen(QColor(t.text))
            font.setBold(False)
            p.setFont(font)
            q_text = f'{qty:,.8f}'.rstrip('0').rstrip('.') + ' 股' if qty is not None else '—'
            p.drawText(QRectF(legend_left + 26 + legend_w * 0.35, legend_y, legend_w * 0.35, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, q_text)

            p.setPen(QColor(t.muted))
            s_text = f'{share:.2%}' if share is not None else '—'
            p.drawText(QRectF(legend_left + legend_w * 0.72, legend_y, legend_w * 0.28, row_h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, s_text)

            legend_y += row_h


class OwnershipAnalysisPage(QWidget):
    def __init__(self,parent):
        super().__init__(parent)
        self.bundle=None;self.data=None;self.identity=None;self.busy=None;self.expanded=set();self.selected=None;self.sort_key='quantity';self.descending=True
        outer=QVBoxLayout(self)
        controls=QHBoxLayout();outer.addLayout(controls)
        self.stock=QComboBox();self.stock.setMinimumWidth(240);self.stock.setToolTip('选择要查看归属与成本的股票（包含全部人员及他人持有的真实持仓股票）')
        self.date=QDateEdit(QDate.currentDate());self.date.setCalendarPopup(True);self.date.setMaximumDate(QDate.currentDate());self.date.setDisplayFormat('yyyy-MM-dd')
        self.search=QLineEdit();self.search.setPlaceholderText('搜索人员')
        for w in (QLabel('股票'),self.stock,QLabel('统计截止日期'),self.date,self.search):controls.addWidget(w)
        for text,action in (('刷新',lambda:self.activate(True)),('重置筛选',self.reset),('导出报表',self.export)):
            b=QPushButton(text);b.clicked.connect(action);controls.addWidget(b)
        self.status=QLabel('请选择真实持仓股票。');self.status.setWordWrap(True);outer.addWidget(self.status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll)
        content=QWidget();scroll.setWidget(content);body=QVBoxLayout(content)
        row=QHBoxLayout();self.cards=[MetricCard('—') for _ in range(6)]
        for card in self.cards:row.addWidget(card,1)
        body.addLayout(row)
        self.check=QLabel();self.check.setWordWrap(True);body.addWidget(self.check)
        charts=QHBoxLayout()
        self.chart=OwnershipPie();self.chart.setMinimumHeight(250);charts.addWidget(self.chart,2)
        self.cost=QLabel();self.cost.setWordWrap(True);charts.addWidget(self.cost,1);body.addLayout(charts)
        self.table=QTreeWidget();self.table.setColumnCount(10);self.table.setHeaderLabels(COLUMNS);self.table.setAlternatingRowColors(True);self.table.setMinimumHeight(330);self.table.setRootIsDecorated(True)
        self.table.header().setSectionsClickable(True);self.table.header().setSortIndicatorShown(True);self.table.header().setSortIndicator(1,Qt.SortOrder.DescendingOrder)
        body.addWidget(self.table)
        self.note=QLabel();self.note.setWordWrap(True);body.addWidget(self.note)
        self.stock.currentIndexChanged.connect(self.stock_changed);self.date.dateChanged.connect(lambda:self.activate(True));self.search.textChanged.connect(self.redraw)
        self.chart.selected.connect(lambda link,name:self.select_person(name))
        self.table.itemExpanded.connect(lambda item:self.expanded.add(item.data(0,Qt.ItemDataRole.UserRole)))
        self.table.itemCollapsed.connect(lambda item:self.expanded.discard(item.data(0,Qt.ItemDataRole.UserRole)))
        self.table.itemClicked.connect(self.table_click);self.table.header().sectionClicked.connect(self.sort)
        self.timer=QTimer(self);self.timer.setInterval(10000);self.timer.timeout.connect(self.activate);self.timer.start()

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
        def task(cancel):
            from portfolio.db.connection import readonly_connection
            from portfolio.db.repositories.analytics import instrument_catalog
            from portfolio.domain.live_quotes import us_session,NY
            bundle=prior or service.calculate_people(QueryScope(cutoff,include_disabled=True,start_date=None),cancel=cancel)
            with readonly_connection(identity[0],cancel=cancel) as c:catalog=instrument_catalog(c)
            now=datetime.now(UTC);quotes,days,halves,covered=main.quote_store.load(now)
            session=us_session(now,days,halves) if cutoff==now.astimezone(ZoneInfo('Asia/Shanghai')).date() and covered and covered[0]<=now.astimezone(NY).date()<=covered[1] else None
            return bundle,catalog,quotes,session,now,analyze(bundle,instrument,catalog,quotes,session,now)
        def ready(value):
            if self.identity!=identity:return
            self.busy=None
            if self.date.date().toPython()!=cutoff:self.activate(True);return
            self.bundle,self.catalog,self.quotes,self.session,self.now,self.data=value
            current=self.stock.currentData()
            self.stock.blockSignals(True);self.stock.clear()
            for option in self.data['choices']:
                label=option.get('label') or option['symbol']
                self.stock.addItem(label,option['id'])
                idx=self.stock.count()-1
                self.stock.setItemData(idx,f"{option['symbol']} · 持有人：{'、'.join(option.get('holders',[])) or '全渠道'}",Qt.ItemDataRole.ToolTipRole)
            index=self.stock.findData(current)
            if index<0:index=self.stock.findData(self.data['instrument_id'])
            self.stock.setCurrentIndex(max(0,index))
            self.stock.blockSignals(False);self.redraw()
            self.status.setText(f'数据源：{identity[0]} · 截止 {identity[1]} · 全部历史人员／全部渠道 · '+('实时/缓存有效报价' if self.session else '历史收盘参考'))
        lane='ownership-analysis';main._submit(lane,task,ready)
        def failed(message):
            self.busy=None;self.status.setText('读取失败：'+message)
        main.jobs[lane].signals.error.connect(failed,Qt.ConnectionType.QueuedConnection)

    def stock_changed(self,*_):
        self.selected=None;self.expanded.clear();self.redraw()

    def redraw(self,*_):
        if not self.bundle:return
        try:self.data=analyze(self.bundle,self.stock.currentData(),self.catalog,self.quotes,self.session,self.now,self.search.text())
        except ValueError as exc:self.status.setText(str(exc));return
        d=self.data;t=tokens(self)
        def fmt(v,percent=False,quantity=False,price=False):
            if v is None:return '—'
            if percent:return f'{v:.2%}'
            if isinstance(v,Decimal):return f'{v:,.8f}'.rstrip('0').rstrip('.') if quantity or price else f'{v:,.2f}'
            return str(v)
        titles=('当前价','总股数（券商）','总成本总额','总市值（券商）','累计盈亏（人员）','持有人数')
        for card,title,key in zip(self.cards,titles,('price','broker_quantity','cost_total','market_value','pnl','holders')):
            value=d['summary'][key];card.setText(title+'\n'+fmt(value,quantity=key=='broker_quantity',price=key=='price'))
            color=t.profit if key=='pnl' and value is not None and value>0 else t.loss if key=='pnl' and value is not None and value<0 else t.heading
            card.setStyleSheet(f'QPushButton {{text-align:left;padding:10px;border:1px solid {t.border};border-radius:10px;background:{t.panel};color:{color};}}')
        rec=d['reconciliation'];self.check.setText(f"核对：券商 {fmt(rec['broker'],quantity=True)} 股 · 全部人员 {fmt(rec['assigned'],quantity=True)} 股 · 未分配／归属差异 {fmt(rec['difference'],quantity=True)} 股 · "+('一致' if rec['ok'] else '存在账户级差异或未知数量，请核对'))
        self.check.setStyleSheet('color:'+str(t.profit if rec['ok'] else t.loss))
        ca=d['cost_analysis'];self.cost.setText('成本分析摘要\n\n最低成本：'+(ca['lowest']['name']+' '+fmt(ca['lowest']['cost']) if ca['lowest'] else '—')+'\n最高成本：'+(ca['highest']['name']+' '+fmt(ca['highest']['cost']) if ca['highest'] else '—')+'\n加权平均成本：'+fmt(ca['weighted'])+('\n部分成本缺失，极值仅比较有效成本。' if not ca['complete'] else ''))
        rows=d['rows']
        self.chart.set_data(d['symbol'],rows,d['summary']['broker_quantity'],self.selected)
        rows=sorted(rows,key=lambda r:(r[self.sort_key] is None,-r[self.sort_key] if self.descending and r[self.sort_key] is not None else r[self.sort_key] or Decimal(0)))
        self.table.blockSignals(True);self.table.clear()
        keys=('quantity','share','cost','cost_total','price','market_value','rate','pnl')
        for r in rows:
            item=QTreeWidgetItem([r['person_name'],*(fmt(r[k],k in ('share','rate'),k=='quantity',k=='price') for k in keys),'查看券商明细'])
            item.setData(0,Qt.ItemDataRole.UserRole,r['person_id']);self.table.addTopLevelItem(item)
            item.setToolTip(0,r.get('warning',''))
            item.setToolTip(5,r.get('quote_note',''))
            for col,key in ((7,'rate'),(8,'pnl')):
                if r[key] is not None:item.setForeground(col,QColor(t.profit if r[key]>0 else t.loss if r[key]<0 else t.heading))
            for b in r['brokers']:
                child=QTreeWidgetItem([b['channel'],fmt(b['quantity'],quantity=True),'—',fmt(b['cost']),fmt(b['cost_total']),fmt(b['price'],price=True),fmt(b['market_value']),'—',fmt(b['pnl']),''])
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
            self.selected=item.text(0)
            self.chart.selected_name=self.selected;self.chart.update()
            if column==9:item.setExpanded(not item.isExpanded())
    def sort(self,column):
        keys={1:'quantity',2:'share',3:'cost',4:'cost_total',6:'market_value',7:'rate',8:'pnl'}
        if column not in keys:return
        self.descending=not self.descending if self.sort_key==keys[column] else True;self.sort_key=keys[column]
        self.table.header().setSortIndicator(column,Qt.SortOrder.DescendingOrder if self.descending else Qt.SortOrder.AscendingOrder);self.redraw()
    def reset(self):
        self.search.clear();self.selected=None;self.expanded.clear();self.sort_key='quantity';self.descending=True
        self.table.header().setSortIndicator(1,Qt.SortOrder.DescendingOrder)
        self.stock.setCurrentIndex(0);self.date.setDate(QDate.currentDate());self.activate(True)
    def export(self):
        if not self.data:return
        path,_=QFileDialog.getSaveFileName(self,'导出股票归属与成本分析','股票归属与成本分析.csv','CSV (*.csv)')
        if not path:return
        target=Path(path)
        if target.exists():target=target.with_name(target.stem+'-'+datetime.now().strftime('%Y%m%d%H%M%S%f')+target.suffix)
        try:
            with target.open('x',encoding='utf-8-sig',newline='') as stream:stream.write(export_csv(self.data))
        except OSError:QMessageBox.warning(self,'导出失败','无法写入所选位置，请重新选择。')
