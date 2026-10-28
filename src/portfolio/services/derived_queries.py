"""Shared, permission-scoped historical contribution queries for Mac and Web."""
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from decimal import Decimal,localcontext
from threading import RLock

from portfolio.db.connection import readonly_connection

from portfolio.domain.analytics import strict_sum
from portfolio.domain.live_quotes import NY,UTC
from portfolio.domain.models import Ledger
from portfolio.services.derived_store import ALGORITHM,DerivedStore
from portfolio.services.derived_codec import dumps


_VALIDATED={}
_LOCK=RLock()


def allowed_people(source,scope):
    if scope.ledger==Ledger.BROKER:return (0,)
    with readonly_connection(source) as c:
        ids=tuple(r[0] for r in c.execute('SELECT id FROM persons WHERE id IN (SELECT person_id FROM person_operations UNION SELECT person_id FROM historical_baselines UNION SELECT person_id FROM cash_events UNION SELECT person_id FROM asset_adjustments)'+('' if scope.include_disabled else ' AND enabled=1')))
    return tuple(p for p in ids if scope.person_ids is None or p in scope.person_ids)


def load_history(source,quotes,scope,start,end):
    from portfolio.services.quote_store import QuoteStore
    quotes=QuoteStore(quotes.path,source=source)
    store=DerivedStore(source)
    from portfolio.services.derived_maintenance import maintenance_status
    ready=store.readiness()
    if not ready['ready']:
        status=maintenance_status(source)
        return {},{'state':'BUILDING' if status['running'] else status['state'],
                   'progress':status['progress'],'generation':status.get('generation'),
                   'message':status['progress'].get('message','尚无已发布五分钟明细，后台任务正在生成。')}
    people=allowed_people(source,scope)
    if not people:return {},{'state':'EMPTY_AUTHORIZED_SCOPE','message':'当前授权范围没有人员。'}
    ledger=scope.ledger.value
    placeholders=','.join('?' for _ in people)
    args=[ledger,*people,str(start),str(end)]
    sql=f'SELECT person_id,day,stamp,channel,ticker,pnl,account_id,instrument_id FROM intraday_contributions WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ?'
    for column,values in (('account_id',scope.account_ids),('instrument_id',scope.instrument_ids)):
        if values is not None:
            if not values:return {},{'state':'EMPTY_FILTER','message':'当前筛选为空。'}
            sql+=f" AND {column} IN ("+','.join('?' for _ in values)+')';args.extend(values)
    with closing(store.connect()) as c:
        # Metadata, owner coverage and amounts must belong to one SQLite read
        # snapshot, even while the producer commits the next owner's partition.
        c.execute('BEGIN')
        has_inputs=bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='partition_inputs'").fetchone())
        inputs={(p,d):revision for p,d,revision in c.execute(f'SELECT person_id,day,quote_revision FROM partition_inputs WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ?',(ledger,*people,str(start),str(end)))} if has_inputs else {}
        partitions=c.execute(f'SELECT person_id,day,dependency,algorithm,status,warning FROM partitions WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ? ORDER BY day',
                             (ledger,*people,str(start),str(end))).fetchall()
        records=c.execute(sql+' ORDER BY day,stamp',args).fetchall()
        coverage=c.execute(f'SELECT DISTINCT person_id,day,stamp FROM intraday_contributions WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ?',
                           (ledger,*people,str(start),str(end))).fetchall()
    valid=set();states={}
    for person,day,dependency,algorithm,status,warning in partitions:
        states[person,day]={'state':status,'warning':warning}
        if algorithm!=ALGORITHM or status in ('INVALID','ERROR'):
            states[person,day]['state']='INVALID' if algorithm!=ALGORITHM else status
            continue
        revision=quotes.day_revision(day)
        if (person,day) in inputs and inputs[person,day]==dumps(revision):
            valid.add((person,day));continue
        # Newly appended bars must not hide previously validated points. Only
        # accept the old prefix when every price used by its checkpoint matches.
        from portfolio.services.derived_codec import loads
        canonical=replace(scope,as_of=date.fromisoformat(day),person_ids=(person,) if person else None)
        from portfolio.services.derived_store import digest
        checkpoint=store.get('intraday-state',digest((canonical.ledger,canonical.person_ids,canonical.as_of)))
        old=loads(inputs[person,day]) if (person,day) in inputs else None
        if checkpoint and old and old[2:]==revision[2:] and checkpoint['price_hash']==quotes.prefix_identity(day,checkpoint['last'],checkpoint['price_ids']):
            valid.add((person,day))
        else:states[person,day]={'state':'INVALID','warning':'依赖已变化或旧计算尚未校验，等待后台重算；不在图表中回放。'}
    by_day=defaultdict(lambda:defaultdict(list))
    owners_at=defaultdict(set)
    for person,day,stamp in coverage:
        if (person,day) in valid:owners_at[day,stamp].add(person)
    for person,day,stamp,channel,ticker,pnl,account,instrument in records:
        if (person,day) not in valid:continue
        by_day[day][stamp].append({'person_id':person,'channel':channel,'ticker':ticker,'pnl':pnl,'account_id':account,'instrument_id':instrument})
    if scope.category or scope.search:
        with readonly_connection(source) as c:categories={r['id']:r['category'] for r in c.execute('SELECT id,category FROM instruments')}
        for day,stamps in by_day.items():
            for stamp,items in stamps.items():
                stamps[stamp]=[r for r in items if (not scope.category or categories.get(r['instrument_id'])==scope.category)
                               and (not scope.search or scope.search.casefold() in (r['ticker']+' '+r['channel']).casefold())]
    # A missing owner partition invalidates the aggregate, never silently omit it.
    valid_days={d for _,d in valid if all((p,d) in valid for p in people)}
    output={};incomplete_points=0
    for day,stamps in by_day.items():
        if any((p,day) not in valid for p in people):continue
        required={p for p in people if states[p,day]['state']!='NO_EXPOSURE'}
        output[day]=[]
        for stamp,items in sorted(stamps.items()):
            if not required<=owners_at[day,stamp]:
                incomplete_points+=1
                continue
            output[day].append((datetime.fromisoformat(stamp),items))
    invalid=sum(v['state']=='INVALID' for v in states.values())
    errors=sum(v['state']=='ERROR' for v in states.values())
    partial=sum(v['state']=='PARTIAL' for v in states.values())
    now=datetime.now(UTC)
    _,calendar,_,_=quotes.load(now)
    expected={d.isoformat() for d in calendar if str(start)<=d.isoformat()<=str(end) and datetime.combine(d-timedelta(days=1),time(20),NY)<=now}
    missing_days={d for d in expected if any((p,d) not in states for p in people)}
    overall='INVALID' if invalid else 'PARTIAL' if missing_days or errors or partial or incomplete_points else 'AVAILABLE' if partitions else 'NOT_GENERATED'
    maintenance=None

    status=maintenance_status(source)
    return output,{'state':overall,'progress':status['progress'],'generation':status.get('generation'),
                   'error_partitions':errors,'partial_partitions':partial,'incomplete_points':incomplete_points,
                   'trading_days':sorted({d.isoformat() for d in calendar}|{d for _,d in states}),'maintenance':maintenance,'not_generated_days':sorted(missing_days),'valid_days':sorted(valid_days),'invalid_partitions':invalid,'partitions':{f'{p}:{d}':v for (p,d),v in states.items()},
                   'message':('受影响分区正在后台增量生成，其他有效日期正常展示。' if invalid or missing_days else f'有效历史数据已加载；计算失败分区 {errors}，缺失/提示分区 {partial}，人员尚未齐备的时间点 {incomplete_points}；对应区段留空。')}


