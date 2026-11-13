"""美股已清仓收益分析看板独立只读聚合服务。

仅基于现有美股持仓明细与交易回放报表进行只读统计汇总：
1. 不修改美股持仓计算引擎（replay / holdings / analytics_service）。
2. 不修改任何历史交易记录或收益口径，与系统现有已清仓个股收益率公式
   (realized_pnl ÷ total_buy_amount) 保持完全一致。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from portfolio.domain.analytics import ratio
from portfolio.services.holdings_management_service import channel_label, serialize_val

ZERO = Decimal("0")
RECON_TOLERANCE = Decimal("0.05")


def _to_dec(val: Any) -> Decimal | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return None


def resolve_close_date_bounds(
    date_range_key: str | None,
    *,
    today: date | None = None,
    custom_start: str | None = None,
    custom_end: str | None = None,
) -> tuple[str | None, str | None]:
    """将清仓日期筛选项解析为 [start_date, end_date] 闭区间 ISO 字符串。"""
    key = (date_range_key or "all").strip().lower()
    ref = today or date.today()
    if key in ("", "all"):
        return None, None
    if key == "6m":
        return _shift_months(ref, -6).isoformat(), ref.isoformat()
    if key == "12m":
        return _shift_months(ref, -12).isoformat(), ref.isoformat()
    if key == "24m":
        return _shift_months(ref, -24).isoformat(), ref.isoformat()
    if key == "ytd":
        return f"{ref.year}-01-01", ref.isoformat()
    if key == "last_year":
        return f"{ref.year - 1}-01-01", f"{ref.year - 1}-12-31"
    if key == "custom":
        s = (custom_start or "").strip()[:10] or None
        e = (custom_end or "").strip()[:10] or None
        return s, e
    return None, None


def _shift_months(ref: date, months: int) -> date:
    """安全按自然月偏移日期。"""
    total_months = ref.year * 12 + (ref.month - 1) + months
    year, month_idx = divmod(total_months, 12)
    month = month_idx + 1
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(ref.day, max_day))


def filter_closed_rows(
    rows: list[dict[str, Any]],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    pnl_status: str | None = None,
) -> list[dict[str, Any]]:
    """按清仓日期范围与盈亏结构分类（win/flat/loss）过滤已清仓记录。"""
    result = []
    pnl_filter = (pnl_status or "").strip().lower()
    for r in rows:
        close_day = str(r.get("last_close_date") or "")[:10]
        if start_date and (not close_day or close_day < start_date):
            continue
        if end_date and (not close_day or close_day > end_date):
            continue
        if pnl_filter in ("win", "flat", "loss"):
            rpnl = _to_dec(r.get("realized_pnl")) or ZERO
            if pnl_filter == "win" and rpnl <= ZERO:
                continue
            if pnl_filter == "flat" and rpnl != ZERO:
                continue
            if pnl_filter == "loss" and rpnl >= ZERO:
                continue
        result.append(r)
    return result


def _replay_key_monthly_pnl(evts_chronological: list[Any]) -> tuple[dict[str, Decimal], dict[str, int], Decimal]:
    """对单个 (person_id, account_id, instrument_id) 流水按移动加权平均成本法还原各月已实现盈亏。"""
    qty = ZERO
    cost = ZERO
    monthly_pnl: dict[str, Decimal] = defaultdict(lambda: ZERO)
    monthly_sells: dict[str, int] = defaultdict(int)
    last_month = ""
    total_pnl = ZERO

    for e in evts_chronological:
        meta = getattr(e, "metadata", {}) or {}
        vals = getattr(e, "values", ())
        day = str(meta.get("business_date") or (vals[0] if vals else ""))[:10]
        month = day[:7]
        if month:
            last_month = month
        side = meta.get("side")
        kind = meta.get("kind")
        q = _to_dec(vals[5] if len(vals) > 5 else 0) or ZERO
        amt = _to_dec(vals[7] if len(vals) > 7 else 0) or ZERO
        post_q = _to_dec(vals[9] if len(vals) > 9 else 0) or ZERO

        if side == "BUY" or (kind != "MARKET_TRADE" and post_q > qty):
            qty = post_q
            cost += amt
        else:
            pre_q = post_q + q
            if pre_q > ZERO and post_q > ZERO:
                alloc = cost * (min(q, pre_q) / pre_q)
            elif qty > ZERO or post_q == ZERO:
                alloc = cost
            else:
                alloc = ZERO
            realized = amt - alloc
            if month:
                monthly_pnl[month] += realized
                monthly_sells[month] += 1
            total_pnl += realized
            cost -= alloc
            qty = post_q

    # 若触发不足 0.5 股免卖清仓规则导致仍有极微残留成本，归集至最后清仓月份以保持总盈亏严丝合缝
    if cost != ZERO and last_month:
        monthly_pnl[last_month] -= cost
        total_pnl -= cost

    return monthly_pnl, monthly_sells, total_pnl


def compute_closed_monthly_pnl(
    analytics,
    closed_rows: list[dict[str, Any]],
    *,
    mode: str = "person",
) -> tuple[list[dict[str, Any]], bool]:
    """计算当前筛选范围内已清仓标的的历史每月已实现盈亏分布。

    优先按「交易回放」流水以移动加权成本法逐笔归集卖出盈亏到各自然月；
    若个别持仓缺少流水或无法与最终已实现盈亏核对一致，仅针对该记录降级按最后清仓日期归集并明确标记。
    """
    replay = analytics.reports.get("交易回放") if analytics and hasattr(analytics, "reports") else None
    by_person_key: dict[tuple[Any, Any, Any], list[Any]] = defaultdict(list)
    by_account_key: dict[tuple[Any, Any], list[tuple[Any, Any, Any]]] = defaultdict(list)
    if replay is not None and getattr(replay, "rows", None):
        # 交易回放默认为倒序（最新在前），此处反转为时间正序
        for row in reversed(replay.rows):
            meta = getattr(row, "metadata", {}) or {}
            k = (meta.get("person_id"), meta.get("account_id"), meta.get("instrument_id"))
            by_person_key[k].append(row)
        for k in by_person_key:
            by_account_key[(k[1], k[2])].append(k)

    monthly_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    monthly_counts: dict[str, int] = defaultdict(int)
    used_fallback = False

    for r in closed_rows:
        expected = _to_dec(r.get("realized_pnl")) or ZERO
        acc_id = r.get("account_id")
        inst_id = r.get("instrument_id")
        person_id = r.get("person_id")
        fallback_month = str(r.get("last_close_date") or "")[:7]

        sub_keys = (
            [(person_id, acc_id, inst_id)]
            if mode == "person"
            else by_account_key.get((acc_id, inst_id), [])
        )

        row_monthly: dict[str, Decimal] = defaultdict(lambda: ZERO)
        row_sells: dict[str, int] = defaultdict(int)
        row_sum = ZERO
        has_replay = False
        for sk in sub_keys:
            evts = by_person_key.get(sk)
            if not evts:
                continue
            has_replay = True
            mp, ms, tot = _replay_key_monthly_pnl(evts)
            for m, v in mp.items():
                row_monthly[m] += v
            for m, cnt in ms.items():
                row_sells[m] += cnt
            row_sum += tot

        if has_replay and abs(row_sum - expected) <= RECON_TOLERANCE:
            for m, v in row_monthly.items():
                if v != ZERO or row_sells.get(m, 0) > 0:
                    monthly_totals[m] += v
                    monthly_counts[m] += row_sells.get(m, 0)
        else:
            used_fallback = True
            if fallback_month:
                monthly_totals[fallback_month] += expected
                monthly_counts[fallback_month] += 1

    series = [
        {
            "month": m,
            "pnl": serialize_val(monthly_totals[m]),
            "pnl_dec": monthly_totals[m],
            "sell_count": monthly_counts[m],
        }
        for m in sorted(monthly_totals)
    ]
    return series, used_fallback


def slice_monthly_series(
    monthly_series: list[dict[str, Any]],
    window: str = "all",
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """按近6月/近12月/近24月/全部时间裁剪月度柱状图，不填造空白零月。"""
    key = (window or "all").strip().lower()
    if key == "all" or not monthly_series:
        return monthly_series
    months_map = {"6m": 6, "12m": 12, "24m": 24}
    span = months_map.get(key)
    if not span:
        return monthly_series
    ref = today or date.today()
    cutoff_date = _shift_months(date(ref.year, ref.month, 1), -(span - 1))
    cutoff_ym = cutoff_date.strftime("%Y-%m")
    return [item for item in monthly_series if item["month"] >= cutoff_ym]


def compute_closed_ticker_top10(closed_rows: list[dict[str, Any]], *, top_n: int = 5) -> dict[str, Any]:
    """按 Ticker 汇总当前有效筛选范围内全部已清仓记录的已实现盈亏，分别取盈利与亏损 Top N。"""
    by_ticker: dict[str, dict[str, Any]] = {}
    total_profit = ZERO
    total_loss = ZERO

    for r in closed_rows:
        tk = str(r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        rpnl = _to_dec(r.get("realized_pnl")) or ZERO
        if rpnl > ZERO:
            total_profit += rpnl
        elif rpnl < ZERO:
            total_loss += rpnl

        raw_ch = str(r.get("channel") or r.get("broker") or "").split(" · #")[0].strip()
        ch_name = channel_label(raw_ch) if raw_ch else ""

        entry = by_ticker.get(tk)
        if entry is None:
            entry = {
                "ticker": tk,
                "net_pnl": ZERO,
                "record_count": 0,
                "channels": set(),
            }
            by_ticker[tk] = entry
        entry["net_pnl"] += rpnl
        entry["record_count"] += 1
        if ch_name:
            entry["channels"].add(ch_name)

    winners_raw = sorted(
        (v for v in by_ticker.values() if v["net_pnl"] > ZERO),
        key=lambda x: (-x["net_pnl"], x["ticker"]),
    )
    losers_raw = sorted(
        (v for v in by_ticker.values() if v["net_pnl"] < ZERO),
        key=lambda x: (x["net_pnl"], x["ticker"]),
    )

    def _serialize(item: dict[str, Any], rank: int) -> dict[str, Any]:
        return {
            "rank": rank,
            "ticker": item["ticker"],
            "realized_pnl": serialize_val(item["net_pnl"]),
            "record_count": item["record_count"],
            "closed_count": item["record_count"],
            "channels": sorted(item["channels"]),
            "brokers": sorted(item["channels"]),
        }

    all_winners = [_serialize(v, i + 1) for i, v in enumerate(winners_raw)]
    all_losers = [_serialize(v, i + 1) for i, v in enumerate(losers_raw)]

    n = max(1, int(top_n or 5))
    winners = all_winners[:n]
    losers = all_losers[:n]

    return {
        "winners": winners,
        "losers": losers,
        "profit_top": winners,
        "loss_top": losers,
        "all_winners": all_winners,
        "all_losers": all_losers,
        "total_realized_profit": serialize_val(total_profit),
        "total_realized_loss": serialize_val(total_loss),
        "scope_profit_total": serialize_val(total_profit),
        "scope_loss_total": serialize_val(total_loss),
        "scope_net_pnl": serialize_val(total_profit + total_loss),
    }


def build_closed_dashboard_analytics(
    analytics,
    closed_rows: list[dict[str, Any]],
    *,
    mode: str = "person",
    trend_window: str = "all",
    today: date | None = None,
    top_n: int = 5,
) -> dict[str, Any]:
    """一次性计算美股已清仓收益分析看板的全部数据（7卡片、渠道汇总、盈亏结构、月度趋势）。"""
    total_buy = ZERO
    total_sell = ZERO
    realized_profit = ZERO
    realized_loss = ZERO
    win_count = 0
    flat_count = 0
    loss_count = 0

    broker_groups: dict[str, dict[str, Any]] = {}

    for r in closed_rows:
        buy = _to_dec(r.get("total_buy_amount")) or ZERO
        sell = _to_dec(r.get("total_sell_amount")) or ZERO
        rpnl = _to_dec(r.get("realized_pnl")) or ZERO

        total_buy += buy
        total_sell += sell
        if rpnl > ZERO:
            realized_profit += rpnl
            win_count += 1
        elif rpnl < ZERO:
            realized_loss += rpnl
            loss_count += 1
        else:
            flat_count += 1

        raw_ch = str(r.get("channel") or "未知渠道").split(" · #")[0].strip()
        ch_name = channel_label(raw_ch) or "未知渠道"
        bg = broker_groups.get(ch_name)
        if bg is None:
            bg = {
                "channel": ch_name,
                "stock_count": 0,
                "buy_amount": ZERO,
                "sell_amount": ZERO,
                "realized_profit": ZERO,
                "realized_loss": ZERO,
                "net_pnl": ZERO,
            }
            broker_groups[ch_name] = bg
        bg["stock_count"] += 1
        bg["buy_amount"] += buy
        bg["sell_amount"] += sell
        bg["net_pnl"] += rpnl
        if rpnl > ZERO:
            bg["realized_profit"] += rpnl
        elif rpnl < ZERO:
            bg["realized_loss"] += rpnl

    net_realized_pnl = realized_profit + realized_loss
    overall_rate = ratio(net_realized_pnl, total_buy) if total_buy > ZERO else None
    total_count = len(closed_rows)
    win_pct_val = round(win_count / total_count * 100, 2) if total_count > 0 else None

    cards = {
        "closed_count": total_count,
        "total_buy_amount": serialize_val(total_buy),
        "total_sell_amount": serialize_val(total_sell),
        "realized_profit": serialize_val(realized_profit),
        "realized_loss": serialize_val(realized_loss),
        "net_realized_pnl": serialize_val(net_realized_pnl),
        "realized_pnl_rate": serialize_val(overall_rate),
        "win_ratio": win_pct_val,
        "win_pct": win_pct_val,
        "win_count": win_count,
        "rate_formula_note": "净已实现盈亏 ÷ 买入总额 × 100%（与系统已清仓个股收益率口径一致）",
    }

    broker_rows = []
    for bg in sorted(broker_groups.values(), key=lambda x: x["net_pnl"], reverse=True):
        b_rate = ratio(bg["net_pnl"], bg["buy_amount"]) if bg["buy_amount"] > ZERO else None
        broker_rows.append({
            "channel": bg["channel"],
            "stock_count": bg["stock_count"],
            "buy_amount": serialize_val(bg["buy_amount"]),
            "sell_amount": serialize_val(bg["sell_amount"]),
            "realized_profit": serialize_val(bg["realized_profit"]),
            "realized_loss": serialize_val(bg["realized_loss"]),
            "net_pnl": serialize_val(bg["net_pnl"]),
            "pnl_rate": serialize_val(b_rate),
        })

    broker_total = {
        "channel": "合计",
        "stock_count": total_count,
        "buy_amount": serialize_val(total_buy),
        "sell_amount": serialize_val(total_sell),
        "realized_profit": serialize_val(realized_profit),
        "realized_loss": serialize_val(realized_loss),
        "net_pnl": serialize_val(net_realized_pnl),
        "pnl_rate": serialize_val(overall_rate),
    }

    def _pct(cnt: int) -> float:
        return round(cnt / total_count * 100, 2) if total_count > 0 else 0.0

    structure = {
        "total": total_count,
        "win_count": win_count,
        "flat_count": flat_count,
        "loss_count": loss_count,
        "win_pct": _pct(win_count),
        "flat_pct": _pct(flat_count),
        "loss_pct": _pct(loss_count),
    }

    full_monthly, used_fallback = compute_closed_monthly_pnl(analytics, closed_rows, mode=mode)
    cum_acc = ZERO
    for item in full_monthly:
        cum_acc += _to_dec(item.get("pnl")) or ZERO
        item["cumulative_pnl"] = serialize_val(cum_acc)

    sliced_monthly = slice_monthly_series(full_monthly, trend_window, today=today)
    monthly_clean = [
        {
            "month": item["month"],
            "pnl": item["pnl"],
            "cumulative_pnl": item["cumulative_pnl"],
            "sell_count": item["sell_count"],
        }
        for item in sliced_monthly
    ]
    ticker_top10 = compute_closed_ticker_top10(closed_rows, top_n=top_n)

    attribution_mode = "close_date_fallback" if used_fallback else "trade_level"
    attribution_note = (
        "部分清仓记录因缺少逐笔回放流水按最终清仓日期归集；其余均按卖出交易逐笔还原移动加权已实现盈亏。"
        if used_fallback
        else "按真实卖出交易与移动加权成本法还原每月已实现盈亏，不整笔堆叠至最终清仓日。"
    )

    return {
        "cards": cards,
        "broker_summary": broker_rows,
        "broker_total": broker_total,
        "structure": structure,
        "monthly_trend": monthly_clean,
        "monthly_all": [
            {
                "month": item["month"],
                "pnl": item["pnl"],
                "cumulative_pnl": item["cumulative_pnl"],
                "sell_count": item["sell_count"],
            }
            for item in full_monthly
        ],
        "ticker_top10": ticker_top10,
        "attribution_mode": attribution_mode,
        "attribution_note": attribution_note,
    }
