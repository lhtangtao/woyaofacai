"""Shared, permission-scoped historical contribution queries for Mac and Web."""
from collections import defaultdict, OrderedDict
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from decimal import Context,Decimal,localcontext
from threading import RLock

from portfolio.db.connection import readonly_connection

from portfolio.domain.analytics import strict_sum
from portfolio.domain.live_quotes import NY,UTC
from portfolio.domain.models import Ledger
from portfolio.services.derived_store import ALGORITHM,DerivedStore
from portfolio.services.derived_codec import dumps


_TREND_ROWS=OrderedDict()
_LOCK=RLock()


def allowed_people(source,scope):
    if scope.ledger==Ledger.BROKER:return (0,)
    with readonly_connection(source) as c:
        ids=tuple(r[0] for r in c.execute('SELECT id FROM persons WHERE id IN (SELECT person_id FROM person_operations UNION SELECT person_id FROM historical_baselines UNION SELECT person_id FROM cash_events UNION SELECT person_id FROM asset_adjustments)'+('' if scope.include_disabled else ' AND enabled=1')))
    return tuple(p for p in ids if scope.person_ids is None or p in scope.person_ids)


def load_history(source,quotes,scope,start,end,*,aggregate=False,channels=None,ticker=None,progress=None):
    from portfolio.services.quote_store import QuoteStore
    quotes=QuoteStore(quotes.path,source=source)
    store=DerivedStore(source)
    from portfolio.services.derived_maintenance import maintenance_status
    from portfolio.services.intraday_pipeline import pipeline_status
    ready=store.readiness()
    if not ready['ready']:
        status=maintenance_status(source)
        live=pipeline_status(source)
        return {},{'state':'BUILDING' if status['running'] or live.get('running') else status['state'],
                   'progress':status['progress'],'generation':status.get('generation'),
                   'message':status['progress'].get('message','历史数据正在计算。') if status['running'] else live['message'],'pipeline':live}
    people=allowed_people(source,scope)
    if not people:return {},{'state':'EMPTY_AUTHORIZED_SCOPE','message':'当前授权范围没有人员。'}
    ledger=scope.ledger.value
    placeholders=','.join('?' for _ in people)
    with closing(store.connect()) as c:
        # Metadata, owner coverage and amounts must belong to one SQLite read
        # snapshot, even while the producer commits the next owner's partition.
        c.execute('BEGIN')
        has_inputs=bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='partition_inputs'").fetchone())
        inputs={(p,d):revision for p,d,revision in c.execute(f'SELECT person_id,day,quote_revision FROM partition_inputs WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ?',(ledger,*people,str(start),str(end)))} if has_inputs else {}
        partitions=c.execute(f'SELECT person_id,day,dependency,algorithm,status,warning,updated_at FROM partitions WHERE ledger=? AND person_id IN ({placeholders}) AND day BETWEEN ? AND ? ORDER BY day',
                             (ledger,*people,str(start),str(end))).fetchall()
    valid=set();states={};markers={}
    revisions=quotes.day_revisions(d for _,d,_,_,_,_,_ in partitions)
    for partition_index,(person,day,dependency,algorithm,status,warning,updated_at) in enumerate(partitions):
        if progress:progress('校验历史分区',partition_index,len(partitions))
        markers[person,day]=updated_at
        states[person,day]={'state':status,'warning':warning}
        if not algorithm.startswith('money3-derived-v1:') or status in ('INVALID','ERROR'):
            states[person,day]['state']='INVALID' if not algorithm.startswith('money3-derived-v1:') else status
            states[person,day]['reason']='D_ALGORITHM_VERSION' if not algorithm.startswith('money3-derived-v1:') else 'C_CALCULATION_FAILED' if status=='ERROR' else 'D_DEPENDENCY_INVALIDATED'
            continue
        revision=revisions[day]
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
        else:states[person,day]={'state':'INVALID','reason':'D_QUOTE_DEPENDENCY','warning':'依赖已变化或旧计算尚未校验，等待后台重算；不在图表中回放。'}
    if progress:progress('校验历史分区',len(partitions),len(partitions))
    # Reject incomplete owner sets before touching the much larger amount table.
    valid_days={d for _,d in valid if all((p,d) in valid for p in people)}
    ready=[(p,d,markers[p,d],sum(states[owner,d]['state']!='NO_EXPOSURE' for owner in people))
           for p,d in sorted(valid) if d in valid_days]
    cache_key=(str(store.path),store.path.stat().st_ino,scope,start,end,tuple(sorted(channels or ())),ticker or '',
               tuple(ready),tuple(sorted((d,dumps(r)) for d,r in revisions.items())))
    cached=None
    if aggregate:
        with _LOCK:
            cached=_TREND_ROWS.get(cache_key)
            if cached is not None:_TREND_ROWS.move_to_end(cache_key)
    if cached is not None:
        output,incomplete_points=cached
        if progress:progress('复用已读取五分钟数据',1,1)
    else:
        output,incomplete_points=_read_points(store,source,scope,ready,aggregate,channels,ticker,progress=progress)
        if aggregate:
            with _LOCK:
                _TREND_ROWS[cache_key]=(output,incomplete_points)
                while len(_TREND_ROWS)>4:_TREND_ROWS.popitem(last=False)
    invalid=sum(v['state']=='INVALID' for v in states.values())
    errors=sum(v['state']=='ERROR' for v in states.values())
    partial=sum(v['state']=='PARTIAL' for v in states.values())
    now=datetime.now(UTC)
    _,calendar,halves,_=quotes.load(now)
    expected={d.isoformat() for d in calendar if str(start)<=d.isoformat()<=str(end) and datetime.combine(d-timedelta(days=1),time(20),NY)<=now}
    missing_days={d for d in expected if any((p,d) not in states for p in people)}
    months={}
    for day in sorted({d for _,d in states}|expected):
        summary=months.setdefault(day[:7],{'saved_days':0,'readable_points':0,'complete_points':0,'not_generated_days':0,'invalid_days':0})
        if any((p,day) in states for p in people):summary['saved_days']+=1
        if day in missing_days:summary['not_generated_days']+=1
        if any(states.get((p,day),{}).get('state') in ('INVALID','ERROR') for p in people):summary['invalid_days']+=1
        samples=output.get(day,())
        summary['readable_points']+=len(samples)
        summary['complete_points']+=sum(bool(items) and all(item['pnl'] is not None for item in items) for _,items in samples)
    overall='INVALID' if invalid else 'PARTIAL' if missing_days or errors or partial or incomplete_points else 'AVAILABLE' if partitions else 'NOT_GENERATED'
    maintenance=None

    status=maintenance_status(source)
    live=pipeline_status(source)
    from portfolio.domain.live_quotes import market_trade_day
    current=market_trade_day(now,calendar,halves).isoformat()
    current_relevant=str(start)<=current<=str(end)
    if invalid or missing_days:
        if current_relevant:message=live['message']
        elif status['running']:message=status['progress'].get('message','历史分区正在计算。')
        else:message='该范围存在未生成或失效分区，当前没有历史重算任务；不会把失效金额展示为有效结果。'
    else:
        message=f'已读取有效历史数据；计算失败分区 {errors}，缺失/提示分区 {partial}，未齐备时间点 {incomplete_points}。'
        warnings=list(dict.fromkeys(v.get('warning','') for v in states.values() if v.get('warning')))
        if warnings:message+=' '+ '；'.join(warnings[:2])
        if current_relevant and live.get('state') not in ('READY','NO_HOLDINGS'):message+=' '+live['message']
    return output,{'state':overall,'progress':status['progress'],'generation':status.get('generation'),
                   'error_partitions':errors,'partial_partitions':partial,'incomplete_points':incomplete_points,'months':months,'aggregated':aggregate,'query_cache_hit':cached is not None,
                   'trading_days':sorted({d.isoformat() for d in calendar}|{d for _,d in states}),'maintenance':maintenance,'not_generated_days':sorted(missing_days),'valid_days':sorted(valid_days),'invalid_partitions':invalid,'partitions':{f'{p}:{d}':v for (p,d),v in states.items()},'blocked_owners':{d:[p for p in people if (p,d) not in valid] for d in sorted({d for _,d in states}) if d not in valid_days},
                   'pipeline':live,'message':message}


