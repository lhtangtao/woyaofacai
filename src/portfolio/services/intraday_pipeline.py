"""Application-level five-minute producer. Views never collect or calculate."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date,datetime,time,timedelta
from decimal import Decimal,localcontext
import json
import fcntl
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


def producer_running(source):
    lock=Path(str(DerivedStore(source).path)+'.producer.lock')
    if not lock.is_file():return False
    with lock.open('r') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        fcntl.flock(handle,fcntl.LOCK_UN)
    return False


def pipeline_status(source):
    store=DerivedStore(source);running=producer_running(source)
    if not running:return {'state':'NOT_STARTED','running':False,'message':'五分钟采集任务未运行；请重新启动软件，已有数据保留。'}
    if not store.available:return {'state':'INITIALIZING','running':True,'message':'正在初始化当天五分钟采集。'}
    try:
        with closing(sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True,timeout=.1)) as c:
            recorded_source=c.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
            if not recorded_source or recorded_source[0]!=str(store.source):raise ValueError('状态源不匹配')
            row=c.execute("SELECT value FROM metadata WHERE key='intraday_status'").fetchone()
        status=json.loads(row[0]) if row else {'state':'STARTING','message':'五分钟采集任务正在启动。'}
        status['running']=True
        checked=status.get('checked_at')
        if checked and status.get('state') in ('COLLECTING','CONNECTING','FETCHING','CALCULATING','STARTING') and datetime.now(UTC)-datetime.fromisoformat(checked)>timedelta(minutes=3):
            status.update(state='WAITING_RESPONSE',message='五分钟任务长时间没有新进度，可能正在等待接口或数据库；请查看日志，不能视为数据已生成。')
        return status
    except (ValueError,OSError,sqlite3.Error):return {'state':'STATUS_UNAVAILABLE','running':True,'message':'五分钟任务已启动，状态暂时读取失败。'}


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
                    from portfolio.services.derived_codec import dumps
                    from portfolio.services.derived_store import ALGORITHM
                    with closing(self.store.connect()) as c:
                        saved=c.execute('SELECT p.algorithm,p.status,i.quote_revision FROM partitions p LEFT JOIN partition_inputs i USING(ledger,person_id,day) WHERE p.ledger=? AND p.person_id=? AND p.day=?',(ledger.value,person,day)).fetchone()
                    if saved and saved[0]==ALGORITHM and saved[1] not in ('INVALID','ERROR') and saved[2]==dumps(self.quotes.day_revision(day)):continue
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

    def _close_attempt(self,identifier,previous,**values):
        """Persist per-security evidence/retry state; both clients share it."""
        import logging
        values.update(checked_at=utcnow(),symbol=values.get('symbol'),day=str(previous))
        with exclusive(self.store.path),closing(self.store.connect(write=True)) as c:
            c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                      (f'close_retry:{identifier}:{previous}',json.dumps(values,ensure_ascii=False)));c.commit()
        if values.get('state')!='CONFIRMED':
            logging.getLogger(__name__).warning('Previous close repair symbol=%s day=%s history=%s fallback=%s retry=%s',
                values.get('symbol'),previous,values.get('history_error'),values.get('fallback_reason'),values.get('next_retry'))

    @staticmethod
    def _close_error(error):
        # Retain useful SDK code/message without logging credentials or URLs.
        import re
        message=re.sub(r'https?://\S+','<url>',str(error))
        message=re.sub(r'(?i)(token|secret|password|authorization|app_key)([\s:=]+)[^\s,;]+',r'\1\2<redacted>',message)
        message=re.sub(r'[A-Za-z0-9_/-]{32,}','<redacted>',message)
        return type(error).__name__+': '+message[:240]

    def prior_closes(self,base,ids,day,now):
        from portfolio.engine.close_baseline import previous_trading_day
        from portfolio.services.live_quotes import create_context,_SDK_VALUES_LOCK
        from longbridge.openapi import AdjustType,Period,TradeSessions
        _,days,_,_=self.quotes.load(now)
        previous=date.fromisoformat(previous_trading_day(SimpleNamespace(trading_days=tuple(d.isoformat() for d in days)),day,calendar=days))
        # Refresh local confirmed facts first: another client may have saved them
        # after this worker's holdings snapshot was prepared.
        base=replace(base,prices=list(base.prices),extras=dict(base.extras),issues=list(base.issues))
        self.quotes.supplement(base,readonly=True)
        known={p.instrument_id for p in base.prices if p.day==str(previous) and p.kind=='DAILY_CLOSE'
               and p.basis=='RAW' and p.status in ('OFFICIAL_CLOSE','CONFIRMED')}
        context=None;changed=False;failed=[]
        if not CREDENTIALS_PATH.is_file():return False
        for identifier in sorted(ids-known):
            symbol=base.instruments[identifier]['symbol']+'.US'
            with closing(self.store.connect()) as c:
                row=c.execute('SELECT value FROM metadata WHERE key=?',(f'close_retry:{identifier}:{previous}',)).fetchone()
            attempt=json.loads(row[0]) if row else {}
            if attempt.get('next_retry') and now<datetime.fromisoformat(attempt['next_retry']):continue
            count=attempt.get('failures',0)+1
            try:
                from portfolio.services.history_facts import calendar_covers
                if previous not in days or date.fromisoformat(day) not in days or not calendar_covers(self.quotes.path,previous,date.fromisoformat(day)):
                    raise ValueError('交易所日历未确认目标交易日/上一交易日，拒绝猜测昨收归属')
                if context is None:context=create_context(CREDENTIALS_PATH)
                with _SDK_VALUES_LOCK:
                    bars=context.history_candlesticks_by_date(symbol,Period.Day,AdjustType.NoAdjust,previous,previous+timedelta(days=1),TradeSessions.Intraday)
                matching=[bar for bar in bars if bar.timestamp.astimezone(NY).date()==previous]
                if not matching:raise ValueError('历史日K未返回目标交易日')
                prices={bar.close for bar in matching}
                if len(prices)!=1:raise ValueError('同一交易日返回冲突日K价格')
                bar=matching[-1]
                changed=self.quotes.save_official_close(symbol,previous,bar.close,bar.timestamp,datetime.now(UTC)) or changed
                self._close_attempt(identifier,previous,symbol=symbol,state='CONFIRMED',failures=0,
                                    source='Longbridge.history_candlesticks_by_date',adjustment='RAW',session='Intraday')
            except Exception as error:
                failed.append((identifier,symbol,count,self._close_error(error)))
        # Batch fallback requests, after every missing security got a daily-K
        # attempt. SecurityQuote has no prev-close date/adjustment evidence in
        # this SDK; its last-price timestamp cannot prove those fields.
        for offset in range(0,len(failed),50):
            group=failed[offset:offset+50];received={};fallback_error=None
            try:
                if context is None:context=create_context(CREDENTIALS_PATH)
                with _SDK_VALUES_LOCK:received={v.symbol:v for v in context.quote([symbol for _,symbol,_,_ in group])}
            except Exception as error:fallback_error=self._close_error(error)
            for identifier,symbol,count,error in group:
                quote=received.get(symbol);candidate=None;quoted_at=None
                reason=fallback_error or 'quote未返回该股票'
                if quote is not None:
                    try:
                        value=quote.prev_close
                        quoted_at=quote.timestamp.astimezone(UTC).isoformat()
                        if not value.is_finite() or value<=0:reason='quote.prev_close非有效正价格'
                        elif quote.timestamp>datetime.now(quote.timestamp.tzinfo):reason='quote报价时间来自未来，拒绝候选'
                        elif quote.timestamp.astimezone(NY).date()!=date.fromisoformat(day):reason='quote最新报价不属于目标交易日，prev_close归属未确认'
                        else:
                            candidate=str(value)
                            reason='quote.prev_close未提供收盘所属日期和复权证据，仅存待确认候选，禁止写入正式收盘价'
                    except Exception as error_detail:reason=self._close_error(error_detail)
                checked=datetime.now(UTC)
                self._close_attempt(identifier,previous,symbol=symbol,state='UNCONFIRMED' if candidate else 'FAILED',
                    failures=count,next_retry=(checked+timedelta(seconds=min(300,20*2**min(count-1,4)))).isoformat(),
                    source='Longbridge.quote.prev_close',adjustment='UNVERIFIED',candidate=candidate,
                    quote_time=quoted_at,history_error=error,fallback_reason=reason)
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
                        if self.prior_closes(base,ids,day,now):
                            # Reuse existing completed bars; only the affected
                            # day is materialized. Busy current-day work will
                            # observe the changed close revision on its next tick.
                            from portfolio.services.derived_dependencies import check_changes
                            try:
                                with exclusive(str(self.store.path)+'.intraday',wait=False),self.store.active(),localcontext() as ctx:
                                    ctx.prec=50
                                    check_changes(self.source,self.store)
                                    bases={ledger:self.snapshot(ledger,day) for ledger in (Ledger.PERSON,Ledger.BROKER)}
                                    self.materialize(day,datetime.now(UTC),bases)
                            except BuildBusyError:pass
                except BuildBusyError:pass
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Close baseline repair failed; current-day collection continues')
            _CLOSE_JOBS[key]=_CLOSE_POOL.submit(repair)

    def collect(self,day,now):
        from portfolio.services.intraday_history import collect_day_prices
        from portfolio.services.quote_diagnostics import observe
        bases={ledger:self.snapshot(ledger,day) for ledger in (Ledger.PERSON,Ledger.BROKER)}
        ids=set()
        for base,_ in bases.values():ids.update(self.required(base,day))
        # Publish saved real bars first. A slow/failed network request must not
        # hold an already usable local curve hostage.
        has_saved=any(self.quotes.intraday_candles(bases[Ledger.PERSON][0].instruments[i]['symbol']+'.US',day)[0] for i in ids)
        latest=None;errors=0
        if has_saved:
            _status(self.store,state='CALCULATING',day=day,message='正在复用本地五分钟价格生成当天全部人员盈亏。')
            latest,errors=self.materialize(day,now,bases)
        self.quote_errors=[]
        def observed(method,symbol,info):
            if info:
                self.quote_errors.append(dict(method=method,symbol=symbol,**info))
                import logging
                logging.getLogger(__name__).warning('Five-minute quote failed day=%s symbol=%s method=%s category=%s detail=%s',day,symbol,method,info['category'],info['detail'])
                _status(self.store,state='CONNECTION_FAILED',day=day,message=f"五分钟行情请求失败 [{info['category']}]；后台继续处理本轮其他股票，已有有效点保留，具体原因见日志。")
            else:
                _status(self.store,state='FETCHING',day=day,message='正在获取缺失的真实五分钟K线；已有本地数据保留。')
        _status(self.store,state='CONNECTING',day=day,message='正在连接长桥并补取当天缺失五分钟行情。')
        with observe(observed):
            result=collect_day_prices(bases[Ledger.PERSON][0],day,now,self.quotes,CREDENTIALS_PATH if CREDENTIALS_PATH.is_file() else None,instrument_ids=ids)
        if self.quote_errors:
            with exclusive(self.store.path),closing(self.store.connect(write=True)) as c:
                c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',('intraday_errors:'+day,json.dumps(self.quote_errors,ensure_ascii=False)));c.commit()
        from portfolio.services.derived_dependencies import check_changes
        changes=check_changes(self.source,self.store)
        if not has_saved or result['saved'] or changes.get('changed'):
            _status(self.store,state='CALCULATING',day=day,message='正在将当天五分钟盈亏增量保存到派生库。')
            latest,errors=self.materialize(day,datetime.now(UTC),bases)
        self.schedule_closes(bases[Ledger.PERSON][0],ids,day,now)
        result['required_symbols']=len(ids)
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
            from time import monotonic
            from portfolio.services.derived_maintenance import build_running,pending_history_days
            if build_running(self.source):return
            with exclusive(str(self.store.path)+'.price-history',wait=False),self.store.active(),localcontext() as ctx:
                ctx.prec=50
                if build_running(self.source):return
                dates=pending_history_days(self.source,self.store,self.quotes,date(2026,1,1),current)
                if not dates:return
                started=monotonic();done=0;failures=[]
                other=Producer(self.source)
                def progress(state):
                    value=dict(state=state,total_units=len(dates),completed_units=done,
                               percent=done*100//len(dates),failed_dates=failures,checked_at=utcnow(),
                               message=f'正在按日期批量补齐历史人员：本批已处理 {done}/{len(dates)} 个缺失日期。')
                    with exclusive(self.store.path),closing(self.store.connect(write=True)) as c:
                        c.execute("INSERT OR REPLACE INTO metadata VALUES ('history_batch_status',?)",(json.dumps(value,ensure_ascii=False),));c.commit()
                progress('CALCULATING_HISTORY')
                for target in dates[:8]:
                    if build_running(self.source) or monotonic()-started>30:break
                    try:other.collect(target.isoformat(),datetime.now(UTC))
                    except Exception:
                        failures.append(target.isoformat())
                        import logging
                        logging.getLogger(__name__).exception('Historical date failed; continue batch')
                    done+=1;progress('CALCULATING_HISTORY')
                progress('PARTIAL' if failures else 'PUBLISHED')
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
            if not result['required_symbols']:
                state,message='NO_HOLDINGS','当前账本没有需要五分钟行情的美股持仓或当日成交。'
            elif not CREDENTIALS_PATH.is_file():
                state,message='CREDENTIALS_MISSING','未找到长桥凭证，无法补取五分钟行情；已有有效点保留。'
            elif result['failed']:
                category=self.quote_errors[-1]['category'] if self.quote_errors else 'API'
                state,message='CONNECTION_FAILED',f'长桥五分钟行情获取失败 [{category}]；稍后自动重试，具体接口原因见本机日志；已有有效点保留。'
            elif errors:
                state,message='CALCULATION_FAILED','部分人员的当天盈亏计算失败；已隔离失败分区，具体原因见日志。'
            elif not latest:
                state,message='NO_DATA','尚无可用五分钟盈亏；接口可能未返回已完成K线，或收益基准尚未齐备；不填零。'
            else:
                state,message='READY','当天真实五分钟价格与人员盈亏已保存；卡片仍按10秒刷新。'
            _status(self.store,state=state,day=target.isoformat(),
                    latest_price=result['latest_price'].isoformat() if result['latest_price'] else None,latest_result=latest,
                    source_version=version,next_poll=(now+timedelta(seconds=60)).isoformat(),
                    missing_symbols=len(result['failed']),failed_partitions=errors,
                    message=message)
            with closing(self.store.connect()) as c:
                historical_invalid=c.execute("SELECT 1 FROM partitions WHERE day<? AND status='INVALID' LIMIT 1",(target.isoformat(),)).fetchone()
                full_generation=c.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()
                from portfolio.services.derived_builder import historical_people
                from portfolio.services.derived_store import ALGORITHM
                incomplete_reports=any(not c.execute('SELECT 1 FROM metadata WHERE key=? AND value=?',(f'report_algorithm:{ledger}:{person}',ALGORITHM)).fetchone()
                                       for ledger,person in [('PERSON',p) for p in historical_people(self.source)]+[('BROKER',0)])
            if latest and not result['failed'] and (historical_invalid or not full_generation or incomplete_reports):
                from portfolio.services.derived_maintenance import schedule_changed
                schedule_changed(self.source)
        if latest and not result['failed']:self.catch_up(target)


def _run(source):
    stop=Event()
    while not stop.is_set():
        try:
            # One lifetime owner across Mac and Web; standby retries ownership
            # after the other process exits. No duplicate expensive collectors.
            with exclusive(str(DerivedStore(source).path)+'.producer',wait=False):
                producer=Producer(source)
                if producer.store.available:
                    try:_status(producer.store,state='STARTING',message='五分钟采集任务已启动，优先处理当前交易日。')
                    except (sqlite3.Error,ValueError):pass
                while not stop.is_set():
                    try:producer.tick()
                    except BuildBusyError:pass
                    except Exception as exc:
                        import logging
                        from portfolio.services.quote_diagnostics import describe_error
                        failure=describe_error(exc)
                        logging.getLogger(__name__).warning('Five-minute task failed type=%s detail=%s',type(exc).__name__,failure['detail'])
                        try:_status(producer.store,state='FAILED',message='五分钟任务本轮失败，稍后自动重试；具体原因见日志，已有有效点保留。',error_type=type(exc).__name__)
                        except Exception:pass
                    stop.wait(10)
        except BuildBusyError:stop.wait(10)