def historical_analytics(source,scope,start,end):
    """Read persisted daily baselines; never call AnalyticsService/replay here."""
    from portfolio.services.derived_codec import loads
    from portfolio.domain.analytics import Report,ReportRow
    from portfolio.engine.performance import DAILY_COLUMNS
    from types import SimpleNamespace
    store=DerivedStore(source);people=allowed_people(source,scope);rows=[];incomplete=False;baseline_end=str(end)
    from portfolio.paths import quote_cache_path
    from portfolio.services.quote_store import QuoteStore
    quote_store=QuoteStore(quote_cache_path(),source=source)
    current_quotes={}
    if quote_store.path.is_file():
        with closing(quote_store.read_connection()) as q:
            if q.execute("SELECT 1 FROM sqlite_master WHERE name='quote_close_revisions'").fetchone():
                current_quotes=dict(q.execute('SELECT day,revision FROM quote_close_revisions'))
    if not store.available or not people:return SimpleNamespace(scope=scope,reports={'每日持仓收益':Report('每日持仓收益',DAILY_COLUMNS,())})
    with closing(store.connect()) as c:
        for person in people:
            stamp=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_checked:{scope.ledger.value}:{person}',)).fetchone()
            algorithm=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_algorithm:{scope.ledger.value}:{person}',)).fetchone()
            quote_row=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_quotes:{scope.ledger.value}:{person}',)).fetchone()
            recorded=__import__('json').loads(quote_row[0]) if quote_row else {}
            changed_quotes=[d for d in recorded.keys()|current_quotes.keys() if recorded.get(d)!=current_quotes.get(d)]
            if not stamp or not algorithm or algorithm[0]!=ALGORITHM:incomplete=True
            impacts=[]
            if stamp:
                impacts=[day for day,p in c.execute("SELECT day,person_id FROM invalidations WHERE detected_at>? AND table_name<>'history_prices'",(stamp[0],))
                         if p in (None,person) and (day is None or not day.startswith('~'))]
            cutoffs=[day for day in impacts+changed_quotes if day is not None]
            if None in impacts:incomplete=True
            if cutoffs:baseline_end=min(baseline_end,(date.fromisoformat(min(cutoffs))-timedelta(days=1)).isoformat())
            for account,instrument,payload in c.execute("SELECT account_id,instrument_id,payload FROM report_details WHERE ledger=? AND person_id=? AND report='每日持仓收益' AND period BETWEEN ? AND ? ORDER BY period,ordinal",(scope.ledger.value,person,str(start),str(end))):
                if scope.account_ids is not None and account not in scope.account_ids:continue
                if scope.instrument_ids is not None and instrument not in scope.instrument_ids:continue
                row=loads(payload)
                if scope.category and row.values[2]!=scope.category:continue
                if scope.search and scope.search.casefold() not in (str(row.values[3])+' '+str(row.values[1])).casefold():continue
                valid=bool(stamp) and bool(algorithm) and algorithm[0]==ALGORITHM and not any(day is None or day<=row.values[0] for day in impacts) and not any(day<=row.values[0] for day in changed_quotes)
                values=row.values if valid else (*row.values[:19],None,*row.values[20:])
                rows.append(ReportRow(values,{**row.metadata,'person_id':person,'baseline_valid':valid}))
    return SimpleNamespace(scope=scope,reports={'每日持仓收益':Report('每日持仓收益',DAILY_COLUMNS,tuple(rows))},incomplete_people=incomplete,baseline_end=None if incomplete else baseline_end)


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
    observed={date.fromisoformat(d) for d in state.get('trading_days',())}
    calendar_days=sorted(d for d in days|observed if first<=d<=last)
    if not calendar_days:return Chart(0,'历史走势','line',(Series('累计盈亏',()),),'缺少真实交易所日历，未猜测交易日。'),state
    daily=defaultdict(list)
    for row in analytics.reports['每日持仓收益'].rows:
        v=row.values
        if str(first)<=v[0]<=str(last) and (channels is None or v[1] in channels) and (not ticker or ticker.upper() in v[3].upper()):daily[v[0]].append(v[19])
    points=[];ticks=[];labels={};boundaries=[];running=Decimal(0) if calendar_days[0]<=first+timedelta(days=7) else None;missing=[]
    if running is None:missing.append('期间起始交易日历/累计基准未确认')
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
                    selected=[x for x in samples[index][1] if (channels is None or x['channel'] in channels) and (not ticker or ticker.upper() in x['ticker'].upper())]
                    gain=strict_sum(Decimal(x['pnl']) if x['pnl'] is not None else None for x in selected) if selected else None
                    value=baseline+gain if baseline is not None and gain is not None else None
                points.append((stamp.isoformat(),value));stamp+=step
            # Daily financial rows still advance the annual baseline when 5-min
            # prices are missing; an unknown daily gain breaks later cumulative data.
            if close<now:
                filtered_exposure=any((channels is None or x['channel'] in channels) and (not ticker or ticker.upper() in x['ticker'].upper()) for _,items in samples for x in items)
                verified_no_exposure=bool(getattr(analytics,'baseline_end',None)) and day<=analytics.baseline_end and not filtered_exposure
                gain=None if getattr(analytics,'incomplete_people',True) else strict_sum(daily[day]) if day in daily else Decimal(0) if verified_no_exposure else None
                if gain is None:missing.append(day)
                running=running+gain if running is not None and gain is not None else None
            if not samples and start<=now:missing.append(day+'（无有效五分钟派生数据）')
    note='五分钟共享明细；月度30分钟/年度2小时采样。正式/原口径日收益参与跨日累计；缺失日内数据留空，不平滑、不补零。'
    if state.get('progress',{}).get('calendar_warnings'):note+=' 日历覆盖元数据不足，使用已保存实际日期，未猜测缺失交易日。'
    if missing:note+=' 缺失或未生成：'+', '.join(missing[:12])+('…' if len(missing)>12 else '')
    chart=Chart(0,f'{period} '+('累计盈亏走势' if mode=='cumulative' else '每日日内盈亏走势'),'line',
                (Series('累计盈亏' if mode=='cumulative' else '当日盈亏',tuple(points)),),note,
                x_labels=labels,x_ticks=tuple(ticks),vertical_lines=tuple(boundaries),highlight_extremes=True)
    return chart,state
