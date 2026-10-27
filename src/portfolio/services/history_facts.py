"""Explicit, additive history import. No UI path may migrate a source database."""
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
    """Per-symbol/day/session evidence; counts alone never establish completeness."""
    from portfolio.domain.live_quotes import NY
    from datetime import date,time
    rows=[]
    with closing(readonly(legacy)) as c:
        halves={r[0] for r in c.execute('SELECT day FROM us_calendar WHERE half_day=1')}
        for symbol,day in c.execute('SELECT DISTINCT symbol,day FROM intraday_candles WHERE day BETWEEN ? AND ? ORDER BY symbol,day',(str(start),str(end))):
            values=c.execute('SELECT stamp,session FROM intraday_candles WHERE symbol=? AND day=? ORDER BY stamp',(symbol,day)).fetchall()
            present={datetime.fromisoformat(r[0]).astimezone(timezone.utc) for r in values if r[1]=='Intraday'}
            d=date.fromisoformat(day);a=datetime.combine(d,time(9,30),NY);b=datetime.combine(d,time(13 if day in halves else 16),NY)
            missing=[]
            while a<b:
                if a.astimezone(timezone.utc) not in present:missing.append(a.astimezone(timezone.utc).isoformat())
                a+=timedelta(minutes=5)
            rows.append({'symbol':symbol,'day':day,'sessions':{s:sum(v[1]==s for v in values) for s in ('Overnight','Pre','Intraday','Post')},'missing_regular_utc':missing,'confirmation':'LEGACY_COMPLETED','extended_hours':'OBSERVED_ONLY'})
    return rows
