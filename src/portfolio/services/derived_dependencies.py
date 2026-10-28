"""Content dependencies; SQLite data_version only avoids repeated scans.

File timestamps and WAL size never determine financial cache validity.
"""
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
import sqlite3
from threading import RLock

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.services.derived_store import ALGORITHM,digest,exclusive,utcnow
from portfolio.services.derived_codec import dumps

_TRACKERS={}
_LOCK=RLock()


def source_identity(source, *, financial_only=False):
    path=Path(source).resolve()
    with _LOCK:
        record=_TRACKERS.get((str(path),financial_only))
        inode=(path.stat().st_dev,path.stat().st_ino)
        if record is not None and record[3]!=inode:
            record[0].close();record=None
        if record is None:
            c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,check_same_thread=False)
            record=[c,None,None,inode];_TRACKERS[str(path),financial_only]=record
        c=record[0];version=c.execute('PRAGMA data_version').fetchone()[0]
        if version!=record[1]:
            c.execute('BEGIN')
            try:
                tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'history_%' ORDER BY name")]
                import hashlib
                h=hashlib.sha256()
                for table in tables:
                    h.update(table.encode())
                    quoted='"'+table.replace('"','""')+'"'
                    for row in c.execute('SELECT * FROM '+quoted+' ORDER BY rowid'):h.update(repr(tuple(row)).encode())
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_fact_revisions'").fetchone():
                    if financial_only:
                        for r in c.execute("SELECT * FROM history_prices WHERE price_type='DAILY_CLOSE' ORDER BY instrument_id,trade_date,stamp_utc"):h.update(repr(tuple(r)).encode())
                    else:
                        for r in c.execute('SELECT day,revision FROM history_fact_revisions ORDER BY day'):h.update(repr(tuple(r)).encode())
                record[1:3]=[version,h.hexdigest()]
            finally:c.rollback()
        return record[2]


def partition_dependency(source,quotes,scope,day,*,snapshot=None):
    if snapshot is None:
        with readonly_connection(source) as c:snapshot=load_snapshot(c,replace(scope,as_of=date.fromisoformat(day)))
        quotes.supplement(snapshot)
    ids={e.instrument_id for e in snapshot.events}
    ids.update(b['instrument_id'] for b in snapshot.baselines if b.get('instrument_id') is not None)
    bars=[]
    for i in sorted(ids):
        instrument=snapshot.instruments[i]
        if instrument['market']=='US':
            rows,_=quotes.intraday_candles(instrument['symbol']+'.US',day)
            bars.append((i,rows))
    with closing(quotes.connect()) as c:
        half=c.execute('SELECT half_day FROM us_calendar WHERE day=?',(day,)).fetchone()
    # Include exact event metadata/fees and baselines; never drop pre-2026 costs.
    prices=[p for p in snapshot.prices if p.kind in ('DAILY_CLOSE','ESTIMATE_CLOSE') and p.day<day]
    return digest((scope.ledger,scope.person_ids,
                   [replace(e,metadata={k:v for k,v in e.metadata.items() if k!='cost_diff'}) for e in snapshot.events],snapshot.actions,snapshot.baselines,
                   snapshot.accounts,snapshot.instruments,prices,bars,tuple(half) if half else None,
                   [r for r in snapshot.extras.get('broker_trades',[]) if r['business_date']<=day],
                   tuple(d for d in snapshot.extras.get('trading_days',()) if str(d)<=day)))


