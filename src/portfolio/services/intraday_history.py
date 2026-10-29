"""Reconstruct intraday equity P&L from completed bars and timed ledger events."""
from bisect import bisect_right
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

from portfolio.db.connection import readonly_connection
from portfolio.db.repositories.analytics import load_snapshot
from portfolio.domain.live_quotes import NY, UTC
from portfolio.domain.trade_time import assumes_open, event_time_utc
from portfolio.engine.close_baseline import previous_trading_day, intraday_closes
from portfolio.engine.replay import action_applies, normalize_quantity, replay
from portfolio.services.live_quotes import _SDK_VALUES_LOCK, create_context


def scope_key(path, scope):
    return hashlib.sha256(json.dumps((str(path), asdict(scope)), sort_keys=True, default=str).encode()).hexdigest()


def source_version(path):
    from portfolio.services.derived_dependencies import source_identity
    from portfolio.services.derived_store import ALGORITHM
    return ALGORITHM + ':' + source_identity(path,financial_only=True)



def regular_gaps(candles, first, last, cutoff):
    present = {stamp for stamp, phase, _ in candles if phase == 'Intraday'}
    missing, stamp = [], first
    while stamp + timedelta(minutes=5) <= min(last, cutoff - timedelta(seconds=60)):
        if stamp not in present:
            missing.append(stamp)
        stamp += timedelta(minutes=5)
    return missing


def gap_description(symbol, stamps):
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('Asia/Shanghai')
    spans, first, last = [], stamps[0], stamps[0]
    for stamp in stamps[1:]:
        if stamp != last + timedelta(minutes=5):
            spans.append((first, last))
            first = stamp
        last = stamp
    spans.append((first, last))
    text = ', '.join(a.astimezone(zone).strftime('%m-%d %H:%M:%S') + '—' + (b+timedelta(minutes=5)).astimezone(zone).strftime('%m-%d %H:%M:%S') for a, b in spans)
    return f'{symbol}：{text}（{len(stamps)}根5分钟K线）'


def event_time(event):
    return event_time_utc(event)


def rebuild_history(path, analytics, credentials_path, store, day, now, cancel=None, *, prepared_snapshot=None):
    with localcontext() as context:
        context.prec = 50
        return _rebuild(path, analytics, credentials_path, store, day, now, cancel, prepared_snapshot)


