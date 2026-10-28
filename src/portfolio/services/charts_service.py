from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal, localcontext

from portfolio.domain.analytics import ZERO, Report, ReportRow, ratio, strict_sum

TITLES = ("当前股票市值占比", "各渠道当前资产占比", "当前持仓分类占比", "上月末股票市值占比", "各渠道上月末资产占比", "上月末持仓分类占比", "每月收益走势", "月度收益率走势（%）", "每月微操收益", "每月净投入走势", "持仓结构占比走势（%）", "各渠道月末股票市值", "前五大持仓集中度（%）", "月末持仓标的数", "已清仓标的盈亏分布", "持仓标的浮动盈亏分布", "全部股票按Ticker汇总盈亏", "累计本金与总资产变化", "月度净投入与月度收益", "本月每日收益与累计收益", "所选月份每日收益与累计收益", "最爽五天", "最惨五天", "全时期盈亏（USD）", "本年盈亏（USD）", "本月盈亏（USD）", "当日盈亏（USD）")


@dataclass(frozen=True)
class Series:
    name: str
    points: tuple[tuple[str, Decimal | None], ...]


@dataclass(frozen=True)
class Chart:
    id: int
    title: str
    kind: str
    series: tuple[Series, ...]
    note: str
    ratio: bool = False
    link: str = ""
    point_labels: dict[str, str] = field(default_factory=dict)
    x_labels: dict[str, str] = field(default_factory=dict)
    x_ticks: tuple[str, ...] = ()
    show_points: bool = True
    vertical_lines: tuple[tuple[str, str], ...] = ()
    highlight_extremes: bool = False
    break_keys: tuple[str, ...] = ()
    line_width: float = 2.0
    calendar_axis: str = ""


def rank_chart(chart, order='profit', offset=0):
    """Sort the complete ranking before taking its page; missing values last."""
    labels = list(dict.fromkeys(key for s in chart.series for key, _ in s.points))
    maps = [dict(s.points) for s in chart.series]
    with localcontext() as ctx:
        ctx.prec = 50
        scores = {key: strict_sum(m.get(key, ZERO) for m in maps) for key in labels}
    known = [key for key in labels if scores[key] is not None]
    known.sort()
    known.sort(key=lambda name: scores[name].copy_abs() if order == 'magnitude' else scores[name], reverse=order != 'loss')
    ranked = (known + sorted(name for name in labels if scores[name] is None))[offset:offset+24]
    title = {'profit': '盈利最多', 'loss': '亏损最多', 'magnitude': '盈亏幅度最大'}[order]
    # Chart 17 has separate positive/negative series; each ticker must occupy one row.
    name = chart.series[0].name if len(chart.series) == 1 else '总盈亏($)'
    return replace(chart, series=(Series(name, tuple((name, scores[name]) for name in ranked)),), note=chart.note + f' · 排序：{title}')


def aggregate(rows, key, value):
    groups = defaultdict(list)
    for row in rows:
        groups[row.values[key]].append(row.values[value])
    return {name: strict_sum(values) for name, values in sorted(groups.items())}


def cumulative(data):
    result, total = {}, ZERO
    for key, value in sorted(data.items()):
        total = total + value if total is not None and value is not None else None
        result[key] = total
    return result


