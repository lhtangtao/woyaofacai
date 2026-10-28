"""All historical owners, offline shared prices, transactional daily partitions."""
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import json
from types import SimpleNamespace

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.live_quotes import NY, UTC
from portfolio.domain.models import QueryScope, Ledger
from portfolio.services.derived_codec import dumps
from portfolio.services.derived_store import ALGORITHM, digest, exclusive, utcnow
from portfolio.services.intraday_history import rebuild_history


def historical_people(source):
    with readonly_connection(source) as c:
        return [r[0] for r in c.execute('''SELECT DISTINCT person_id FROM (
            SELECT person_id FROM person_operations UNION SELECT person_id FROM historical_baselines
            UNION SELECT person_id FROM cash_events UNION SELECT person_id FROM asset_adjustments)
            WHERE person_id IS NOT NULL ORDER BY person_id''')]


class PartitionWriter:
    def __init__(self, quotes,derived,scope,dependency,*,incremental=False):
        self.quotes,self.derived,self.scope,self.dependency=quotes,derived,scope,dependency
        self.saved=False
        self.incremental=incremental
    def __getattr__(self,name): return getattr(self.quotes,name)
    def checkpoint_key(self):
        return digest((self.scope.ledger,self.scope.person_ids,self.scope.as_of))
    def load_checkpoint(self,dependency):
        return self.derived.get('intraday-state',self.checkpoint_key(),dependency) if self.incremental else None
    def save_historical_pnl(self,key,day,rows,source_version,warning='',*,input_revision=None,checkpoint=None,resume_after=None):
        ledger=self.scope.ledger.value;person=self.scope.person_ids[0] if self.scope.person_ids else 0
        records=[]
        for stamp,phase,items in rows:
            for v in items:
                records.append((ledger,person,v['account_id'],v['instrument_id'],day,stamp.astimezone(UTC).isoformat(),
                                v['channel'],v['ticker'],v['quantity'],v['market_value'],v['opening_value'],v['buy'],v['sell'],v['pnl'],
                                'MISSING_PRICE_OR_LEDGER' if v['pnl'] is None else v.get('status','OBSERVED')))
        state='PARTIAL' if warning or any(r[-2] is None for r in records) else 'HISTORICAL_OBSERVED'
        if not records and warning=='当前筛选没有当日期初持仓或成交。':state='NO_EXPOSURE'
        elif not records and not resume_after:
            state='PARTIAL'
            warning=warning or '尚无已完成的真实五分钟K线；等待补齐，不填零。'
        if (records or resume_after) and datetime.combine(date.fromisoformat(day),time(20),NY)>datetime.now(UTC):state='PROVISIONAL'
        revision=self.quotes.day_revision(day)
        if input_revision is not None and revision!=input_revision:
            raise ValueError('行情依赖在保存前变化，原有效分区保留。')
        with exclusive(self.derived.path), closing(self.derived.connect(write=True)) as c:
            c.execute('BEGIN IMMEDIATE')
            if resume_after:
                c.execute('DELETE FROM intraday_contributions WHERE ledger=? AND person_id=? AND day=? AND stamp>?',(ledger,person,day,resume_after.isoformat()))
            else:c.execute('DELETE FROM intraday_contributions WHERE ledger=? AND person_id=? AND day=?',(ledger,person,day))
            c.executemany('INSERT INTO intraday_contributions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',records)
            c.execute('INSERT OR REPLACE INTO partition_inputs VALUES (?,?,?,?)',(ledger,person,day,dumps(input_revision if input_revision is not None else revision)))
            c.execute('INSERT OR REPLACE INTO partitions VALUES (?,?,?,?,?,?,?,?)',
                      (ledger,person,day,self.dependency,ALGORITHM,state,warning,utcnow()))
            if checkpoint:
                import hashlib,zlib
                checkpoint['warning']=warning
                payload=zlib.compress(dumps(checkpoint).encode())
                c.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)',('intraday-state',self.checkpoint_key(),checkpoint['state_dependency'],ALGORITHM,payload,hashlib.sha256(payload).hexdigest(),utcnow()))
            elif not resume_after:
                c.execute("DELETE FROM objects WHERE kind='intraday-state' AND object_key=?",(self.checkpoint_key(),))
            mark_partition_publication(c)
            c.commit()
        self.saved=True
        self.status=state


