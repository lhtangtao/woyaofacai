"""Persist reusable engine units, not UI filter/report combinations.

Replay is split by owner/account/security and calendar month. Performance and
micro matching are cached by that same unit; portfolio ratios are recomputed.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta

from portfolio.domain.analytics import Report, ratio, strict_sum
from portfolio.domain.models import Ledger
from portfolio.services.derived_store import ACTIVE_STORE, digest


def units(snapshot):
    keys = {e.key for e in snapshot.events}
    person = snapshot.scope.ledger == Ledger.PERSON
    for b in snapshot.baselines:
        key = (b['person_id'] if person else None,b['account_id'],b['instrument_id'])
        if b['baseline_role']=='OPENING' and b['scope_type']==('PERSON_ACCOUNT' if person else 'ACCOUNT') and key[1] is not None and key[2] is not None:
            s=snapshot.scope
            if ((s.person_ids is None or key[0] in s.person_ids) and (s.account_ids is None or key[1] in s.account_ids)
                and (s.instrument_ids is None or key[2] in s.instrument_ids) and (not s.category or snapshot.instruments[key[2]]['category']==s.category)
                and (not person or s.include_disabled or snapshot.persons.get(key[0],{}).get('enabled'))): keys.add(key)
    return sorted(keys,key=repr)


def unit_snapshot(snapshot,key):
    person,account,instrument=key
    events=[replace(e,metadata={k:v for k,v in e.metadata.items() if k!='cost_diff'}) for e in snapshot.events if e.key==key]
    # Order conflicts can involve another security; retain orders sharing this account.
    brokers=[r for r in snapshot.extras.get('broker_trades',[]) if r['account_id']==account]
    extras={'broker_trades':brokers,'trading_days':snapshot.extras.get('trading_days',()),
            'performance_price_days':sorted({p.day for p in snapshot.prices if p.kind in ('DAILY_CLOSE','ESTIMATE_CLOSE')})}
    return replace(snapshot,scope=replace(snapshot.scope,person_ids=(person,) if person is not None else None,
                   account_ids=(account,),instrument_ids=(instrument,),start_date=None,search='',include_disabled=True),
                   events=events,actions=[a for a in snapshot.actions if a['instrument_id']==instrument],
                   baselines=[b for b in snapshot.baselines if b['account_id']==account and b['instrument_id']==instrument and (person is None or b['person_id']==person)],
                   prices=[p for p in snapshot.prices if p.instrument_id==instrument],extras=extras,issues=[])


def input_value(snapshot, *, prices=True):
    return (snapshot.scope.ledger,snapshot.scope.reporting_currency,snapshot.events,snapshot.actions,snapshot.baselines,
            [p for p in snapshot.prices if p.kind in ('DAILY_CLOSE','ESTIMATE_CLOSE')] if prices else (),snapshot.accounts,snapshot.instruments,
            snapshot.extras.get('broker_trades',()),snapshot.extras.get('performance_price_days',()) if prices else ())


def replay_cached(snapshot,capture_days,cancel,raw):
    store=ACTIVE_STORE.get()
    if not store or not store.available:return raw(snapshot,capture_days,cancel)
    from portfolio.engine.replay import grain_conflicts
    # Grain conflicts are evaluated globally before separating owner keys.
    positions,traces,captures={}, {}, {d:{} for d in capture_days}
    for key in units(snapshot):
        atom=unit_snapshot(snapshot,key)
        dates=[e.day for e in atom.events]+[b['baseline_date'] for b in atom.baselines]
        if not dates:continue
        start=min(dates);end=snapshot.scope.as_of.isoformat()
        requested=sorted(set(capture_days)|{end})
        months=sorted({d[:7] for d in requested if d>=start}|{e.day[:7] for e in atom.events}|{a['effective_date'][:7] for a in atom.actions if a['effective_date']>=start})
        state={};all_traces={};all_issues=[];previous='';costs={}
        for month in months:
            last=min(end, (date(int(month[:4])+ (month[5:]=='12'),1 if month[5:]=='12' else int(month[5:])+1,1)-timedelta(days=1)).isoformat())
            if last<start:continue
            # Capture every day so arbitrary historic as_of does not create a
            # separate whole-report cache. Stored states only for held keys.
            cursor=max(date.fromisoformat(start),date.fromisoformat(month+'-01'))
            days=[]
            while cursor.isoformat()<=last:days.append(cursor.isoformat());cursor+=timedelta(days=1)
            prefix=replace(atom,scope=replace(atom.scope,as_of=date.fromisoformat(last)),events=[e for e in atom.events if e.day<=last],
                           actions=[a for a in atom.actions if a['effective_date']<=last],
                           baselines=[b for b in atom.baselines if b['baseline_date']<=last],issues=[])
            prefix.extras={**atom.extras,'broker_trades':[r for r in atom.extras['broker_trades'] if r['business_date']<=last]}
            dep=digest((input_value(prefix,prices=False),state,previous,days))
            cache_key=digest((snapshot.scope.ledger,key,month,last,snapshot.scope.reporting_currency))
            cached=store.get('replay-month',cache_key,dep)
            if cached is None:
                result=raw(prefix,days,cancel,initial=deepcopy(state),after=previous)
                cached=(result,prefix.issues,prefix.extras.get('cost_diffs',{}))
                store.put('replay-month',cache_key,dep,cached)
            (state,month_traces,states),issues,month_costs=cached
            all_traces.update(month_traces);all_issues.extend(issues);costs.update(month_costs)
            for d in capture_days:
                if d in states:captures[d].update(deepcopy(states[d]))
            previous=last
        positions.update(deepcopy(state));traces.update(all_traces);snapshot.issues.extend(all_issues)
        snapshot.extras.setdefault('cost_diffs',{}).update(costs)
        for e in snapshot.events:
            if e.id in costs:e.metadata['cost_diff']=costs[e.id]
    return positions,traces,captures


def performance_cached(snapshot,micro_profit,cancel,raw):
    store=ACTIVE_STORE.get()
    # Original multi-owner grouped-position semantics (not sums of rates) stay
    # in the original engine. Its replay still uses cached per-owner checkpoints.
    owners={k[0] for k in units(snapshot)}
    if not store or not store.available or len(owners)>1:return raw(snapshot,micro_profit,cancel=cancel)
    positions,traces,reports,days={}, {}, {}, set()
    for key in units(snapshot):
        atom=unit_snapshot(snapshot,key)
        micro={k:v for k,v in (micro_profit or {}).items() if k[1:]==key[1:]}
        dep=digest((input_value(atom),micro))
        ck=digest((snapshot.scope.ledger,key,snapshot.scope.reporting_currency))
        cached=store.get('performance-unit',ck,dep)
        if cached is None:
            result=raw(atom,micro,cancel=cancel)
            cached=(result,atom.issues,atom.extras.get('cost_diffs',{}))
            store.put('performance-unit',ck,dep,cached)
        (pos,trace,reps,ds),issues,costs=cached
        positions.update(deepcopy(pos));traces.update(trace);days.update(ds);snapshot.issues.extend(issues)
        snapshot.extras.setdefault('cost_diffs',{}).update(costs)
        for name,rep in reps.items():
            if name not in reports:reports[name]=rep
            else:reports[name]=replace(reports[name],rows=reports[name].rows+rep.rows)
    # Market share is a portfolio denominator, never sum/average unit rates.
    for name,rep in list(reports.items()):
        rows=sorted(rep.rows,key=lambda r:(r.values[0],r.metadata['account_id'],r.metadata['instrument_id']))
        if name in ('每日持仓收益','每月持仓收益'):
            totals=defaultdict(list)
            for r in rows:
                if r.values[8] is not None and r.values[8]>0:totals[r.values[0]].append(r.values[11])
            rows=[replace(r,values=(*r.values[:12],ratio(r.values[11],strict_sum(totals[r.values[0]])),*r.values[13:])) for r in rows]
        reports[name]=replace(rep,rows=tuple(rows))
    return positions,traces,reports,sorted(days)


def scalping_cached(snapshot,policy,cancel,raw):
    store=ACTIVE_STORE.get()
    if not store or not store.available:return raw(snapshot,policy=policy,cancel=cancel)
    from portfolio.engine.scalping import MICRO_COLUMNS,PAIR_COLUMNS
    summary=[];pairs=[];tags={};profits=defaultdict(lambda:0)
    for key in units(snapshot):
        atom=unit_snapshot(snapshot,key);dep=digest((input_value(atom,prices=False),policy,bool(atom.prices)))
        ck=digest((snapshot.scope.ledger,key,policy))
        cached=store.get('micro-unit',ck,dep)
        if cached is None:
            result=raw(atom,policy=policy,cancel=cancel);cached=(result,atom.issues)
            store.put('micro-unit',ck,dep,cached)
        (s,p,t,g),issues=cached
        summary.extend(s.rows);pairs.extend(p.rows);tags.update(t);snapshot.issues.extend(issues)
        for k,v in g.items():profits[k]+=v
    return Report('每月微操收益',MICRO_COLUMNS,tuple(summary)),Report('微操配对证据',PAIR_COLUMNS,tuple(pairs)),tags,dict(profits)
