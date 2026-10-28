"""Today's movement for currently held securities; no historical/source mutation."""
from decimal import Decimal, localcontext

from portfolio.domain.analytics import Report, ReportRow, ratio, strict_sum
from portfolio.domain.live_quotes import NY, SESSION_LABELS
from portfolio.engine.close_baseline import previous_trading_day, trusted_close, valid_opening, admissible_close
from portfolio.engine.holdings import MIN_OPEN_QUANTITY


def today_holdings(analytics, live, session):
    historical = session is None
    day = analytics.latest_profit_day if historical else (session.end if session.name == 'Overnight' else session.start).astimezone(NY).date().isoformat()
    phase = '收盘' if historical else SESSION_LABELS[session.name]
    expected_close = previous_trading_day(analytics, day) if day else None
    daily = {(r.metadata['account_id'], r.metadata['instrument_id']): r for r in analytics.reports['每日持仓收益'].rows if r.values[0] == day}
    stocks = {(r.metadata['account_id'], r.metadata['instrument_id']): r for r in analytics.reports.get('股票汇总', Report('', (), ())).rows}
    closes = {};close_kinds={}
    for row in analytics.reports.get('行情状态', Report('', (), ())).rows:
        if day and row.values[3] is not None and admissible_close(row.values[1],row.values[2],row.values[4],row.values[5],expected_close):
            key = row.metadata['instrument_id']
            if key not in closes or close_kinds[key]!='DAILY_CLOSE' and row.values[2]=='DAILY_CLOSE':
                closes[key] = (row.values[1], row.values[3])
                close_kinds[key]=row.values[2]
    last_close = max((v[0] for v in closes.values()), default=None)
    unit = '$' if analytics.scope.reporting_currency == 'USD' else '¥'
    with localcontext() as ctx:
        ctx.prec = 50
        result = []
        total_mv = strict_sum(r.values[5] for r in live.rows if r.values[3] is not None and r.values[3] >= MIN_OPEN_QUANTITY)
        for row in live.rows:
            v, meta = row.values, dict(row.metadata)
            if v[3] is not None and v[3] < MIN_OPEN_QUANTITY:
                continue
            key = (meta['account_id'], meta['instrument_id'])
            current = daily.get(key)
            previous, baseline_day = None, None
            if current and current.values[5] is not None and current.values[5] > 0 and current.values[15] is not None:
                previous = current.values[15] / current.values[5]
                baseline_day = '该交易日期初收盘基准'
            elif analytics.scope.reporting_currency == 'USD':
                close = closes.get(meta['instrument_id'])
                if close and close[0] == last_close:
                    baseline_day, previous = close
                elif not historical and trusted_close(meta, expected_close):
                    previous, baseline_day = meta.get('historical_price'), analytics.latest_profit_day
            usable = (meta.get('valuation_source') in ('DAILY_CLOSE', 'ESTIMATE_CLOSE') and meta.get('valuation_date') == day) if historical else meta.get('live_session') == phase
            if not historical and current and not valid_opening(current, expected_close):
                previous = None
            price = v[4] if usable else None
            change = ratio(price - previous, previous) if price is not None and previous is not None else None
            profit = None
            if usable and v[3] is not None:
                if current:
                    opening, bought, sold = current.values[15:18]
                    if historical:
                        profit = current.values[19] if current.values[8] == v[3] else None
                    elif valid_opening(current, expected_close) and price is not None and all(x is not None for x in (opening, bought, sold)):
                        profit = v[3] * price - opening - bought + sold
                elif not historical and previous is not None and price is not None:
                    profit = v[3] * (price - previous)
            meta.update(trading_day=day, previous_close_day=baseline_day, today_pnl=profit, current_asset=v[5],
                        today_formula='实时市值－上一收盘市值－当日买入金额＋当日卖出金额；仅当前仍持有的标的，包含这些标的当日已发生的买卖现金流。',
                        completeness='COMPLETE' if profit is not None else 'INCOMPLETE')
            channel = {'LONGBRIDGE': '长桥', 'FIRSTRADE': '第一证券', 'SCHWAB': '嘉信'}.get(str(v[0]).upper(), v[0])
            stock = stocks.get(key)
            realized = stock.values[15] if stock else 0
            lifetime = v[10] + realized if v[10] is not None and realized is not None else None
            cost = v[6]
            recovery = (cost - price) / price if cost is not None and price is not None and price > 0 and cost > price else None
            prop_val = (v[5] / total_mv) if (total_mv and total_mv > 0 and v[5] is not None) else None
            result.append(ReportRow((channel, v[2], v[3], previous, price, change, profit,
                                     phase if usable else '等待报价', v[-2] if not historical else day or '—', v[6], lifetime, v[5], recovery, prop_val), meta))
    note = f'交易日：{day or "无数据"} · {phase}{"历史收盘数据" if historical else "实时估值，每10秒刷新"}。只展示当前仍持有的标的；股票涨跌幅相对昨收，盈亏计入当日买卖现金流。今日已全清仓的标的不计入本页，与总览全部交易盈亏可能不同；缺少报价/基准显示—；当前市值沿用实时/参考估值，参考价口径见行情状态。'
    if any(r.metadata.get('valuation_source') == 'ESTIMATE_CLOSE' for r in live.rows):
        note += ' 含本机历史估值，非正式收盘数据。'
    if historical:
        selected = analytics.scope.as_of.isoformat()
        note = f'所选日期：{selected} · 有效收盘日：{day or "无数据"}。' + ('所选日无收盘收益记录，展示最近有效收盘数据。' if selected != day else '') + note
    return Report('今日盈亏', ('渠道', 'Ticker', '持仓数量', f'昨收价({unit})', f'最新价({unit})', '今日涨跌幅', f'今日盈亏({unit})', '行情时段', '报价时间', f'成本价({unit})', f'累计盈亏({unit})', f'当前市值({unit})', '回本涨幅', '占比'), tuple(result), note)


