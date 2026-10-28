"""Published derived SQLite objects with dependency validation and atomic writes."""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib.resources import files
import os
from pathlib import Path
import sqlite3
import zlib

from portfolio.paths import derived_path
from portfolio.services.derived_codec import dumps, loads

def _algorithm_version():
    root=files('portfolio')
    paths=tuple('engine/'+p.name for p in root.joinpath('engine').iterdir() if p.name.endswith('.py')) + (
           'domain/trade_time.py','domain/analytics.py','domain/models.py',
           'services/intraday_history.py','services/derived_engine.py')
    return 'money3-derived-v1:' + hashlib.sha256(b''.join(root.joinpath(p).read_bytes() for p in sorted(paths))).hexdigest()[:20]


ALGORITHM = _algorithm_version()
ACTIVE_STORE = ContextVar('money3_derived_store', default=None)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class SourceChangedError(ValueError):
    pass


class BuildBusyError(ValueError):
    pass


@contextmanager
def exclusive(path, *, wait=True):
    """OS releases the lock on crash; shared by CLI and both application processes."""
    path = Path(str(path) + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise BuildBusyError('派生数据正在由另一进程生成，请稍后重试。') from exc
        try: yield
        finally: fcntl.flock(handle, fcntl.LOCK_UN)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


class DerivedStore:
    def __init__(self, source, path=None):
        self.source = Path(source).expanduser().resolve()
        self.path = Path(path) if path else derived_path(self.source)

    @property
    def available(self):
        return self.path.is_file()

    def connect(self, *, write=False):
        if not self.available: raise ValueError('派生库未初始化，请先执行 derived init。')
        c = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=60,factory=ClosingConnection)
        c.execute('PRAGMA busy_timeout=60000')
        try:source = c.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
        except BaseException:
            c.close();raise
        if not source or source[0] != str(self.source):
            c.close(); raise ValueError('派生库的数据源不匹配，拒绝读取。')
        return c

    def readiness(self):
        """Cheap read-only readiness probe; safe for UI/WSGI startup."""
        if not self.available:return {'ready':False,'state':'NOT_INITIALIZED','generation':None}
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=.05)) as c:
                meta=dict(c.execute('SELECT key,value FROM metadata'))
                if meta.get('source')!=str(self.source):return {'ready':False,'state':'UNINITIALIZED','generation':None}
                generation=meta.get('generation')
                published=c.execute("SELECT 1 FROM jobs WHERE id=? AND state='PUBLISHED'",(generation,)).fetchone() if generation else None
                valid=c.execute("SELECT 1 FROM partitions WHERE algorithm LIKE 'money3-derived-v1:%' AND status NOT IN ('INVALID','ERROR') LIMIT 1").fetchone()
                incremental=meta.get('partition_generation') if (meta.get('partition_algorithm') or '').startswith('money3-derived-v1:') else None
                ready=bool(valid and ((published and (meta.get('algorithm') or '').startswith('money3-derived-v1:')) or incremental))
                generation=(generation or '')+':'+incremental if incremental else generation
                return {'ready':ready,'state':'AVAILABLE' if ready else 'INITIAL_GENERATION_REQUIRED',
                        'generation':generation,'initialized':True}
        except (sqlite3.DatabaseError,ValueError):
            return {'ready':False,'state':'UNINITIALIZED','generation':None}

    def initialize(self):
        if self.path.exists():
            try:
                with closing(self.connect(write=True)) as c:
                    c.executescript(files('portfolio').joinpath('resources/derived_schema.sql').read_text())
                return
            except (sqlite3.DatabaseError,ValueError):
                # Only rebuildable derived files are quarantined. Preserve their
                # WAL/SHM as well; source trades and prices are never touched.
                import uuid
                suffix='.uninitialized-'+uuid.uuid4().hex
                for extra in ('','-wal','-shm'):
                    original=Path(str(self.path)+extra)
                    if original.exists():os.replace(original,Path(str(self.path)+suffix+extra))
        if not self.source.is_file():raise ValueError('原始事实库不存在，未创建派生库。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        import uuid
        temporary=self.path.with_name(self.path.name+'.init-'+uuid.uuid4().hex)
        c = sqlite3.connect(temporary)
        try:
            c.executescript(files('portfolio').joinpath('resources/derived_schema.sql').read_text())
            c.executemany('INSERT INTO metadata VALUES (?,?)', [('source', str(self.source)),('algorithm', ALGORITHM),('created_at', utcnow())])
            c.commit()
        finally: c.close()
        os.chmod(temporary, 0o600)
        with temporary.open('rb') as handle:os.fsync(handle.fileno())
        os.replace(temporary,self.path)

    def get(self, kind, key, dependency=None):
        if not self.available: return None
        with self.connect() as c:
            sql='SELECT payload,checksum FROM objects WHERE kind=? AND object_key=? AND algorithm=?'
            args=[kind,key,ALGORITHM]
            if dependency is not None:sql+=' AND dependency=?';args.append(dependency)
            row=c.execute(sql,args).fetchone()
        if not row: return None
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise ValueError('派生数据校验失败，拒绝展示损坏结果。')
        return loads(zlib.decompress(row[0]).decode())

    def put(self, kind, key, dependency, value):
        if not self.available: return
        data = zlib.compress(dumps(value).encode())
        with exclusive(self.path):
            with self.connect(write=True) as c:
                c.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)',
                          (kind,key,dependency,ALGORITHM,data,hashlib.sha256(data).hexdigest(),utcnow()))

    @contextmanager
    def active(self):
        token = ACTIVE_STORE.set(self)
        try: yield
        finally: ACTIVE_STORE.reset(token)

    def status(self):
        if not self.available: return {'state':'NOT_INITIALIZED'}
        with self.connect() as c:
            return {'state':'AVAILABLE','algorithm':ALGORITHM,
                    'published_algorithm':dict(c.execute('SELECT key,value FROM metadata')).get('algorithm'),
                    'objects':c.execute('SELECT count(*) FROM objects').fetchone()[0],
                    'partitions':c.execute('SELECT status,count(*) FROM partitions GROUP BY status').fetchall(),
                    'jobs':c.execute('SELECT mode,state,started_at,finished_at,detail FROM jobs ORDER BY started_at DESC LIMIT 3').fetchall()}

    def put_many(self, entries):
        """Batch immutable computation units in a single SQLite transaction."""
        if not self.available:return
        rows=[]
        for kind,key,dependency,value in entries:
            data=zlib.compress(dumps(value).encode())
            rows.append((kind,key,dependency,ALGORITHM,data,hashlib.sha256(data).hexdigest(),utcnow()))
        with exclusive(self.path),self.connect(write=True) as c:
            c.executemany('INSERT OR REPLACE INTO objects VALUES (?,?,?,?,?,?,?)',rows)

    def merge_units(self,other,since):
        """Bound memory/lock time; checkpoints publish with their date rows."""
        with other.connect() as source:
            cursor=source.execute("SELECT * FROM objects WHERE algorithm=? AND kind<>'intraday-state' AND updated_at>=?",(ALGORITHM,since))
            while True:
                rows=cursor.fetchmany(32)
                if not rows:return
                for row in rows:
                    if hashlib.sha256(row[4]).hexdigest()!=row[5]:raise ValueError('计算单元校验失败，未发布。')
                with exclusive(self.path),self.connect(write=True) as target:
                    target.executemany('''INSERT INTO objects VALUES (?,?,?,?,?,?,?)
                        ON CONFLICT(kind,object_key) DO UPDATE SET dependency=excluded.dependency,
                        algorithm=excluded.algorithm,payload=excluded.payload,checksum=excluded.checksum,
                        updated_at=excluded.updated_at WHERE excluded.updated_at>objects.updated_at''',rows)
