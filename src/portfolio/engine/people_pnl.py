"""Person-ledger attribution with a separate broker reconciliation residual."""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from portfolio.domain.analytics import Report, ReportRow, ratio, strict_sum
from portfolio.domain.live_quotes import NY, SESSION_LABELS
from portfolio.engine.live_valuation import live_holdings

ZERO = Decimal(0)


@dataclass
class PeopleBundle:
    aggregate: object
    persons: tuple
    broker: object


def profile(analytics, person, name, instruments, quotes, session, now, day, previous):
    live, *_ = live_holdings(analytics, instruments, quotes, session, now)
    opened = {(r.metadata['account_id'], r.metadata['instrument_id']): r for r in live.rows}
    daily = {(r.metadata['account_id'], r.metadata['instrument_id']): r for r in analytics.reports['每日持仓收益'].rows if r.values[0] == day}
    phase = SESSION_LABELS[session.name] if session else None
    details = []
    cleared = []
    for stock in analytics.reports['股票汇总'].rows:
        s, meta = stock.values, dict(stock.metadata)
        key = (meta['account_id'], meta['instrument_id'])
        holding, flow = opened.get(key), daily.get(key)
        quantity = s[4]
        value = holding.values[5] if holding else ZERO if quantity == 0 else None
        net = s[10]
        cost, price = s[7], holding.values[4] if holding else s[5]
        if session:
            if holding:
                if flow:
                    opening, bought, sold = flow.values[15:18]
                else:
                    trusted = meta.get('valuation_source') == 'DAILY_CLOSE' and meta.get('valuation_date') == previous
                    opening, bought, sold = s[6] if trusted else None, ZERO, ZERO
                gain = value-opening-bought+sold if holding.metadata.get('live_session') == phase and all(v is not None for v in (value, opening, bought, sold)) else None
            elif flow:
                opening, bought, sold = flow.values[15:18]
                gain = -opening-bought+sold if quantity == 0 and all(v is not None for v in (opening, bought, sold)) else None
            else:
                gain = ZERO if quantity == 0 else None
        else:
            gain = flow.values[19] if flow else ZERO if quantity == 0 else None
        realized = s[15]
        floating = holding.values[10] if holding else ZERO if quantity == 0 else None
        lifetime = realized+floating if realized is not None and floating is not None else None
        channel = {'LONGBRIDGE': '长桥', 'FIRSTRADE': '第一证券', 'SCHWAB': '嘉信'}.get(str(s[0]).upper(), s[0])
        prev_price = None
        if flow and flow.values[5] and flow.values[5] > 0 and flow.values[15] is not None:
            prev_price = flow.values[15] / flow.values[5]
        elif holding and holding.metadata.get('historical_price'):
            prev_price = holding.metadata.get('historical_price')
        elif meta.get('valuation_date') == previous and s[5]:
            prev_price = s[5]
        change = ratio(price - prev_price, prev_price) if price is not None and prev_price is not None and prev_price > 0 and (quantity > 0 or (flow and flow.values[19])) else None
        if change is None and gain is not None and flow and flow.values[5] and flow.values[5] > 0 and flow.values[15]:
            change = ratio(gain, flow.values[15])
        meta.update(person_id=person, person_name=name, daily_active=bool(holding or flow), realized=realized, floating=floating)
        details.append(ReportRow((name, channel, s[2], s[3], quantity, cost, price, gain, change, lifetime, value, net), meta))
        if quantity == 0 and flow:
            cleared.append(gain)
    gain = strict_sum(r.values[7] for r in details)
    value = strict_sum(r.values[10] for r in details)
    realized = strict_sum(r.metadata['realized'] for r in details)
    floating = strict_sum(r.metadata['floating'] for r in details)
    lifetime = realized+floating if realized is not None and floating is not None else None
    net = strict_sum(r.values[11] for r in details)
    row = ReportRow((name, gain, ratio(gain, value-gain if value is not None and gain is not None else None), value, strict_sum(cleared), lifetime, realized, floating, net), {'person_id': person})
    return row, tuple(details)


def day_previous(analytics, day):
    return max((r.values[0] for r in analytics.reports['每日持仓收益'].rows if r.values[0] < day), default=None)


