"""Read-only, single-owner/point audit; writes only a new private export folder."""
import argparse
import csv
import json
from collections import defaultdict
from dataclasses import replace
from datetime import date,datetime,time as clock_time,timedelta
from decimal import Decimal,localcontext
from pathlib import Path
import sqlite3

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.analytics import strict_sum
from portfolio.domain.live_quotes import UTC,NY
from portfolio.domain.models import QueryScope
from portfolio.domain.trade_time import event_time_utc
from portfolio.engine.close_baseline import previous_trading_day,intraday_closes
from portfolio.engine.replay import replay,normalize_quantity
from portfolio.paths import DATABASE_PATH,derived_path,quote_cache_path
from portfolio.services.quote_store import QuoteStore


def connect(path):
    connection=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def audit(source,quotes,day,person,stamp,channel=None):
    scope=QueryScope(day,person_ids=(person,),include_disabled=True)
    with readonly_connection(source) as connection:snapshot=load_snapshot(connection,scope)
    store=QuoteStore(quotes,source=source)
    store.supplement(snapshot,readonly=True)
    _,days,_,_=store.load(datetime.now(UTC))
    expected=previous_trading_day(type('Calendar',(),{'trading_days':snapshot.extras.get('trading_days',())})(),str(day),calendar=days)
    baselines=intraday_closes(snapshot.prices,expected)
    past=replace(snapshot,scope=replace(scope,as_of=day-timedelta(days=1)),
                 events=[e for e in snapshot.events if e.day<str(day)],
                 actions=[a for a in snapshot.actions if a['effective_date']<str(day)],
                 baselines=[b for b in snapshot.baselines if b['baseline_date']<str(day)],extras={**snapshot.extras,'_positions_only':True},issues=[])
    # No DerivedStore.active(): this one-owner holding check is in memory only.
    positions,_,_=replay(past)
    initial=defaultdict(Decimal)
    for key,position in positions.items():initial[key[1:]]+=position.quantity
    events=defaultdict(list)
    for event in snapshot.events:
        if event.day==str(day):events[event.account_id,event.instrument_id].append(event)
    connection=connect(derived_path(source))
    try:
        records=[dict(r) for r in connection.execute("SELECT * FROM intraday_contributions WHERE ledger='PERSON' AND person_id=? AND day=? ORDER BY stamp,ticker,channel",(person,str(day)))]
    finally:connection.close()
    if channel:records=[r for r in records if r['channel']==channel]
    totals=defaultdict(list)
    for row in records:totals[row['stamp']].append(Decimal(row['pnl']) if row['pnl'] is not None else None)
    complete={time:strict_sum(values) for time,values in totals.items() if all(v is not None for v in values)}
    if not complete:raise ValueError('当前范围没有完整的已存时间点；未生成替代数据。')
    peak=max(complete,key=complete.get)
    requested=stamp.astimezone(UTC).isoformat()
    chosen=sorted({requested,peak})
    session_start=datetime.combine(day-timedelta(days=1),clock_time(20),NY).astimezone(UTC)
    session_end=datetime.combine(day,clock_time(20),NY).astimezone(UTC)
    output=[]
    for row in records:
        if row['stamp'] not in chosen:continue
        identifier=row['instrument_id'];key=row['account_id'],identifier;point=datetime.fromisoformat(row['stamp'])
        opening_quantity=initial[key];price_info=baselines.get(identifier)
        quantity=opening_quantity;buys=[];sells=[];warnings=[]
        for event in events[key]:
            try:time=event_time_utc(event)
            except (ValueError,TypeError):warnings.append('当日存在无法确认时刻的流水');continue
            if not session_start<=time<session_end:warnings.append('成交时刻与账务日窗口不一致');continue
            if time>point:continue
            if event.amount is None or event.metadata.get('link_status')=='REVIEW_REQUIRED':warnings.append('成交金额或归属待确认')
            quantity+=event.quantity if event.incoming else -event.quantity
            (buys if event.incoming else sells).append(event.amount)
        quantity=normalize_quantity(quantity)
        if opening_quantity<0 or quantity<0:warnings.append('期初或当时持仓为负，不能确认收益')
        if any(a['effective_date']==str(day) and a['instrument_id']==identifier for a in snapshot.actions):warnings.append('当日公司行动需单独核对')
        bars,_=store.intraday_candles(row['ticker']+'.US',str(day))
        price=next((value for time,phase,value in reversed(bars) if time+timedelta(minutes=5)<=point),None)
        base=price_info.value if price_info else None
        opening=opening_quantity*base if base is not None else Decimal(0) if opening_quantity==0 else None
        buy,sell=strict_sum(buys),strict_sum(sells)
        corrected=quantity*price-opening-buy+sell if not warnings and price is not None and all(v is not None for v in (opening,buy,sell)) else None
        old_opening=Decimal(row['opening_value']) if row['opening_value'] is not None else None
        old_price=old_opening/opening_quantity if opening_quantity and old_opening is not None else None
        old_dates=[p.day for p in snapshot.prices if p.instrument_id==identifier and p.kind=='DAILY_CLOSE' and p.value==old_price and p.day<str(day)]
        output.append(dict(time=row['stamp'],channel=row['channel'],ticker=row['ticker'],opening_quantity=opening_quantity,
            stored_basis_day=max(old_dates,default='未知'),stored_basis_price=old_price,stored_opening_value=old_opening,
            expected_basis_day=expected,expected_basis_source=price_info.status if price_info else 'MISSING',expected_basis_price=base,
            expected_opening_value=opening,five_minute_price=price,buy=buy,sell=sell,actual_quantity=quantity,
            stored_quantity=row['quantity'],stored_pnl=row['pnl'],same_point_pnl=corrected,
            closed_today=quantity==0 and (opening_quantity!=0 or bool(events[key])),warnings='；'.join(warnings)))
    summaries=[]
    for time in chosen:
        selected=[r for r in output if r['time']==time]
        old=strict_sum(Decimal(r['stored_pnl']) if r['stored_pnl'] is not None else None for r in selected)
        new=strict_sum(r['same_point_pnl'] for r in selected)
        summaries.append(dict(time=time,rows=len(selected),stored_pnl=old,same_point_pnl=new,
                              basis_difference=old-new if old is not None and new is not None else None,
                              closed_today=sum(r['closed_today'] for r in selected)))
    return output,dict(day=str(day),person_id=person,channel=channel or '全部渠道',peak_time=peak,summaries=summaries,
                       note='存储金额用于审计旧计算，非当前有效缓存。统一前收盘口径重算只在内存进行，未回写。卡片原10秒快照未保留，不能精确重现截图卡片。')


