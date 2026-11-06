"""Batch, cached historical replay and before-state projection; no new cost model."""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal,localcontext
from threading import RLock
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.models import Ledger
from portfolio.domain.analytics import Position
from portfolio.domain.trade_time import event_order_time
from portfolio.engine.replay import replay,opening_positions,normalize_quantity,action_applies
from portfolio.services.derived_store import DerivedStore,digest
from portfolio.services.derived_engine import input_value

_CACHE=OrderedDict();_LOCK=RLock()


def context(connection,page,cancel=None):
    people=tuple(sorted({r.metadata['person_id'] for r in page.rows if r.metadata['person_id'] is not None}))
    accounts=tuple(sorted({r.metadata['account_id'] for r in page.rows}));ids=tuple(sorted({r.metadata['instrument_id'] for r in page.rows}))
    scope=replace(page.scope,person_ids=people if page.scope.ledger==Ledger.PERSON else None,account_ids=accounts,instrument_ids=ids,start_date=None,search='',audit=False,include_disabled=True,reporting_currency='USD')
    snapshot=load_snapshot(connection,scope,with_prices=False,cancel=cancel)
    snapshot.extras['broker_trades']=[r for r in snapshot.extras['broker_trades'] if r['business_date']<=str(scope.as_of)]
    key=digest((scope.as_of,input_value(snapshot,prices=False)))
    with _LOCK:cached=_CACHE.get(key)
    if cached is not None:return cached
    # Engine monthly atomic units persist through the existing active store when
    # available. The source ledger remains in this single read transaction.
    source=next((r[2] for r in connection.execute('PRAGMA database_list') if r[1]=='main'),None)
    store=DerivedStore(source) if source else None
    if store and store.available:
        with store.active():positions,traces,_=replay(snapshot,cancel=cancel)
    else:positions,traces,_=replay(snapshot,cancel=cancel)
    before={}
    with localcontext() as ctx:
        ctx.prec=50
        seeds,cutoffs=opening_positions(snapshot);state={}
        events=[(e.day,event_order_time(e),1,e.sequence,e.id,'TRADE',e) for e in snapshot.events]
        events += [(a['effective_date'],a['effective_date']+' 00:00:00',0,0,a['id'],'ACTION',a) for a in snapshot.actions]
        events += [(cutoffs[k],cutoffs[k]+' 99:00:00',0,0,0,'OPENING',p) for k,p in seeds.items()]
        for day,_,_,_,_,kind,event in sorted(events,key=lambda v:v[:5]):
            if kind=='OPENING':state[event.key]=deepcopy(event);continue
            if kind=='ACTION':
                factor=Decimal(event['ratio_numerator'])/Decimal(event['ratio_denominator'])
                for k,p in state.items():
                    if p is not None and action_applies(event,k,snapshot) and day>cutoffs.get(k,''):p.quantity=normalize_quantity(p.quantity*factor)
                continue
            if event.id not in traces:continue
            p=state.get(event.key,Position(event.key))
            before[event.id]=(p.quantity,p.cost) if p is not None else (None,None)
            quantity,net,cost=traces[event.id]
            # Install the authoritative engine trace, never calculate the fill's
            # cost independently. Position.cost supplies split-adjusted state.
            state[event.key]=Position(event.key,quantity=quantity,buy=net,sell=Decimal(0)) if quantity is not None else None
    cached=(positions,traces,dict(snapshot.extras.get('cost_diffs',{})),before)
    try:
        from portfolio.engine.scalping import scalping
        _, _, micro_tags, _ = scalping(snapshot, cancel=cancel)
    except Exception:
        micro_tags = {}
    cached = (*cached, micro_tags)
    with _LOCK:
        _CACHE[key]=cached;_CACHE.move_to_end(key)
        while len(_CACHE)>8:_CACHE.popitem(last=False)
    return cached