def _rebuild(path, analytics, credentials_path, store, day, now, cancel, prepared_snapshot):
    target = date.fromisoformat(day)
    start = datetime.combine(target - timedelta(days=1), time(20), NY).astimezone(UTC)
    end = datetime.combine(target, time(20), NY).astimezone(UTC)
    cutoff = min(now.astimezone(UTC), end)
    version = source_version(path)
    if analytics.trading_days and day not in analytics.trading_days:
        return '选中日期不是美股交易日，没有当日交易时段曲线。'
    if cutoff <= start:
        return '本交易日尚未开始；曲线覆盖美东前晚20:00至当晚20:00，按接口实际返回时段展示。'
    if prepared_snapshot is None:
        with readonly_connection(path, cancel=cancel) as connection:
            snapshot = load_snapshot(connection, analytics.scope, cancel=cancel)
        store.supplement(snapshot)
    else:
        snapshot = prepared_snapshot
    _, days, half_days, _ = store.load(now)
    # Prepared callers include real dates discovered in saved price tables.
    # A shorter calendar cache must not make those dates disappear.
    days=days|{date.fromisoformat(d) for d in analytics.trading_days}
    # Calendar caches may start on Jan 2. Actual earlier daily-close dates
    # retain the Dec 31 year-opening baseline instead of guessing Jan 1.
    if days:
        first_calendar=min(days)
        days|={date.fromisoformat(p.day) for p in snapshot.prices if p.kind=='DAILY_CLOSE'
               and snapshot.instruments.get(p.instrument_id,{}).get('market')=='US'
               and p.basis in ('RAW','UNKNOWN') and date.fromisoformat(p.day)<first_calendar}
    regular_start = datetime.combine(target, time(9, 30), NY).astimezone(UTC)
    regular_end = datetime.combine(target, time(13 if target in half_days else 16), NY).astimezone(UTC)
    past = replace(snapshot, scope=replace(snapshot.scope, as_of=target-timedelta(days=1)), events=[e for e in snapshot.events if e.day < day],
                   actions=[a for a in snapshot.actions if a['effective_date'] < day],
                   baselines=[b for b in snapshot.baselines if b['baseline_date'] < day],
                   extras={**snapshot.extras,'_positions_only':True}, issues=[])
    positions, _, _ = replay(past, cancel=cancel)
    events = [e for e in snapshot.events if e.day == day]
    keys = {k for k, p in positions.items() if p.quantity >= Decimal('0.5')}
    keys.update(e.key for e in events)
    keys = {k for k in keys if snapshot.instruments[k[2]]['market'] == 'US'}
    if not keys:
        return '当前筛选没有当日期初持仓或成交。'
    expected = previous_trading_day(analytics, day, calendar=days)
    baseline_prices=intraday_closes(snapshot.prices,expected)
    closes={identifier:price.value for identifier,price in baseline_prices.items()}
    groups = defaultdict(list)
    for key in keys:
        groups[(key[1], key[2])].append(key)
    timed, invalid = defaultdict(list), set()
    notices = []
    missing_baselines=sorted({snapshot.instruments[k[2]]['symbol'] for k in keys if k in positions and positions[k].quantity and k[2] not in closes})
    if missing_baselines:notices.append(f'缺少上一交易日 {expected} 的可信收盘基准：'+','.join(missing_baselines)+'；不回退到更早收盘价。')
    if any(p.kind=='ESTIMATE_CLOSE' for p in baseline_prices.values()):notices.append(f'期初基准含 {expected} 近收盘估值（CLOSE_ESTIMATE），与持仓卡片规则一致，非正式收盘价。')
    estimated_open = 0
    for event in events:
        if event.key not in keys:
            continue
        group = event.account_id, event.instrument_id
        try:
            if assumes_open(event):
                estimated_open += 1
            stamp = event_time(event)
            if stamp < start:
                stamp = start
            elif stamp >= end:
                stamp = end - timedelta(seconds=1)
            timed[group].append((stamp, event.sequence, event.id, event))
        except (ValueError, TypeError):
            invalid.add(group)
            notices.append(f'{snapshot.instruments[event.instrument_id]["symbol"]} 流水 {event.id} 缺少可靠成交时刻')
    if estimated_open:
        notices.append(f'按开盘时刻估算：{estimated_open}笔仅日期流水统一视为09:30美东成交（用户指定），未改写原始流水。')
    for baseline in snapshot.baselines:
        if baseline.get('baseline_role') == 'OPENING' and baseline['baseline_date'] == day and (baseline['account_id'], baseline['instrument_id']) in groups:
            invalid.add((baseline['account_id'], baseline['instrument_id']))
            notices.append('当日开账基准缺少盘中生效时刻')
    for action in snapshot.actions:
        if action['effective_date'] == day:
            for group, group_keys in groups.items():
                if any(action_applies(action, key, snapshot) for key in group_keys):
                    notices.append(f'{snapshot.instruments[group[1]]["symbol"]} 当日拆并股按开盘时刻09:30美东生效')
    prices,covered_until={},{}
    fetch_failures=set()
    if credentials_path:
        fetch_failures=collect_day_prices(snapshot,day,now,store,credentials_path,instrument_ids={k[2] for k in keys})['failed']
    for instrument in sorted({k[2] for k in keys}):
        cached,fetched=store.intraday_candles(snapshot.instruments[instrument]['symbol']+'.US',day)
        prices[instrument]=cached
        last_bar=max((r[0]+timedelta(minutes=5) for r in cached),default=start)
        covered_until[instrument]=cutoff if (cached or instrument in closes) else min(cutoff,max(last_bar,fetched or start))
    input_revision = store.day_revision(day)
    # Quantity is advanced chronologically; cash flows enter only AFTER their
    # actual execution, including ownership transfers. Split ratios change
    # quantity, never the previous close market-value baseline.
    state = {}
    for group, group_keys in sorted(groups.items()):
        initial = sum((positions[k].quantity for k in group_keys if k in positions), Decimal(0))
        opening = initial * closes[group[1]] if initial >= Decimal('0.5') and group[1] in closes else Decimal(0) if initial < Decimal('0.5') else None
        split_ratio = Decimal(1)
        for action in snapshot.actions:
            if action['effective_date'] == day:
                for k in group_keys:
                    if k in positions and action_applies(action, k, snapshot):
                        split_ratio *= (Decimal(action['ratio_numerator']) / Decimal(action['ratio_denominator']))
        bad = group in invalid or any('成交粒度冲突' in positions[k].warnings for k in group_keys if k in positions)
        state[group] = [initial, opening, Decimal(0), Decimal(0), 0, sorted(timed[group]), bad, split_ratio, False]
    from portfolio.services.derived_store import digest
    baseline_identity=tuple((i,p.day,p.kind,p.status,p.value,p.basis) for i,p in sorted(baseline_prices.items()))
    state_dependency=digest((snapshot.scope.ledger,snapshot.scope.person_ids,day,state,baseline_identity))
    checkpoint=store.load_checkpoint(state_dependency) if hasattr(store,'load_checkpoint') else None
    price_ids=tuple(sorted(prices))
    resume_after=None
    if checkpoint and checkpoint.get('price_hash')==store.prefix_identity(day,checkpoint['last'],price_ids):
        state=checkpoint['state'];resume_after=checkpoint['last']
    rows=[];missing_regular=defaultdict(set)
    observed={stamp+timedelta(minutes=5) for candles in prices.values() for stamp,_,_ in candles if stamp+timedelta(minutes=5)<=cutoff}
    if observed or closes:
        cur_slot = start + timedelta(minutes=5)
        while cur_slot <= cutoff:
            observed.add(cur_slot)
            cur_slot += timedelta(minutes=5)
    stamp=(resume_after or start)+timedelta(minutes=5)
    saved_state=deepcopy(state) if resume_after else None
    saved_last=resume_after
    while stamp <= cutoff:
        if cancel and cancel.is_set():
            raise ValueError('查询已取消')
        values = []
        for group, s in state.items():
            candles = prices[group[1]]
            index = bisect_right([item[0] for item in candles], stamp - timedelta(minutes=5)) - 1
            if index >= 0:
                price = candles[index][2]
            else:
                price = closes.get(group[1])
            # During the regular session an older bar is not a current price.
            # The valuation stamp is the end of its completed five-minute bar.
            missing_bar = regular_start < stamp <= regular_end and (
                index < 0 or candles[index][0] != stamp-timedelta(minutes=5)
                or candles[index][1] != 'Intraday')
            applied = s[8] if len(s) > 8 else False
            ratio = s[7] if len(s) > 7 else Decimal(1)
            if not applied and ratio != Decimal(1):
                prev_close = closes.get(group[1])
                early_post_split = (
                    index >= 0
                    and price is not None
                    and prev_close is not None
                    and prev_close > 0
                    and abs(price - (prev_close / ratio)) < abs(price - prev_close)
                )
                if stamp >= regular_start or early_post_split:
                    if len(s) > 8:
                        s[8] = True
                    s[0] = normalize_quantity(s[0] * ratio)
            elif not applied and stamp >= regular_start and len(s) > 8:
                s[8] = True
            while s[4] < len(s[5]) and s[5][s[4]][0] <= stamp:
                event = s[5][s[4]][3]
                quantity = s[0] + (event.quantity if event.incoming else -event.quantity)
                s[0] = quantity if assumes_open(event) else normalize_quantity(quantity)
                if event.amount is None or event.metadata.get('link_status') == 'REVIEW_REQUIRED':
                    s[6] = True
                else:
                    s[2 if event.incoming else 3] += event.amount
                s[4] += 1
            s[0] = normalize_quantity(s[0])
            if missing_bar and s[0] > 0:
                price = None
                missing_regular[group[1]].add(stamp-timedelta(minutes=5))
            # A security with no exposure yet needs no invented price. No
            # forward-looking bar is used even if the provider returns it.
            # Money2 policy: debt shares valued at zero.
            market_value = (Decimal(0) if s[0] <= 0 else s[0] * price
                            if price is not None and stamp <= covered_until[group[1]] else None)
            profit = market_value - s[1] - s[2] + s[3] if market_value is not None and s[1] is not None and not s[6] else None
            channel = snapshot.accounts[group[0]]['broker_code']
            channel = {'LONGBRIDGE': '长桥', 'FIRSTRADE': '第一证券', 'SCHWAB': '嘉信'}.get(channel, channel)
            values.append(dict(channel=channel, ticker=snapshot.instruments[group[1]]['symbol'],
                               account_id=group[0], instrument_id=group[1], quantity=str(s[0]),
                               market_value=str(market_value) if market_value is not None else None,
                               opening_value=str(s[1]) if s[1] is not None else None,buy=str(s[2]),sell=str(s[3]),
                               pnl=str(profit) if profit is not None else None))
        if stamp in observed:
            rows.append((stamp,'History5',values))
            saved_state=deepcopy(state);saved_last=stamp
        stamp += timedelta(minutes=5)
    if source_version(path) != version:
        raise ValueError('源数据库在计算期间发生变化，本次曲线未保存。')
    if missing_regular:
        descriptions = [gap_description(snapshot.instruments[i]['symbol']+'.US', sorted(stamps))
                        + ('，接口补取失败' if i in fetch_failures else '') for i, stamps in sorted(missing_regular.items())]
        notices.insert(0, '盘中行情缺失（北京时间；对应区段已断线，不沿用旧价）：\n'+'\n'.join(descriptions))
    if resume_after and checkpoint.get('warning'):notices.insert(0,checkpoint['warning'])
    warning = '；'.join(dict.fromkeys(notices))
    if not rows and not resume_after:warning=warning or '尚无已完成的真实五分钟K线；等待采集或补取，不填零。'
    if store.day_revision(day) != input_revision:
        raise ValueError('五分钟行情在计算期间变化，本次结果未保存。')
    saved_checkpoint=None
    if saved_last:
        saved_checkpoint=dict(state=saved_state,last=saved_last,price_ids=price_ids,price_hash=store.prefix_identity(day,saved_last,price_ids),state_dependency=state_dependency)
    store.save_historical_pnl(scope_key(path,analytics.scope),day,rows,version,warning,input_revision=input_revision,checkpoint=saved_checkpoint,resume_after=resume_after)
    return warning


