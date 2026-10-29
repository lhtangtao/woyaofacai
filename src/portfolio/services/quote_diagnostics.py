"""Scoped SDK diagnostics; never retain credentials, account payloads or prices."""
from contextlib import contextmanager
from contextvars import ContextVar
import re

_OBSERVER=ContextVar('money3_quote_observer',default=None)


def describe_error(error,secrets=()):
    text=str(error)
    for value in secrets:
        if value:text=text.replace(value,'<redacted>')
    text=re.sub(r'https?://\S+','<url>',text)
    text=re.sub(r'(?i)(token|secret|password|authorization|app_key)([\s:=]+)[^\s,;]+',r'\1\2<redacted>',text)
    text=re.sub(r'[A-Za-z0-9_/-]{32,}','<redacted>',text)
    lower=text.lower()
    category=('AUTH' if any(v in lower for v in ('401','unauthorized','invalid token','token expired')) else
              'PERMISSION' if any(v in lower for v in ('403','permission','not entitled')) else
              'NETWORK' if any(v in lower for v in ('timeout','timed out','connection','connect','tls','eof','network','dns')) else 'API')
    return {'category':category,'detail':type(error).__name__+': '+text[:240]}


@contextmanager
def observe(observer):
    token=_OBSERVER.set(observer)
    try:yield
    finally:_OBSERVER.reset(token)


def _notify(method,symbol,info):
    observer=_OBSERVER.get()
    if observer:
        try:observer(method,symbol,info)
        except Exception as error:
            import logging
            logging.getLogger(__name__).warning('Quote diagnostic update failed type=%s',type(error).__name__)


def report(method,symbol,error,secrets=()):
    info=describe_error(error,secrets)
    _notify(method,symbol,info)
    return info


class ObservedContext:
    """Only the five-minute producer opts in; SDK calls/return types unchanged."""
    def __init__(self,context,secrets):self._context=context;self._secrets=secrets
    def __getattr__(self,name):
        member=getattr(self._context,name)
        if name not in ('history_candlesticks_by_date','history_candlesticks_by_offset'):return member
        def call(*args,**kwargs):
            symbol=args[0] if args else kwargs.get('symbol','')
            _notify(name,symbol,None)
            try:return member(*args,**kwargs)
            except Exception as error:
                info=report(name,symbol,error,self._secrets)
                # Existing collector catches errors. Keep its behavior while
                # reporting a sanitized reason to the owning producer.
                raise ValueError(info['detail']) from None
        return call


def observing():return _OBSERVER.get() is not None
