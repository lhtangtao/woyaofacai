"""Complete-match filtering and exact database aggregates for the trade center."""
from decimal import Decimal,localcontext
from portfolio.db.repositories.trades import _base_query,has_fee_data

REVIEW="(link_status='REVIEW_REQUIRED' OR parse_status IN ('PENDING','REVIEW_REQUIRED') OR batch_status<>'SUCCESS' OR fee_status IN ('UNMATCHED_ORDER','ORDER_QUANTITY_MISMATCH','ALLOCATION_MISMATCH'))"
SORTS={'trade_time':'trade_time','broker':'broker_code','person_name':'person_name','symbol':'symbol','category':'category','side':'side','counterparty':'counterparty','price':'price_e8','quantity':'quantity_e8','amount':'amount_e8','fees':'fees_e8','kind':'kind','order_id':'order_id','record_id':'id',
       'current_price':'current_price COLLATE EXACT_DECIMAL','post_quantity':'post_quantity COLLATE EXACT_DECIMAL','post_cost':'post_cost COLLATE EXACT_DECIMAL','post_net':'post_net COLLATE EXACT_DECIMAL','cost_diff':'cost_diff COLLATE EXACT_DECIMAL'}
DERIVED_SORTS={'current_price','post_quantity','post_cost','post_net','cost_diff'}


class ExactSum:
    def __init__(self):self.value=0
    def step(self,value):
        if value is not None:self.value+=int(value)
    def finalize(self):return str(self.value)


def configure(connection):
    connection.create_aggregate('exact_e8_sum',1,ExactSum)
    connection.create_collation('EXACT_DECIMAL',lambda a,b:(Decimal(a)>Decimal(b))-(Decimal(a)<Decimal(b)))
    connection.create_function('decimal_compare',2,lambda a,b:None if a is None else (Decimal(a)>Decimal(b))-(Decimal(a)<Decimal(b)))
    def instant(stamp,day,utc,broker):
        from types import SimpleNamespace
        from portfolio.domain.trade_time import event_time_utc
        try:return event_time_utc(SimpleNamespace(time=stamp,day=day,metadata=dict(executed_at_utc=utc,broker_code=broker))).isoformat()
        except (ValueError,TypeError):return None
    connection.create_function('trade_instant',4,instant)


def base(connection,scope,filters):
    query,args=_base_query(scope,filters,fees_available=has_fee_data(connection),all_currencies=True)
    query=query.replace("coalesce(o.legacy_order_id,'') AS order_id","coalesce(nullif(o.legacy_order_id,''),t.broker_order_id,'') AS order_id")
    if not has_fee_data(connection):query="SELECT *, 'NOT_COLLECTED' AS fee_status,'' AS fee_source FROM ("+query+")"
    return 'SELECT *,trade_instant(book_time,business_date,executed_at_utc,broker_code) AS trade_time FROM ('+query+')',args


def filtered(query,args,params,*,derived=False):
    where=[];args=list(args)
    if params.get('symbol'):
        where.append("instr(upper(symbol),upper(?))>0");args.append(params['symbol'].strip())
    if params.get('broker'):
        where.append('(broker_code=? OR display_name=?)');args.extend([params['broker']]*2)
    if params.get('kind'):where.append('kind=?');args.append(params['kind'])
    if params.get('source_kind'):where.append('source_kind=?');args.append(params['source_kind'])
    if params.get('review_only'):where.append(REVIEW)
    for name,column in (('quantity','quantity_e8'),('price','price_e8'),('amount','amount_e8')):
        for suffix,operator in (('min','>='),('max','<=')):
            value=params.get(name+'_'+suffix)
            if value not in (None,''):
                with localcontext() as ctx:
                    ctx.prec=80;scaled=Decimal(str(value))*100000000
                    # SQLite raw e8 values compare to exact integral boundaries.
                    boundary=int(scaled.to_integral_value(rounding='ROUND_CEILING' if suffix=='min' else 'ROUND_FLOOR'))
                where.append(f'{column}{operator}?');args.append(boundary)
    if derived:
        for suffix,operator in (('min','>='),('max','<=')):
            value=params.get('cost_'+suffix)
            if value not in (None,''):where.append(f'decimal_compare(cost_diff,?){operator}0');args.append(str(value))
        if params.get('affects_only'):where.append('affects_quantity=1')
    return 'SELECT * FROM ('+query+')'+(' WHERE '+' AND '.join(where) if where else ''),args