def check_changes(source,derived):
    """Record additions/edits/deletions, invalidate before any recomputation.

    Scope uncertainty intentionally expands to the full subsequent period.
    Fine grained engine object hashes still reuse unaffected atomic units.
    """
    if not derived.available:return {'state':'NOT_INITIALIZED','changed':0}
    with closing(derived.connect()) as c:
        version=c.execute("SELECT value FROM metadata WHERE key='checked_algorithm'").fetchone()
    if not version or version[0]!=ALGORITHM:
        with exclusive(derived.path),closing(derived.connect(write=True)) as c:
            c.execute("UPDATE partitions SET status='INVALID' WHERE algorithm<>?",(ALGORITHM,))
            c.execute("INSERT OR REPLACE INTO metadata VALUES ('checked_algorithm',?)",(ALGORITHM,));c.commit()
    identity=source_identity(source)
    with closing(derived.connect()) as c:
        previous=c.execute("SELECT value FROM metadata WHERE key='checked_source'").fetchone()
        if previous and previous[0]==identity:return {'state':'UNCHANGED','changed':0}
        old={(r[0],r[1]):tuple(r[2:]) for r in c.execute('SELECT * FROM source_manifest')}
    current={};raw_dependencies={}
    names=('broker_trades','person_operations','historical_baselines','corporate_actions','asset_adjustments','cash_events','market_prices','fx_rates','persons','broker_accounts','instruments','instrument_aliases','broker_fee_records','person_trade_fees')
    with readonly_connection(source) as c:
        existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        trade_refs={};operation_refs={}
        for name in names:
            if name not in existing:continue
            primary_key=tuple(r['name'] for r in sorted(c.execute('PRAGMA table_info('+name+')'),key=lambda r:r['pk']) if r['pk'])
            if not primary_key:
                raise ValueError(f'缓存依赖表 {name} 缺少稳定主键，已停止检查；原始数据未改动。')
            for r in c.execute('SELECT * FROM '+name):
                row=dict(r)
                day=next((row.get(k) for k in ('business_date','baseline_date','effective_date','effective_from','trade_date','rate_date') if row.get(k)),None)
                if name=='market_prices' and not day:
                    day=(str(row.get('period_key'))[:7]+'-01') if row.get('price_type')=='MONTH_CLOSE' else '~LIVE'
                detail=(day,row.get('person_id'),row.get('account_id'),row.get('instrument_id'))
                if name=='broker_trades':trade_refs[row['id']]=detail
                if name=='person_operations':operation_refs[row['id']]=detail
                if name in ('broker_fee_records','person_trade_fees'):
                    detail=operation_refs.get(row.get('person_operation_id') or row.get('operation_id')) or trade_refs.get(row.get('broker_trade_id')) or detail
                # Fee tables use broker_trade_id/operation_id rather than id.
                # Preserve existing single-column identities and encode every
                # component of a composite key; never merge rows by a fallback.
                row_key=str(row[primary_key[0]]) if len(primary_key)==1 else dumps(tuple((column,row[column]) for column in primary_key))
                current[name,row_key]=(digest(row),*detail)
                if row.get('source_record_id'):raw_dependencies.setdefault(row['source_record_id'],[]).append(detail)
        for r in c.execute('SELECT * FROM raw_records'):
            row=dict(r);refs=raw_dependencies.get(row['id'],[])
            days=[x[0] for x in refs if x[0]]
            detail=(min(days),None,None,None) if days else ('~AUDIT_ONLY',None,None,None)
            current['raw_records',str(row['id'])]=(digest(row),*detail)
        if 'history_fact_revisions' in existing:
            for r in c.execute('SELECT day,revision FROM history_fact_revisions'):
                current['history_prices',r['day']]=(digest(tuple(r)),r['day'],None,None,None)
            # Close corrections affect cross-day cumulative baselines, while a
            # new 5-minute price only affects its own contribution partition.
            from collections import defaultdict
            closes=defaultdict(list)
            for r in c.execute("SELECT instrument_id,trade_date,stamp_utc,price,confirmation,adjustment,provider FROM history_prices WHERE price_type='DAILY_CLOSE' ORDER BY instrument_id,trade_date,stamp_utc,provider"):
                closes[r['trade_date']].append(tuple(r))
            for day,values in closes.items():current['history_closes',day]=(digest(values),day,None,None,None)
    changes=[]
    for key in old.keys()|current.keys():
        if old.get(key)==current.get(key):continue
        for value in (old.get(key),current.get(key)):
            if value:changes.append((key[0],*value[1:],'SOURCE_CHANGED'))
    with exclusive(derived.path), closing(derived.connect(write=True)) as c:
        c.execute('BEGIN IMMEDIATE')
        for table,day,person,account,instrument,reason in changes:
            c.execute('INSERT INTO invalidations(detected_at,table_name,day,person_id,account_id,instrument_id,reason) VALUES (?,?,?,?,?,?,?)',(utcnow(),table,day,person,account,instrument,reason))
            # Broker/order/price metadata can affect ownership reconciliation.
            # Widen when ownership mapping is not provable.
            if day and day.startswith('~'):continue
            if table=='history_prices' and day:
                # Five-minute changes are validated against partition_inputs
                # and the exact checkpoint price prefix by the shared reader.
                # An append leaves old points valid; a correction hides them.
                # A close correction is separately tracked as history_closes.
                continue
            elif table=='person_operations' and person is not None and day:
                c.execute("UPDATE partitions SET status='INVALID' WHERE ledger='PERSON' AND person_id=? AND day>=?",(person,day))
            elif day:c.execute("UPDATE partitions SET status='INVALID' WHERE day>=?",(day,))
            else:c.execute("UPDATE partitions SET status='INVALID'")
        c.execute('DELETE FROM source_manifest')
        c.executemany('INSERT INTO source_manifest VALUES (?,?,?,?,?,?,?)',[(t,k,*v) for (t,k),v in current.items()])
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('checked_source',?)",(identity,));c.commit()
    financial_changed=any(table!='history_prices' and not (day and day.startswith('~'))
                          for table,day,person,account,instrument,reason in changes)
    return {'state':'CHANGED' if changes else 'UNCHANGED','changed':len(changes),'financial_changed':financial_changed}