def build_charts(analytics, *, selected_month=None):
    reports = analytics.reports
    holdings, closed, stocks = (reports[n].rows for n in ("当前持仓", "清仓收益", "股票汇总"))
    monthly, daily, micro, net = (reports[n].rows for n in ("每月持仓收益", "每日持仓收益", "每月微操收益", "月度净投入明细"))
    month_gain = aggregate(monthly, 0, 20)
    months = sorted(month_gain)
    latest_month = analytics.latest_profit_day[:7] if analytics.latest_profit_day else analytics.scope.as_of.isoformat()[:7]
    y, m = map(int, latest_month.split("-"))
    base_y, base_m = map(int, (selected_month or latest_month).split('-'))
    previous_month = f"{base_y if base_m > 1 else base_y - 1:04}-{base_m - 1 if base_m > 1 else 12:02}"
    month_end = {mo: [r for r in monthly if r.values[0] == mo] for mo in months}
    values = {mo: strict_sum(r.values[11] for r in rows) for mo, rows in month_end.items()}
    investments = aggregate(net, 0, 8)
    principal = cumulative({mo: investments.get(mo, ZERO) for mo in months})
    daily_gain = aggregate(daily, 0, 19)
    result = []

    def add(id, datasets, kind="bar", *, percentage=False, link=""):
        series = tuple(Series(name, tuple(data.items())) for name, data in datasets.items())
        complete = all(value is not None for s in series for key, value in s.points if key.strip())
        note = f"EXCEL_COMPAT · {analytics.scope.reporting_currency} · 基准 {analytics.scope.as_of} · 人员 {analytics.scope.person_ids or '全部启用'} · 账户 {analytics.scope.account_ids or '全部'} · " + ("含待审/口径提示" if analytics.issues else "完整")
        if not complete:
            note += " · 含缺失值，缺失点不绘制，不补零"
        title = TITLES[id - 1].replace("USD", analytics.scope.reporting_currency)
        snapshot_month = selected_month if id in (1, 2, 3) else previous_month if id in (4, 5, 6) else None
        if snapshot_month:
            stamp = f'{snapshot_month} 月末'
            if analytics.latest_profit_day and analytics.latest_profit_day.startswith(snapshot_month):
                stamp = f'{snapshot_month}（截至 {analytics.latest_profit_day}）'
            title = title.replace('当前', stamp).replace('上月末', stamp)
            note += f' · 月度快照 {stamp}'
        result.append(Chart(id, title, kind, series, note, percentage, link))

    def top_two(data):
        if any(v is None for v in data.values()):
            return data
        items = sorted(data.items(), key=lambda p: p[1], reverse=True)
        return dict(items[:2] + ([("其他", sum((v for _, v in items[2:]), ZERO))] if len(items) > 2 else []))

    if selected_month:
        snapshot = month_end.get(selected_month, [])
        add(1, {"月末市值": top_two(aggregate(snapshot, 3, 11))}, "pie", link="symbol")
        add(2, {"月末资产": aggregate(snapshot, 1, 11)}, "pie", link="broker")
        add(3, {"月末市值": aggregate(snapshot, 2, 11)}, "pie", link="category")
    else:
        add(1, {"当前市值": top_two(aggregate(holdings, 2, 5))}, "pie", link="symbol")
        add(2, {"当前资产": aggregate(holdings, 0, 5)}, "pie", link="broker")
        add(3, {"当前市值": aggregate(holdings, 1, 5)}, "pie", link="category")
    prior = month_end.get(previous_month, [])
    add(4, {"上月末市值": top_two(aggregate(prior, 3, 11))}, "pie", link="symbol")
    add(5, {"上月末资产": aggregate(prior, 1, 11)}, "pie", link="broker")
    add(6, {"上月末市值": aggregate(prior, 2, 11)}, "pie", link="category")
    add(7, {"每月收益($)": month_gain}, link="month")
    add(8, {"月度收益率": {mo: ratio(gain, values[mo] - gain if values[mo] is not None and gain is not None else None) for mo, gain in month_gain.items()}}, "line", percentage=True, link="month")
    add(9, {"微操收益($)": aggregate(micro, 0, 11)}, link="month")
    add(10, {"净投入": investments}, link="month")
    add(11, {cat + "占比": {mo: ratio(strict_sum(r.values[11] for r in rows if r.values[2] == cat), values[mo]) for mo, rows in month_end.items()} for cat in ("大盘", "杠杆", "正股")}, "line", percentage=True, link="month")
    channels = sorted({r.values[1] for r in monthly})
    add(12, {ch: {mo: strict_sum(r.values[11] for r in rows if r.values[1] == ch) for mo, rows in month_end.items()} for ch in channels}, "line", link="month")
    concentration, count = {}, {}
    for mo, rows in month_end.items():
        positive = [r for r in rows if r.values[8] is not None and r.values[8] > ZERO]
        market_values = [r.values[11] for r in positive]
        concentration[mo] = ratio(sum(sorted(market_values, reverse=True)[:5], ZERO), values[mo]) if all(v is not None for v in market_values) else None
        count[mo] = Decimal(len(positive)) if all(r.values[8] is not None for r in rows) else None
    add(13, {"前五大持仓占比": concentration}, "line", percentage=True, link="month")
    add(14, {"月末持仓标的数": count}, link="month")
    add(15, {"已清仓盈亏($)": {f"{r.values[0]} · {r.values[2]}": r.values[7] for r in closed}}, "horizontal", link="symbol_suffix")
    add(16, {"浮动盈亏($)": {f"{r.values[0]} · {r.values[2]}": r.values[10] for r in holdings}}, "horizontal", link="symbol_suffix")
    contributions = aggregate(stocks, 2, 17)
    add(17, {"盈利": {s: max(v, ZERO) if v is not None else None for s, v in contributions.items()}, "亏损": {s: min(v, ZERO) if v is not None else None for s, v in contributions.items()}}, "horizontal", link="symbol")
    add(18, {"累计本金基柱": {mo: min(principal[mo], values[mo]) if principal[mo] is not None and values[mo] is not None else None for mo in months},
             "资产高于本金": {mo: max(values[mo] - principal[mo], ZERO) if principal[mo] is not None and values[mo] is not None else None for mo in months},
             "资产低于本金": {mo: max(principal[mo] - values[mo], ZERO) if principal[mo] is not None and values[mo] is not None else None for mo in months}, "总资产": values}, "capital_assets", link="month")
    add(19, {"净投入": investments, "月度收益": month_gain}, link="month")
    for id, mo in ((20, latest_month), (21, selected_month or latest_month)):
        data = {day: value for day, value in daily_gain.items() if day.startswith(mo)}
        add(id, {"每日收益": data, "累计收益": cumulative(data)}, "line", link="day")
    valid = [(day, val) for day, val in daily_gain.items() if val is not None]
    add(22, {"最高日收益": dict(sorted(valid, key=lambda p: p[1], reverse=True)[:5])}, "horizontal", link="day")
    add(23, {"最低日收益": dict(sorted(valid, key=lambda p: p[1])[:5])}, "horizontal", link="day")
    for id, predicate in ((24, None), (25, lambda d: d.startswith(str(y))), (26, lambda d: d.startswith(latest_month)), (27, lambda d: d == analytics.latest_profit_day)):
        if predicate is None:
            channels_data, categories_data = aggregate(stocks, 0, 17), aggregate(stocks, 1, 17)
        else:
            subset = [r for r in daily if predicate(r.values[0])]
            channels_data, categories_data = aggregate(subset, 1, 19), aggregate(subset, 2, 19)
        add(id, {"盈亏": {**{f"渠道  {k}": v for k, v in channels_data.items()}, " ": None, **{f"分类  {k}": v for k, v in categories_data.items()}}}, "horizontal", link="contribution")
    trend_columns = ("月份", "每月收益", "累计收益", "净投入", "累计本金", "总资产", "大盘占比", "杠杆占比", "其他占比（正股）", *(ch + "月收益" for ch in channels), *(ch + "股票总资产" for ch in channels))
    running = cumulative(month_gain)
    trends = []
    for mo in months:
        shares = [ratio(strict_sum(r.values[11] for r in month_end[mo] if r.values[2] == cat), values[mo]) for cat in ("大盘", "杠杆", "正股")]
        channel_gain = [strict_sum(r.values[20] for r in month_end[mo] if r.values[1] == ch) for ch in channels]
        channel_assets = [strict_sum(r.values[11] for r in month_end[mo] if r.values[1] == ch) for ch in channels]
        trends.append(ReportRow((mo, month_gain[mo], running[mo], investments.get(mo, ZERO), principal[mo], values[mo], *shares, *channel_gain, *channel_assets)))
    reports["月度趋势"] = Report("月度趋势", trend_columns, tuple(reversed(trends)))
    return tuple(result)
