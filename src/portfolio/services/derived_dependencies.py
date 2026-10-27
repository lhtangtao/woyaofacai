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
from portfolio.services.derived_store import digest,exclusive,utcnow

_TRACKERS={}
_LOCK=RLock()


def source_identity(source):
    path=Path(source).resolve()
    with _LOCK:
        record=_TRACKERS.get(str(path))
        if record is None:
            c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,check_same_thread=False)
            record=[c,None,None];_TRACKERS[str(path)]=record
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
                # Confirmed daily history influences reports; intraday bars only
                # influence their own partition dependencies, not all reports.
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():
                    for r in c.execute("SELECT * FROM history_prices WHERE price_type='DAILY_CLOSE' ORDER BY instrument_id,trade_date,stamp_utc"):h.update(repr(tuple(r)).encode())
                record[1:]=[version,h.hexdigest()]
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
    # Include exact event metadata/fees and baselines; never drop pre-2026 costs.
    prices=[p for p in snapshot.prices if p.kind in ('DAILY_CLOSE','ESTIMATE_CLOSE') and p.day<day]
    return digest((scope.ledger,scope.person_ids,snapshot.events,snapshot.actions,snapshot.baselines,
                   snapshot.accounts,snapshot.instruments,prices,bars,
                   [r for r in snapshot.extras.get('broker_trades',[]) if r['business_date']<=day],
                   snapshot.extras.get('trading_days',())))


def check_changes(source,derived):
    """Record additions/edits/deletions, invalidate before any recomputation.

    Scope uncertainty intentionally expands to the full subsequent period.
    Fine grained engine object hashes still reuse unaffected atomic units.
    """
    if not derived.available:return {'state':'NOT_INITIALIZED','changed':0}
    identity=source_identity(source)
    with closing(derived.connect()) as c:
        previous=c.execute("SELECT value FROM metadata WHERE key='checked_source'").fetchone()
        if previous and previous[0]==identity:return {'state':'UNCHANGED','changed':0}
        old={(r[0],r[1]):tuple(r[2:]) for r in c.execute('SELECT * FROM source_manifest')}
    current={}
    names=('broker_trades','person_operations','historical_baselines','corporate_actions','asset_adjustments','cash_events','market_prices','fx_rates','persons','broker_accounts','instruments','instrument_aliases','broker_fee_records','person_trade_fees','raw_records')
    with readonly_connection(source) as c:
        existing={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for name in names:
            if name not in existing:continue
            for r in c.execute('SELECT * FROM '+name):
                row=dict(r);day=next((row.get(k) for k in ('business_date','baseline_date','effective_date','effective_from','trade_date','rate_date') if row.get(k)),None)
                current[name,str(row['id'])]=(digest(row),day,row.get('person_id'),row.get('account_id'),row.get('instrument_id'))
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
            if table=='person_operations' and person is not None and day:
                c.execute("UPDATE partitions SET status='INVALID' WHERE ledger='PERSON' AND person_id=? AND day>=?",(person,day))
            elif day:c.execute("UPDATE partitions SET status='INVALID' WHERE day>=?",(day,))
            else:c.execute("UPDATE partitions SET status='INVALID'")
        c.execute('DELETE FROM source_manifest')
        c.executemany('INSERT INTO source_manifest VALUES (?,?,?,?,?,?,?)',[(t,k,*v) for (t,k),v in current.items()])
        c.execute("INSERT OR REPLACE INTO metadata VALUES ('checked_source',?)",(identity,));c.commit()
    return {'state':'CHANGED' if changes else 'UNCHANGED','changed':len(changes)}
