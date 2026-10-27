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
    def __init__(self, quotes,derived,scope,dependency):
        self.quotes,self.derived,self.scope,self.dependency=quotes,derived,scope,dependency
        self.saved=False
    def __getattr__(self,name): return getattr(self.quotes,name)
    def save_historical_pnl(self,key,day,rows,source_version,warning='',*,input_revision=None):
        ledger=self.scope.ledger.value;person=self.scope.person_ids[0] if self.scope.person_ids else 0
        records=[]
        for stamp,phase,items in rows:
            for v in items:
                records.append((ledger,person,v['account_id'],v['instrument_id'],day,stamp.astimezone(UTC).isoformat(),
                                v['channel'],v['ticker'],v['quantity'],v['market_value'],v['opening_value'],v['buy'],v['sell'],v['pnl'],
                                'MISSING_PRICE_OR_LEDGER' if v['pnl'] is None else 'OBSERVED'))
        state='PARTIAL' if warning or any(r[-2] is None for r in records) else 'HISTORICAL_OBSERVED'
        if not records and warning=='当前筛选没有当日期初持仓或成交。':state='NO_EXPOSURE'
        elif not records:raise ValueError(warning or '历史计算未返回有效结果，原分区保留。')
        if records and datetime.combine(date.fromisoformat(day),time(20),NY)>datetime.now(UTC):state='PROVISIONAL'
        revision=self.quotes.day_revision(day)
        if input_revision is not None and revision!=input_revision:
            raise ValueError('行情依赖在保存前变化，原有效分区保留。')
        with exclusive(self.derived.path), closing(self.derived.connect(write=True)) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM intraday_contributions WHERE ledger=? AND person_id=? AND day=?',(ledger,person,day))
            c.executemany('INSERT INTO intraday_contributions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',records)
            c.execute('INSERT OR REPLACE INTO partition_inputs VALUES (?,?,?,?)',(ledger,person,day,dumps(input_revision if input_revision is not None else revision)))
            c.execute('INSERT OR REPLACE INTO partitions VALUES (?,?,?,?,?,?,?,?)',
                      (ledger,person,day,self.dependency,ALGORITHM,state,warning,utcnow()))
            c.commit()
        self.saved=True


def build_all(source,derived,quotes,*,start=date(2026,1,1),end=None,force=False,progress=print):
    from portfolio.services.analytics_service import AnalyticsService
    from portfolio.services.derived_dependencies import partition_dependency, check_changes
    end=end or datetime.now(NY).date()
    now=datetime.now(UTC)
    # Calendar is exchange evidence, never fabricate weekday sessions.
    _,days,_,_=quotes.load(now)
    from portfolio.services.history_facts import calendar_covers
    if not calendar_covers(quotes.path,start,end):
        raise ValueError('交易所日历覆盖证据不完整；先执行 derived fetch-calendar --start YYYY-MM-DD --end YYYY-MM-DD。')
    trading_days=sorted(d.isoformat() for d in days if start<=d<=end)
    if not trading_days:raise ValueError('未保存覆盖所选期间的交易所日历；先补全真实日历。')
    service=AnalyticsService(source,quote_store=quotes,derived_store=derived)
    targets=[(Ledger.PERSON,(p,)) for p in historical_people(source)]+[(Ledger.BROKER,None)]
    with derived.active():
        for ledger,persons in targets:
            scope=QueryScope(end,ledger=ledger,person_ids=persons,include_disabled=True,start_date=None)
            # One full daily pipeline per historical person warms atomic replay,
            # micro and performance; all prior trades remain available for costs.
            result=service.calculate(scope,use_cache=False)
            person=persons[0] if persons else 0
            with exclusive(derived.path), closing(derived.connect(write=True)) as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('DELETE FROM report_details WHERE ledger=? AND person_id=?',(ledger.value,person))
                records=[]
                financial={'每日持仓收益','每月持仓收益','月度净投入明细','完整交易净投入','每月微操收益','微操配对证据','当前持仓','清仓收益','股票汇总','人员资产','账户资产','交易回放'}
                for name,report in result.reports.items():
                    if name not in financial:continue
                    for index,row in enumerate(report.rows):
                        period=str(row.metadata.get('valuation_date') or row.metadata.get('month') or (row.values[0] if name in {'每日持仓收益','每月持仓收益','月度净投入明细','完整交易净投入','每月微操收益','微操配对证据','交易回放'} and row.values else end.isoformat()))[:10]
                        records.append((ledger.value,person,name,period,row.metadata.get('account_id') or 0,row.metadata.get('instrument_id') or 0,index,dumps(row)))
                c.executemany('INSERT INTO report_details VALUES (?,?,?,?,?,?,?,?)',records);c.commit()
            with readonly_connection(source) as c:snapshot=load_snapshot(c,scope)
            quotes.supplement(snapshot)
            for day in trading_days:
                d=date.fromisoformat(day)
                if datetime.combine(d-timedelta(days=1),time(20),NY)>=now:continue
                sub_scope=replace(scope,as_of=d)
                sub=replace(snapshot,scope=sub_scope,events=[e for e in snapshot.events if e.day<=day],
                            actions=[a for a in snapshot.actions if a['effective_date']<=day],
                            baselines=[b for b in snapshot.baselines if b['baseline_date']<=day],
                            prices=[p for p in snapshot.prices if p.day<=day],issues=[])
                dep=partition_dependency(source,quotes,sub_scope,day,snapshot=sub)
                with closing(derived.connect()) as c:
                    existing=c.execute('SELECT dependency,algorithm,status FROM partitions WHERE ledger=? AND person_id=? AND day=?',(ledger.value,person,day)).fetchone()
                if not force and existing and existing[0]==dep and existing[1]==ALGORITHM and existing[2] not in ('INVALID','ERROR','PROVISIONAL'):continue
                writer=PartitionWriter(quotes,derived,sub_scope,dep)
                warning=rebuild_history(source,SimpleNamespace(scope=sub_scope,trading_days=tuple(x.isoformat() for x in days)),None,writer,day,now,prepared_snapshot=sub)
                if not writer.saved:
                    writer.save_historical_pnl('',day,[], '',warning)
                progress(f'{ledger.value} person={person} {day}: {warning or "saved"}')
    return {'people':len(targets)-1,'days':len(trading_days)}


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
                def save_historical_pnl(self,key,day,rows,version,warning='',*,input_revision=None):
                    if source_version(source)!=expected_source:raise ValueError('原始账本变化，未保存本次日内分区。')
                    self.dependency=partition_dependency(source,quotes,scope,day)
                    super().save_historical_pnl(key,day,rows,version,warning,input_revision=input_revision)
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
