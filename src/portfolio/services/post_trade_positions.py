"""Enrich one batch with authoritative historical traces and timestamped quotes."""
from dataclasses import replace
from datetime import datetime,time
from decimal import Decimal
from portfolio.domain.models import Ledger
from portfolio.domain.live_quotes import UTC,NY
from portfolio.services.trade_replay_context import context


def current_quotes(connection,rows):
    from portfolio.services.web_today import desktop_quote_store
    now=datetime.now(UTC);result={}
    try:cached=desktop_quote_store().load(now)[0]
    except Exception:cached={}
    instruments={(r.metadata['symbol'],r.metadata.get('market','US')) for r in rows}
    for symbol,market in instruments:
        candidates=[q for (s,_),q in cached.items() if s==symbol+'.'+market and q.timestamp<=now and q.price.is_finite() and q.price>0]
        if candidates:
            q=max(candidates,key=lambda q:q.timestamp)
            status='实时缓存' if (now-q.timestamp).total_seconds()<=180 else '延迟/旧报价'
            result[symbol,market]=(q.price,q.timestamp.isoformat(),status);continue
        raw=connection.execute("SELECT price_e8,trade_date,quoted_at_utc,price_type,price_status FROM market_prices WHERE symbol IN (?,?) AND price_e8>0 AND trade_date<=? AND price_type IN ('LATEST','DAILY_CLOSE') ORDER BY trade_date DESC,quoted_at_utc DESC LIMIT 8",(symbol,symbol+'.'+market,now.astimezone(NY).date().isoformat())).fetchall()
        valid=[]
        for value,day,stamp,kind,price_status in raw:
            try:
                instant=datetime.fromisoformat(stamp.replace('Z','+00:00')) if stamp else datetime.combine(datetime.fromisoformat(day).date(),time(16),NY)
                if instant.tzinfo is None:instant=instant.replace(tzinfo=UTC)
                if instant<=now:valid.append((instant,Decimal(value)/Decimal(100000000),kind,stamp,day,price_status))
            except (ValueError,TypeError):continue
        if valid:
            instant,value,kind,stamp,day,price_status=max(valid,key=lambda r:r[0]);result[symbol,market]=(value,stamp or day+'（缺报价时刻）',('收盘参考' if kind=='DAILY_CLOSE' else '数据库参考报价')+' / '+price_status)
        else:result[symbol,market]=(None,None,'缺少有效行情')
    return result


def attach_post_trade_positions(connection,page,*,cancel=None):
    if not page.rows:return page
    res=context(connection,page,cancel)
    positions,traces,costs,before=res[0],res[1],res[2],res[3]
    micro_tags=res[4] if len(res)>4 else {}
    prices=current_quotes(connection,page.rows);rows=[]
    for row in page.rows:
        data=row.metadata;quantity,net,cost=traces.get(row.record_id,(None,None,None));pre_qty,pre_cost=before.get(row.record_id,(None,None))
        key=(data['person_id'] if page.scope.ledger==Ledger.PERSON else None,data['account_id'],data['instrument_id'])
        bad=data.get('link_status')=='REVIEW_REQUIRED' or key in positions and '成交粒度冲突' in positions[key].warnings
        if bad or data['currency']!='USD':quantity=net=cost=pre_qty=pre_cost=None
        delta=costs.get(row.record_id) if quantity is not None and cost is not None and not bad else None
        if quantity is None:state='UNAVAILABLE';note='记录未进入有效回放、归属待核查或币种不在现有USD成本引擎范围；未知不补零。'
        elif quantity<0:state='NEGATIVE';note='该操作后为负仓，需要核对历史交易或期初持仓。'
        else:state='AVAILABLE';note='按人员、账户、证券独立回放，包含原开账基准、此前事件、拆股及划转；净投入成本沿用EXCEL_COMPAT。'
        if data['kind']!='MARKET_TRADE':change='—（非普通买卖）';delta=None
        elif pre_qty==0 and quantity is not None and quantity>0:change='建仓'
        elif delta is None:change='—'
        elif delta==0:change='不变'
        else:change=('降低' if delta<0 else '提高')+f' {abs(delta):,.4f}/股'
        price,stamp,status=prices.get((data['symbol'],data.get('market','US')),(None,None,'缺少有效行情'))
        values=list(row.values);values[9:12]=[quantity,net,cost]
        rows.append(replace(row,values=tuple(values),metadata={**data,'post_quantity':quantity,'post_net':net,'post_cost':cost,
                    'pre_quantity':pre_qty,'pre_cost':pre_cost,'post_quantity_status':state,'post_quantity_note':note,'post_cost_note':note,
                    'cost_diff':delta,'cost_change':change,'current_price':price,'quote_time':stamp,'quote_status':status,
                    'micro_tag':micro_tags.get(row.record_id,''),
                    'affects_quantity':pre_qty!=quantity if pre_qty is not None and quantity is not None else None,
                    'realized_pnl':None,'realized_note':'现有净投入引擎未提供可信的逐笔已实现盈亏；不使用成交价与成本价自行推算。'}))
    return replace(page,rows=tuple(rows))