def history_days(source,quotes,start,end):
    """Use saved calendar/actual price dates; incomplete metadata is not no data."""
    from portfolio.services.history_facts import calendar_covers,readonly
    _,days,_,_=quotes.load(datetime.now(UTC))
    days={d for d in days if start<=d<=end};warnings=[]
    complete=quotes.path.is_file() and calendar_covers(quotes.path,start,end)
    if not complete:
        warnings.append('交易所日历覆盖元数据不足，复用已保存日历及真实历史价格日期；缺失时段不填造值。')
        if quotes.path.is_file():
            with closing(readonly(quotes.path)) as c:
                for day, in c.execute('SELECT DISTINCT day FROM intraday_candles WHERE day BETWEEN ? AND ?',(str(start),str(end))):days.add(date.fromisoformat(day))
        with readonly_connection(source) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():
                for day, in c.execute('SELECT DISTINCT trade_date FROM history_prices WHERE trade_date BETWEEN ? AND ?',(str(start),str(end))):days.add(date.fromisoformat(day))
            for day, in c.execute("SELECT DISTINCT m.trade_date FROM market_prices m JOIN instruments i ON (m.symbol=i.symbol OR m.symbol=i.symbol||'.US') WHERE i.market='US' AND m.price_type='DAILY_CLOSE' AND m.trade_date BETWEEN ? AND ?",(str(start),str(end))):days.add(date.fromisoformat(day))
    return sorted(days),warnings


def record_error(derived,quotes,ledger,person,day,dependency,error):
    """Hide only the failed partition; preserve its old rows for recovery."""
    # A source/cache read must not hold the derived write lock. Failure to read
    # a new revision must not abort the remaining independent dates.
    try:revision=dumps(quotes.day_revision(day))
    except Exception:revision=None
    with exclusive(derived.path),closing(derived.connect(write=True)) as c:
        c.execute('INSERT OR REPLACE INTO partitions VALUES (?,?,?,?,?,?,?,?)',
                  (ledger,person,day,dependency or 'ERROR',ALGORITHM,'ERROR',f'该日期计算失败（{type(error).__name__}）：{str(error)[:300]}；其他日期可继续查看。',utcnow()))
        if revision is not None:c.execute('INSERT OR REPLACE INTO partition_inputs VALUES (?,?,?,?)',(ledger,person,day,revision))
        c.commit()


def _reusable_reports(derived,ledger,person,end,quote_inputs):
    with closing(derived.connect()) as c:
        meta=dict(c.execute("SELECT key,value FROM metadata WHERE key LIKE ?",(f'report_%:{ledger}:{person}',)))
        checked=meta.get(f'report_checked:{ledger}:{person}')
        if not checked or meta.get(f'report_algorithm:{ledger}:{person}')!=ALGORITHM or meta.get(f'report_through:{ledger}:{person}','')!=str(end):return False
        if json.loads(meta.get(f'report_quotes:{ledger}:{person}','{}'))!=quote_inputs:return False
        return not c.execute("SELECT 1 FROM invalidations WHERE detected_at>? AND table_name<>'history_prices' AND (person_id IS NULL OR person_id=?) AND (day IS NULL OR (day NOT LIKE '~%' AND day<=?)) LIMIT 1",(checked,person,str(end))).fetchone()