def collect_day_prices(snapshot,day,now,store,credentials_path,*,instrument_ids=None):
    """One global symbol/day collection; only absent completed bars are saved."""
    target=date.fromisoformat(day)
    start=datetime.combine(target-timedelta(days=1),time(20),NY).astimezone(UTC)
    end=datetime.combine(target,time(20),NY).astimezone(UTC)
    cutoff=min(now.astimezone(UTC)-timedelta(seconds=60),end)
    _,_,halves,_=store.load(now)
    regular_start=datetime.combine(target,time(9,30),NY).astimezone(UTC)
    regular_end=datetime.combine(target,time(13 if target in halves else 16),NY).astimezone(UTC)
    context=None;saved=0;failed=set();latest=None
    for identifier in sorted(instrument_ids or ()):
        instrument=snapshot.instruments[identifier]
        if instrument['market']!='US':continue
        symbol=instrument['symbol']+'.US'
        cached,fetched=store.intraday_candles(symbol,day)
        # Existing local bars are copied as legacy evidence, never upgraded to
        # confirmed provider bars and never downloaded again just for an owner.
        if cached and store.source:
            from portfolio.services.history_facts import append_completed
            append_completed(store.source,symbol,day,cached,confirmation='LEGACY_COMPLETED')
        present={stamp for stamp,_,_ in cached}
        holes=regular_gaps(cached,regular_start,regular_end,cutoff+timedelta(seconds=60))
        last=max(present,default=None)
        latest_complete=datetime.fromtimestamp(int(cutoff.timestamp())//300*300,UTC)
        complete_tail=last is not None and last+timedelta(minutes=5)>=latest_complete
        historical_done=cutoff>=end and fetched is not None and fetched>=end
        due=(holes or not cached or (not complete_tail and not historical_done)) and (fetched is None or now-fetched>=timedelta(seconds=60))
        if due and credentials_path:
            try:
                from longbridge.openapi import AdjustType,Period,TradeSessions
                if context is None:context=create_context(credentials_path)
                found=[]
                with _SDK_VALUES_LOCK:
                    if not cached:
                        found.extend(context.history_candlesticks_by_date(symbol,Period.Min_5,AdjustType.NoAdjust,target-timedelta(days=1),target,TradeSessions.All))
                    else:
                        wanted=sorted(set(holes))
                        if last+timedelta(minutes=5)<latest_complete:wanted.append(last+timedelta(minutes=5))
                        # Bound each request to a missing span; SDK positioning
                        # may include one stored anchor, which is never re-saved.
                        spans=[]
                        for stamp in sorted(set(wanted)):
                            if not spans or stamp>spans[-1][1]+timedelta(minutes=5):spans.append([stamp,stamp])
                            else:spans[-1][1]=stamp
                        for first,stop in spans:
                            if first>last:stop=cutoff-timedelta(minutes=5)
                            count=min(1000,max(2,int((stop-first).total_seconds()//300)+2))
                            found.extend(context.history_candlesticks_by_offset(symbol,Period.Min_5,AdjustType.NoAdjust,True,count,(first-timedelta(minutes=5)).astimezone(NY).replace(tzinfo=None),TradeSessions.All))
                new=[]
                for bar in found:
                    stamp=bar.timestamp.astimezone(UTC);phase=str(bar.trade_session).rsplit('.',1)[-1];price=Decimal(str(bar.close))
                    if stamp not in present and start<=stamp and stamp+timedelta(minutes=5)<=cutoff and phase in ('Overnight','Pre','Intraday','Post') and price.is_finite() and price>0:
                        new.append((stamp,phase,price));present.add(stamp)
                store.save_intraday_candles(symbol,day,new,now)
                saved+=len(new)
                cached,_=store.intraday_candles(symbol,day)
                remaining=regular_gaps(cached,regular_start,regular_end,cutoff+timedelta(seconds=60))
                if remaining:
                    from portfolio.services.quote_diagnostics import report
                    report('history_coverage',symbol,ValueError(
                        f'{day}: provider returned {len(found)} bars; saved {len(new)} missing bars; '
                        f'{len(remaining)} completed regular-session bars still missing '
                        f'({remaining[0].isoformat()} to {remaining[-1].isoformat()}); retry eligible in 60 seconds'))
            except Exception as exc:
                failed.add(identifier)
                from portfolio.services.quote_diagnostics import describe_error
                import logging
                info=describe_error(exc)
                logging.getLogger(__name__).warning('Five-minute recovery failed symbol=%s day=%s category=%s detail=%s',symbol,day,info['category'],info['detail'])
        remaining=regular_gaps(cached,regular_start,regular_end,cutoff+timedelta(seconds=60))
        if remaining:
            failed.add(identifier)
            import logging
            logging.getLogger(__name__).warning('Five-minute recovery incomplete symbol=%s day=%s missing=%s first=%s last=%s',symbol,day,len(remaining),remaining[0].isoformat(),remaining[-1].isoformat())
        point=max((r[0]+timedelta(minutes=5) for r in cached),default=None)
        if point:latest=max(latest,point) if latest else point
    return {'saved':saved,'failed':failed,'latest_price':latest}
