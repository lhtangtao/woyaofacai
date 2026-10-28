"""Application-level five-minute producer. Views never collect or calculate."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from decimal import Decimal,localcontext
import json
import sqlite3
from pathlib import Path
from threading import Event,RLock,Thread
from types import SimpleNamespace

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.live_quotes import NY,UTC,market_trade_day
from portfolio.domain.models import QueryScope,Ledger
from portfolio.engine.replay import replay
from portfolio.paths import CREDENTIALS_PATH,quote_cache_path
from portfolio.services.derived_store import BuildBusyError,DerivedStore,exclusive,utcnow

_THREADS={};_LOCK=RLock()
_HISTORY_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='money3-price-gaps')
_CLOSE_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='money3-close-repair')
_CLOSE_JOBS={}


def pipeline_status(source):
    store=DerivedStore(source)
    if not store.available:return {'state':'INITIALIZING','message':'正在准备五分钟行情采集。'}
    try:
        with closing(store.connect()) as c:row=c.execute("SELECT value FROM metadata WHERE key='intraday_status'").fetchone()
        return json.loads(row[0]) if row else {'state':'STARTING','message':'五分钟采集任务正在启动。'}
    except (ValueError,OSError,sqlite3.Error):return {'state':'INITIALIZING','message':'五分钟采集等待数据库初始化。'}


def _status(store,**values):
    values['checked_at']=utcnow()
    with exclusive(store.path),closing(store.connect(write=True)) as c:
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('intraday_status',?)",(json.dumps(values,ensure_ascii=False),));c.commit()


def start_pipeline(source):
    source=Path(source).resolve()
    if not source.is_file():return
    with _LOCK:
        key=str(source)
        if key in _THREADS and _THREADS[key].is_alive():return
        worker=Thread(target=_run,args=(source,),daemon=True,name='money3-five-minute')
        _THREADS[key]=worker;worker.start()


class Producer:
    def __init__(self,source):
        from portfolio.services.quote_store import QuoteStore
        self.source=source;self.store=DerivedStore(source)
        self.quotes=QuoteStore(quote_cache_path(),source=source)
        self.snapshots={};self.history_job=None;self.history_cursor=0;self.calendar_attempt=None;self.close_attempts={}

    def snapshot(self,ledger,day):
        from portfolio.services.intraday_history import source_version
        version=source_version(self.source)
        close_revision=self.quotes.day_revision(day)[3]
        key=(ledger,day,version,close_revision)
        if key not in self.snapshots:
            scope=QueryScope(date.fromisoformat(day),ledger=ledger,include_disabled=True)
            with readonly_connection(self.source,timeout=120) as c:snapshot=load_snapshot(c,scope)
            self.quotes.supplement(snapshot)
            self.snapshots={k:v for k,v in self.snapshots.items() if k[0]!=ledger}
            self.snapshots[key]=snapshot
        return self.snapshots[key],version

    def required(self,snapshot,day):
        target=date.fromisoformat(day)
        past=replace(snapshot,scope=replace(snapshot.scope,as_of=target-timedelta(days=1)),
                     events=[e for e in snapshot.events if e.day<day],actions=[a for a in snapshot.actions if a['effective_date']<day],
                     baselines=[b for b in snapshot.baselines if b['baseline_date']<day],extras={**snapshot.extras,'_positions_only':True},issues=[])
        positions,_,_=replay(past)
        ids={k[2] for k,p in positions.items() if p.quantity>=Decimal('.5') or p.quantity<0}
        ids.update(e.instrument_id for e in snapshot.events if e.day==day)
        return {i for i in ids if snapshot.instruments[i]['market']=='US'}

    def materialize(self,day,now,bases):
        from portfolio.services.derived_builder import PartitionWriter,historical_people,record_error
        from portfolio.services.derived_dependencies import partition_dependency
        from portfolio.services.intraday_history import _rebuild,source_version
        people=historical_people(self.source);latest=None;errors=0
        _,days,_,_=self.quotes.load(now)
        # collect() is called only for an exchange-confirmed current date or a
        # historical date discovered from locally saved price evidence.
        days=days|{date.fromisoformat(day)}
        for ledger,persons in ((Ledger.PERSON,people),(Ledger.BROKER,[0])):
            base,version=bases[ledger]
            for person in persons:
                scope=replace(base.scope,person_ids=(person,) if person else None)
                sub=replace(base,scope=scope,events=[e for e in base.events if not person or e.person_id==person],
                            baselines=[b for b in base.baselines if b.get('person_id')==person] if person else [b for b in base.baselines if b.get('scope_type') in ('ACCOUNT','TOTAL')],issues=[])
                try:
                    class Writer(PartitionWriter):
                        def save_historical_pnl(writer,key,day,rows,unused,warning='',**kwargs):
                            if source_version(self.source)!=version:raise ValueError('交易数据变化，本次分区不发布。')
                            writer.dependency=partition_dependency(self.source,self.quotes,scope,day,snapshot=sub)
                            super().save_historical_pnl(key,day,rows,unused,warning,**kwargs)
                    writer=Writer(self.quotes,self.store,scope,'',incremental=True)
                    warning=_rebuild(self.source,SimpleNamespace(scope=scope,trading_days=tuple(d.isoformat() for d in days)),None,writer,day,now,None,sub)
                    if not writer.saved:writer.save_historical_pnl('',day,[],'',warning)
                except Exception as exc:
                    errors+=1;record_error(self.store,self.quotes,ledger.value,person,day,'',exc)
                    import logging
                    logging.getLogger(__name__).exception('Five-minute owner partition failed')
        with closing(self.store.connect()) as c:
            latest=c.execute("SELECT max(stamp) FROM intraday_contributions WHERE day=? AND pnl IS NOT NULL",(day,)).fetchone()[0]
        return latest,errors

    def calendar(self,now):
        _,days,halves,covered=self.quotes.load(now)
        today=now.astimezone(NY).date()
        if (not covered or not covered[0]<=today+timedelta(days=1)<=covered[1]) and self.calendar_attempt!=today and CREDENTIALS_PATH.is_file():
            self.calendar_attempt=today
            try:
                from portfolio.services.history_coverage import fetch_calendar
                result=fetch_calendar(CREDENTIALS_PATH,today-timedelta(days=7),today+timedelta(days=14))
                self.quotes.save_calendar(result.days,result.halves,(today-timedelta(days=7),today+timedelta(days=14)))
                _,days,halves,_=self.quotes.load(now)
            except Exception:
                import logging
                logging.getLogger(__name__).warning('Five-minute calendar unavailable; using saved dates')
        return days,halves

    def prior_closes(self,base,ids,day,now):
        from portfolio.engine.close_baseline import previous_trading_day
        from portfolio.services.live_quotes import create_context,_SDK_VALUES_LOCK
        _,days,_,_=self.quotes.load(now)
        previous=date.fromisoformat(previous_trading_day(SimpleNamespace(trading_days=tuple(d.isoformat() for d in days)),day,calendar=days))
        known={p.instrument_id for p in base.prices if p.day==previous.isoformat() and p.kind=='DAILY_CLOSE' and p.basis in ('RAW','UNKNOWN')}
        context=None;changed=False
        if not CREDENTIALS_PATH.is_file():return False
        for identifier in sorted(ids-known):
            symbol=base.instruments[identifier]['symbol']+'.US';key=(symbol,previous)
            if key in self.close_attempts and now-self.close_attempts[key]<timedelta(minutes=30):continue
            self.close_attempts[key]=now
            marker=f'close_attempt:{identifier}:{previous}'
            with closing(self.store.connect()) as c:
                attempted=c.execute('SELECT value FROM metadata WHERE key=?',(marker,)).fetchone()
            if attempted and now-datetime.fromisoformat(attempted[0])<timedelta(minutes=30):continue
            with exclusive(self.store.path),closing(self.store.connect(write=True)) as c:
                c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(marker,now.isoformat()));c.commit()
            try:
                from longbridge.openapi import AdjustType,Period,TradeSessions
                if context is None:context=create_context(CREDENTIALS_PATH)
                with _SDK_VALUES_LOCK:
                    bars=context.history_candlesticks_by_date(symbol,Period.Day,AdjustType.NoAdjust,previous,previous+timedelta(days=1),TradeSessions.Intraday)
                    for bar in bars:
                        if bar.timestamp.astimezone(NY).date()==previous and bar.close.is_finite() and bar.close>0:
                            self.quotes.save_official_close(symbol,previous,bar.close,bar.timestamp,now);changed=True
            except Exception:
                import logging
                logging.getLogger(__name__).warning('Previous close unavailable; baseline remains missing')
            # One symbol per background repair turn; a missing provider result
            # must never hold up all current-day candle collection.
            break
        return changed

    def schedule_closes(self,base,ids,day,now):
        if not CREDENTIALS_PATH.is_file():return
        key=str(self.source)
        with _LOCK:
            previous=_CLOSE_JOBS.get(key)
            if previous and not previous.done():return
            def repair():
                try:
                    with exclusive(str(self.store.path)+'.close-repair',wait=False):
                        self.prior_closes(base,ids,day,now)
                except BuildBusyError:pass
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Close baseline repair failed; current-day collection continues')
            _CLOSE_JOBS[key]=_CLOSE_POOL.submit(repair)

    def collect(self,day,now):
        from portfolio.services.intraday_history import collect_day_prices
        bases={ledger:self.snapshot(ledger,day) for ledger in (Ledger.PERSON,Ledger.BROKER)}
        ids=set()
        for base,_ in bases.values():ids.update(self.required(base,day))
        result=collect_day_prices(bases[Ledger.PERSON][0],day,now,self.quotes,CREDENTIALS_PATH if CREDENTIALS_PATH.is_file() else None,instrument_ids=ids)
        from portfolio.services.derived_dependencies import check_changes
        check_changes(self.source,self.store)
        latest,errors=self.materialize(day,now,bases)
        self.schedule_closes(bases[Ledger.PERSON][0],ids,day,now)
        return result,latest,errors

    def catch_up(self,current):
        if self.history_job is not None and not self.history_job.done():return
        if self.history_job is not None:
            try:self.history_job.result()
            except BuildBusyError:pass
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Historical price recovery failed; retry remaining partitions')
        def work():
            # One past date per turn; the current-day producer has its own lock
            # and can continue while historical local recovery/network work runs.
            with exclusive(str(self.store.path)+'.price-history',wait=False),self.store.active(),localcontext() as ctx:
                ctx.prec=50
                from portfolio.services.derived_builder import history_days
                dates,_=history_days(self.source,self.quotes,date(2026,1,1),current)
                dates=[d for d in sorted(dates,reverse=True) if d<current]
                from portfolio.services.derived_builder import historical_people
                from portfolio.services.derived_codec import dumps
                from portfolio.services.derived_store import ALGORITHM
                expected=len(historical_people(self.source))+1
                pending=[]
                with closing(self.store.connect()) as c:
                    for candidate in dates:
                        partitions=c.execute('SELECT p.algorithm,p.status,i.quote_revision FROM partitions p LEFT JOIN partition_inputs i USING(ledger,person_id,day) WHERE p.day=?',(candidate.isoformat(),)).fetchall()
                        revision=dumps(self.quotes.day_revision(candidate.isoformat()))
                        if len(partitions)!=expected or any(a!=ALGORITHM or state not in ('HISTORICAL_OBSERVED','NO_EXPOSURE') or inputs!=revision for a,state,inputs in partitions):pending.append(candidate)
                if not pending:return
                target=pending[self.history_cursor%len(pending)];self.history_cursor+=1
                other=Producer(self.source)
                other.collect(target.isoformat(),datetime.now(UTC))
        self.history_job=_HISTORY_POOL.submit(work)

    def tick(self):
        from portfolio.services.history_facts import ensure_history_schema
        from portfolio.services.derived_dependencies import check_changes
        now=datetime.now(UTC)
        with exclusive(str(self.store.path)+'.intraday',wait=False),self.store.active(),localcontext() as ctx:
            ctx.prec=50
            with exclusive(self.store.path):self.store.initialize()
            with closing(self.quotes.connect()):pass
            ensure_history_schema(self.source)
            changes=check_changes(self.source,self.store)
            days,halves=self.calendar(now)
            target=market_trade_day(now,days,halves)
            if target not in days:
                _status(self.store,state='NO_SESSION',message='交易所日历未确认当前交易日，未制造行情。');return
            previous=pipeline_status(self.source)
            from portfolio.services.intraday_history import source_version
            version=source_version(self.source)
            if not changes.get('changed') and previous.get('source_version')==version and previous.get('day')==target.isoformat() and previous.get('next_poll') and now<datetime.fromisoformat(previous['next_poll']):return
            _status(self.store,state='COLLECTING',day=target.isoformat(),message='正在收集真实已完成五分钟K线并更新全部人员。')
            result,latest,errors=self.collect(target.isoformat(),now)
            _status(self.store,state='PARTIAL' if result['failed'] or errors or not latest else 'READY',day=target.isoformat(),
                    latest_price=result['latest_price'].isoformat() if result['latest_price'] else None,latest_result=latest,
                    source_version=version,next_poll=(now+timedelta(seconds=60)).isoformat(),
                    missing_symbols=len(result['failed']),failed_partitions=errors,
                    message='五分钟采集/计算存在缺失，已有有效点保留。' if result['failed'] or errors or not latest else '五分钟行情和全人员盈亏已落库；卡片仍按10秒刷新。')
            with closing(self.store.connect()) as c:
                historical_invalid=c.execute("SELECT 1 FROM partitions WHERE day<? AND status='INVALID' LIMIT 1",(target.isoformat(),)).fetchone()
                full_generation=c.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()
                from portfolio.services.derived_builder import historical_people
                from portfolio.services.derived_store import ALGORITHM
                incomplete_reports=any(not c.execute('SELECT 1 FROM metadata WHERE key=? AND value=?',(f'report_algorithm:{ledger}:{person}',ALGORITHM)).fetchone()
                                       for ledger,person in [('PERSON',p) for p in historical_people(self.source)]+[('BROKER',0)])
            if historical_invalid or not full_generation or incomplete_reports:
                from portfolio.services.derived_maintenance import schedule_changed
                schedule_changed(self.source)
        self.catch_up(target)


def _run(source):
    producer=Producer(source);stop=Event()
    while not stop.is_set():
        try:producer.tick()
        except BuildBusyError:pass
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception('Five-minute producer failed; existing data retained')
            try:_status(producer.store,state='FAILED',message='五分钟任务暂时失败，将自动重试；已保存曲线不清空。',error_type=type(exc).__name__)
            except Exception:pass
        stop.wait(10)
