"""Read-only dashboard projection of the existing monthly equity ledger."""
from collections import defaultdict
from dataclasses import replace

from portfolio.domain.analytics import ZERO, ratio, strict_sum
from portfolio.services.charts_service import Series, build_charts, month_end_positions

TREND_CHART_IDS = (11, 12, 13, 14)
CATEGORIES = ("大盘", "杠杆", "正股")
CAPITAL_NOTE = "累计净买入为报表覆盖期间累计买入减卖出。市值差额不等于投资收益，股票市值不含现金和融资。"
METRICS = (
    ("market_value", "月末股票市值", "money"), ("net_buy", "累计净买入", "money"),
    ("difference", "市值差额", "money"), ("large", "大盘占比", "ratio"),
    ("leveraged", "杠杆占比", "ratio"), ("equity", "正股占比", "ratio"),
    ("concentration", "前五大集中度", "ratio"), ("count", "持仓标的数", "count"),
)


def prior_month(month):
    year, number = map(int, month.split("-"))
    return f"{year - (number == 1):04d}-{number - 1 or 12:02d}"


def calendar_months(months):
    if not months:
        return []
    current = months[-1]
    result = []
    while current >= months[0]:
        result.append(current)
        current = prior_month(current)
    return list(reversed(result))


def change(current, previous):
    return current - previous if current is not None and previous is not None else None


def build_portfolio_trend(analytics):
    charts = {c.id: c for c in (analytics.charts or build_charts(analytics))}
    monthly = defaultdict(list)
    for row in analytics.reports["每月持仓收益"].rows:
        monthly[row.values[0]].append(row)

    # Only use a month-end calendar check when the calendar extends beyond
    # that month. Without it, keep past reported snapshots with their date.
    cutoff = analytics.scope.as_of.isoformat()[:7]
    calendar = analytics.trading_days
    calendar_end = max(calendar, default="")[:7]
    final_sessions = {day[:7]: day for day in sorted(calendar)}
    valuation_dates = {
        month: max((r.metadata.get("valuation_date") or "" for r in rows), default="")
        for month, rows in monthly.items()
    }
    months = []
    for month in sorted(monthly):
        final = final_sessions.get(month) if month < calendar_end else None
        if final and valuation_dates[month] != final:
            continue
        if month < cutoff or (final and analytics.latest_profit_day == final):
            months.append(month)
    axis_months = calendar_months(months)
    eligible = set(months)
    capital = charts[18].extra
    concentrations = dict(charts[13].series[0].points)
    counts = dict(charts[14].series[0].points)
    channels = [s.name for s in charts[12].series]
    result = []
    for month in months:
        rows = monthly[month]
        market = capital["market_values"][month]
        net = capital["principal"][month]
        positions, unknown = month_end_positions(rows)
        stock_rows = []
        for ticker, group in positions.items():
            parts = [{
                "broker": r.values[1], "account": r.metadata.get("account_id"),
                "category": r.values[2], "quantity": r.values[8], "price": r.values[10], "market_value": r.values[11],
            } for r in group]
            value = strict_sum(r.values[11] for r in group)
            categories = sorted({r.values[2] for r in group if r.values[2]})
            channel_parts = defaultdict(list)
            category_parts = defaultdict(list)
            for part in parts:
                channel_parts[part["broker"]].append(part)
                category_parts[part["category"]].append(part)

            def totals(groups):
                return {
                    key: {field: strict_sum(part[field] for part in members) for field in ("market_value", "quantity")}
                    for key, members in groups.items()
                }

            stock_rows.append({
                "ticker": ticker, "category": " / ".join(categories),
                "quantity": strict_sum(r.values[8] for r in group),
                "market_value": value, "share": ratio(value, market), "parts": parts,
                "by_channel": totals(channel_parts), "by_category": totals(category_parts),
            })
        stock_rows.sort(key=lambda row: (row["market_value"] is None, -(row["market_value"] or ZERO), row["ticker"]))
        cat_rows = {
            category: strict_sum(r.values[11] for r in rows if r.values[2] == category)
            for category in CATEGORIES
        }
        channel_rows = {
            channel: strict_sum(r.values[11] for r in rows if r.values[1] == channel)
            for channel in channels
        }
        result.append({
            "month": month,
            "valuation_date": valuation_dates[month],
            "market_value": market, "net_buy": net, "difference": change(market, net),
            **{key: ratio(cat_rows[category], market) for (key, _, _), category in zip(METRICS[3:6], CATEGORIES)},
            "concentration": concentrations[month], "count": counts[month],
            "categories": cat_rows, "channels": channel_rows, "stocks": stock_rows,
            "holdings_incomplete": unknown or market is None,
        })
    active_channels = {
        r.values[1] for month in months for r in monthly[month]
        if r.values[8] is None or r.values[8] > ZERO
    }
    titles = {11: "持仓结构占比走势", 13: "前五大持仓集中度走势"}
    plotted = []
    for chart_id in TREND_CHART_IDS:
        chart = charts[chart_id]
        series = []
        stacked = chart_id in (11, 12)
        plotted_series = [s for s in chart.series if chart_id != 12 or s.name in active_channels]
        points_by_series = [dict(s.points) for s in plotted_series]
        visible_months = eligible
        if stacked:
            visible_months = {month for month in eligible if all(points.get(month) is not None for points in points_by_series)}
        for s, points in zip(plotted_series, points_by_series):
            name = s.name.removesuffix("占比") if chart_id == 11 else s.name
            series.append(Series(name, tuple((month, points.get(month) if month in visible_months else None)
                                             for month in axis_months)))
        plotted.append(replace(chart, title=titles.get(chart_id, chart.title), series=tuple(series),
                               kind="stacked_area" if stacked else chart.kind,
                               note="月末历史估值 · 缺失数据留空"))
    return {
        "months": result, "charts": tuple(plotted),
        "currency": analytics.scope.reporting_currency, "note": CAPITAL_NOTE,
        "metrics": METRICS,
    }