def build_all(source,derived,quotes,*,start=date(2026,1,1),end=None,force=False,progress=print,on_progress=None,retry_errors=True,on_reports=None,on_partition=None):
    from portfolio.services.analytics_service import AnalyticsService
    from portfolio.services.derived_dependencies import partition_dependency
    import logging
    from portfolio.services.derived_store import SourceChangedError
    end=end or datetime.now(NY).date();now=datetime.now(UTC)
    days,calendar_warnings=history_days(source,quotes,start,end)
    days=[d for d in days if datetime.combine(d-timedelta(days=1),time(20),NY)<now]
    recent=sorted(days,reverse=True)[:2]
    days=recent+[d for d in sorted(days) if d not in recent]
    if not days:raise ValueError('本地没有可计算的已发生历史日期；缺失行情未伪造。')
    targets=[(Ledger.PERSON,(p,)) for p in historical_people(source)]+[(Ledger.BROKER,None)]
    total=len(targets)*(len(days)+1);done=errors=partial=report_errors=skipped=0
    def notify(state,message):
        if on_progress:on_progress(dict(state=state,message=message,completed_units=done,total_units=total,errors=errors,partial=partial,report_errors=report_errors))
    service=AnalyticsService(source,quote_store=quotes,derived_store=derived)
    financial={'每日持仓收益','每月持仓收益','月度净投入明细','完整交易净投入','每月微操收益','微操配对证据','当前持仓','清仓收益','股票汇总','人员资产','账户资产','交易回放'}
    with derived.active():
        prepared=[]
        for ledger,persons in targets:
            scope=QueryScope(end,ledger=ledger,person_ids=persons,include_disabled=True,start_date=None)
            person=persons[0] if persons else 0
            notify('CALCULATING_REPORTS','正在计算全部历史权益人员的持仓与财务报表。')
            try:
                with closing(quotes.read_connection()) as q:
                    quote_inputs=dict(q.execute('SELECT day,revision FROM quote_close_revisions WHERE day<=?',(str(end),)))
                if force or not _reusable_reports(derived,ledger.value,person,end,quote_inputs):
                    result=service.calculate(scope,use_cache=False)
                    with exclusive(derived.path),closing(derived.connect(write=True)) as c:
                        c.execute('BEGIN IMMEDIATE')
                        c.execute('DELETE FROM report_details WHERE ledger=? AND person_id=?',(ledger.value,person))
                        records=[]
                        for name,report in result.reports.items():
                            if name not in financial:continue
                            for index,row in enumerate(report.rows):
                                period=str(row.metadata.get('valuation_date') or row.metadata.get('month') or (row.values[0] if name in {'每日持仓收益','每月持仓收益','月度净投入明细','完整交易净投入','每月微操收益','微操配对证据','交易回放'} and row.values else end.isoformat()))[:10]
                                records.append((ledger.value,person,name,period,row.metadata.get('account_id') or 0,row.metadata.get('instrument_id') or 0,index,dumps(row)))
                        c.executemany('INSERT INTO report_details VALUES (?,?,?,?,?,?,?,?)',records)
                        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_checked:{ledger.value}:{person}',utcnow()))
                        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_algorithm:{ledger.value}:{person}',ALGORITHM))
                        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_through:{ledger.value}:{person}',str(end)))
                        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_quotes:{ledger.value}:{person}',json.dumps(quote_inputs)))
                        c.commit()
                if on_reports:on_reports(ledger.value,person,quote_inputs)
            except SourceChangedError:raise
            except Exception:
                report_errors+=1
                logging.getLogger(__name__).exception('Historical report unit failed; continue independent dates')
            done+=1
            try:
                with readonly_connection(source) as c:snapshot=load_snapshot(c,scope)
                quotes.supplement(snapshot,readonly=True)
            except Exception as exc:
                for d in days:
                    record_error(derived,quotes,ledger.value,person,d.isoformat(),'',exc)
                    errors+=1;done+=1
                    notify('CALCULATING_HISTORY','部分账本输入无法读取，已隔离失败分区，继续其他历史人员。')
                continue
            prepared.append((ledger,person,scope,snapshot))
        for d in days:
            for ledger,person,scope,snapshot in prepared:
                day=d.isoformat();dep=''
                notify('CALCULATING_HISTORY',f'正在处理历史五分钟数据：{day}。')
                try:
                    sub_scope=replace(scope,as_of=d)
                    sub=replace(snapshot,scope=sub_scope,events=[e for e in snapshot.events if e.day<=day],
                                actions=[a for a in snapshot.actions if a['effective_date']<=day],
                                baselines=[b for b in snapshot.baselines if b['baseline_date']<=day],
                                prices=[p for p in snapshot.prices if p.day<=day],issues=[])
                    dep=partition_dependency(source,quotes,sub_scope,day,snapshot=sub)
                    with closing(derived.connect()) as c:
                        existing=c.execute('SELECT dependency,algorithm,status FROM partitions WHERE ledger=? AND person_id=? AND day=?',(ledger.value,person,day)).fetchone()
                    excluded=('INVALID','PROVISIONAL')+ (('ERROR',) if retry_errors else ())
                    if not force and existing and existing[0]==dep and existing[1]==ALGORITHM and existing[2] not in excluded:
                        skipped+=1
                        if existing[2]=='ERROR':errors+=1
                        elif existing[2]=='PARTIAL':partial+=1
                    else:
                        writer=PartitionWriter(quotes,derived,sub_scope,dep)
                        warning=rebuild_history(source,SimpleNamespace(scope=sub_scope,trading_days=tuple(x.isoformat() for x in days)),None,writer,day,now,prepared_snapshot=sub)
                        if not writer.saved:writer.save_historical_pnl('',day,[], '',warning)
                        if writer.status=='PARTIAL':partial+=1
                        progress(f'{ledger.value} person={person} {day}: {warning or "saved"}')
                    if on_partition:on_partition(ledger.value,person,day)
                except SourceChangedError:raise
                except Exception as exc:
                    record_error(derived,quotes,ledger.value,person,day,dep,exc)
                    if on_partition:on_partition(ledger.value,person,day)
                    errors+=1
                    logging.getLogger(__name__).exception('Historical date partition failed; continue remaining dates')
                done+=1
                notify('CALCULATING_HISTORY',f'已处理 {done}/{total} 个计算单元；失败 {errors}，缺失/提示 {partial}。')
    return {'people':len(targets)-1,'days':len(days),'errors':errors,'partial':partial,'report_errors':report_errors,
            'skipped':skipped,'calendar_warnings':calendar_warnings}


