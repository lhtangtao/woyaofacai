"""Background changed-only generation after the user's initial publication."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import RLock
from time import monotonic

from portfolio.services.derived_store import DerivedStore

_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='money3-derived')
_LOCK=RLock()
_JOBS={}


def schedule_changed(source,start=None):
    store=DerivedStore(source)
    if not store.available:return 'NOT_INITIALIZED'
    with closing(store.connect()) as c:
        if not c.execute("SELECT 1 FROM metadata WHERE key='generation'").fetchone():return 'INITIAL_GENERATION_REQUIRED'
    key=str(store.source)
    with _LOCK:
        prior=_JOBS.get(key)
        if prior and not prior[0].done():return 'BUILDING'
        if prior and monotonic()-prior[1]<60:return 'RETRY_LATER'
        def work():
            from portfolio.derived import rebuild
            from datetime import date
            first=min(start or date(2026,1,1),date(2026,1,1))
            try:return rebuild(store.source,mode='changed',start=first)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning('Derived incremental generation failed: %s',type(exc).__name__)
                return {'state':'FAILED','message':'增量生成失败，原有效代保留；失效区段继续留空。'}
        _JOBS[key]=(_POOL.submit(work),monotonic())
    return 'BUILDING'