class _DecimalTotal:
    def __init__(self):self.total=Decimal(0);self.unknown=False;self.context=Context(prec=50)
    def step(self,value):
        if value is None:self.unknown=True
        else:
            self.total=self.context.add(self.total,Decimal(value))
    def finalize(self):return None if self.unknown else str(self.total)


def _read_points(store,source,scope,ready,aggregate,channels,ticker,progress=None):
    if not ready:return {},0
    if aggregate and channels is None and not ticker and not scope.search and not scope.category and scope.account_ids is None and scope.instrument_ids is None:
        from portfolio.services.derived_continuity import cached_history
        persisted=cached_history(store,scope,ready,progress=progress)
        if persisted is not None:return persisted
    predicates=[];filters=[]
    for column,values in (('account_id',scope.account_ids),('instrument_id',scope.instrument_ids),('channel',channels)):
        if values is not None:
            predicates.append('0' if not values else f"c.{column} IN ("+','.join('?' for _ in values)+')')
            filters.extend(values)
    if ticker:predicates.append('instr(upper(c.ticker),upper(?))>0');filters.append(ticker.strip())
    if scope.search:predicates.append("instr(lower(c.ticker||' '||c.channel),?)>0");filters.append(scope.search.casefold())
    if scope.category:
        with readonly_connection(source) as src:ids=[r[0] for r in src.execute('SELECT id FROM instruments WHERE category=?',(scope.category,))]
        predicates.append('0' if not ids else 'c.instrument_id IN ('+','.join('?' for _ in ids)+')');filters.extend(ids)
    matches=' AND '.join(predicates) or '1'
    values=','.join('(?,?,?,?)' for _ in ready)
    sql=f"""WITH ready(person_id,day,updated_at,required) AS (VALUES {values}), base AS (
        SELECT c.*,r.required,CASE WHEN {matches} THEN 1 ELSE 0 END matched
        FROM ready r JOIN partitions p ON p.ledger=? AND p.person_id=r.person_id AND p.day=r.day
        JOIN intraday_contributions c INDEXED BY ix_intraday_person_day ON c.ledger=p.ledger AND c.person_id=p.person_id AND c.day=p.day
        WHERE p.updated_at=r.updated_at AND p.algorithm LIKE 'money3-derived-v1:%' AND p.status NOT IN ('INVALID','ERROR')) """
    args=[v for row in ready for v in row]+filters+[scope.ledger.value]
    output=defaultdict(list);incomplete=0
    read_days=set();total_days=len({d for _,d,_,_ in ready})
    if progress:progress('查询五分钟明细',None,None)
    with closing(store.connect()) as connection:
        connection.execute('BEGIN')
        if aggregate:
            connection.create_aggregate('decimal_total',1,_DecimalTotal)
            query=sql+"""SELECT day,stamp,CASE WHEN sum(matched)>0 THEN decimal_total(CASE WHEN matched THEN pnl ELSE '0' END) ELSE NULL END,
                    count(DISTINCT person_id),max(required),sum(matched) FROM base GROUP BY day,stamp ORDER BY day,stamp"""
            for day,stamp,pnl,owners,required,count in connection.execute(query,args):
                if day not in read_days:
                    if progress:progress('读取五分钟明细（按交易日）',len(read_days),total_days)
                    read_days.add(day)
                if owners<required:incomplete+=1;continue
                items=({'pnl':pnl,'channel':'','ticker':''},) if count else ()
                output[day].append((datetime.fromisoformat(stamp),items))
        else:
            by_stamp=defaultdict(list);owners_at=defaultdict(set);requirements={}
            for row in connection.execute(sql+'SELECT person_id,day,stamp,channel,ticker,pnl,account_id,instrument_id,quantity,required,matched FROM base ORDER BY day,stamp',args):
                person,day,stamp,channel,symbol,pnl,account,instrument,quantity,required,matched=row
                if day not in read_days:
                    if progress:progress('读取五分钟明细（按交易日）',len(read_days),total_days)
                    read_days.add(day)
                owners_at[day,stamp].add(person);requirements[day,stamp]=required
                if matched:by_stamp[day,stamp].append(dict(person_id=person,channel=channel,ticker=symbol,pnl=pnl,account_id=account,instrument_id=instrument,quantity=quantity))
            for day,stamp in sorted(owners_at):
                if len(owners_at[day,stamp])<requirements[day,stamp]:incomplete+=1;continue
                output[day].append((datetime.fromisoformat(stamp),by_stamp[day,stamp]))
    if progress:progress('读取五分钟明细（按交易日）',total_days,total_days)
    return dict(output),incomplete


