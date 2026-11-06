"""Shared read-only trade center, SQL summaries, lazy details and CSV stream."""
from dataclasses import replace
from datetime import datetime,date
from decimal import Decimal,InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from portfolio.db.connection import readonly_connection
from portfolio.db.repositories import trade_center as repo
from portfolio.db.repositories.provenance import get_provenance,redact
from portfolio.domain.models import TradeFilters,TradePage,TradeRow,from_e8
from portfolio.services.post_trade_positions import attach_post_trade_positions

COLUMNS=('交易时间','券商渠道','归属人员','Ticker','分类','交易方向','当前价','成交价','成交数量','成交金额','手续费','成交后持仓','成交后成本价','成本变化','操作')
KEYS=('trade_date','broker','person_name','symbol','category','side','current_price','price','quantity','amount','fees','post_quantity','post_cost','cost_change','action')
SIDES={'BUY':'买入','SELL':'卖出','TRANSFER_IN':'转入','TRANSFER_OUT':'转出'}
KINDS={'MARKET_TRADE':'市场交易','OWNER_TRANSFER':'人员划转','EXTERNAL_TRANSFER':'外部划转','BOOK_SETTLEMENT':'账务结算'}


def validate(params):
    p={k:v for k,v in params.items() if v not in (None,'')}
    for k in ('quantity_min','quantity_max','price_min','price_max','amount_min','amount_max','cost_min','cost_max'):
        if k in p:
            try:v=Decimal(str(p[k]))
            except InvalidOperation as exc:raise ValueError('数值筛选必须是有效十进制数。') from exc
            if not v.is_finite() or len(str(p[k]))>60 or v.copy_abs()>Decimal('90000000000') or v.adjusted() < -100:raise ValueError('数值筛选范围无效。')
            if not k.startswith('cost_') and v<0:raise ValueError('数量、价格、金额范围不可为负数。')
            p[k]=str(v)
    for name in ('quantity','price','amount','cost'):
        if name+'_min' in p and name+'_max' in p and Decimal(p[name+'_min'])>Decimal(p[name+'_max']):raise ValueError('筛选下限不可大于上限。')
    for k in ('review_only','affects_only'):
        p[k]=str(p.get(k,'')).lower() in ('1','true','yes')
    if p.get('micro'):raise ValueError('当前账本未提供可核验的逐笔微操标记，暂不支持此筛选；请使用微操报表的配对证据。')
    p['side']={'买入':'BUY','卖出':'SELL','转入':'TRANSFER_IN','转出':'TRANSFER_OUT'}.get(p.get('side'),p.get('side'))
    return p


def raw_trade(data):
    d=dict(data)
    values=(d['book_time'],d['broker_code'],d['category'],d['symbol'],SIDES.get(d['side'],d['side']),from_e8(d['quantity_e8']),from_e8(d['price_e8']),from_e8(d['amount_e8']),None,None,None,None,KINDS.get(d['kind'],d['kind']),d['counterparty'],d['order_id'],d['source_path'])
    return TradeRow(d['id'],d['source_record_id'],values,d)


def dto(row):
    m=row.metadata;v=row.values;instant=m.get('trade_time')
    try:display=datetime.fromisoformat(instant).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')+' 北京时间' if instant else m['book_time']+'（原账务时钟）'
    except (ValueError,TypeError):display=m['book_time']+'（原账务时钟）'
    if instant and not m.get('executed_at_utc'):
        display+='（日期型估算）' if str(m['book_time']).endswith(('00:00:00','00:00')) or len(str(m['book_time']))==10 else '（账务时钟换算）'
    return dict(id=row.record_id,record_id=row.record_id,source_record_id=row.source_record_id,trade_date=display,trade_time=instant,
        broker=m['broker_code'],person_name=m['person_name'] or '券商真实账本',symbol=m['symbol'],category=m['category'],side=v[4] if m['kind']=='MARKET_TRADE' else KINDS.get(m['kind'],m['kind'])+' / '+v[4],side_code=m['side'],kind=v[12],kind_code=m['kind'],
        current_price=m.get('current_price'),quote_time=m.get('quote_time'),quote_status=m.get('quote_status'),price=v[6],quantity=v[5],amount=v[7],currency=m['currency'],fees=from_e8(m.get('fees_e8')),
        post_quantity=m.get('post_quantity'),post_cost=m.get('post_cost'),pre_quantity=m.get('pre_quantity'),pre_cost=m.get('pre_cost'),post_net=m.get('post_net'),
        cost_diff=m.get('cost_diff'),cost_change=m.get('cost_change','—'),post_status=m.get('post_quantity_status'),cost_note=m.get('post_cost_note'),
        order_id=m.get('order_id'),execution_id=m.get('broker_execution_id'),account=m['display_name'],fee_status=m.get('fee_status'),fee_source=m.get('fee_source'),link_status=m.get('link_status'),
        source=Path(m['source_path']).name if m.get('source_path') else m.get('source_kind'),action='查看',realized_pnl=None)


