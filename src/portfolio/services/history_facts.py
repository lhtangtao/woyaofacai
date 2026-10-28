"""Additive price-save/import entries with backups; no UI-triggered migration."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from pathlib import Path
import sqlite3

from portfolio.services.derived_store import exclusive, utcnow

SCHEMA = '''
CREATE TABLE IF NOT EXISTS history_prices(
 instrument_id INTEGER NOT NULL REFERENCES instruments(id), trade_date TEXT NOT NULL,
 stamp_utc TEXT NOT NULL, price_type TEXT NOT NULL, session TEXT NOT NULL,
 adjustment TEXT NOT NULL, provider TEXT NOT NULL, price TEXT NOT NULL,
 confirmation TEXT NOT NULL, imported_at TEXT NOT NULL, source_digest TEXT NOT NULL,
 PRIMARY KEY(instrument_id,trade_date,stamp_utc,price_type,session,adjustment,provider));
CREATE INDEX IF NOT EXISTS ix_history_price_day ON history_prices(trade_date,instrument_id,price_type);
CREATE TABLE IF NOT EXISTS history_fact_revisions(day TEXT PRIMARY KEY,revision INTEGER NOT NULL);
CREATE TRIGGER IF NOT EXISTS history_revision_insert AFTER INSERT ON history_prices BEGIN
 INSERT INTO history_fact_revisions VALUES (NEW.trade_date,1) ON CONFLICT(day) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER IF NOT EXISTS history_revision_delete AFTER DELETE ON history_prices BEGIN
 INSERT INTO history_fact_revisions VALUES (OLD.trade_date,1) ON CONFLICT(day) DO UPDATE SET revision=revision+1; END;
CREATE TRIGGER IF NOT EXISTS history_revision_update AFTER UPDATE ON history_prices BEGIN
 INSERT INTO history_fact_revisions VALUES (OLD.trade_date,1) ON CONFLICT(day) DO UPDATE SET revision=revision+1;
 INSERT INTO history_fact_revisions VALUES (NEW.trade_date,1) ON CONFLICT(day) DO UPDATE SET revision=revision+1; END;
CREATE TABLE IF NOT EXISTS history_price_imports(
 id TEXT PRIMARY KEY,started_at TEXT NOT NULL,source_digest TEXT NOT NULL,
 inserted INTEGER NOT NULL,backup_path TEXT NOT NULL);
'''


def readonly(path):
    c = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def table_digest(c, table):
    # Table names originate exclusively in sqlite_master or internal constants.
    quoted = '"'+table.replace('"','""')+'"'
    h = hashlib.sha256()
    for row in c.execute('SELECT * FROM '+quoted+' ORDER BY rowid'):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def inventory(source, legacy):
    result = {}
    for label, path in [('facts',source),('legacy',legacy)]:
        if not Path(path).is_file():
            result[label] = {'state':'MISSING'}; continue
        with closing(readonly(path)) as c:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            result[label] = {'tables':tables,'integrity':c.execute('PRAGMA quick_check').fetchone()[0]}
            if 'intraday_candles' in tables:
                result[label]['five_minute_months'] = [tuple(r) for r in c.execute('SELECT substr(day,1,7),count(*),count(distinct symbol),count(distinct day) FROM intraday_candles GROUP BY 1')]
            if 'history_prices' in tables:
                result[label]['history'] = [tuple(r) for r in c.execute('SELECT price_type,confirmation,count(*) FROM history_prices GROUP BY 1,2')]
    return result


def import_history(source, legacy):
    """Backup then one transaction. Existing fact rows are never overwritten."""
    source, legacy = Path(source).resolve(), Path(legacy).resolve()
    if source == legacy: raise ValueError('事实库与旧行情库不能相同。')
    with exclusive(source), closing(readonly(source)) as before, closing(readonly(legacy)) as old:
        for c in (before,old):
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('数据库完整性检查失败；未执行迁移。')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = source.with_name(source.name+'.before-history-'+stamp+'.bak')
        with closing(sqlite3.connect(backup)) as target: before.backup(target)
        backup.chmod(0o600)
        protected = [r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'history_%'")]
        hashes = {name:table_digest(before,name) for name in protected}
        symbols = {}
        for r in before.execute('SELECT id,symbol,market FROM instruments'):
            symbols.setdefault(r['symbol']+'.'+r['market'],set()).add(r['id'])
        old.execute('BEGIN')  # one consistent legacy snapshot, including WAL
        source_hash = hashlib.sha256((table_digest(old,'intraday_candles')+table_digest(old,'official_daily_closes')).encode()).hexdigest()
        with closing(sqlite3.connect(source,timeout=60)) as out:
            out.execute('PRAGMA foreign_keys=ON')
            # executescript would commit an existing transaction: begin inside it.
            out.executescript('BEGIN IMMEDIATE;\n'+SCHEMA)
            try:
                # Detect a concurrent sync between backup and write lock.
                if any(table_digest(out,n)!=v for n,v in hashes.items()):
                    raise ValueError('源库在备份后发生变化，请重新执行初始化。')
                inserted = 0
                def save(symbol,day,stamp,kind,session,value,confirmation):
                    nonlocal inserted
                    ids = symbols.get(symbol,set())
                    if len(ids)!=1: raise ValueError(f'行情证券无法唯一映射：{symbol}；已回滚本次导入。')
                    price = Decimal(value)
                    instant = datetime.fromisoformat(stamp)
                    if instant.tzinfo is None or not price.is_finite() or price<=0:
                        raise ValueError('历史行情包含无时区时间或无效价格；已回滚。')
                    key = (next(iter(ids)),day,instant.astimezone(timezone.utc).isoformat(),kind,session,'RAW','Longbridge')
                    prior = out.execute('SELECT price FROM history_prices WHERE instrument_id=? AND trade_date=? AND stamp_utc=? AND price_type=? AND session=? AND adjustment=? AND provider=?',key).fetchone()
                    if prior and Decimal(prior[0])!=price:
                        raise ValueError(f'已有权威行情冲突：{symbol} {day}；禁止静默覆盖。')
                    if not prior:
                        out.execute('INSERT INTO history_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)',(*key,str(price),confirmation,utcnow(),source_hash));inserted+=1
                for r in old.execute('SELECT symbol,day,stamp,session,close FROM intraday_candles'):
                    save(*r[:3],'FIVE_MINUTE',r[3],r[4],'LEGACY_COMPLETED')
                for r in old.execute('SELECT symbol,day,price,candle_time FROM official_daily_closes'):
                    save(r[0],r[1],r[3],'DAILY_CLOSE','Intraday',r[2],'CONFIRMED')
                if any(table_digest(out,n)!=v for n,v in hashes.items()): raise ValueError('原始表内容校验不一致。')
                if out.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('外键完整性检查失败。')
                out.execute('INSERT INTO history_price_imports VALUES (?,?,?,?,?)',(stamp,utcnow(),source_hash,inserted,str(backup)))
                out.commit()
            except BaseException:
                out.rollback(); raise
        return {'inserted':inserted,'backup':str(backup),'legacy_preserved':True}


class HistoryFacts:
    def __init__(self, source): self.source = Path(source).resolve()

    def bars(self, symbol, day):
        with closing(readonly(self.source)) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone(): return [],None
            rows = c.execute("SELECT h.stamp_utc,h.session,h.price,h.imported_at FROM history_prices h JOIN instruments i ON i.id=h.instrument_id WHERE i.symbol||'.'||i.market=? AND h.trade_date=? AND h.price_type='FIVE_MINUTE' AND h.adjustment='RAW' ORDER BY h.stamp_utc",(symbol,day)).fetchall()
        # Multiple providers at the same instant must agree, never silently sum.
        values = {}
        for stamp,phase,price,_ in rows:
            key=datetime.fromisoformat(stamp)
            item=(key,phase,Decimal(price))
            if key in values and values[key]!=item: raise ValueError('历史价格来源冲突，需先核对。')
            values[key]=item
        return list(values.values()),max((datetime.fromisoformat(r[3]) for r in rows),default=None)

    def closes(self, as_of):
        with closing(readonly(self.source)) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():return []
            return [tuple(r) for r in c.execute("SELECT instrument_id,trade_date,price,stamp_utc FROM history_prices WHERE price_type='DAILY_CLOSE' AND confirmation='CONFIRMED' AND adjustment='RAW' AND trade_date<=?",(str(as_of),))]


def coverage(source, legacy, start, end):
    """Required exposures plus per-session gaps; completely read-only."""
    from collections import defaultdict
    from datetime import date,time
    from dataclasses import replace
    from portfolio.db.connection import readonly_connection
    from portfolio.db.repositories.analytics import load_snapshot
    from portfolio.domain.models import QueryScope,Ledger
    from portfolio.domain.live_quotes import NY
    from portfolio.engine.replay import replay
    required=set();rows=[]
    with closing(readonly(legacy)) as c:
        days=[r[0] for r in c.execute('SELECT day FROM us_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(str(start),str(end)))]
        halves={r[0] for r in c.execute('SELECT day FROM us_calendar WHERE half_day=1')}
        span=c.execute('SELECT first_day,last_day FROM coverage WHERE id=1').fetchone()
        calendar_complete=calendar_covers(legacy,start,end)
        observed={(r[0],r[1]) for r in c.execute('SELECT DISTINCT symbol,day FROM intraday_candles WHERE day BETWEEN ? AND ?',(str(start),str(end)))}
    for ledger in (Ledger.PERSON,Ledger.BROKER):
        scope=QueryScope(end,ledger=ledger,include_disabled=True)
        with readonly_connection(source) as c:snapshot=load_snapshot(c,scope,with_prices=False)
        _,_,captures=replay(snapshot,capture_days=days)
        prior=set()
        for day in days:
            held={k[2] for k,p in captures[day].items() if p.quantity!=0}
            touched={e.instrument_id for e in snapshot.events if e.day==day}
            required.update((snapshot.instruments[i]['symbol']+'.US',day) for i in prior|held|touched if snapshot.instruments[i]['market']=='US')
            prior=held
    for symbol,day in sorted(required|observed):
        with closing(readonly(legacy)) as c:
            raw=c.execute('SELECT stamp,session,close FROM intraday_candles WHERE symbol=? AND day=? ORDER BY stamp',(symbol,day)).fetchall()
        merged={datetime.fromisoformat(r[0]).astimezone(timezone.utc):(r[1],Decimal(r[2])) for r in raw}
        facts,_=HistoryFacts(source).bars(symbol,day)
        for stamp,phase,price in facts:merged[stamp.astimezone(timezone.utc)]=(phase,price)
        present={stamp for stamp,(phase,price) in merged.items() if phase=='Intraday' and price.is_finite() and price>0}
        d=date.fromisoformat(day);a=datetime.combine(d,time(9,30),NY);b=datetime.combine(d,time(13 if day in halves else 16),NY)
        missing=[]
        while a<b:
            if a.astimezone(timezone.utc) not in present:missing.append(a.astimezone(timezone.utc).isoformat())
            a+=timedelta(minutes=5)
        confirmation=defaultdict(int)
        with closing(readonly(source)) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():
                for r in c.execute("SELECT h.confirmation,count(*) FROM history_prices h JOIN instruments i ON i.id=h.instrument_id WHERE i.symbol||'.'||i.market=? AND h.trade_date=? AND h.price_type='FIVE_MINUTE' GROUP BY h.confirmation",(symbol,day)):confirmation[r[0]]+=r[1]
        rows.append({'symbol':symbol,'day':day,'required':(symbol,day) in required,
                     'sessions':{phase:sum(v[0]==phase for v in merged.values()) for phase in ('Overnight','Pre','Intraday','Post')},
                     'missing_regular_utc':missing,'confirmation':dict(confirmation) or {'LEGACY_METADATA_UNAVAILABLE':len(raw)},
                     'extended_hours':'OBSERVED_ONLY_NOT_ASSERTED_COMPLETE'})
    return {'calendar_complete':calendar_complete,'required_symbol_days':len(required),'symbol_days':rows}


def append_completed(source,symbol,day,rows,*,kind='FIVE_MINUTE',confirmation='PROVIDER_COMPLETED'):
    """Authorized quote-save entry; only usable after explicit fact initialization.

    No DDL, source ledger updates, replacement, or erasure are permitted here.
    """
    source=Path(source).resolve()
    with closing(readonly(source)) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():return False
        ids=[r[0] for r in c.execute("SELECT id FROM instruments WHERE symbol||'.'||market=?",(symbol,))]
    if len(ids)!=1:raise ValueError('行情证券缺少唯一稳定标识。')
    with exclusive(source),closing(sqlite3.connect(source,timeout=60)) as c:
        c.execute('BEGIN IMMEDIATE')
        for stamp,phase,value in rows:
            if stamp.tzinfo is None or not value.is_finite() or value<=0:raise ValueError('无效已完成行情。')
            if kind=='FIVE_MINUTE' and stamp+timedelta(minutes=5)>datetime.now(timezone.utc):raise ValueError('未完成K线不得归档。')
            key=(ids[0],str(day),stamp.astimezone(timezone.utc).isoformat(),kind,phase,'RAW','Longbridge')
            old=c.execute('SELECT price FROM history_prices WHERE instrument_id=? AND trade_date=? AND stamp_utc=? AND price_type=? AND session=? AND adjustment=? AND provider=?',key).fetchone()
            if old and Decimal(old[0])!=value:raise ValueError('已归档价格冲突；保留原值，请通过明确行情修正流程核对。')
            if not old:
                evidence=hashlib.sha256(repr((*key,str(value))).encode()).hexdigest()
                c.execute('INSERT INTO history_prices VALUES (?,?,?,?,?,?,?,?,?,?,?)',(*key,str(value),confirmation,utcnow(),evidence))
        c.commit()
    return True


def calendar_covers(legacy,start,end):
    with closing(readonly(legacy)) as c:
        ranges=[tuple(r) for r in c.execute('SELECT first_day,last_day FROM coverage')]
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='calendar_ranges'").fetchone():
            ranges.extend(tuple(r) for r in c.execute('SELECT first_day,last_day FROM calendar_ranges'))
    cursor=str(start)
    for first,last in sorted(ranges):
        if last<cursor:continue
        if first>cursor:return False
        if last>=str(end):return True
        from datetime import date
        cursor=(date.fromisoformat(last)+timedelta(days=1)).isoformat()
    return False


def fetch_missing_prices(source,legacy,start,end,credentials):
    """Explicit network tool: only missing symbol-days; save only absent bars."""
    from portfolio.services.quote_store import QuoteStore
    from portfolio.services.live_quotes import create_context,_SDK_VALUES_LOCK
    from portfolio.domain.live_quotes import NY,UTC
    from longbridge.openapi import AdjustType,Period,TradeSessions
    from datetime import date,time
    audit=coverage(source,legacy,start,end)
    if not audit['calendar_complete']:raise ValueError('请先补全交易所日历覆盖证据。')
    store=QuoteStore(legacy,source=source);context=None;saved=0;requested=0
    now=datetime.now(UTC)
    for item in audit['symbol_days']:
        if not item['required'] or not item['missing_regular_utc']:continue
        symbol,day=item['symbol'],item['day'];d=date.fromisoformat(day)
        if datetime.combine(d,time(9,30),NY)>now:continue
        old,_=store.intraday_candles(symbol,day);present={v[0].astimezone(UTC) for v in old}
        if context is None:context=create_context(credentials)
        with _SDK_VALUES_LOCK:
            found=context.history_candlesticks_by_date(symbol,Period.Min_5,AdjustType.NoAdjust,d-timedelta(days=1),d,TradeSessions.All)
        requested+=1;rows=[]
        first=datetime.combine(d-timedelta(days=1),time(20),NY).astimezone(UTC)
        last=datetime.combine(d,time(20),NY).astimezone(UTC)
        for bar in found:
            stamp=bar.timestamp.astimezone(UTC);phase=str(bar.trade_session).rsplit('.',1)[-1];value=Decimal(str(bar.close))
            if stamp not in present and first<=stamp and stamp+timedelta(minutes=5)<=min(last,now) and phase in ('Overnight','Pre','Intraday','Post') and value.is_finite() and value>0:
                rows.append((stamp,phase,value));present.add(stamp)
        if rows:store.save_intraday_candles(symbol,day,rows,now);saved+=len(rows)
    return {'requested_symbol_days':requested,'saved_bars':saved,'confirmation':'PROVIDER_COMPLETED','extended_hours':'observed only; no completeness claim'}


def ensure_history_schema(source):
    """Authorized price-save initialization only; backup before additive DDL."""
    source=Path(source).resolve()
    with exclusive(source),closing(readonly(source)) as original:
        if original.execute("SELECT 1 FROM sqlite_master WHERE name='history_prices'").fetchone():return
        if original.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('事实库完整性检查失败，未初始化价格表。')
        backup=source.with_name(source.name+'.before-price-store-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.bak')
        with closing(sqlite3.connect(backup)) as target:original.backup(target)
        backup.chmod(0o600)
        with closing(sqlite3.connect(source,timeout=60)) as target:
            try:
                target.executescript('BEGIN IMMEDIATE;\n'+SCHEMA)
                target.commit()
            except BaseException:target.rollback();raise
