"""Shared, permission-scoped historical contribution queries for Mac and Web."""
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from decimal import Decimal,localcontext
from threading import RLock

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.analytics import strict_sum
from portfolio.domain.live_quotes import NY,UTC
from portfolio.domain.models import Ledger
from portfolio.services.derived_store import ALGORITHM,DerivedStore
from portfolio.services.derived_codec import dumps
from portfolio.services.derived_dependencies import check_changes,partition_dependency,source_identity

_VALIDATED={}
_LOCK=RLock()


def allowed_people(source,scope):
    if scope.ledger==Ledger.BROKER:return (0,)
    with readonly_connection(source) as c:
        ids=tuple(r[0] for r in c.execute('SELECT id FROM persons WHERE id IN (SELECT person_id FROM person_operations UNION SELECT person_id FROM historical_baselines UNION SELECT person_id FROM cash_events UNION SELECT person_id FROM asset_adjustments)'+('' if scope.include_disabled else ' AND enabled=1')))
    return tuple(p for p in ids if scope.person_ids is None or p in scope.person_ids)


def load_history(source,quotes,scope,start,end):
    store=DerivedStore(source)
    if not store.available:return {},{'state':'NOT_INITIALIZED','message':'历史派生库尚未初始化，请执行初始化及全人员生成。'}
    check_changes(source,store)
    people=allowed_people(source,scope)
    if not people:return {},{'state':'EMPTY_AUTHORIZED_SCOPE','message':'当前授权范围没有人员。'}
    ledger=scope.ledger.value
    placeholders=','.join('?' for _ in people)
    with closing(store.connect()) as c:
        has_inputs=bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='partition_inputs'").fetchone())
        inputs={(p,d):revision for p,d,revision in c.execute(f'SELECT person_id,day,quote_revision FROM partition_inputs WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ?',(ledger,*people,str(start),str(end)))} if has_inputs else {}
        partitions=c.execute(f'SELECT person_id,day,dependency,algorithm,status,warning FROM partitions WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ? ORDER BY day',
                             (ledger,*people,str(start),str(end))).fetchall()
    valid=set();states={};source_revision=source_identity(source)
    # Load one authoritative ledger snapshot per owner, then slice by day.
    snapshots={}
    for person,day,dependency,algorithm,status,warning in partitions:
        states[person,day]={'state':status,'warning':warning}
        if algorithm!=ALGORITHM:
            states[person,day]={'state':'INVALID','warning':'财务算法版本变化，需重算。'}
            continue
        if status in ('INVALID','ERROR'):continue
        canonical=replace(scope,as_of=date.fromisoformat(day),person_ids=(person,) if person else None,
                          account_ids=None,instrument_ids=None,category=None,search='',start_date=None,include_disabled=True,reporting_currency='USD')
        revision=quotes.day_revision(day)
        if (person,day) in inputs:
            if inputs[person,day]==dumps(revision):valid.add((person,day))
            else:states[person,day]={'state':'INVALID','warning':'历史行情或交易日历依赖变化，需增量重算。'}
            continue
        cache_key=(str(store.source),person,ledger,day,dependency,source_revision,revision,ALGORITHM)
        with _LOCK:known=_VALIDATED.get(cache_key)
        if known is None:
            if person not in snapshots:
                full=replace(canonical,as_of=date.fromisoformat(str(end)))
                with readonly_connection(source) as c:snapshots[person]=load_snapshot(c,full)
                quotes.supplement(snapshots[person])
            base=snapshots[person]
            sub=replace(base,scope=canonical,events=[e for e in base.events if e.day<=day],
                        actions=[a for a in base.actions if a['effective_date']<=day],baselines=[b for b in base.baselines if b['baseline_date']<=day],
                        prices=[p for p in base.prices if p.day<=day],issues=[])
            known=partition_dependency(source,quotes,canonical,day,snapshot=sub)==dependency
            with _LOCK:
                if len(_VALIDATED)>10000:_VALIDATED.clear()
                _VALIDATED[cache_key]=known
        if known:valid.add((person,day))
        else:states[person,day]={'state':'INVALID','warning':'原始数据或行情发生变化，需增量重算。'}
    by_day=defaultdict(lambda:defaultdict(list))
    with closing(store.connect()) as c:
        sql=f'SELECT person_id,day,stamp,channel,ticker,pnl,account_id,instrument_id FROM intraday_contributions WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ?'
        args=[ledger,*people,str(start),str(end)]
        for column,values in (('account_id',scope.account_ids),('instrument_id',scope.instrument_ids)):
            if values is not None:
                if not values:return {},{'state':'EMPTY_FILTER','message':'当前筛选为空。'}
                sql+=f" AND {column} IN ("+','.join('?' for _ in values)+')';args.extend(values)
        for person,day,stamp,channel,ticker,pnl,account,instrument in c.execute(sql+' ORDER BY day,stamp',args):
            if (person,day) not in valid:continue
            by_day[day][stamp].append({'person_id':person,'channel':channel,'ticker':ticker,'pnl':pnl,'account_id':account,'instrument_id':instrument})
    # Category/search are key filters in the original repository; use the same
    # key selection before reading contributions, including all opening history.
    if scope.category or scope.search:
        from portfolio.services.derived_engine import units
        with readonly_connection(source) as c:filtered=load_snapshot(c,replace(scope,as_of=date.fromisoformat(str(end))))
        permitted=set(units(filtered))
        for day,stamps in by_day.items():
            for stamp,items in stamps.items():
                stamps[stamp]=[r for r in items if ((r['person_id'] or None),r['account_id'],r['instrument_id']) in permitted]
    # A missing owner partition invalidates the aggregate, never silently omit it.
    valid_days={d for _,d in valid if all((p,d) in valid for p in people)}
    output={}
    for day,stamps in by_day.items():
        if any((p,day) not in valid for p in people):continue
        output[day]=[(datetime.fromisoformat(stamp),items) for stamp,items in sorted(stamps.items())]
    invalid=sum(v['state']=='INVALID' for v in states.values())
    now=datetime.now(UTC)
    _,calendar,_,_=quotes.load(now)
    expected={d.isoformat() for d in calendar if str(start)<=d.isoformat()<=str(end) and datetime.combine(d-timedelta(days=1),time(20),NY)<=now}
    missing_days={d for d in expected if any((p,d) not in states for p in people)}
    overall='INVALID' if invalid else 'PARTIAL' if missing_days else 'AVAILABLE' if partitions else 'NOT_GENERATED'
    maintenance=None
    if invalid or missing_days:
        from portfolio.services.derived_maintenance import schedule_changed
        maintenance=schedule_changed(source,start=date.fromisoformat(str(start)))
    return output,{'state':overall,'maintenance':maintenance,'not_generated_days':sorted(missing_days),'valid_days':sorted(valid_days),'invalid_partitions':invalid,'partitions':{f'{p}:{d}':v for (p,d),v in states.items()},
                   'message':('历史派生分区已失效，请执行增量重算。' if invalid else '历史派生数据尚未生成，请执行全人员预计算。' if not partitions else '使用共享历史派生数据；缺失区段留空，未确认价格保留原口径状态。')}


