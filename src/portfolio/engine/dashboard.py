import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from portfolio.domain.analytics import ZERO, Metric, Report, ReportRow, ratio, strict_sum
from portfolio.engine.cash import cash_balance

KPI_NAMES = ("当前总资产", "当日收益率", "全时期总盈亏", "已清仓收益", "浮动盈亏", "回本缺口", "昨日盈亏", "今日盈亏", "上月盈亏", "本月盈亏", "去年盈亏", "本年盈亏", "本月微操收益", "本年微操收益", "本月收益率", "本年收益率", "全时期收益率", "当前持仓胜率")
KPI_FORMULAS = ("当前持仓市值之和；不包含现金或融资", "今日盈亏 ÷ (股票市值−今日盈亏)", "完整清仓收益 + 当前持仓净投入法盈亏", "最终数量为零的分组，累计卖出−累计买入", "当前市值−累计净投入；不是标准未实现损益", "逐条亏损绝对值之和，盈利不抵扣", "最大收益日期前一有数据交易日", "每日持仓收益最大日期的收益之和", "最大收益日期的上一自然月每日收益之和", "最大收益日期所在自然月每日收益之和", "最大收益日期的上一自然年每日收益之和", "最大收益日期所在自然年每日收益之和", "月内微操配对收益；不叠加到组合收益", "自然年微操配对收益；不叠加到组合收益", "本月盈亏 ÷ (股票市值−本月盈亏)，非标准回报率", "本年盈亏 ÷ (股票市值−本年盈亏)，非标准回报率", "全时期盈亏 ÷ (当前及清仓累计净投入)，分母≤0 不适用", "当前盈利记录数 ÷ 当前持仓记录数，零收益不算盈利")
SUMMARY_COLUMNS = ("渠道", "当前资产", "当前本金", "当前股票数", "资产占比", "已清仓收益", "浮动盈亏", "本年盈亏", "总盈亏", "盈利股票数", "亏损股票数", "回本缺口", "当前现金")


def grouped(rows, index):
    result = defaultdict(list)
    for row in rows:
        result[row.values[index]].append(row)
    return result


def live_daily_rows(rows, metrics):
    """View-only trading-day replacement; original per-security rows stay intact."""
    original = tuple(rows)
    metric = next((m for m in metrics if m.name == '今日盈亏'), None)
    if metric is None or metric.status not in ('LIVE_ESTIMATE', 'INCOMPLETE') or not metric.formula.startswith('长桥'):
        return original, None
    try:
        day = date.fromisoformat(metric.day[:10]).isoformat()
    except ValueError:
        return original, None
    values = [None]*23
    values[0], values[19] = day, metric.value
    rate_metric = next((m for m in metrics if m.name == '当日收益率'), None)
    asset_metric = next((m for m in metrics if m.name == '当前总资产'), None)
    if asset_metric and asset_metric.value is not None:
        values[11] = asset_metric.value
        if metric.value is not None:
            values[15] = asset_metric.value - metric.value
    meta = {'live_estimate': True, 'formula': metric.formula, 'stamp': metric.day}
    if rate_metric and rate_metric.value is not None:
        meta['rate'] = rate_metric.value
    live = ReportRow(tuple(values), meta)
    return tuple(r for r in original if r.values[0] != day)+(live,), day


def daily_rates(daily_rows, metrics=None, live_day=None):
    by_day = defaultdict(list)
    for r in daily_rows:
        by_day[r.values[0]].append(r)
    live_rate = None
    if metrics:
        live_metric = next((m for m in metrics if m.name == '当日收益率'), None)
        if live_metric and live_metric.value is not None:
            live_rate = live_metric.value
    rates = {}
    for day, rows in by_day.items():
        if day == live_day:
            if live_rate is not None:
                rates[day] = live_rate
                continue
            meta_rate = rows[0].metadata.get('rate') if rows else None
            if meta_rate is not None:
                rates[day] = meta_rate
                continue
        gain = strict_sum(r.values[19] for r in rows)
        if gain is None:
            rates[day] = None
            continue
        opening = strict_sum(r.values[15] for r in rows)
        if opening is None or opening <= ZERO:
            ending = strict_sum(r.values[11] for r in rows)
            opening = ending - gain if ending is not None and ending - gain > ZERO else None
        rates[day] = ratio(gain, opening if opening and opening > ZERO else None)
    return rates


