"""Published derived SQLite objects with dependency validation and atomic writes."""
from contextlib import contextmanager
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
    paths=('engine/replay.py','engine/performance.py','engine/scalping.py','engine/holdings.py',
           'domain/trade_time.py','services/intraday_history.py','services/derived_engine.py')
    return 'money3-derived-v1:' + hashlib.sha256(b''.join(root.joinpath(p).read_bytes() for p in paths)).hexdigest()[:20]


ALGORITHM = _algorithm_version()
ACTIVE_STORE = ContextVar('money3_derived_store', default=None)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive(path, *, wait=True):
    """OS releases the lock on crash; shared by CLI and both application processes."""
    path = Path(str(path) + '.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise ValueError('派生数据正在由另一进程生成，请稍后重试。') from exc
        try: yield
        finally: fcntl.flock(handle, fcntl.LOCK_UN)


class DerivedStore:
    def __init__(self, source, path=None):
        self.source = Path(source).expanduser().resolve()
        self.path = Path(path) if path else derived_path(self.source)

    @property
    def available(self):
        return self.path.is_file()

    def connect(self, *, write=False):
        if not self.available: raise ValueError('派生库未初始化，请先执行 derived init。')
        c = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=60)
        c.execute('PRAGMA busy_timeout=60000')
        source = c.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
        if not source or source[0] != str(self.source):
            c.close(); raise ValueError('派生库的数据源不匹配，拒绝读取。')
        return c

    def initialize(self):
        if self.path.exists():
            with self.connect(): pass
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Callers hold exclusive publication lock. SQLite rollback journal avoids
        # replacing a file with an attached live WAL at generation publication.
        c = sqlite3.connect(self.path)
        try:
            c.executescript(files('portfolio').joinpath('resources/derived_schema.sql').read_text())
            c.executemany('INSERT INTO metadata VALUES (?,?)', [('source', str(self.source)),('algorithm', ALGORITHM),('created_at', utcnow())])
            c.commit()
        finally: c.close()
        os.chmod(self.path, 0o600)

    def get(self, kind, key, dependency):
        if not self.available: return None
        with self.connect() as c:
            row = c.execute('SELECT payload,checksum FROM objects WHERE kind=? AND object_key=? AND dependency=? AND algorithm=?',
                            (kind,key,dependency,ALGORITHM)).fetchone()
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
                    'objects':c.execute('SELECT count(*) FROM objects').fetchone()[0],
                    'partitions':c.execute('SELECT status,count(*) FROM partitions GROUP BY status').fetchall(),
                    'jobs':c.execute('SELECT mode,state,started_at,finished_at,detail FROM jobs ORDER BY started_at DESC LIMIT 3').fetchall()}