def historical_analytics(source,scope,start,end,*,progress=None):
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
        # Count and stream the same snapshot while background reports publish.
        c.execute('BEGIN')
        total_rows=sum(c.execute("SELECT count(*) FROM report_details WHERE ledger=? AND person_id=? AND report='每日持仓收益' AND period BETWEEN ? AND ?",(scope.ledger.value,p,str(start),str(end))).fetchone()[0] for p in people) if progress else 0
        read_rows=0
        if progress:progress('读取历史日报基准',0,total_rows)
        for person in people:
            stamp=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_checked:{scope.ledger.value}:{person}',)).fetchone()
            algorithm=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_algorithm:{scope.ledger.value}:{person}',)).fetchone()
            quote_row=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_quotes:{scope.ledger.value}:{person}',)).fetchone()
            recorded=__import__('json').loads(quote_row[0]) if quote_row else {}
            through=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_through:{scope.ledger.value}:{person}',)).fetchone()
            through=through[0] if through else str(end)
            # Today's changing snapshots cannot invalidate yesterday's frozen reports.
            changed_quotes=[d for d in recorded.keys()|current_quotes.keys() if d<=through and recorded.get(d)!=current_quotes.get(d)]
            baseline_end=min(baseline_end,through)
            if not stamp or not algorithm or not algorithm[0].startswith('money3-derived-v1:'):incomplete=True
            impacts=[]
            if stamp:
                impacts=[day for day,p in c.execute("SELECT day,person_id FROM invalidations WHERE detected_at>? AND table_name<>'history_prices'",(stamp[0],))
                         if p in (None,person) and (day is None or not day.startswith('~'))]
            cutoffs=[day for day in impacts+changed_quotes if day is not None]
            if None in impacts:incomplete=True
            if cutoffs:baseline_end=min(baseline_end,(date.fromisoformat(min(cutoffs))-timedelta(days=1)).isoformat())
            for account,instrument,payload in c.execute("SELECT account_id,instrument_id,payload FROM report_details WHERE ledger=? AND person_id=? AND report='每日持仓收益' AND period BETWEEN ? AND ? ORDER BY period,ordinal",(scope.ledger.value,person,str(start),str(end))):
                read_rows+=1
                if progress:progress('读取历史日报基准',read_rows,total_rows)
                if scope.account_ids is not None and account not in scope.account_ids:continue
                if scope.instrument_ids is not None and instrument not in scope.instrument_ids:continue
                row=loads(payload)
                if scope.category and row.values[2]!=scope.category:continue
                if scope.search and scope.search.casefold() not in (str(row.values[3])+' '+str(row.values[1])).casefold():continue
                valid=bool(stamp) and bool(algorithm) and algorithm[0].startswith('money3-derived-v1:') and not any(day is None or day<=row.values[0] for day in impacts) and not any(day<=row.values[0] for day in changed_quotes)
                values=row.values if valid else (*row.values[:19],None,*row.values[20:])
                rows.append(ReportRow(values,{**row.metadata,'person_id':person,'baseline_valid':valid}))
    return SimpleNamespace(scope=scope,reports={'每日持仓收益':Report('每日持仓收益',DAILY_COLUMNS,tuple(rows))},incomplete_people=incomplete,baseline_end=None if incomplete else baseline_end)