def summary(connection,query,args):
    row=connection.execute(f"SELECT count(*),sum(CASE WHEN kind='MARKET_TRADE' AND side='BUY' THEN 1 ELSE 0 END),sum(CASE WHEN kind='MARKET_TRADE' AND side='SELL' THEN 1 ELSE 0 END),sum(CASE WHEN {REVIEW} THEN 1 ELSE 0 END) FROM ({query})",args).fetchone()
    currencies=[]
    for currency,buy,sell,fee,unknown in connection.execute(f"SELECT currency,CASE WHEN sum(CASE WHEN kind='MARKET_TRADE' AND side='BUY' AND amount_e8 IS NULL THEN 1 ELSE 0 END)>0 THEN NULL ELSE exact_e8_sum(CASE WHEN kind='MARKET_TRADE' AND side='BUY' THEN amount_e8 END) END,CASE WHEN sum(CASE WHEN kind='MARKET_TRADE' AND side='SELL' AND amount_e8 IS NULL THEN 1 ELSE 0 END)>0 THEN NULL ELSE exact_e8_sum(CASE WHEN kind='MARKET_TRADE' AND side='SELL' THEN amount_e8 END) END,exact_e8_sum(fees_e8),sum(CASE WHEN fees_e8 IS NULL THEN 1 ELSE 0 END) FROM ({query}) GROUP BY currency ORDER BY currency",args):
        def money(v):
            with localcontext() as ctx:ctx.prec=50;return Decimal(v)/Decimal(100000000) if v is not None else None
        currencies.append(dict(currency=currency,buy_amount=money(buy),sell_amount=money(sell),fees=money(fee) if not unknown else None,known_fees=money(fee),unknown_fees=unknown))
    return dict(matched=row[0],buys=row[1] or 0,sells=row[2] or 0,review=row[3] or 0,currencies=currencies)


def order_sql(sort,descending):
    if sort not in SORTS:raise ValueError('不支持该排序列。')
    field=SORTS[sort];direction='DESC' if descending else 'ASC'
    return f' ORDER BY ({field} IS NULL) ASC,{field} {direction},sequence_no {direction},id {direction}'


def related(connection,scope,meta):
    query,args=base(connection,scope,None)
    clauses=['id=?'];values=[meta['id']]
    if meta.get('broker_trade_id') is not None:clauses.append('broker_trade_id=?');values.append(meta['broker_trade_id'])
    # An order is only an association within its real account/security.
    if meta.get('order_id'):clauses.append('(order_id=? AND account_id=? AND instrument_id=?)');values.extend((meta['order_id'],meta['account_id'],meta['instrument_id']))
    if meta.get('kind')=='OWNER_TRANSFER':clauses.append('(source_record_id=? AND kind=?)');values.extend((meta['source_record_id'],'OWNER_TRANSFER'))
    return [dict(r) for r in connection.execute('SELECT * FROM ('+query+') WHERE ('+' OR '.join(clauses)+') ORDER BY book_time,id LIMIT 201',args+values)]


def people(connection,ids):
    if ids is None:return [dict(r) for r in connection.execute('SELECT id,name,enabled FROM persons ORDER BY id')]
    if not ids:return []
    return [dict(r) for r in connection.execute('SELECT id,name,enabled FROM persons WHERE id IN ('+','.join('?' for _ in ids)+') ORDER BY id',ids)]