def compound_rates(days, day_rates):
    compounded = Decimal(1)
    observations = 0
    for day in sorted(days):
        rate = day_rates.get(day)
        if rate is None:
            return None
        compounded *= (1 + rate)
        observations += 1
    return compounded - 1 if observations > 0 else None


def monthly_rates(daily_rows, year, day_rates, metrics=None, live_day=None):
    months = defaultdict(list)
    for d in day_rates:
        if d.startswith(f'{year:04}-'):
            months[int(d[5:7])].append(d)
    month_metric = next((m for m in metrics if m.name == '本月收益率'), None) if metrics else None
    rates = []
    for m in range(1, 13):
        if live_day and live_day.startswith(f'{year:04}-{m:02}') and month_metric and month_metric.value is not None:
            rates.append(month_metric.value)
        elif m in months:
            rates.append(compound_rates(months[m], day_rates))
        else:
            rates.append(None)
    return tuple(rates)


def quarterly_rates(daily_rows, year, day_rates):
    quarters = defaultdict(list)
    for d in day_rates:
        if d.startswith(f'{year:04}-'):
            quarters[(int(d[5:7]) - 1) // 3].append(d)
    return tuple(compound_rates(quarters[q], day_rates) if q in quarters else None for q in range(4))


def annual_rate(daily_rows, year, day_rates, metrics=None, live_day=None):
    year_metric = next((m for m in metrics if m.name == '本年收益率'), None) if metrics else None
    if live_day and live_day.startswith(f'{year:04}-') and year_metric and year_metric.value is not None:
        return year_metric.value
    year_days = [d for d in day_rates if d.startswith(f'{year:04}-')]
    return compound_rates(year_days, day_rates) if year_days else None


def monthly_gains(daily_rows, year):
    """Use the same daily P/L basis as the overview KPIs, preserving unknowns."""
    months = defaultdict(list)
    for row in daily_rows:
        day = row.values[0]
        if day.startswith(f'{year:04}-'):
            months[int(day[5:7])].append(row.values[19])
    gains = tuple(strict_sum(months[m]) if m in months else None for m in range(1, 13))
    total = strict_sum(value for values in months.values() for value in values) if months else None
    return gains, total


def quarterly_gains(daily_rows, year):
    groups = defaultdict(list)
    for row in daily_rows:
        if row.values[0].startswith(f'{year:04}-'):
            groups[(int(row.values[0][5:7]) - 1) // 3].append(row.values[19])
    return tuple(strict_sum(groups[q]) if q in groups else None for q in range(4))


def metric_gap(name, daily_rows, as_of):
    """Explain missing period KPIs without conflating no observations with gaps."""
    rows = tuple(daily_rows)
    latest = max((r.values[0] for r in rows), default=as_of.isoformat())
    year, month = int(latest[:4]), int(latest[5:7])
    periods = {'去年盈亏': str(year - 1), '本年盈亏': str(year), '本年收益率': str(year),
               '本月盈亏': latest[:7], '本月收益率': latest[:7],
               '上月盈亏': f'{year if month > 1 else year - 1:04}-{month - 1 if month > 1 else 12:02}'}
    prefix = periods.get(name)
    if prefix is None:
        return '数据未完整', '所需数据缺失或计算条件不满足。'
    selected = [r for r in rows if r.values[0].startswith(prefix)]
    if not selected:
        return '该期间无收益记录', f'{prefix}在当前人员、渠道和股票筛选下没有每日收益记录，不代表数据损坏，也不按零处理。'
    missing = [r for r in selected if r.values[19] is None]
    if missing:
        groups = defaultdict(list)
        for row in missing:
            groups[(row.values[1], row.values[3])].append(row.values[0])
        details = '\n'.join(f'{channel} / {ticker}：{min(days)}至{max(days)}，{len(days)}条收益无法计算'
                            for (channel, ticker), days in sorted(groups.items()))
        return '数据未完整', f'{prefix}有{len(missing)}条每日收益无法确认，不能用部分金额代替完整合计。\n{details}\n请在每日持仓收益核对数量、行情和期初数据。'
    return '暂不适用', '该期间收益已存在，但收益率分母等计算条件不满足。'


def overview(snapshot, reports):
    holdings = reports["当前持仓"].rows
    closed = reports["清仓收益"].rows
    daily = reports["每日持仓收益"].rows
    micro = reports["每月微操收益"].rows
    day_groups = grouped(daily, 0)
    days = sorted(day_groups)
    latest = days[-1] if days else None
    today = strict_sum(r.values[19] for r in day_groups[latest]) if latest else None
    yesterday = strict_sum(r.values[19] for r in day_groups[days[-2]]) if len(days) > 1 else None
    month = latest[:7] if latest else snapshot.scope.as_of.isoformat()[:7]
    year = int(month[:4])
    y, m = map(int, month.split("-"))
    prior_month = f"{y if m > 1 else y - 1:04}-{m - 1 if m > 1 else 12:02}"
    month_gain = strict_sum(r.values[19] for r in daily if r.values[0].startswith(month)) if latest else None
    last_month = strict_sum(r.values[19] for r in daily if r.values[0].startswith(prior_month)) if any(r.values[0].startswith(prior_month) for r in daily) else None
    year_gain = strict_sum(r.values[19] for r in daily if r.values[0].startswith(str(year))) if latest else None
    last_year = strict_sum(r.values[19] for r in daily if r.values[0].startswith(str(year - 1))) if any(r.values[0].startswith(str(year - 1)) for r in daily) else None
    value = strict_sum(r.values[5] for r in holdings)
    floating = strict_sum(r.values[10] for r in holdings)
    if "股票汇总" in reports and any(r.values[3] in ("负仓异常", "成交待核对") for r in reports["股票汇总"].rows):
        value, floating = None, None
    realized = strict_sum(r.values[7] for r in closed)
    lifetime = floating + realized if floating is not None and realized is not None else None
    contributions = [r.values[10] for r in holdings] + [r.values[7] for r in closed]
    deficit = strict_sum(max(-v, ZERO) if v is not None else None for v in contributions)
    principal = strict_sum([r.values[9] for r in holdings] + [r.values[5] - r.values[6] if r.values[5] is not None and r.values[6] is not None else None for r in closed])
    micro_month = strict_sum(r.values[11] for r in micro if r.values[0] == month)
    micro_year = strict_sum(r.values[11] for r in micro if r.values[0].startswith(str(year)))
    values = (value, ratio(today, value - today if value is not None and today is not None else None), lifetime, realized,
              floating, deficit, yesterday, today, last_month, month_gain, last_year, year_gain, micro_month, micro_year,
              ratio(month_gain, value - month_gain if value is not None and month_gain is not None else None),
              ratio(year_gain, value - year_gain if value is not None and year_gain is not None else None), ratio(lifetime, principal),
              Decimal(sum(r.values[10] is not None and r.values[10] > ZERO for r in holdings)) / Decimal(len(holdings)) if holdings and all(r.values[10] is not None for r in holdings) else None)
    review = any(i.severity in ("ERROR", "WARN") for i in snapshot.issues)
    metrics = tuple(Metric(name, number, formula, snapshot.scope.as_of.isoformat() if idx in (0, 2, 3, 4, 5, 16, 17) else latest or "无收益日",
                           idx in (1, 14, 15, 16, 17), "INCOMPLETE" if number is None else "REVIEW_REQUIRED" if review else "COMPLETE")
                    for idx, (name, number, formula) in enumerate(zip(KPI_NAMES, values, KPI_FORMULAS)))
    if any(r.metadata.get('historical_estimate') for r in daily):
        from dataclasses import replace
        metrics = tuple(replace(m, formula=m.formula+'；含本机历史行情估值，非正式收盘数据。',
                                day=m.day+' · 含历史估值', status='INCOMPLETE' if m.value is None else 'REVIEW_REQUIRED') for m in metrics)
    additions = {}
    for dimension, name in ((0, "渠道汇总"), (1, "分类汇总")):
        result = []
        categories = sorted({r.values[dimension] for r in [*holdings, *closed]})
        for category in categories:
            hs, cs = [r for r in holdings if r.values[dimension] == category], [r for r in closed if r.values[dimension] == category]
            mv = strict_sum(r.values[5] for r in hs)
            fp = strict_sum(r.values[10] for r in hs)
            cp = strict_sum(r.values[7] for r in cs)
            net = strict_sum([r.values[9] for r in hs] + ([r.values[5] - r.values[6] if r.values[5] is not None and r.values[6] is not None else None for r in cs] if dimension == 0 else []))
            profits = [r.values[10] for r in hs] + [r.values[7] for r in cs]
            account_ids = {r.metadata["account_id"] for r in [*hs, *cs]}
            cash = strict_sum(cash_balance(snapshot, account) for account in account_ids) if dimension == 0 else None
            if dimension == 0 and cash is None:
                from portfolio.domain.models import Ledger
                is_personal_or_all = (snapshot.scope.ledger == Ledger.PERSON and (snapshot.scope.person_ids is None or 1 in snapshot.scope.person_ids))
                channel_cash = snapshot.extras.get("channel_cash", {})
                if is_personal_or_all and category in channel_cash:
                    cash = channel_cash[category]
            annual = [r for r in daily if str(r.values[0]).startswith(str(snapshot.scope.as_of.year)) and r.values[dimension + 1] == category]
            annual_gain = strict_sum(r.values[19] for r in annual) if annual else None
            result.append(ReportRow((category, mv, net, len(hs), ratio(mv, value), cp, fp,
                                     annual_gain,
                                     fp + cp if fp is not None and cp is not None else None,
                                     sum(v is not None and v > ZERO for v in profits), sum(v is not None and v < ZERO for v in profits),
                                     strict_sum(max(-v, ZERO) if v is not None else None for v in profits), *([cash] if dimension == 0 else [])),
                                    {"current_principal_basis": "当前+清仓净投入" if dimension == 0 else "仅当前持仓净投入", "cash_note": "无可信现金期初余额时不显示现金余额"}))
        additions[name] = Report(name, SUMMARY_COLUMNS if dimension == 0 else ("分类", *SUMMARY_COLUMNS[1:-1]), tuple(result))
    calendar_rows = []
    for week in calendar.Calendar().monthdatescalendar(y, m):
        pnl = []
        cells = []
        for day in week[:5]:
            key = day.isoformat()
            gain = strict_sum(r.values[19] for r in day_groups[key]) if key in day_groups and day.month == m else None
            cells.extend((key if day.month == m else "", gain))
            if day.month == m and key in day_groups:
                pnl.append(gain)
        calendar_rows.append(ReportRow((*cells, strict_sum(pnl) if pnl else None), {"month": month}))
    additions["收益日历"] = Report("收益日历", ("周一", "盈亏一", "周二", "盈亏二", "周三", "盈亏三", "周四", "盈亏四", "周五", "盈亏五", "本周合计"), tuple(calendar_rows))
    quarters = []
    for q in range(1, 5):
        months = [f"{year}-{(q - 1) * 3 + n:02}" for n in (1, 2, 3)]
        gains = [strict_sum(r.values[19] for r in daily if r.values[0].startswith(mo)) if any(r.values[0].startswith(mo) for r in daily) else None for mo in months]
        compounded, valid, observations = Decimal(1), True, 0
        for day in days:
            if day[:7] not in months:
                continue
            opening = strict_sum(r.values[15] for r in day_groups[day])
            gain = strict_sum(r.values[19] for r in day_groups[day])
            if gain is None or opening is None:
                valid = False
            elif opening > ZERO:
                compounded *= 1 + gain / opening
                observations += 1
        quarter = compounded - 1 if valid and observations else None
        last_month_number = q * 3
        end = date(year, last_month_number, calendar.monthrange(year, last_month_number)[1])
        annualized = (1 + quarter) ** 4 - 1 if quarter is not None and latest and date.fromisoformat(latest) >= end else None
        quarters.append(ReportRow((f"{year} Q{q}", *gains, quarter, annualized), {"sample_days": observations, "formula": "Π(1+当日收益/期初市值)−1；完整季度才年化", "quarter_end": end.isoformat()}))
    additions["季度矩阵"] = Report("季度矩阵", ("季度", "首月收益", "次月收益", "末月收益", "季度复利收益率", "完整季度年化"), tuple(quarters))
    return metrics, additions, latest
