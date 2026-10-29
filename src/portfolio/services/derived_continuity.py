"""Continuous owner/day coverage and reusable exact five-minute totals."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime,timedelta
from decimal import Decimal,localcontext
import hashlib,zlib
from portfolio.services.derived_store import ALGORITHM,digest,exclusive,utcnow
from portfolio.services.derived_codec import dumps


def key(ledger,person,day):return digest((ledger,person,str(day)))


def save_total(connection,ledger,person,day,revision):
    groups=defaultdict(list)
    for stamp,pnl in connection.execute('SELECT stamp,pnl FROM intraday_contributions INDEXED BY ix_intraday_person_day WHERE ledger=? AND person_id=? AND day=? ORDER BY stamp',(ledger,person,day)):
        groups[stamp].append(pnl)
    with localcontext() as ctx:
        ctx.prec=50
        points=[(stamp,str(sum((Decimal(v) for v in values),Decimal(0))) if all(v is not None for v in values) else None) for stamp,values in groups.items()]
    payload=zlib.compress(dumps(points).encode())
    connection.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)',('owner-day-pnl',key(ledger,person,day),revision,ALGORITHM,payload,hashlib.sha256(payload).hexdigest(),utcnow()))


def total(store,ledger,person,day,revision):
    return store.get('owner-day-pnl',key(ledger,person,day),revision)


def intact(store,quotes,ledger,person,day,scope):
    """Check the whole prefix, including missing rows between existing endpoints."""
    checkpoint=store.get('intraday-state',digest((scope.ledger,scope.person_ids,scope.as_of)))
    if not checkpoint:return False
    expected=checkpoint.get('coverage_stamps')
    if expected is None:
        # Upgrade old checkpoints from actual local price evidence, never from
        # an assumed 288-point day (extended sessions may legitimately be absent).
        expected=sorted({(stamp+timedelta(minutes=5)).isoformat() for i in checkpoint['price_ids']
                         for stamp,_,_ in quotes.intraday_candles(_symbol(quotes.source,i),day)[0]
                         if stamp+timedelta(minutes=5)<=checkpoint['last']})
    required=len(checkpoint['state'])
    with closing(store.connect()) as c:
        observed=dict(c.execute('SELECT stamp,count(*) FROM intraday_contributions INDEXED BY ix_intraday_person_day WHERE ledger=? AND person_id=? AND day=? GROUP BY stamp',(ledger,person,day)))
    return set(observed)==set(expected) and all(count==required for count in observed.values())


def _symbol(source,identifier):
    from portfolio.db.connection import readonly_connection
    with readonly_connection(source) as c:return c.execute("SELECT symbol||'.US' FROM instruments WHERE id=?",(identifier,)).fetchone()[0]


def ensure_total(store,ledger,person,day):
    with exclusive(store.path),closing(store.connect(write=True)) as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT updated_at,status,algorithm FROM partitions WHERE ledger=? AND person_id=? AND day=?',(ledger,person,day)).fetchone()
        # A verified legacy checkpoint is upgraded once, avoiding repeated
        # raw-price coverage scans on every background pass.
        from portfolio.domain.models import Ledger
        from datetime import date
        from portfolio.services.derived_codec import loads
        checkpoint_key=digest((Ledger(ledger),(person,) if person else None,date.fromisoformat(day)))
        cp=c.execute("SELECT payload,checksum FROM objects WHERE kind='intraday-state' AND object_key=? AND algorithm=?",(checkpoint_key,ALGORITHM)).fetchone()
        if cp and hashlib.sha256(cp[0]).hexdigest()==cp[1]:
            value=loads(zlib.decompress(cp[0]).decode())
            if 'coverage_stamps' not in value:
                value['coverage_stamps']=[r[0] for r in c.execute('SELECT DISTINCT stamp FROM intraday_contributions INDEXED BY ix_intraday_person_day WHERE ledger=? AND person_id=? AND day=? ORDER BY stamp',(ledger,person,day))]
                payload=zlib.compress(dumps(value).encode())
                c.execute("UPDATE objects SET payload=?,checksum=? WHERE kind='intraday-state' AND object_key=?",(payload,hashlib.sha256(payload).hexdigest(),checkpoint_key))
        if c.execute("SELECT 1 FROM objects WHERE kind='owner-day-pnl' AND object_key=? AND dependency=? AND algorithm=?",(key(ledger,person,day),row[0] if row else '',ALGORITHM)).fetchone():
            c.commit();return
        if row and row[1] not in ('INVALID','ERROR') and row[2]==ALGORITHM:
            save_total(c,ledger,person,day,row[0])
        c.commit()


def cached_history(store,scope,ready,*,progress=None):
    """Common unfiltered charts read daily persisted totals, not stock rows."""
    from portfolio.services.derived_codec import loads
    output=defaultdict(dict)
    with closing(store.connect()) as c:
        c.execute('BEGIN')
        for index,(person,day,updated,required) in enumerate(ready):
            if progress:progress('读取已保存五分钟汇总',index,len(ready))
            partition=c.execute('SELECT updated_at,status FROM partitions WHERE ledger=? AND person_id=? AND day=?',(scope.ledger.value,person,day)).fetchone()
            if not partition or partition[0]!=updated or partition[1] in ('INVALID','ERROR'):return None
            row=c.execute("SELECT payload,checksum FROM objects WHERE kind='owner-day-pnl' AND object_key=? AND dependency=? AND algorithm=?",(key(scope.ledger.value,person,day),updated,ALGORITHM)).fetchone()
            if not row:return None
            if hashlib.sha256(row[0]).hexdigest()!=row[1]:raise ValueError('五分钟汇总校验失败，拒绝展示')
            for stamp,value in loads(zlib.decompress(row[0]).decode()):
                output[day].setdefault(stamp,[]).append(value)
    result={};incomplete=0;requirements={d:r for _,d,_,r in ready}
    with localcontext() as ctx:
        ctx.prec=50
        for day,points in output.items():
            result[day]=[]
            for stamp,values in sorted(points.items()):
                if len(values)<requirements[day]:incomplete+=1;continue
                value=str(sum((Decimal(v) for v in values),Decimal(0))) if all(v is not None for v in values) else None
                result[day].append((datetime.fromisoformat(stamp),({'pnl':value,'channel':'','ticker':''},)))
    if progress:progress('读取已保存五分钟汇总',len(ready),len(ready))
    return result,incomplete