def people_reports(bundle, instruments, quotes, session, now):
    analytics = bundle.aggregate
    if analytics.scope.as_of != now.astimezone().date() or analytics.scope.reporting_currency != 'USD':
        session = None
    books = [analytics, bundle.broker, *(a for _, _, a in bundle.persons)]
    last_close = max((a.latest_profit_day for a in books if a.latest_profit_day), default=analytics.scope.as_of.isoformat())
    day = (session.end if session.name == 'Overnight' else session.start).astimezone(NY).date().isoformat() if session else last_close
    with localcontext() as ctx:
        ctx.prec = 50
        previous = max((d for a in books if (d := day_previous(a, day))), default=None)
        profiles = [profile(a, person, name, instruments, quotes, session, now, day, previous) for person, name, a in bundle.persons]
        broker, broker_details = profile(bundle.broker, 'broker', '券商合计', instruments, quotes, session, now, day, previous)
        rows = [row for row, _ in profiles]
        details = [r for _, items in profiles for r in items]
        differences = [None] * 9
        differences[0] = '未分配／归属差异'
        own = {}
        for r in details:
            own.setdefault((r.metadata['account_id'], r.metadata['instrument_id']), []).append(r)
        residual_details = []
        for b in broker_details:
            key = (b.metadata['account_id'], b.metadata['instrument_id'])
            group = own.pop(key, [])
            vals = list(b.values)
            vals[0], vals[3] = differences[0], '归属差异'
            for i in (4, 7, 9, 10, 11):
                total = strict_sum(r.values[i] for r in group)
                vals[i] = b.values[i]-total if b.values[i] is not None and total is not None else None
            # 差异少于 0.5 股的微小拆分/归属零头予以忽略
            if vals[4] is not None and 0 < abs(vals[4]) < Decimal('0.5'):
                continue
            vals[5] = vals[11]/vals[4] if vals[11] is not None and vals[4] is not None and vals[4] > 0 else None
            if any(vals[i] is None or vals[i] != 0 for i in (4, 7, 9, 10, 11)):
                residual_details.append(ReportRow(tuple(vals), {**b.metadata, 'person_id': 'residual', 'daily_active': True}))
        # Owner-only instruments remain explicit negative reconciliation rows.
        for group in own.values():
            vals = list(group[0].values)
            vals[0], vals[3], vals[5] = differences[0], '归属差异', None
            for i in (4, 7, 9, 10, 11):
                total = strict_sum(r.values[i] for r in group)
                vals[i] = -total if total is not None else None
            if vals[4] is not None and 0 < abs(vals[4]) < Decimal('0.5'):
                continue
            residual_details.append(ReportRow(tuple(vals), {**group[0].metadata, 'person_id': 'residual', 'daily_active': True}))

        if residual_details:
            details.extend(residual_details)
            differences[1] = strict_sum(r.values[7] for r in residual_details)
            differences[3] = strict_sum(r.values[10] for r in residual_details)
            differences[4] = strict_sum(r.values[7] for r in residual_details if r.values[4] == 0)
            differences[5] = strict_sum(r.values[9] for r in residual_details)
            differences[6] = strict_sum(r.metadata.get('realized') for r in residual_details)
            differences[7] = strict_sum(r.metadata.get('floating') for r in residual_details)
            differences[8] = strict_sum(r.values[11] for r in residual_details)
            differences[2] = ratio(differences[1], differences[3]-differences[1] if differences[3] is not None and differences[1] is not None else None)
            rows.append(ReportRow(tuple(differences), {'person_id': 'residual'}))

        broker_values = list(broker.values)
        if not residual_details:
            broker_values[1] = strict_sum(r.values[1] for r in rows)
            broker_values[3] = strict_sum(r.values[3] for r in rows)
            broker_values[4] = strict_sum(r.values[4] for r in rows)
            broker_values[5] = strict_sum(r.values[5] for r in rows)
            broker_values[6] = strict_sum(r.values[6] for r in rows)
            broker_values[7] = strict_sum(r.values[7] for r in rows)
            broker_values[8] = strict_sum(r.values[8] for r in rows)
        else:
            broker_values[6] = strict_sum(r.values[6] for r in rows)
            broker_values[7] = strict_sum(r.values[7] for r in rows)
        broker = ReportRow(tuple(broker_values), broker.metadata)
    unit = '$' if analytics.scope.reporting_currency == 'USD' else '¥'
    chosen = analytics.scope.as_of.isoformat()
    status = f'所选日期 {chosen} · 美股交易日 {day} · {SESSION_LABELS[session.name]}实时估值' if session else f'所选日期 {chosen} · 收盘收益日 {day}'
    if not session:
        if analytics.scope.as_of.weekday() >= 5:
            status += ' · 休市，以下为最近交易日盈亏' if chosen != day else ' · 休市，仅含源账本当日操作（如有）'
        elif chosen != day:
            status += ' · 该日无收益记录，以下为最近有效收盘日盈亏'
    note = status + f' · 币种 {analytics.scope.reporting_currency}。按人员权益账本统计，包含当天全清仓股票；累计盈亏=已清仓收益+持仓浮动盈亏，清仓与浮盈分类按各人员状态汇总。未分配／归属差异=券商账本合计减人员合计，可能包含停用人员、未归属记录及账本差异，不自动分摊；缺失不填零。资产不含现金。'
    summary = Report('人员盈亏', ('人员', f'当日盈亏({unit})', '当日收益率', f'持仓市值({unit})', f'当日已清仓盈亏({unit})', f'累计盈亏({unit})', f'已清仓收益({unit})', f'持仓浮动盈亏({unit})', f'累计净投入({unit})'), tuple(rows), note)
    detail = Report('人员股票盈亏', ('人员', '渠道', 'Ticker', '状态', '数量', f'成本价({unit})', f'最新价({unit})', f'当日盈亏({unit})', '当日涨幅', f'累计盈亏({unit})', f'持仓市值({unit})', f'累计净投入({unit})'), tuple(details), note)
    return summary, detail, broker