def history_audit(source,quotes,person):
    result={f'2026-{month:02}':{} for month in range(1,7)}
    for path,sql,prefix in (
        (quotes,"SELECT substr(day,1,7),count(*),count(distinct day) FROM intraday_candles WHERE day BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY 1",'legacy_raw'),
        (source,"SELECT substr(trade_date,1,7),count(*),count(distinct trade_date) FROM history_prices WHERE price_type='FIVE_MINUTE' AND trade_date BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY 1",'fact_raw')):
        connection=connect(path)
        try:
            for month,count,dates in connection.execute(sql):result[month].update({prefix+'_rows':count,prefix+'_dates':dates})
        finally:connection.close()
    connection=connect(derived_path(source))
    try:
        args=(person,)
        for month,dates,points,price,baseline,ledger in connection.execute("SELECT substr(day,1,7),count(distinct day),count(distinct stamp),sum(market_value IS NULL),sum(opening_value IS NULL),sum(pnl IS NULL AND market_value IS NOT NULL AND opening_value IS NOT NULL) FROM intraday_contributions WHERE ledger='PERSON' AND person_id=? AND day BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY 1",args):
            result[month].update(derived_dates=dates,derived_points=points,missing_price_rows=price,missing_baseline_rows=baseline,unconfirmed_ledger_rows=ledger)
        for month,count in connection.execute("SELECT substr(day,1,7),count(*) FROM (SELECT day,stamp FROM intraday_contributions WHERE ledger='PERSON' AND person_id=? AND day BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY day,stamp HAVING max(pnl IS NULL)=0) GROUP BY 1",args):result[month]['complete_combo_points']=count
        for month,status,algorithm,count in connection.execute("SELECT substr(day,1,7),status,algorithm,count(*) FROM partitions WHERE ledger='PERSON' AND person_id=? AND day BETWEEN '2026-01-01' AND '2026-06-30' GROUP BY 1,2,3",args):result[month].setdefault('partitions',[]).append(dict(status=status,algorithm=algorithm,count=count))
    finally:connection.close()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DATABASE_PATH)
    parser.add_argument('--quotes',type=Path,default=quote_cache_path())
    parser.add_argument('--day',type=date.fromisoformat,required=True)
    parser.add_argument('--person-id',type=int,required=True)
    parser.add_argument('--stamp',type=datetime.fromisoformat,required=True)
    parser.add_argument('--channel')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.stamp.tzinfo is None:parser.error('时间必须带时区。')
    # Never overwrite an earlier private report.
    args.output.mkdir(parents=True,exist_ok=False)
    with localcontext() as context:
        context.prec=50
        rows,summary=audit(args.db,args.quotes,args.day,args.person_id,args.stamp,args.channel)
        summary['history']=history_audit(args.db,args.quotes,args.person_id)
    with (args.output/'per_stock.csv').open('x',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else []);writer.writeheader();writer.writerows(rows)
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str)+'\n')
    print(json.dumps(summary['summaries'],ensure_ascii=False,default=str))
    print(str(args.output.resolve()))


if __name__=='__main__':main()