def day_history(source,quotes,scope,day):
    rows,status=load_history(source,quotes,scope,day,day)
    return tuple((stamp,'History5',items) for stamp,items in rows.get(day,())),status


def trend(source,quotes,analytics,period,*,year=False,channels=None,ticker=None,mode='cumulative',now=None,history_data=None):
    """No invented intraday points and no smoothing to a different daily result."""
    from portfolio.services.charts_service import Chart,Series
    now=now or datetime.now(UTC)
    if year:
        first=date(int(period),1,1);last=date(int(period),12,31);step=timedelta(hours=2)
    else:
        import calendar
        y,m=map(int,period.split('-'));first=date(y,m,1);last=date(y,m,calendar.monthrange(y,m)[1]);step=timedelta(minutes=30)
    by_day,state=history_data if history_data is not None else load_history(source,quotes,analytics.scope,first,last)
    _,days,halves,_=quotes.load(now)
    calendar_days=sorted(d for d in days if first<=d<=last)
    if not calendar_days:return Chart(0,'历史走势','line',(Series('累计盈亏',()),),'缺少真实交易所日历，未猜测交易日。'),state
    daily=defaultdict(list)
    for row in analytics.reports['每日持仓收益'].rows:
        v=row.values
        if str(first)<=v[0]<=str(last) and (channels is None or v[1] in channels) and (not ticker or v[3].upper()==ticker.upper()):daily[v[0]].append(v[19])
    points=[];ticks=[];labels={};boundaries=[];running=Decimal(0);missing=[]
    with localcontext() as ctx:
        ctx.prec=50
        for d in calendar_days:
            day=d.isoformat();start=datetime.combine(d-timedelta(days=1),time(20),NY).astimezone(UTC)
            end=datetime.combine(d,time(20),NY).astimezone(UTC)
            close=datetime.combine(d,time(13 if d in halves else 16),NY).astimezone(UTC)
            ticks.append(start.isoformat());labels[start.isoformat()]=start.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).strftime('%m-%d\n%H:%M')
            for h,m,label in ((4,0,'盘前'),(9,30,'盘中'),(13 if d in halves else 16,0,'盘后')):
                boundaries.append((datetime.combine(d,time(h,m),NY).astimezone(UTC).isoformat(),label,'dot'))
            samples=by_day.get(day,[]);index=-1;stamp=start
            baseline=running if mode=='cumulative' else Decimal(0)
            while stamp<end:
                while index+1<len(samples) and samples[index+1][0]<=stamp:index+=1
                value=None
                # At most one 5-minute interval; never stretch a sample across a gap.
                if index>=0 and timedelta(0)<=stamp-samples[index][0]<timedelta(minutes=5) and stamp<=now:
                    selected=[x for x in samples[index][1] if (channels is None or x['channel'] in channels) and (not ticker or x['ticker'].upper()==ticker.upper())]
                    gain=strict_sum(Decimal(x['pnl']) if x['pnl'] is not None else None for x in selected) if selected else None
                    value=baseline+gain if baseline is not None and gain is not None else None
                points.append((stamp.isoformat(),value));stamp+=step
            # Daily financial rows still advance the annual baseline when 5-min
            # prices are missing; an unknown daily gain breaks later cumulative data.
            if close<now:
                filtered_exposure=any((channels is None or x['channel'] in channels) and (not ticker or x['ticker'].upper()==ticker.upper()) for _,items in samples for x in items)
                verified_no_exposure=day in state.get('valid_days',()) and not filtered_exposure
                gain=strict_sum(daily[day]) if day in daily else Decimal(0) if verified_no_exposure else None
                if gain is None:missing.append(day)
                running=running+gain if running is not None and gain is not None else None
            if not samples and start<=now:missing.append(day+'（无有效五分钟派生数据）')
    note='五分钟共享明细；月度30分钟/年度2小时采样。正式/原口径日收益参与跨日累计；缺失日内数据留空，不平滑、不补零。'
    if missing:note+=' 缺失或未生成：'+', '.join(missing[:12])+('…' if len(missing)>12 else '')
    chart=Chart(0,f'{period} '+('累计盈亏走势' if mode=='cumulative' else '每日日内盈亏走势'),'line',
                (Series('累计盈亏' if mode=='cumulative' else '当日盈亏',tuple(points)),),note,
                x_labels=labels,x_ticks=tuple(ticks),vertical_lines=tuple(boundaries),highlight_extremes=True)
    return chart,state
