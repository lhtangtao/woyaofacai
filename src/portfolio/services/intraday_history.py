"""Reconstruct intraday equity P&L from completed bars and timed ledger events."""
from bisect import bisect_right
from collections import defaultdict
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
from portfolio.engine.close_baseline import previous_trading_day
from portfolio.engine.replay import action_applies, normalize_quantity, replay
from portfolio.services.live_quotes import _SDK_VALUES_LOCK, create_context


def scope_key(path, scope):
    return hashlib.sha256(json.dumps((str(path), asdict(scope)), sort_keys=True, default=str).encode()).hexdigest()


def source_version(path):
    files = (Path(path), Path(str(path)+'-wal'))
    return json.dumps(('intraday-channel-timezones-v4', [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files if p.exists()]))


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


def rebuild_history(path, analytics, credentials_path, store, day, now, cancel=None):
    with localcontext() as context:
        context.prec = 50
        return _rebuild(path, analytics, credentials_path, store, day, now, cancel)


def _rebuild(path, analytics, credentials_path, store, day, now, cancel):
    from longbridge.openapi import AdjustType, Period, TradeSessions
    target = date.fromisoformat(day)
    start = datetime.combine(target - timedelta(days=1), time(20), NY).astimezone(UTC)
    end = datetime.combine(target, time(20), NY).astimezone(UTC)
    cutoff = min(now.astimezone(UTC), end)
    version = source_version(path)
    if analytics.trading_days and day not in analytics.trading_days:
        return '选中日期不是美股交易日，没有当日交易时段曲线。'
    if cutoff <= start:
        return '本交易日尚未开始；曲线覆盖美东前晚20:00至当晚20:00，按接口实际返回时段展示。'
    with readonly_connection(path, cancel=cancel) as connection:
        snapshot = load_snapshot(connection, analytics.scope, cancel=cancel)
    store.supplement(snapshot)
    _, days, half_days, _ = store.load(now)
    regular_start = datetime.combine(target, time(9, 30), NY).astimezone(UTC)
    regular_end = datetime.combine(target, time(13 if target in half_days else 16), NY).astimezone(UTC)
    past = replace(snapshot, events=[e for e in snapshot.events if e.day < day],
                   actions=[a for a in snapshot.actions if a['effective_date'] < day],
                   baselines=[b for b in snapshot.baselines if b['baseline_date'] < day], issues=[])
    positions, _, _ = replay(past, cancel=cancel)
    events = [e for e in snapshot.events if e.day == day]
    keys = {k for k, p in positions.items() if p.quantity >= Decimal('0.5') or p.quantity < 0}
    keys.update(e.key for e in events)
    keys = {k for k in keys if snapshot.instruments[k[2]]['market'] == 'US'}
    if not keys:
        return '当前筛选没有当日期初持仓或成交。'
    expected = previous_trading_day(analytics, day, calendar=days)
    closes = {p.instrument_id: p.value for p in snapshot.prices
              if p.day == expected and p.kind == 'DAILY_CLOSE' and p.basis in ('RAW', 'UNKNOWN')}
    if not closes:
        price_days = [p.day for p in snapshot.prices if p.kind == 'DAILY_CLOSE' and p.basis in ('RAW', 'UNKNOWN') and p.day < day]
        if price_days:
            expected = max(price_days)
            closes = {p.instrument_id: p.value for p in snapshot.prices
                      if p.day == expected and p.kind == 'DAILY_CLOSE' and p.basis in ('RAW', 'UNKNOWN')}
    groups = defaultdict(list)
    for key in keys:
        groups[(key[1], key[2])].append(key)
    timed, invalid = defaultdict(list), set()
    notices = []
    estimated_open = 0
    for event in events:
        if event.key not in keys:
            continue
        group = event.account_id, event.instrument_id
        try:
            if assumes_open(event):
                estimated_open += 1
            stamp = event_time(event)
            if not start <= stamp < end:
                raise ValueError('成交时间与业务日期不一致')
            timed[group].append((stamp, event.sequence, event.id, event))
        except (ValueError, TypeError):
            invalid.add(group)
            notices.append(f'{snapshot.instruments[event.instrument_id]["symbol"]} 流水 {event.id} 缺少可靠成交时刻')
    if estimated_open:
        notices.append(f'第一证券按开盘时刻估算：{estimated_open}笔仅日期流水统一视为09:30美东成交（用户指定），未改写原始流水。')
    for baseline in snapshot.baselines:
        if baseline.get('baseline_role') == 'OPENING' and baseline['baseline_date'] == day and (baseline['account_id'], baseline['instrument_id']) in groups:
            invalid.add((baseline['account_id'], baseline['instrument_id']))
            notices.append('当日开账基准缺少盘中生效时刻')
    for action in snapshot.actions:
        if action['effective_date'] == day:
            for group, group_keys in groups.items():
                if any(action_applies(action, key, snapshot) for key in group_keys):
                    invalid.add(group)
                    notices.append(f'{snapshot.instruments[group[1]]["symbol"]} 当日拆并股缺少准确生效时刻，暂不确认盘中盈亏')
    # Fetch only a missing tail. Confirmed completed candles never change into
    # current quotes, and no provisional final candle enters the archive.
    prices, covered_until, context = {}, {}, None
    fetch_failures = set()
    for instrument in sorted({k[2] for k in keys}):
        if cancel and cancel.is_set():
            raise ValueError('查询已取消')
        symbol = snapshot.instruments[instrument]['symbol'] + '.US'
        cached, fetched = store.intraday_candles(symbol, day)
        holes = regular_gaps(cached, regular_start, regular_end, cutoff)
        due = (fetched is None or ((not cached or fetched < end)
               and int(now.timestamp()) // 300 > int(fetched.timestamp()) // 300)
               or (holes and now - fetched >= timedelta(seconds=60)))
        if due:
            try:
                if context is None:
                    context = create_context(credentials_path)
                with _SDK_VALUES_LOCK:
                    if cached:
                        candles = context.history_candlesticks_by_offset(symbol, Period.Min_5, AdjustType.NoAdjust,
                                                                        True, 1000, cached[-1][0].astimezone(NY).replace(tzinfo=None), TradeSessions.All)
                    else:
                        candles = context.history_candlesticks_by_date(symbol, Period.Min_5, AdjustType.NoAdjust,
                                                                      target - timedelta(days=1), target, TradeSessions.All)
                    def decode(values):
                        result = []
                        for candle in values:
                            stamp = candle.timestamp.astimezone(UTC)
                            phase = str(candle.trade_session).rsplit('.', 1)[-1]
                            value = Decimal(str(candle.close))
                            if start <= stamp and stamp + timedelta(minutes=5) <= cutoff and phase in ('Overnight', 'Pre', 'Intraday', 'Post') and value.is_finite() and value > 0:
                                result.append((stamp, phase, value))
                        return result
                    found = decode(candles)
                    # An empty tail is not evidence of complete coverage.
                    # Repair older holes as well as a provider's empty offset.
                    if cached and regular_gaps(cached + found, regular_start, regular_end, cutoff):
                        found += decode(context.history_candlesticks_by_date(symbol, Period.Min_5, AdjustType.NoAdjust,
                                                                            target, target, TradeSessions.All))
                if found:
                    store.save_intraday_candles(symbol, day, found, now)
                cached, fetched = store.intraday_candles(symbol, day)
            except Exception:
                fetch_failures.add(instrument)
        prices[instrument] = cached
        covered_until[instrument] = min(fetched, cutoff) if fetched else start
    # Quantity is advanced chronologically; cash flows enter only AFTER their
    # actual execution, including ownership transfers. Split ratios change
    # quantity, never the previous close market-value baseline.
    state = {}
    for group, group_keys in groups.items():
        initial = sum((positions[k].quantity for k in group_keys if k in positions), Decimal(0))
        opening = initial * closes[group[1]] if initial != 0 and group[1] in closes else Decimal(0) if initial == 0 else None
        quantity = initial
        for action in snapshot.actions:
            if action['effective_date'] == day:
                for k in group_keys:
                    if k in positions and action_applies(action, k, snapshot):
                        quantity += positions[k].quantity * (Decimal(action['ratio_numerator']) / Decimal(action['ratio_denominator']) - 1)
        bad = group in invalid or initial < 0 or any('成交粒度冲突' in positions[k].warnings for k in group_keys if k in positions)
        state[group] = [quantity, opening, Decimal(0), Decimal(0), 0, sorted(timed[group]), bad]
    rows = []
    missing_regular = defaultdict(set)
    stamp = start + timedelta(minutes=5)
    while stamp <= cutoff:
        if cancel and cancel.is_set():
            raise ValueError('查询已取消')
        values = []
        for group, s in state.items():
            while s[4] < len(s[5]) and s[5][s[4]][0] <= stamp:
                event = s[5][s[4]][3]
                quantity = s[0] + (event.quantity if event.incoming else -event.quantity)
                s[0] = quantity if assumes_open(event) else normalize_quantity(quantity)
                if event.amount is None or event.metadata.get('link_status') == 'REVIEW_REQUIRED' or (s[0] < 0 and not assumes_open(event)):
                    s[6] = True
                else:
                    s[2 if event.incoming else 3] += event.amount
                s[4] += 1
            s[0] = normalize_quantity(s[0])
            if s[0] < 0:
                s[6] = True
            candles = prices[group[1]]
            index = bisect_right([item[0] for item in candles], stamp - timedelta(minutes=5)) - 1
            price = candles[index][2] if index >= 0 else None
            if regular_start < stamp <= regular_end:
                exact = index >= 0 and candles[index][0] == stamp-timedelta(minutes=5) and candles[index][1] == 'Intraday'
                if not exact:
                    price = None
                    if s[0] != 0 and stamp <= cutoff-timedelta(seconds=60):
                        missing_regular[group[1]].add(stamp-timedelta(minutes=5))
            # A security with no exposure yet needs no invented price. No
            # forward-looking bar is used even if the provider returns it.
            market_value = (Decimal(0) if s[0] == 0 else s[0] * price
                            if price is not None and stamp <= covered_until[group[1]] else None)
            profit = market_value - s[1] - s[2] + s[3] if market_value is not None and s[1] is not None and not s[6] else None
            channel = snapshot.accounts[group[0]]['broker_code']
            channel = {'LONGBRIDGE': '长桥', 'FIRSTRADE': '第一证券', 'SCHWAB': '嘉信'}.get(channel, channel)
            values.append(dict(channel=channel, ticker=snapshot.instruments[group[1]]['symbol'], pnl=str(profit) if profit is not None else None))
        rows.append((stamp, 'History5', values))
        stamp += timedelta(minutes=5)
    if source_version(path) != version:
        return '源数据库在计算期间发生变化，已停止保存本次曲线，请刷新重算。'
    if missing_regular:
        descriptions = [gap_description(snapshot.instruments[i]['symbol']+'.US', sorted(stamps))
                        + ('，接口补取失败' if i in fetch_failures else '') for i, stamps in sorted(missing_regular.items())]
        notices.insert(0, '盘中行情缺失（北京时间；对应区段已断线，不沿用旧价）：\n'+'\n'.join(descriptions))
    warning = '；'.join(dict.fromkeys(notices))
    store.save_historical_pnl(scope_key(path, analytics.scope), day, rows, version, warning)
    return warning
