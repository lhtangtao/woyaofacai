"""An isolated live overlay; never mutate the historical Analytics object."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext

from portfolio.domain.analytics import Report, ReportRow, ratio, strict_sum
from portfolio.domain.live_quotes import SESSION_LABELS, quote_usable
from portfolio.engine.close_baseline import previous_trading_day, trusted_close, valid_opening
from portfolio.engine.holdings import MIN_OPEN_QUANTITY


def live_holdings(analytics, instruments, quotes, session, now):
    base = analytics.reports['当前持仓']
    with localcontext() as context:
        context.prec = 50
        rows = []
        for row in base.rows:
            values, metadata = list(row.values), dict(row.metadata)
            instrument = instruments.get(metadata['instrument_id'], {})
            symbol = instrument.get('symbol', '') + '.US'
            quote = quotes.get((symbol, session.name)) if session else None
            price, stamp, phase, status = values[4], '—', '收盘参考', '未取得本时段报价，保留数据库参考价'
            if instrument.get('market') != 'US':
                status = '本次实时行情仅覆盖美股，保留数据库参考价'
            elif quote_usable(quote, session, now):
                price, stamp, phase = quote.price, quote.timestamp.astimezone().strftime('%m-%d %H:%M:%S %Z'), SESSION_LABELS[quote.session]
                status = ('已接收长桥报价（本机缓存）' if quote.source == 'cache' else '已接收长桥报价') if now - quote.timestamp <= timedelta(seconds=180) else '报价较旧，请关注时间'
            elif session is None:
                status = '休市或连接未就绪，保留数据库参考价'
            quantity, net = values[3], values[9]
            value = quantity * price if quantity is not None and price is not None else None
            pnl = value - net if value is not None and net is not None else None
            cost = values[6]
            values[4], values[5], values[10], values[11] = price, value, pnl, ratio(pnl, net)
            values[12] = (cost - price) / price if cost is not None and price is not None and price > 0 and cost > price else None
            fresh = status.startswith('已接收长桥报价')
            short_status = ('缓存' if quote and quote.source == 'cache' else '实时') if fresh else '旧报价' if status.startswith('报价较旧') else '参考价'
            metadata.update(live_quote_time=stamp, live_session=phase, live_status=status, live_fresh=fresh,
                            historical_price=row.values[4], live_overlay=True, quote_source=quote.source if quote_usable(quote, session, now) else 'reference')
            rows.append(ReportRow((*values, phase, stamp, short_status), metadata))
        total = strict_sum(r.values[5] for r in rows)
        changed = []
        for row in rows:
            values = list(row.values)
            values[13] = ratio(values[5], total)
            changed.append(replace(row, values=tuple(values)))
        floating = strict_sum(r.values[10] for r in rows)
        new_count = sum(r.metadata['live_fresh'] for r in rows)
    report = Report('实时持仓', (*base.columns, '行情时段', '报价时间', '行情状态'), tuple(changed),
                    '长桥实时估值：仅美股，本机SQLite行情缓存。无本时段报价使用标记的数据库参考价，历史报表保持收盘口径。默认显示常用实时列，右键表头可展开累计买卖等完整字段。')
    # Empty holdings are a known zero, not a missing quote converted to zero.
    return report, total if rows else Decimal(0), floating if rows else Decimal(0), new_count


def live_metrics(analytics, report, session, now):
    """Current cards only; use trading-day cashflows and prior close as the base."""
    if session is None:
        return analytics.metrics
    from portfolio.domain.live_quotes import NY
    day = session.end.astimezone(NY).date().isoformat() if session.name == 'Overnight' else session.start.astimezone(NY).date().isoformat()
    phase = SESSION_LABELS[session.name]
    expected_close = previous_trading_day(analytics, day)
    rows = report.rows
    covered = sum(r.metadata.get('live_session') == phase for r in rows)
    stamp = f'{day} {phase} · {covered}/{len(rows)}报价'
    original = {m.name: m for m in analytics.metrics}
    with localcontext() as ctx:
        ctx.prec = 50
        total = strict_sum(r.values[5] for r in rows) if rows else Decimal(0)
        floating = strict_sum(r.values[10] for r in rows) if rows else Decimal(0)
        realized = original['已清仓收益'].value
        lifetime = floating + realized if floating is not None and realized is not None else None
        closed = analytics.reports['清仓收益'].rows
        deficit = strict_sum(max(-v, Decimal(0)) if v is not None else None for v in [*(r.values[10] for r in rows), *(r.values[7] for r in closed)])
        daily = analytics.reports['每日持仓收益'].rows
        today_rows = {(r.metadata['account_id'], r.metadata['instrument_id']): r for r in daily if r.values[0] == day}
        amounts = []
        active = set()
        historical = {(r.metadata['account_id'], r.metadata['instrument_id']): r for r in analytics.reports['当前持仓'].rows}
        for row in rows:
            key = (row.metadata['account_id'], row.metadata['instrument_id'])
            active.add(key)
            current = today_rows.get(key)
            if current:
                opening, bought, sold = current.values[15:18]
                if not valid_opening(current, expected_close):
                    opening = None
            else:
                old = historical[key]
                opening, bought, sold = old.values[5] if trusted_close(old.metadata, expected_close) else None, Decimal(0), Decimal(0)
            value = row.values[5]
            known = row.metadata.get('live_session') == phase and all(v is not None for v in (value, opening, bought, sold))
            amounts.append(value - opening - bought + sold if known else None)
        # Include today's fully sold securities and their actual cash flows.
        for key, row in today_rows.items():
            if key not in active:
                opening, bought, sold = row.values[15:18]
                if not valid_opening(row, expected_close):
                    opening = None
                amounts.append(-opening - bought + sold if row.values[8] is not None and row.values[8] < MIN_OPEN_QUANTITY and all(v is not None for v in (opening, bought, sold)) else None)
        today = strict_sum(amounts) if amounts else Decimal(0)
        def period_gain(name, prefix):
            previous = original[name].value
            latest = analytics.latest_profit_day or ''
            if not latest.startswith(prefix):
                return today
            if latest < day:
                return previous + today if previous is not None and today is not None else None
            recorded_today = original['今日盈亏'].value
            if previous is not None and recorded_today is not None and today is not None:
                return previous - recorded_today + today
            # Display reports may have a narrower start filter than the KPI.
            first_day = prefix + ('-01' if len(prefix) == 7 else '-01-01')
            if analytics.scope.start_date and analytics.scope.start_date.isoformat() > first_day:
                return None
            return strict_sum([*(r.values[19] for r in daily if r.values[0].startswith(prefix) and r.values[0] < day), today])
        month = period_gain('本月盈亏', day[:7])
        year = period_gain('本年盈亏', day[:4])
        prior_days = [r.values[0] for r in daily if r.values[0] < day]
        prior_day = max(prior_days, default=None)
        yesterday = strict_sum(r.values[19] for r in daily if r.values[0] == prior_day) if prior_day else None
        principal = strict_sum([*(r.values[9] for r in rows), *(r.values[5] - r.values[6] if r.values[5] is not None and r.values[6] is not None else None for r in closed)])
        updates = {'当前总资产': total, '浮动盈亏': floating, '全时期总盈亏': lifetime, '回本缺口': deficit,
                   '昨日盈亏': yesterday, '今日盈亏': today, '当日收益率': ratio(today, total - today if total is not None and today is not None else None),
                   '本月盈亏': month, '本年盈亏': year,
                   '本月收益率': ratio(month, total - month if total is not None and month is not None else None),
                   '本年收益率': ratio(year, total - year if total is not None and year is not None else None),
                   '全时期收益率': ratio(lifetime, principal),
                   '当前持仓胜率': Decimal(sum(r.values[10] > 0 for r in rows)) / len(rows) if rows and all(r.values[10] is not None for r in rows) else None}
        formula = f'长桥{phase}实时估值；美股交易日 {day}；覆盖 {covered}/{len(rows)} 条持仓。今日盈亏=实时市值−上一交易日收盘市值−当日买入金额+当日卖出金额；当日收益率=今日盈亏÷(实时市值−今日盈亏)。无当日流水时使用同数量的最近收盘市值；缺少报价或基准保持缺失，不作零处理。实时估值同步计入总览日历与月度/季度/年度汇总；历史源报表不改写。'
        estimated_previous = any(r.values[0] == prior_day and r.metadata.get('historical_estimate') for r in daily)
        return tuple(replace(m, value=updates[m.name], day=(f'{prior_day} '+('历史估值' if estimated_previous else '收盘') if m.name == '昨日盈亏' and prior_day else stamp), formula=('上一有收益记录的交易日收盘盈亏，不包含当前夜盘变化。' if m.name == '昨日盈亏' else formula), status='LIVE_ESTIMATE' if updates[m.name] is not None else 'INCOMPLETE') if m.name in updates else m for m in analytics.metrics)


def live_summaries(analytics, report, total, session=None, now=None):
    """Generate real-time 渠道汇总 and 分类汇总 reports using live_holdings."""
    from portfolio.domain.analytics import ZERO, Report, ReportRow, ratio, strict_sum
    from portfolio.engine.dashboard import SUMMARY_COLUMNS
    holdings = report.rows
    closed = analytics.reports.get('清仓收益', Report('', (), ())).rows
    summaries = {}
    for dimension, name in ((0, "渠道汇总"), (1, "分类汇总")):
        result = []
        categories = sorted({r.values[dimension] for r in [*holdings, *closed]})
        for category in categories:
            hs = [r for r in holdings if r.values[dimension] == category]
            cs = [r for r in closed if r.values[dimension] == category]
            mv = strict_sum(r.values[5] for r in hs) if hs else Decimal(0)
            fp = strict_sum(r.values[10] for r in hs) if hs else Decimal(0)
            cp = strict_sum(r.values[7] for r in cs) if cs else Decimal(0)
            net = strict_sum([r.values[9] for r in hs] + ([r.values[5] - r.values[6] if r.values[5] is not None and r.values[6] is not None else None for r in cs] if dimension == 0 else []))
            profits = [r.values[10] for r in hs] + [r.values[7] for r in cs]
            orig_rep = analytics.reports.get(name)
            cash = None
            annual_gain = None
            if orig_rep:
                for orig_row in orig_rep.rows:
                    if orig_row.values[0] == category:
                        if '当前现金' in orig_rep.columns:
                            cash = orig_row.values[orig_rep.columns.index('当前现金')]
                        if '本年盈亏' in orig_rep.columns:
                            annual_gain = orig_row.values[orig_rep.columns.index('本年盈亏')]
            if dimension == 0 and cash is None and hasattr(analytics, "extras"):
                from portfolio.domain.models import Ledger
                is_personal_or_all = (analytics.scope.ledger == Ledger.PERSON and (analytics.scope.person_ids is None or 1 in analytics.scope.person_ids))
                channel_cash = getattr(analytics, "extras", {}).get("channel_cash", {})
                if is_personal_or_all and category in channel_cash:
                    cash = channel_cash[category]
            if session is not None and now is not None:
                from types import SimpleNamespace
                subset = {}
                for report_name, col in [('当前持仓', dimension), ('清仓收益', dimension), ('每日持仓收益', dimension + 1)]:
                    old = analytics.reports[report_name]
                    subset[report_name] = replace(old, rows=tuple(r for r in old.rows if r.values[col] == category))
                recorded = [r.values[19] for r in subset['每日持仓收益'].rows if r.values[0] == analytics.latest_profit_day]
                metrics = tuple(replace(m, value=annual_gain if m.name == '本年盈亏' else strict_sum(recorded))
                                if m.name in ('本年盈亏', '今日盈亏') else m for m in analytics.metrics)
                part = SimpleNamespace(reports=subset, metrics=metrics, scope=analytics.scope,
                                       latest_profit_day=analytics.latest_profit_day)
                overlay = replace(report, rows=tuple(hs))
                annual_gain = next(m.value for m in live_metrics(part, overlay, session, now) if m.name == '本年盈亏')
            result.append(ReportRow((category, mv, net, len(hs), ratio(mv, total), cp, fp,
                                     annual_gain,
                                     fp + cp if fp is not None and cp is not None else None,
                                     sum(v is not None and v > ZERO for v in profits), sum(v is not None and v < ZERO for v in profits),
                                     strict_sum(max(-v, ZERO) if v is not None else None for v in profits), *([cash] if dimension == 0 else [])),
                                    {"current_principal_basis": "当前+清仓净投入" if dimension == 0 else "仅当前持仓净投入", "cash_note": "无可信现金期初余额时不显示现金余额"}))
        summaries[name] = Report(name, SUMMARY_COLUMNS if dimension == 0 else ("分类", *SUMMARY_COLUMNS[1:-1]), tuple(result),
                                 note="长桥实时估值：按最新盘中报价动态汇总。")
    if '人员资产' in analytics.reports:
        from collections import defaultdict
        pa = analytics.reports['人员资产']
        price_map = {r.metadata['instrument_id']: r.values[4] for r in report.rows if 'instrument_id' in r.metadata}
        live_person_totals = defaultdict(Decimal)
        for r in pa.rows:
            iid = r.metadata.get('instrument_id')
            qty = r.values[3]
            lp = price_map.get(iid, r.values[6])
            if qty and qty > ZERO and lp is not None:
                live_person_totals[r.metadata.get('person_id')] += qty * lp

        live_pa_rows = []
        for r in pa.rows:
            iid = r.metadata.get('instrument_id')
            qty = r.values[3]
            cost = r.values[5]
            net = r.values[12]
            lp = price_map.get(iid, r.values[6])
            if qty and qty > ZERO:
                lmv = qty * lp if lp is not None else None
                lpnl = (lmv - net) if (lmv is not None and net is not None) else None
                lrate = ratio(lpnl, net)
                lrec = ((cost - lp) / lp) if (cost is not None and lp is not None and lp > ZERO and cost > lp) else None
                tot = live_person_totals[r.metadata.get('person_id')]
                lweight = ratio(lmv, tot) if tot else None
                new_vals = list(r.values)
                new_vals[6] = lp
                new_vals[7] = lmv
                new_vals[8] = lpnl
                new_vals[9] = lrate
                new_vals[10] = lrec
                new_vals[11] = lweight
                live_pa_rows.append(ReportRow(tuple(new_vals), r.metadata))
            else:
                sell_p = r.metadata.get('sell_price')
                if sell_p and sell_p > ZERO and lp is not None:
                    new_vals = list(r.values)
                    new_vals[6] = lp
                    new_vals[10] = (lp - sell_p) / sell_p
                    live_pa_rows.append(ReportRow(tuple(new_vals), r.metadata))
                else:
                    live_pa_rows.append(r)
        summaries['人员资产'] = Report(pa.name, pa.columns, tuple(live_pa_rows), pa.note)
    return summaries