def day_history(source,quotes,scope,day):
    rows,status=load_history(source,quotes,scope,day,day)
    return tuple((stamp,'History5',items) for stamp,items in rows.get(day,())),status


def trend(source,quotes,analytics,period,*,year=False,channels=None,ticker=None,mode='cumulative',now=None,history_data=None,progress=None):
    """No invented intraday points and no smoothing to a different daily result."""
    from portfolio.services.charts_service import Chart,Series
    now=now or datetime.now(UTC)
    if year:
        first=date(int(period),1,1);last=date(int(period),12,31);step=timedelta(hours=2)
    else:
        import calendar
        y,m=map(int,period.split('-'));first=date(y,m,1);last=date(y,m,calendar.monthrange(y,m)[1]);step=timedelta(minutes=30)
    by_day,state=history_data if history_data is not None else load_history(source,quotes,analytics.scope,first,last)
    from portfolio.services.derived_builder import historical_report_end
    expected_baseline=historical_report_end(min(last,analytics.scope.as_of),now)
    report_pending=bool(getattr(analytics,'incomplete_people',True) or not getattr(analytics,'baseline_end',None) or analytics.baseline_end<str(expected_baseline))
    _,days,halves,_=quotes.load(now)
    observed={date.fromisoformat(d) for d in state.get('trading_days',())}
    calendar_days=sorted(d for d in days|observed if first<=d<=last)
    if not calendar_days:return Chart(0,'历史走势','line',(Series('累计盈亏',()),),'缺少真实交易所日历，未猜测交易日。'),state
    daily=defaultdict(list)
    for row in analytics.reports['每日持仓收益'].rows:
        v=row.values
        if str(first)<=v[0]<=str(last) and (channels is None or v[1] in channels) and (not ticker or ticker.upper() in v[3].upper()):daily[v[0]].append(v[19])
    points=[];ticks=[];labels={};boundaries=[];missing=[];baseline_breaks=[];recovered=[];breaks=[];bridge_rejected=[]
    running=Decimal(0) if calendar_days[0]<=first+timedelta(days=7) and not getattr(analytics,'incomplete_people',True) and getattr(analytics,'baseline_end','') and analytics.baseline_end>=str(first) else None
    if running is None:missing.append('期间起始交易日历/累计基准未确认')
    daily_rows=defaultdict(list)
    for row in analytics.reports['每日持仓收益'].rows:
        v=row.values
        if str(first)<=v[0]<=str(last) and (channels is None or v[1] in channels) and (not ticker or ticker.upper() in v[3].upper()):daily_rows[v[0]].append(row)
    bridge={};bridge_allowed=running is not None and not getattr(analytics,'incomplete_people',True)
    permitted_people=allowed_people(source,analytics.scope)
    last_month=None
    with localcontext() as ctx:
        ctx.prec=50
        for day_index,d in enumerate(calendar_days):
            if progress:progress('整理曲线采样（按交易日）',day_index,len(calendar_days))
            day=d.isoformat();start=datetime.combine(d-timedelta(days=1),time(20),NY).astimezone(UTC)
            end=datetime.combine(d,time(20),NY).astimezone(UTC)
            close=datetime.combine(d,time(13 if d in halves else 16),NY).astimezone(UTC)
            if not year or d.month!=last_month:
                ticks.append(start.isoformat());labels[start.isoformat()]=f'{d.month}月' if year else d.strftime('%m-%d')
                boundaries.append((start.isoformat(),'','dot'))
            last_month=d.month
            samples=by_day.get(day,[])
            baseline=running if mode=='cumulative' else Decimal(0)
            day_valid=day in state.get('valid_days',())
            observations=[];last_stamp=None;segment=0
            for stamp,items in samples:
                selected=list(items) if state.get('aggregated') else [x for x in items if (channels is None or x['channel'] in channels) and (not ticker or ticker.upper() in x['ticker'].upper())]
                gain=strict_sum(Decimal(x['pnl']) if x['pnl'] is not None else None for x in selected) if selected else (Decimal(0) if day_valid else None)
                value=baseline+gain if baseline is not None and gain is not None else None
                observations.append((stamp,value,segment))
                last_stamp=stamp
            # A real timestamp is chosen in each interval, including a partial
            # current interval.
            has_day_obs = any(v is not None for _, v, _ in observations) or (day_valid and baseline is not None and start <= now)
            if not points or points[-1][0]!=start.isoformat():
                start_val = baseline if (mode == 'cumulative' and has_day_obs) else None
                points.append((start.isoformat(), start_val))
            bucket_start=start;index=0
            while bucket_start<end:
                stop=bucket_start+step;chosen=None
                while index<len(observations) and observations[index][0]<=stop:
                    stamp,value,current_segment=observations[index];index+=1
                    if stamp<=bucket_start:continue
                    if stamp<=now:chosen=(stamp,value,current_segment)
                if chosen:
                    stamp,value,current_segment=chosen
                    points.append((stamp.isoformat(),value))
                else:
                    if points[-1][0]!=stop.isoformat():points.append((stop.isoformat(),None))
                bucket_start=stop
            if close<now:
                filtered_exposure=any(bool(items) if state.get('aggregated') else any((channels is None or x['channel'] in channels) and (not ticker or ticker.upper() in x['ticker'].upper()) for x in items) for _,items in samples)
                verified_no_exposure=bool(getattr(analytics,'baseline_end',None)) and day<=analytics.baseline_end and not filtered_exposure and not daily_rows[day] and (day_valid or (all(e.get('quantity')==0 for e in bridge.values()) and all(state.get('partitions',{}).get(f'{p}:{day}',{}).get('state')=='NO_EXPOSURE' for p in permitted_people)))
                gain=None if getattr(analytics,'incomplete_people',True) else strict_sum(daily[day]) if day in daily else Decimal(0) if verified_no_exposure else None
                candidate=running+gain if running is not None and gain is not None else None
                if gain is None:missing.append(day);baseline_breaks.append(day)
                # Reconcile an absolute prefix using the engine's existing
                # MV - opening MV - buys + sells identity. Missing prices may
                # recover; missing cash flows/opening values must remain unknown.
                for row in daily_rows[day]:
                    v=row.values;m=row.metadata;key=(m.get('person_id'),m.get('account_id'),m.get('instrument_id'))
                    entry=bridge.setdefault(key,dict(opening=v[15],buy=Decimal(0),sell=Decimal(0),ending=None,valid=True))
                    entry['valid'] &= bool(m.get('baseline_valid',False))
                    entry['buy']=entry['buy']+v[16] if entry['buy'] is not None and v[16] is not None else None
                    entry['sell']=entry['sell']+v[17] if entry['sell'] is not None and v[17] is not None else None
                    trusted=(m.get('valuation_source')=='DAILY_CLOSE' or m.get('valuation_source')=='ESTIMATE_CLOSE' and m.get('price_status')=='CLOSE_ESTIMATE' or v[8]==0)
                    entry['ending']=v[11] if trusted else None
                    entry['quantity']=v[8];entry['day']=day
                if any(e.get('day')!=day and e.get('quantity')!=0 for e in bridge.values()):bridge_allowed=False
                absolute=strict_sum(e['ending']-e['opening']-e['buy']+e['sell'] if e['valid'] and all(e[k] is not None for k in ('ending','opening','buy','sell')) else None for e in bridge.values()) if bridge else Decimal(0) if verified_no_exposure else None
                if candidate is not None and absolute is not None and abs(candidate-absolute)>Decimal('0.00000001'):
                    bridge_allowed=False;bridge_rejected.append(day)
                if candidate is None and bridge_allowed and absolute is not None:
                    candidate=absolute;recovered.append(day)
                running=candidate
            if not samples and start<=now:missing.append(day+'（无有效五分钟派生数据）')
    note='五分钟共享明细；月度30分钟/年度2小时采样。正式/原口径日收益参与跨日累计；缺失日内数据留空，不平滑、不补零。'
    if report_pending:
        state={**state,'baseline_state':'REPORT_PENDING'}
        note+=' 累计所需日报尚未生成或版本过期，后台将重试；可切换“每日日内盈亏”查看已保存五分钟点。'
    if state.get('progress',{}).get('calendar_warnings'):note+=' 日历覆盖元数据不足，使用已保存实际日期，未猜测缺失交易日。'
    if missing:note+=' 缺失或未生成：'+', '.join(missing[:12])+('…' if len(missing)>12 else '')
    state={**state,'baseline_break_days':baseline_breaks,'baseline_recovered_days':recovered,'baseline_rejected_days':bridge_rejected}
    if baseline_breaks:note+=' 累计未知日：'+','.join(baseline_breaks[:5])+'；没有可信基准不补零。'
    if bridge_rejected:note+=' 绝对基准与日报前缀核对不一致，停止使用恢复基准：'+','.join(bridge_rejected[:5])
    if recovered:note+=' 已用经核对的期初市值、实际买卖现金流与可信日末估值恢复累计基准：'+','.join(recovered[:5])
    chart=Chart(0,f'{period} '+('累计盈亏走势' if mode=='cumulative' else '每日日内盈亏走势'),'line',
                (Series('累计盈亏' if mode=='cumulative' else '当日盈亏',tuple(points)),),note,
                x_labels=labels,x_ticks=tuple(ticks),vertical_lines=tuple(boundaries),highlight_extremes=True,
                show_points=False,line_width=1.5,calendar_axis='year' if year else 'month',break_keys=tuple(breaks))
    if progress:progress('整理曲线采样（按交易日）',len(calendar_days),len(calendar_days))
    return chart,state