def rebuild_scope_day(source,analytics,credentials,quotes,day,now,cancel=None):
    """Canonical owner partitions; one shared quote fetch, no filter-key copies."""
    from portfolio.services.derived_store import DerivedStore
    from portfolio.services.derived_dependencies import partition_dependency
    from portfolio.services.derived_queries import allowed_people
    from portfolio.services.intraday_history import _rebuild
    from decimal import localcontext
    derived=DerivedStore(source)
    people=allowed_people(source,analytics.scope)
    warnings=[]
    with exclusive(str(derived.path)+'.build',wait=False),derived.active(),localcontext() as ctx:
        ctx.prec=50
        for person in people:
            scope=replace(analytics.scope,as_of=date.fromisoformat(day),person_ids=(person,) if person else None,
                          include_disabled=True,account_ids=None,instrument_ids=None,category=None,search='',start_date=None,reporting_currency='USD')
            from portfolio.services.intraday_history import source_version
            expected_source=source_version(source)
            with readonly_connection(source,cancel=cancel) as c:snapshot=load_snapshot(c,scope,cancel=cancel)
            quotes.supplement(snapshot)
            # Downloading missing shared prices happens inside the original
            # calculator; fingerprint AFTER it has stored the new completed bars.
            class PendingWriter(PartitionWriter):
                def save_historical_pnl(self,key,day,rows,version,warning='',*,input_revision=None,checkpoint=None,resume_after=None):
                    if source_version(source)!=expected_source:raise ValueError('原始账本变化，未保存本次日内分区。')
                    self.dependency=partition_dependency(source,quotes,scope,day)
                    super().save_historical_pnl(key,day,rows,version,warning,input_revision=input_revision,checkpoint=checkpoint,resume_after=resume_after)
            writer=PendingWriter(quotes,derived,scope,'')
            message=_rebuild(source,SimpleNamespace(scope=scope,trading_days=analytics.trading_days),credentials,writer,day,now,cancel,snapshot)
            if not writer.saved:
                writer.dependency=partition_dependency(source,quotes,scope,day)
                writer.save_historical_pnl('',day,[],'',message)
            if message:warnings.append(message)
    return '；'.join(dict.fromkeys(warnings))


