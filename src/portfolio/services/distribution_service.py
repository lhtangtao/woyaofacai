"""持仓分布数据服务 (Distribution Service)

负责计算股票、券商渠道及分类的持仓市值、占比、历史月末快照对比、持仓来源拆分等。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple


ZERO = Decimal("0")


CHANNEL_CN_MAP = {
    "LONGBRIDGE": "长桥",
    "LONG BRIDGE": "长桥",
    "长桥证券": "长桥",
    "SCHWAB": "嘉信",
    "CHARLES SCHWAB": "嘉信",
    "嘉信理财": "嘉信",
    "FIRSTRADE": "第一证券",
    "FIRST TRADE": "第一证券",
    "BBAE": "BBAE",
    "IBKR": "盈透证券",
    "INTERACTIVE BROKERS": "盈透证券",
    "FUTU": "富途证券",
    "MOOMOO": "富途证券",
    "TIGER": "老虎证券",
}


def to_chinese_channel(channel: Any) -> str:
    """将英文渠道代码统一转换为规范中文名称"""
    if not channel:
        return ""
    s = str(channel).strip()
    return CHANNEL_CN_MAP.get(s.upper(), s)


def to_dec(val: Any) -> Decimal:
    if val is None:
        return ZERO
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return ZERO


@dataclass
class HoldingItem:
    channel: str
    category: str
    ticker: str
    name: str
    shares: Decimal
    price: Decimal
    market_value: Decimal
    cost_price: Optional[Decimal]
    invested: Optional[Decimal]
    profit: Optional[Decimal]
    profit_rate: Optional[Decimal]
    weight: Decimal = ZERO
    prev_weight: Decimal = ZERO
    diff_pp: Decimal = ZERO
    person_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AggregatedItem:
    name: str  # Ticker 或 渠道名 或 分类名
    display_name: str
    market_value: Decimal
    weight: Decimal  # 百分比 0-100
    prev_market_value: Decimal = ZERO
    prev_weight: Decimal = ZERO  # 百分比 0-100
    diff_pp: Decimal = ZERO  # 百分点差值 weight - prev_weight
    category: str = ""
    channel: str = ""
    shares: Decimal = ZERO
    prev_shares: Decimal = ZERO
    shares_change: Decimal = ZERO
    market_value_change: Decimal = ZERO
    items_count: int = 1
    prev_items_count: int = 0


@dataclass
class DistributionSummary:
    current_market_value: Decimal
    prev_market_value: Decimal
    value_change: Decimal
    value_change_rate: Optional[Decimal]
    holding_count: int
    prev_holding_count: int
    holding_count_change: int
    holding_count_change_rate: Optional[Decimal]
    top5_concentration: Decimal
    prev_top5_concentration: Decimal
    top5_concentration_diff_pp: Decimal
    top5_concentration_change_rate: Optional[Decimal]
    as_of_label: str
    prev_as_of_label: str

    stocks_top: List[AggregatedItem]  # Top 5 + 其他
    all_stocks: List[AggregatedItem]  # 全部按 Ticker 聚合
    channels: List[AggregatedItem]
    categories: List[AggregatedItem]
    raw_holdings: List[HoldingItem]  # 原始持仓记录


def get_available_months(analytics) -> List[str]:
    """获取所有历史可选月份（从每月持仓收益报表中提取）"""
    if not analytics or "每月持仓收益" not in analytics.reports:
        return []
    months = {r.values[0] for r in analytics.reports["每月持仓收益"].rows if r.values and r.values[0]}
    return sorted(months, reverse=True)


def calculate_distribution(
    analytics,
    *,
    selected_month: Optional[str] = None,
    compare_month: Optional[str] = None,
    person_filter: Optional[str] = None,
    channel_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    search_keyword: Optional[str] = None,
) -> Optional[DistributionSummary]:
    """根据筛选条件计算当前与对比时点的持仓分布概览数据"""
    if not analytics or "当前持仓" not in analytics.reports:
        return None

    reports = analytics.reports
    curr_rep = reports["当前持仓"]
    month_rep = reports.get("每月持仓收益")

    # 1. 确定观察时点数据与基准月份
    months = get_available_months(analytics)
    as_of_label = "当前 / 最新"
    if selected_month and selected_month in months and month_rep:
        as_of_label = f"{selected_month} 月末"
        # 从每月持仓收益提取该月末记录
        # 列: 0:月份, 1:渠道, 2:分类, 3:Ticker, 4:状态, ..., 8:月末持仓数量, 10:收盘价, 11:月末市值
        obs_rows = [r for r in month_rep.rows if r.values[0] == selected_month and to_dec(r.values[8]) > ZERO]
        curr_items = []
        for r in obs_rows:
            curr_items.append(
                HoldingItem(
                    channel=to_chinese_channel(r.values[1]),
                    category=str(r.values[2]),
                    ticker=str(r.values[3]),
                    name=str(r.values[3]),
                    shares=to_dec(r.values[8]),
                    price=to_dec(r.values[10]),
                    market_value=to_dec(r.values[11]),
                    cost_price=to_dec(r.values[9]) if r.values[9] is not None else None,
                    invested=to_dec(r.values[23]) if len(r.values) > 23 else None,
                    profit=to_dec(r.values[20]) if len(r.values) > 20 else None,
                    profit_rate=to_dec(r.values[13]) if len(r.values) > 13 else None,
                    metadata=dict(r.metadata) if hasattr(r, "metadata") and r.metadata else {},
                )
            )
    else:
        # 当前最新持仓
        # 列: 0:渠道, 1:分类, 2:Ticker, 3:持仓数量, 4:当前价, 5:持仓市值, 6:成本价, 10:持仓盈亏, 11:比例
        obs_rows = [r for r in curr_rep.rows if to_dec(r.values[3]) > ZERO]
        curr_items = []
        for r in obs_rows:
            curr_items.append(
                HoldingItem(
                    channel=to_chinese_channel(r.values[0]),
                    category=str(r.values[1]),
                    ticker=str(r.values[2]),
                    name=str(r.values[2]),
                    shares=to_dec(r.values[3]),
                    price=to_dec(r.values[4]),
                    market_value=to_dec(r.values[5]),
                    cost_price=to_dec(r.values[6]) if r.values[6] is not None else None,
                    invested=to_dec(r.values[9]) if len(r.values) > 9 else None,
                    profit=to_dec(r.values[10]) if len(r.values) > 10 else None,
                    profit_rate=to_dec(r.values[11]) if len(r.values) > 11 else None,
                    metadata=dict(r.metadata) if hasattr(r, "metadata") and r.metadata else {},
                )
            )

    # 2. 确定对比时点数据（默认上月末）
    if not compare_month:
        if selected_month and selected_month in months:
            # 取 selected_month 的上一月
            try:
                sy, sm = map(int, selected_month.split("-"))
                prev_cal = f"{sy if sm > 1 else sy - 1:04d}-{sm - 1 if sm > 1 else 12:02d}"
                compare_month = prev_cal if prev_cal in months else (months[months.index(selected_month) + 1] if months.index(selected_month) + 1 < len(months) else "")
            except Exception:
                compare_month = months[1] if len(months) > 1 else (months[0] if months else "")
        else:
            # 观察时点为当前/最新，对比时点默认取上月末（即历史最近一个完整月末）
            compare_month = months[1] if len(months) > 1 else (months[0] if months else "")

    prev_as_of_label = f"上月末 ({compare_month})" if compare_month else "无对比"
    prev_items: List[HoldingItem] = []
    if compare_month and month_rep:
        cmp_rows = [r for r in month_rep.rows if r.values[0] == compare_month and to_dec(r.values[8]) > ZERO]
        for r in cmp_rows:
            prev_items.append(
                HoldingItem(
                    channel=to_chinese_channel(r.values[1]),
                    category=str(r.values[2]),
                    ticker=str(r.values[3]),
                    name=str(r.values[3]),
                    shares=to_dec(r.values[8]),
                    price=to_dec(r.values[10]),
                    market_value=to_dec(r.values[11]),
                    cost_price=to_dec(r.values[9]) if r.values[9] is not None else None,
                    invested=to_dec(r.values[23]) if len(r.values) > 23 else None,
                    profit=to_dec(r.values[20]) if len(r.values) > 20 else None,
                    profit_rate=to_dec(r.values[13]) if len(r.values) > 13 else None,
                    metadata=dict(r.metadata) if hasattr(r, "metadata") and r.metadata else {},
                )
            )

    # 3. 过滤处理（渠道、分类、搜索关键词）
    def filter_item(it: HoldingItem) -> bool:
        if channel_filter and channel_filter not in ("全部", "全部渠道"):
            if to_chinese_channel(it.channel) != to_chinese_channel(channel_filter):
                return False
        if category_filter and category_filter not in ("全部", "全部分类") and it.category != category_filter:
            return False
        if search_keyword:
            kw = search_keyword.strip().upper()
            if kw not in it.ticker.upper() and kw not in it.name.upper() and kw not in it.channel.upper():
                return False
        return True

    filtered_curr = [it for it in curr_items if filter_item(it)]
    filtered_prev = [it for it in prev_items if filter_item(it)]

    # 4. 基础统计
    curr_total_mv = sum((it.market_value for it in filtered_curr), ZERO)
    prev_total_mv = sum((it.market_value for it in filtered_prev), ZERO)

    value_change = curr_total_mv - prev_total_mv
    value_change_rate = (value_change / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else None

    # 计算各项占比并回填
    for it in filtered_curr:
        it.weight = (it.market_value / curr_total_mv * Decimal("100")) if curr_total_mv > ZERO else ZERO

    # 唯一标的数
    unique_tickers = {it.ticker for it in filtered_curr}
    holding_count = len(unique_tickers)

    # 5. 按 Ticker 聚合
    # 当前
    ticker_curr: Dict[str, Dict[str, Any]] = {}
    for it in filtered_curr:
        if it.ticker not in ticker_curr:
            ticker_curr[it.ticker] = {
                "name": it.ticker,
                "display_name": it.name or it.ticker,
                "category": it.category,
                "channel": it.channel,
                "market_value": ZERO,
                "shares": ZERO,
                "count": 0,
            }
        ticker_curr[it.ticker]["market_value"] += it.market_value
        ticker_curr[it.ticker]["shares"] += it.shares
        ticker_curr[it.ticker]["count"] += 1

    # 对比期
    ticker_prev_mv: Dict[str, Decimal] = {}
    ticker_prev_shares: Dict[str, Decimal] = {}
    for it in filtered_prev:
        ticker_prev_mv[it.ticker] = ticker_prev_mv.get(it.ticker, ZERO) + it.market_value
        ticker_prev_shares[it.ticker] = ticker_prev_shares.get(it.ticker, ZERO) + it.shares

    all_stocks: List[AggregatedItem] = []
    for t, data in ticker_curr.items():
        mv = data["market_value"]
        w = (mv / curr_total_mv * Decimal("100")) if curr_total_mv > ZERO else ZERO
        pmv = ticker_prev_mv.get(t, ZERO)
        pw = (pmv / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else ZERO
        diff = w - pw
        sh = data["shares"]
        psh = ticker_prev_shares.get(t, ZERO)
        all_stocks.append(
            AggregatedItem(
                name=t,
                display_name=data["display_name"],
                market_value=mv,
                weight=w,
                prev_market_value=pmv,
                prev_weight=pw,
                diff_pp=diff,
                category=data["category"],
                channel=data["channel"] if data["count"] == 1 else "多渠道",
                shares=sh,
                prev_shares=psh,
                shares_change=sh - psh,
                market_value_change=mv - pmv,
                items_count=data["count"],
            )
        )

    # 按当前市值降序排序
    all_stocks.sort(key=lambda x: x.market_value, reverse=True)

    # 前五大持仓集中度
    top5_mv = sum((it.market_value for it in all_stocks[:5]), ZERO)
    top5_concentration = (top5_mv / curr_total_mv * Decimal("100")) if curr_total_mv > ZERO else ZERO

    # Top 5 + 其他
    stocks_top = list(all_stocks[:5])
    if len(all_stocks) > 5:
        other_curr_mv = sum((it.market_value for it in all_stocks[5:]), ZERO)
        other_prev_mv = sum((ticker_prev_mv.get(t, ZERO) for t in ticker_prev_mv if t not in {it.name for it in stocks_top}), ZERO)
        other_w = (other_curr_mv / curr_total_mv * Decimal("100")) if curr_total_mv > ZERO else ZERO
        other_pw = (other_prev_mv / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else ZERO
        stocks_top.append(
            AggregatedItem(
                name="其他",
                display_name="其他持仓标的",
                market_value=other_curr_mv,
                weight=other_w,
                prev_market_value=other_prev_mv,
                prev_weight=other_pw,
                diff_pp=other_w - other_pw,
                market_value_change=other_curr_mv - other_prev_mv,
                category="混合",
                channel="混合",
                items_count=len(all_stocks) - 5,
            )
        )

    # 6. 按渠道聚合
    ch_curr: Dict[str, Dict[str, Any]] = {}
    for it in filtered_curr:
        if it.channel not in ch_curr:
            ch_curr[it.channel] = {"market_value": ZERO, "tickers": set()}
        ch_curr[it.channel]["market_value"] += it.market_value
        ch_curr[it.channel]["tickers"].add(it.ticker)

    ch_prev: Dict[str, Dict[str, Any]] = {}
    for it in filtered_prev:
        if it.channel not in ch_prev:
            ch_prev[it.channel] = {"market_value": ZERO, "tickers": set()}
        ch_prev[it.channel]["market_value"] += it.market_value
        ch_prev[it.channel]["tickers"].add(it.ticker)

    channels: List[AggregatedItem] = []
    # 结合当前与上期所有出现过的渠道
    all_channel_names = sorted(set(ch_curr.keys()) | set(ch_prev.keys()))
    for ch in all_channel_names:
        mv = ch_curr.get(ch, {}).get("market_value", ZERO)
        t_count = len(ch_curr.get(ch, {}).get("tickers", set()))
        w = (mv / curr_total_mv * Decimal("100")) if curr_total_mv > ZERO else ZERO
        pmv = ch_prev.get(ch, {}).get("market_value", ZERO)
        prev_t_count = len(ch_prev.get(ch, {}).get("tickers", set()))
        pw = (pmv / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else ZERO
        channels.append(
            AggregatedItem(
                name=ch,
                display_name=ch,
                market_value=mv,
                weight=w,
                prev_market_value=pmv,
                prev_weight=pw,
                diff_pp=w - pw,
                market_value_change=mv - pmv,
                items_count=t_count,
                prev_items_count=prev_t_count,
            )
        )
    channels.sort(key=lambda x: x.market_value, reverse=True)

    # 7. 按分类聚合
    cat_curr: Dict[str, Dict[str, Any]] = {}
    for it in filtered_curr:
        if it.category not in cat_curr:
            cat_curr[it.category] = {"market_value": ZERO, "tickers": set()}
        cat_curr[it.category]["market_value"] += it.market_value
        cat_curr[it.category]["tickers"].add(it.ticker)

    cat_prev: Dict[str, Dict[str, Any]] = {}
    for it in filtered_prev:
        if it.category not in cat_prev:
            cat_prev[it.category] = {"market_value": ZERO, "tickers": set()}
        cat_prev[it.category]["market_value"] += it.market_value
        cat_prev[it.category]["tickers"].add(it.ticker)

    categories: List[AggregatedItem] = []
    all_cat_names = sorted(set(cat_curr.keys()) | set(cat_prev.keys()))
    for ct in all_cat_names:
        mv = cat_curr.get(ct, {}).get("market_value", ZERO)
        t_count = len(cat_curr.get(ct, {}).get("tickers", set()))
        w = (mv / curr_total_mv * Decimal("100")) if curr_total_mv > ZERO else ZERO
        pmv = cat_prev.get(ct, {}).get("market_value", ZERO)
        prev_t_count = len(cat_prev.get(ct, {}).get("tickers", set()))
        pw = (pmv / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else ZERO
        categories.append(
            AggregatedItem(
                name=ct,
                display_name=ct,
                market_value=mv,
                weight=w,
                prev_market_value=pmv,
                prev_weight=pw,
                diff_pp=w - pw,
                market_value_change=mv - pmv,
                items_count=t_count,
                prev_items_count=prev_t_count,
            )
        )
    categories.sort(key=lambda x: x.market_value, reverse=True)

    # 对 raw_holdings 补充 prev_weight 与 diff_pp
    for it in filtered_curr:
        pmv = ticker_prev_mv.get(it.ticker, ZERO)
        it.prev_weight = (pmv / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else ZERO
        it.diff_pp = it.weight - it.prev_weight

    # 8. 对比期标的数及变化
    prev_unique_tickers = {it.ticker for it in filtered_prev}
    prev_holding_count = len(prev_unique_tickers)
    holding_count_change = holding_count - prev_holding_count
    holding_count_change_rate = (
        (Decimal(holding_count_change) / Decimal(prev_holding_count) * Decimal("100"))
        if prev_holding_count > 0
        else None
    )

    # 9. 对比期前五大持仓集中度及变化
    sorted_prev_tickers = sorted(ticker_prev_mv.values(), reverse=True)
    prev_top5_mv = sum(sorted_prev_tickers[:5], ZERO)
    prev_top5_concentration = (prev_top5_mv / prev_total_mv * Decimal("100")) if prev_total_mv > ZERO else ZERO
    top5_concentration_diff_pp = top5_concentration - prev_top5_concentration
    top5_concentration_change_rate = (
        (top5_concentration_diff_pp / prev_top5_concentration * Decimal("100"))
        if prev_top5_concentration > ZERO
        else None
    )

    return DistributionSummary(
        current_market_value=curr_total_mv,
        prev_market_value=prev_total_mv,
        value_change=value_change,
        value_change_rate=value_change_rate,
        holding_count=holding_count,
        prev_holding_count=prev_holding_count,
        holding_count_change=holding_count_change,
        holding_count_change_rate=holding_count_change_rate,
        top5_concentration=top5_concentration,
        prev_top5_concentration=prev_top5_concentration,
        top5_concentration_diff_pp=top5_concentration_diff_pp,
        top5_concentration_change_rate=top5_concentration_change_rate,
        as_of_label=as_of_label,
        prev_as_of_label=prev_as_of_label,
        stocks_top=stocks_top,
        all_stocks=all_stocks,
        channels=channels,
        categories=categories,
        raw_holdings=filtered_curr,
    )


def get_stock_holding_sources(analytics, ticker: str) -> List[Dict[str, Any]]:
    """提取某 Ticker 在各人员/渠道的持仓来源明细"""
    if not analytics:
        return []
    sources = []
    # 从当前持仓报表中提取该 ticker
    curr_rep = analytics.reports.get("当前持仓")
    if curr_rep:
        for r in curr_rep.rows:
            if str(r.values[2]).upper() == ticker.upper() and to_dec(r.values[3]) > ZERO:
                # metadata 中可能包含 account_id, person_ids 等
                meta = dict(r.metadata) if hasattr(r, "metadata") and r.metadata else {}
                sources.append({
                    "channel": to_chinese_channel(r.values[0]),
                    "category": str(r.values[1]),
                    "ticker": str(r.values[2]),
                    "shares": to_dec(r.values[3]),
                    "price": to_dec(r.values[4]),
                    "market_value": to_dec(r.values[5]),
                    "cost_price": to_dec(r.values[6]),
                    "profit": to_dec(r.values[10]),
                    "profit_rate": to_dec(r.values[11]),
                    "account_id": meta.get("account_id"),
                    "person_ids": meta.get("person_ids"),
                    "instrument_id": meta.get("instrument_id"),
                    "metadata": meta,
                })
    return sources