class TradeCenterService:
    def __init__(self,path):self.path=Path(path).resolve()

    def prepare(self,c,scope,params,cancel=None):
        repo.configure(c);params=validate(params)
        filters=TradeFilters(side=params.get('side'),link_status=params.get('link_status'),source_file=params.get('source_file',''),include_reversed=False,include_reference=False)
        raw,args=repo.base(c,scope,filters);query,args=repo.filtered(raw,args,params)
        sort=params.get('sort_col','trade_time');desc=params.get('sort_dir','desc')!='asc'
        enriched={}
        if sort in repo.DERIVED_SORTS or params.get('cost_min') is not None or params.get('cost_max') is not None or params.get('affects_only'):
            candidates=tuple(raw_trade(r) for r in c.execute(query,args))
            if candidates:
                page=TradePage(candidates,len(candidates),0,len(candidates),scope,'')
                enriched={r.record_id:r for r in attach_post_trade_positions(c,page,cancel=cancel).rows}
            def value(record,key):
                row=enriched.get(record);v=row.metadata.get(key) if row else None
                return int(v) if isinstance(v,bool) else str(v) if v is not None else None
            c.create_function('trade_projection',2,value)
            query="SELECT *,trade_projection(id,'current_price') AS current_price,trade_projection(id,'post_quantity') AS post_quantity,trade_projection(id,'post_cost') AS post_cost,trade_projection(id,'cost_diff') AS cost_diff,trade_projection(id,'affects_quantity') AS affects_quantity FROM ("+query+")"
            query,args=repo.filtered(query,args,{k:v for k,v in params.items() if k.startswith('cost_') or k=='affects_only'},derived=True)
        return query,args,repo.order_sql(sort,desc),enriched,params

    def page(self,scope,params,*,cancel=None,catalog_scope=None):
        with readonly_connection(self.path,cancel=cancel,timeout=60) as c:
            query,args,order,enriched,p=self.prepare(c,scope,params,cancel)
            page=int(p.get('page',1));size=int(p.get('size',200))
            if page<1 or not 1<=size<=1000:raise ValueError('页码或每页数量无效，最多1000条。')
            stats=repo.summary(c,query,args)
            total_pages=max(1,(stats['matched']+size-1)//size);page=min(page,total_pages)
            records=[dict(r) for r in c.execute(query+order+' LIMIT ? OFFSET ?',[*args,size,(page-1)*size])]
            rows=tuple(raw_trade(r) for r in records)
            batch=TradePage(rows,stats['matched'],page-1,size,scope,'')
            rows=tuple(enriched[r.record_id] for r in rows) if enriched else attach_post_trade_positions(c,batch,cancel=cancel).rows
            base,base_args=repo.base(c,replace(scope,search='',category=None),None)
            options=[dict(r) for r in c.execute('SELECT DISTINCT broker_code,category,kind,side,source_kind FROM ('+base+')',base_args)]
            return dict(status='ok',persons=repo.people(c,(catalog_scope or scope).person_ids),columns=COLUMNS,keys=KEYS,items=[dto(r) for r in rows],total=stats['matched'],page=page,size=size,total_pages=total_pages,stats=stats,options=options,
                        sort_col=p.get('sort_col','trade_time'),sort_dir=p.get('sort_dir','desc'),
                        note='统计覆盖全部匹配记录；人员权益操作账本不与券商真实成交账本混加，总数为操作记录数。金额和手续费按币种列示。北京时间展示，保留原账务时钟及真实UTC。历史成本按现有净投入引擎回放；微操逐笔标记与逐笔已实现盈亏没有可靠证据时不展示。',source=str(self.path))

    def detail(self,scope,params,record_id,*,restricted=False,cancel=None):
        with readonly_connection(self.path,cancel=cancel,timeout=60) as c:
            repo.configure(c)
            # Detail/related records stay inside the authorized personnel scope,
            # while list-only filters do not hide a reliably linked record.
            detail_scope=replace(scope,start_date=None,search='',category=None,account_ids=None,instrument_ids=None)
            base,args=repo.base(c,detail_scope,None)
            raw=c.execute('SELECT * FROM ('+base+') WHERE id=?',args+[int(record_id)]).fetchone()
            if raw is None:raise ValueError('记录不存在或不在授权人员范围。')
            page=TradePage((raw_trade(raw),),1,0,1,detail_scope,'')
            row=attach_post_trade_positions(c,page,cancel=cancel).rows[0];item=dto(row);m=row.metadata
            provenance=get_provenance(c,row.source_record_id,sensitive=False)
            if restricted:provenance['payload']='共享原始载荷受人员数据隔离保护，未返回全文；本人的交易字段及来源编号仍可追溯。'
            related=repo.related(c,detail_scope,m)
            query,args,order,enriched,p=self.prepare(c,scope,params,cancel)
            clause=order.removeprefix(' ORDER BY ')
            nav=c.execute('SELECT * FROM (SELECT id,lag(id) OVER (ORDER BY '+clause+') AS previous_id,lead(id) OVER (ORDER BY '+clause+') AS next_id,row_number() OVER (ORDER BY '+clause+') AS position,count(*) OVER () AS matched FROM ('+query+')) WHERE id=?',args+[int(record_id)]).fetchone()
            sections={
                '交易概览':{'交易时间':item['trade_date'],'真实成交UTC':m.get('executed_at_utc'),'时间说明':'真实UTC优先；无精确UTC时按既有渠道时钟解析，纯日期使用既定美东09:30估算，原账务时间完整保留。','原账务时间':m['book_time'],'账务日期':m['business_date'],'订单编号':m.get('order_id'),'交易记录ID':row.record_id,'人员':item['person_name'],'券商/账户':item['broker']+' / '+item['account'],'Ticker':item['symbol'],'分类':item['category'],'方向/类型':item['side']+' / '+item['kind'],'当前价':item['current_price'],'报价时间':item['quote_time'],'行情状态':item['quote_status'],'成交价':item['price'],'数量':item['quantity'],'成交金额':item['amount'],'金额口径':m.get('amount_basis'),'手续费':item['fees'],'币种':item['currency'],'记录粒度':m.get('record_granularity'),'关联说明':'订单号用于关联，交易记录ID标识本账本记录；同一订单可对应多笔成交。'},
                '成本变化':{'成交前持仓':item['pre_quantity'],'成交后持仓':item['post_quantity'],'成交前成本价':item['pre_cost'],'成交后成本价':item['post_cost'],'每股成本变化':item['cost_change'],'成本状态':item['post_status'],'计算说明':item['cost_note'],'本笔已实现盈亏':None,'已实现盈亏说明':m['realized_note']},
                '来源追溯':{'数据来源':m.get('source_kind'),'原始记录ID':row.source_record_id,'订单编号':m.get('order_id'),'成交编号':m.get('broker_execution_id'),'导入批次':provenance.get('batch_id'),'来源文件':Path(m['source_path']).name if m.get('source_path') else None,'工作表':m.get('source_sheet'),'原始行号':m.get('source_row'),'解析/审计说明':provenance.get('parse_message'),'归属关联状态':m.get('link_status'),'费用来源':m.get('fee_source'),'费用核实状态':m.get('fee_status'),'解析状态':m.get('parse_status'),'记录状态':m.get('record_state'),'账务角色':m.get('accounting_role'),'原始证据':provenance},
            }
            return dict(status='ok',item=item,sections=redact(sections),related=[dict(record_id=r['id'],source_record_id=r['source_record_id'],symbol=r['symbol'],person_name=r['person_name'],side=dto(raw_trade(r))['side'],trade_date=dto(raw_trade(r))['trade_date'],book_time=r['book_time'],order_id=r.get('order_id')) for r in related[:200] if r['id']!=row.record_id],related_truncated=len(related)>200,navigation=dict(nav) if nav else {})

    def export(self,scope,params,*,cancel=None):
        import csv,io
        from portfolio.services.export_service import safe_text
        with readonly_connection(self.path,cancel=cancel,timeout=300) as c:
            query,args,order,enriched,p=self.prepare(c,scope,params,cancel)
            stream=io.StringIO();writer=csv.writer(stream)
            extra=('currency','record_id','source_record_id','order_id','execution_id','source','quote_time','quote_status','pre_quantity','pre_cost','post_status')
            writer.writerow((*COLUMNS[:-1],*extra));yield '\ufeff'+stream.getvalue();stream.seek(0);stream.truncate(0)
            cursor=c.execute(query+order,args)
            while True:
                records=cursor.fetchmany(500)
                if not records:break
                raw=tuple(raw_trade(r) for r in records)
                batch=TradePage(raw,len(raw),0,len(raw),scope,'')
                rows=tuple(enriched[r.record_id] for r in raw) if enriched else attach_post_trade_positions(c,batch,cancel=cancel).rows
                for row in rows:
                    item=dto(row);writer.writerow([safe_text(item.get(k)) for k in (*KEYS[:-1],*extra)])
                yield stream.getvalue();stream.seek(0);stream.truncate(0)