def validate_generation(derived,expected_people):
    """Exact monthly/daily accounting checks; NULLs stay explicitly incomplete."""
    from collections import defaultdict
    from decimal import Decimal,localcontext
    from portfolio.services.derived_codec import loads
    from portfolio.domain.analytics import strict_sum
    checks=0;unknown=0
    with closing(derived.connect()) as c,localcontext() as ctx:
        ctx.prec=50
        present={r[0] for r in c.execute("SELECT DISTINCT person_id FROM partitions WHERE ledger='PERSON'")}
        if not set(expected_people)<=present:raise ValueError('有历史人员未完成预计算，本代不得发布。')
        for ledger,person in c.execute('SELECT DISTINCT ledger,person_id FROM report_details'):
            daily=defaultdict(list)
            for account,instrument,payload in c.execute("SELECT account_id,instrument_id,payload FROM report_details WHERE ledger=? AND person_id=? AND report='每日持仓收益'",(ledger,person)):
                row=loads(payload);daily[row.values[0][:7],account,instrument].append(row.values[19])
            for account,instrument,payload in c.execute("SELECT account_id,instrument_id,payload FROM report_details WHERE ledger=? AND person_id=? AND report='每月持仓收益'",(ledger,person)):
                row=loads(payload);expected=strict_sum(daily[row.values[0],account,instrument]);actual=row.values[20]
                if expected is None or actual is None:unknown+=1;continue
                if abs(expected-actual)>Decimal('0.00000001'):raise ValueError('每日/月度收益核对不一致；本代未发布。')
                checks+=1
        for payload,checksum in c.execute('SELECT payload,checksum FROM objects'):
            import hashlib
            if hashlib.sha256(payload).hexdigest()!=checksum:raise ValueError('计算对象校验失败；本代未发布。')
    return {'daily_monthly_checks':checks,'incomplete_comparisons':unknown,'historical_people':len(expected_people)}


def mark_partition_publication(connection):
    """A commit-visible revision for read-only clients, even before a full run ends."""
    import uuid
    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('partition_generation',?)",(uuid.uuid4().hex,))
    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('partition_algorithm',?)",(ALGORITHM,))


def publish_reports(staging,published,ledger,person,expected_source,quotes=None,quote_inputs=None):
    from portfolio.services.intraday_history import source_version
    if source_version(published.source)!=expected_source:return False
    if quotes is not None and quote_inputs is not None:
        with closing(quotes.read_connection()) as q:current=dict(q.execute('SELECT day,revision FROM quote_close_revisions'))
        if any(current.get(day)!=revision for day,revision in quote_inputs.items()):return False
    with closing(staging.connect()) as c:
        rows=c.execute('SELECT * FROM report_details WHERE ledger=? AND person_id=?',(ledger,person)).fetchall()
        end_row=c.execute('SELECT value FROM metadata WHERE key=?',(f'report_through:{ledger}:{person}',)).fetchone()
        staging_end=end_row[0] if end_row else ''
    with exclusive(published.path),closing(published.connect(write=True)) as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('DELETE FROM report_details WHERE ledger=? AND person_id=?',(ledger,person))
        c.executemany('INSERT INTO report_details VALUES (?,?,?,?,?,?,?,?)',rows)
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_checked:{ledger}:{person}',utcnow()))
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_algorithm:{ledger}:{person}',ALGORITHM))
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_through:{ledger}:{person}',str(staging_end)))
        c.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(f'report_quotes:{ledger}:{person}',json.dumps(quote_inputs or {})))
        c.commit()
    return True