def today_summary(rows):
    with localcontext() as ctx:
        ctx.prec = 50
        profit = strict_sum(r.values[6] for r in rows)
        value = strict_sum(r.values[2] * r.values[4] if r.values[2] is not None and r.values[4] is not None else None for r in rows)
        rates = {}
        for row in rows:
            rates.setdefault(row.values[1], row.values[5])
        up = sum(v is not None and v > 0 for v in rates.values())
        down = sum(v is not None and v < 0 for v in rates.values())
        flat = sum(v is not None and v == 0 for v in rates.values())
        unknown = sum(v is None for v in rates.values())
        return profit, ratio(profit, value - profit if value is not None and profit is not None else None), up, down, flat, unknown


def intraday_scope_parts(items):
    """Describe the saved point's scope; never turn missing P&L into zero."""
    if not items:return {'current_holdings':None,'closed_today':None,'other':None}
    groups={'current_holdings':[],'closed_today':[],'other':[]}
    with localcontext() as ctx:
        ctx.prec=50
        for item in items:
            quantity=Decimal(item['quantity']) if item.get('quantity') is not None else None
            key='current_holdings' if quantity is not None and quantity>=MIN_OPEN_QUANTITY else 'closed_today' if quantity==0 else 'other'
            groups[key].append(Decimal(item['pnl']) if item['pnl'] is not None else None)
        return {key:strict_sum(values) for key,values in groups.items()}


def channel_summary(rows):
    """Exact channel totals; reference valuations stay available without live quotes."""
    with localcontext() as ctx:
        ctx.prec = 50
        groups = {}
        for row in rows:
            groups.setdefault(row.values[0], []).append(row)
        return tuple((channel, strict_sum(r.values[6] for r in group),
                      strict_sum(r.metadata.get('current_asset') for r in group))
                     for channel, group in sorted(groups.items()))


def today_summary_row(rows, columns, channel=None):
    """Shared display total for the filtered holdings, never a synthetic trade."""
    with localcontext() as ctx:
        ctx.prec = 50
        values = [None] * len(columns)
        values[0] = f'{channel} 汇总' if channel else '全部渠道汇总'
        values[6], values[5] = today_summary(rows)[:2]
        values[10] = strict_sum(row.values[10] for row in rows)
        values[11] = strict_sum(row.values[11] for row in rows)
        return ReportRow(tuple(values), {'display_summary': True})


def lifetime_summary(analytics, live):
    """All trades through the selected date, including fully closed securities."""
    with localcontext() as ctx:
        ctx.prec = 50
        groups = {}
        for row in live.rows:
            channel = {'LONGBRIDGE': '长桥', 'FIRSTRADE': '第一证券', 'SCHWAB': '嘉信'}.get(str(row.values[0]).upper(), row.values[0])
            groups.setdefault(channel, [[], []])[1].append(row.values[10])
        for row in analytics.reports.get('清仓收益', Report('', (), ())).rows:
            channel = {'LONGBRIDGE': '长桥', 'FIRSTRADE': '第一证券', 'SCHWAB': '嘉信'}.get(str(row.values[0]).upper(), row.values[0])
            groups.setdefault(channel, [[], []])[0].append(row.values[7])
        result = {}
        for channel, (closed, opened) in groups.items():
            realized, floating = strict_sum(closed), strict_sum(opened)
            result[channel] = (realized, floating, realized + floating if realized is not None and floating is not None else None)
        return result


def lifetime_total(totals, channel=None):
    with localcontext() as ctx:
        ctx.prec = 50
        return strict_sum(v[2] for key, v in totals.items() if channel is None or key == channel) if totals else None
