"""Shared background bootstrap, progress and incremental derived maintenance."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
import fcntl
import json
import os
from pathlib import Path
from threading import RLock
from time import monotonic
import uuid

from portfolio.services.derived_store import BuildBusyError,DerivedStore,utcnow

_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='money3-derived')
_LOCK=RLock()
_JOBS={}
RUNNING={'INITIALIZING','CHECKING','CALCULATING_REPORTS','CALCULATING_HISTORY','VALIDATING','PUBLISHING'}


def build_running(source):
    lock=Path(str(DerivedStore(source).path)+'.build.lock')
    if not lock.is_file():return False
    with lock.open('r') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        fcntl.flock(handle,fcntl.LOCK_UN)
    return False


def write_progress(source,**values):
    """Caller owns the existing build lock; atomic JSON shared by both clients."""
    path=Path(str(DerivedStore(source).path)+'.progress.json')
    try:previous=json.loads(path.read_text())
    except (OSError,ValueError):previous={}
    if not isinstance(previous,dict):previous={}
    if values.get('job_id')!=previous.get('job_id'):previous={}
    previous.update(values);previous['updated_at']=utcnow()
    total=previous.get('total_units',0);done=previous.get('completed_units',0)
    previous['percent']=min(100,done*100//total) if total else None
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    with temporary.open('x') as stream:
        os.chmod(temporary,0o600)
        json.dump(previous,stream,ensure_ascii=False)
    os.replace(temporary,path)


def maintenance_status(source):
    store=DerivedStore(source);status=store.readiness()
    try:progress=json.loads(Path(str(store.path)+'.progress.json').read_text())
    except (OSError,ValueError):progress={}
    if not isinstance(progress,dict):progress={}
    running=build_running(source)
    if running:
        if progress.get('state') not in RUNNING:
            progress={'state':'CHECKING','message':'正在更新共享历史分区。'}
        progress.setdefault('state','CHECKING')
        progress.setdefault('message','另一进程正在生成共享历史数据。')
    elif progress.get('state') in RUNNING:
        progress.update(state='INTERRUPTED',message='上次历史生成已中断，将自动重试；原有效数据保留。')
    elif not progress:
        progress={'state':status['state'],'message':'历史缓存可用。' if status['ready'] else '正在准备首次全人员历史预计算。'}
    return {**status,'running':running,'progress':progress}


def needs_rebuild(source,store,quotes,start):
    """Check journals/partitions only; unchanged startup never replays history."""
    from datetime import datetime,time,timedelta
    from portfolio.domain.live_quotes import NY,UTC
    from portfolio.services.derived_builder import historical_people,history_days
    from portfolio.services.derived_codec import dumps
    from portfolio.services.derived_store import ALGORITHM
    if not store.readiness()['ready']:return True
    people=historical_people(source)
    with closing(store.connect()) as c:
        rows=c.execute('SELECT ledger,person_id,day,algorithm,status FROM partitions').fetchall()
        if any(r[3]!=ALGORITHM or r[4]=='INVALID' for r in rows):return True
        inputs=c.execute('SELECT day,quote_revision FROM partition_inputs GROUP BY day,quote_revision').fetchall()
    revisions={}
    for day,revision in inputs:
        if day not in revisions:revisions[day]=dumps(quotes.day_revision(day))
        if revision!=revisions[day]:return True
    existing={(ledger,person,day) for ledger,person,day,_,_ in rows}
    now=datetime.now(UTC)
    days,_=history_days(source,quotes,start,datetime.now(NY).date())
    for day in days:
        if datetime.combine(day-timedelta(days=1),time(20),NY)>now:continue
        for ledger,person in [('PERSON',p) for p in people]+[('BROKER',0)]:
            if (ledger,person,day.isoformat()) not in existing:return True
    return False


def schedule_changed(source,start=None):
    """Always allow first generation; expensive checks run in the worker."""
    store=DerivedStore(source)
    if not store.source.is_file():return 'SOURCE_MISSING'
    key=str(store.source)
    with _LOCK:
        prior=_JOBS.get(key)
        if prior and not prior[0].done():return 'BUILDING'
        if build_running(source):return 'BUILDING'
        if prior and monotonic()-prior[1]<60:return 'RETRY_LATER'
        def work():
            from portfolio.derived import rebuild
            try:
                return rebuild(store.source,mode='changed',start=min(start or date(2026,1,1),date(2026,1,1)),automatic=True)
            except BuildBusyError:
                # Follow the actual owner's shared progress; never overwrite it.
                return {'state':'BUILDING'}
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Background derived generation failed')
                return {'state':'FAILED'}
        _JOBS[key]=(_POOL.submit(work),monotonic())
    return 'BUILDING'