def publish_partition(staging,published,quotes,ledger,person,day,expected_source):
    """Validate and release one complete date, preserving newer live rows."""
    from portfolio.services.intraday_history import source_version
    if source_version(published.source)!=expected_source:return False
    args=(ledger,person,day)
    with closing(staging.connect()) as c:
        partition=c.execute('SELECT * FROM partitions WHERE ledger=? AND person_id=? AND day=?',args).fetchone()
        inputs=c.execute('SELECT * FROM partition_inputs WHERE ledger=? AND person_id=? AND day=?',args).fetchone()
        if not partition or partition[4]!=ALGORITHM or partition[5]=='INVALID' or not inputs:return False
        if inputs[3]!=dumps(quotes.day_revision(day)):return False
        rows=c.execute('SELECT * FROM intraday_contributions WHERE ledger=? AND person_id=? AND day=? ORDER BY stamp',args).fetchall()
        scope=QueryScope(date.fromisoformat(day),ledger=Ledger(ledger),person_ids=(person,) if person else None,include_disabled=True)
        key=digest((scope.ledger,scope.person_ids,scope.as_of))
        checkpoint=c.execute("SELECT * FROM objects WHERE kind='intraday-state' AND object_key=? AND algorithm=?",(key,ALGORITHM)).fetchone()
    with exclusive(published.path),closing(published.connect(write=True)) as c:
        c.execute('BEGIN IMMEDIATE')
        previous=c.execute('SELECT updated_at FROM partitions WHERE ledger=? AND person_id=? AND day=?',args).fetchone()
        if partition[5]=='ERROR':
            # A stale staging failure cannot conceal a newer successful live
            # result, or a healthy result for exactly the same quote inputs.
            healthy=c.execute('SELECT p.algorithm,p.status,i.quote_revision FROM partitions p LEFT JOIN partition_inputs i USING(ledger,person_id,day) WHERE p.ledger=? AND p.person_id=? AND p.day=?',args).fetchone()
            if (previous and previous[0]>=partition[7]) or (healthy and healthy[0]==ALGORITHM and healthy[1] not in ('INVALID','ERROR') and healthy[2]==inputs[3]):return False
            c.execute('INSERT OR REPLACE INTO partitions VALUES (?,?,?,?,?,?,?,?)',partition)
            c.execute('INSERT OR REPLACE INTO partition_inputs VALUES (?,?,?,?)',inputs)
            mark_partition_publication(c);c.commit()
            return True
        latest=c.execute('SELECT max(stamp) FROM intraday_contributions WHERE ledger=? AND person_id=? AND day=?',args).fetchone()[0]
        incoming=max((r[5] for r in rows),default=None)
        if (previous and previous[0]>=partition[7]) or (latest and incoming and latest>incoming):return False
        c.execute('DELETE FROM intraday_contributions WHERE ledger=? AND person_id=? AND day=?',args)
        c.executemany('INSERT INTO intraday_contributions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
        c.execute('INSERT OR REPLACE INTO partitions VALUES (?,?,?,?,?,?,?,?)',partition)
        c.execute('INSERT OR REPLACE INTO partition_inputs VALUES (?,?,?,?)',inputs)
        if checkpoint:c.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)',checkpoint)
        mark_partition_publication(c);c.commit()
    return True
