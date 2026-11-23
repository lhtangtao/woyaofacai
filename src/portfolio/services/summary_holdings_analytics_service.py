"""美股「持仓管理 → 汇总」独立只读统计与跨渠道聚合服务。

设计原则与对账守恒约束：
1. 仅进行只读聚合，不修改任何底层账本或核心计算逻辑。
2. 所有金额、数量、盈亏、收益率核心计算严格使用 Decimal，严禁 float 参与财务核心汇总。
3. 严守三标签页对账守恒：
   - 汇总「当前浮动盈亏」 == 「持仓中」浮动盈亏之和
   - 汇总「历史已实现盈亏」 == 「已清仓」已实现盈亏之和
   - 汇总「累计总盈亏」 == 「当前浮动盈亏」 + 「历史已实现盈亏」
   - 汇总「当前持仓总市值 / 总成本」 == 「持仓中」总市值 / 总成本
4. 将同一只股票（Ticker）跨不同券商渠道的「持有中」与「已清仓」记录合并为一行，
   同时保留分渠道拆解列表（channel_breakdown），支撑明细表与右侧抽屉联动展示。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import logging
from pathlib import Path
from typing import Any

from portfolio.domain.analytics import ratio
from portfolio.services.holdings_management_service import (
    channel_label,
    serialize_val,
)
from portfolio.services.open_holdings_analytics_service import (
    load_instrument_metadata_map,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")


def _to_dec(val: Any) -> Decimal | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _norm_channel(raw: Any) -> str:
    text = str(raw or "").split(" · #")[0].strip()
    return channel_label(text) or "未知渠道"


def _resolve_category(row: dict[str, Any], meta_map: dict[str, dict[str, str]]) -> str:
    tk = str(row.get("ticker") or "").strip().upper()
    clean_tk = tk.replace(".US", "")
    meta = meta_map.get(clean_tk) or meta_map.get(tk) or {}
    cat = (
        row.get("industry")
        or row.get("category")
        or meta.get("industry")
        or meta.get("category")
        or ""
    )
    cat = str(cat).strip()
    return cat if cat else "未分类"


def _build_channel_tags(channels: list[dict[str, Any]]) -> str:
    """生成如「长桥(持) · BBAE(清)」的跨渠道状态摘要文本。"""
    # 按券商名归并（若同一券商有多人或多账户，同时存在持有与清仓则标为 持/清）
    by_broker: dict[str, set[str]] = {}
    for ch in channels:
        b = ch["channel"]
        by_broker.setdefault(b, set()).add(ch["status"])

    parts: list[str] = []
    # 优先排持有中的渠道在前，其次按渠道名排序
    ordered = sorted(
        by_broker.items(),
        key=lambda kv: (0 if "持有中" in kv[1] else 1, kv[0]),
    )
    for broker_name, statuses in ordered:
        has_open = "持有中" in statuses
        has_closed = "已清仓" in statuses
        if has_open and has_closed:
            tag = "持/清"
        elif has_open:
            tag = "持"
        else:
            tag = "清"
        parts.append(f"{broker_name}({tag})")
    return "·".join(parts)


def _aggregate_ticker_rows(
    open_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    meta_map: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """将持有中行与已清仓行按股票代码（Ticker）跨渠道合并。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in open_rows:
        tk = str(r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        grouped[tk].append({**r, "_row_status": "持有中"})

    for r in closed_rows:
        tk = str(r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        grouped[tk].append({**r, "_row_status": "已清仓"})

    merged_rows: list[dict[str, Any]] = []
    for tk, items in grouped.items():
        clean_tk = tk.replace(".US", "")
        meta = meta_map.get(clean_tk) or meta_map.get(tk) or {}
        stock_name = str(meta.get("name") or tk)
        category = _resolve_category(items[0], meta_map)
        for it in items[1:]:
            if category == "未分类":
                c = _resolve_category(it, meta_map)
                if c != "未分类":
                    category = c
                    break

        qty = ZERO
        mv = ZERO
        cost = ZERO
        floating_pnl = ZERO
        realized_pnl = ZERO
        buy_amt = ZERO
        sell_amt = ZERO
        has_buy_amt = False
        has_sell_amt = False
        has_mv = False
        has_cost = False
        buy_count = 0
        sell_count = 0
        has_trade_counts = False

        current_price: Decimal | None = None
        quote_status = None
        quote_time = None
        has_open = False
        has_closed = False

        person_ids: set[int] = set()
        person_names: set[str] = set()
        channel_breakdown: list[dict[str, Any]] = []
        distinct_brokers: set[str] = set()

        for it in items:
            st = it["_row_status"]
            ch_name = _norm_channel(it.get("channel"))
            distinct_brokers.add(ch_name)
            p_id = it.get("person_id")
            if p_id is not None:
                person_ids.add(int(p_id))
            p_name = str(it.get("person_name") or "").strip()
            if p_name:
                person_names.add(p_name)

            it_qty = _to_dec(it.get("quantity")) or ZERO
            it_mv = _to_dec(it.get("market_value"))
            it_cost = _to_dec(it.get("cost_amount"))
            it_price = _to_dec(it.get("current_price"))
            it_buy_amt = _to_dec(it.get("total_buy_amount"))
            it_sell_amt = _to_dec(it.get("total_sell_amount"))
            it_bc = it.get("buy_count")
            it_sc = it.get("sell_count")

            if it_price is not None and it_price > ZERO and current_price is None:
                current_price = it_price
                quote_status = it.get("quote_status")
                quote_time = it.get("quote_time")

            if it_buy_amt is not None:
                buy_amt += it_buy_amt
                has_buy_amt = True
            if it_sell_amt is not None:
                sell_amt += it_sell_amt
                has_sell_amt = True
            if it_bc is not None:
                buy_count += int(it_bc)
                has_trade_counts = True
            if it_sc is not None:
                sell_count += int(it_sc)
                has_trade_counts = True

            if st == "持有中":
                has_open = True
                it_float = _to_dec(it.get("total_pnl")) or ZERO
                it_real = ZERO
                qty += it_qty
                if it_mv is not None:
                    mv += it_mv
                    has_mv = True
                if it_cost is not None:
                    cost += it_cost
                    has_cost = True
                floating_pnl += it_float
                # 持有中行若无 total_buy_amount，至少以当前持仓成本兜底参考（仅当整行无买入额时）
                ch_sub = it_float
            else:
                has_closed = True
                it_float = ZERO
                it_real = _to_dec(it.get("realized_pnl"))
                if it_real is None:
                    it_real = _to_dec(it.get("total_pnl")) or ZERO
                realized_pnl += it_real
                ch_sub = it_real

            channel_breakdown.append({
                "person_id": p_id,
                "person_name": p_name or "—",
                "account_id": it.get("account_id"),
                "instrument_id": it.get("instrument_id"),
                "channel": ch_name,
                "status": st,
                "quantity": serialize_val(it_qty if st == "持有中" else ZERO),
                "market_value": serialize_val((it_mv or ZERO) if st == "持有中" else ZERO),
                "cost_amount": serialize_val((it_cost or ZERO) if st == "持有中" else ZERO),
                "floating_pnl": serialize_val(it_float),
                "realized_pnl": serialize_val(it_real),
                "subtotal_pnl": serialize_val(ch_sub),
                "total_buy_amount": serialize_val(it_buy_amt),
                "total_sell_amount": serialize_val(it_sell_amt),
                "buy_count": it_bc,
                "sell_count": it_sc,
            })

        total_pnl = floating_pnl + realized_pnl
        avg_cost = ratio(cost, qty) if (has_open and qty > ZERO and has_cost) else None
        # 综合总收益率：优先以历史累计买入总额为分母，若缺失则退回当前持仓成本
        denom = buy_amt if (has_buy_amt and buy_amt > ZERO) else (cost if (has_cost and cost > ZERO) else None)
        total_pnl_rate = ratio(total_pnl, denom) if (denom is not None and denom > ZERO) else None

        agg_status = "持有中" if has_open else "已全清仓"
        is_cross_channel = (len(distinct_brokers) > 1) or (has_open and has_closed)
        channels_display = _build_channel_tags(channel_breakdown)
        trade_counts_display = f"{buy_count}/{sell_count}" if has_trade_counts else "—"

        # 按渠道归并排序（持有中在前）
        channel_breakdown.sort(key=lambda c: (0 if c["status"] == "持有中" else 1, c["channel"], c["person_name"]))

        merged_rows.append({
            "ticker": tk,
            "stock_name": stock_name,
            "industry": category,
            "category": category,
            "status": agg_status,
            "has_open": has_open,
            "has_closed": has_closed,
            "is_cross_channel": is_cross_channel,
            "channels_display": channels_display,
            "channels_set": sorted(distinct_brokers),
            "channel_breakdown": channel_breakdown,
            "person_ids": sorted(person_ids),
            "person_names": sorted(person_names),
            "quantity": serialize_val(qty),
            "cost_price": serialize_val(avg_cost),
            "current_price": serialize_val(current_price),
            "market_value": serialize_val(mv if has_open and has_mv else ZERO),
            "cost_amount": serialize_val(cost if has_open and has_cost else ZERO),
            "floating_pnl": serialize_val(floating_pnl),
            "realized_pnl": serialize_val(realized_pnl),
            "total_pnl": serialize_val(total_pnl),
            "total_pnl_rate": serialize_val(total_pnl_rate),
            "total_buy_amount": serialize_val(buy_amt if has_buy_amt else None),
            "total_sell_amount": serialize_val(sell_amt if has_sell_amt else None),
            "buy_count": buy_count if has_trade_counts else None,
            "sell_count": sell_count if has_trade_counts else None,
            "trade_counts_display": trade_counts_display,
            "quote_status": quote_status,
            "quote_time": quote_time,
        })

    merged_rows.sort(key=lambda x: Decimal(str(x.get("total_pnl") or 0)), reverse=True)
    return merged_rows


def _build_broker_distribution(
    open_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """按券商渠道统计当前市值、浮动盈亏、已实现盈亏、累计总盈亏与去重标的数。"""
    groups: dict[str, dict[str, Any]] = {}
    all_tickers: set[str] = set()

    def _ensure(ch: str) -> dict[str, Any]:
        if ch not in groups:
            groups[ch] = {
                "name": ch,
                "market_value": ZERO,
                "cost_amount": ZERO,
                "floating_pnl": ZERO,
                "realized_pnl": ZERO,
                "tickers": set(),
            }
        return groups[ch]

    for r in open_rows:
        ch = _norm_channel(r.get("channel"))
        g = _ensure(ch)
        tk = str(r.get("ticker") or "").strip().upper()
        if tk:
            g["tickers"].add(tk)
            all_tickers.add(tk)
        g["market_value"] += _to_dec(r.get("market_value")) or ZERO
        g["cost_amount"] += _to_dec(r.get("cost_amount")) or ZERO
        g["floating_pnl"] += _to_dec(r.get("total_pnl")) or ZERO

    for r in closed_rows:
        ch = _norm_channel(r.get("channel"))
        g = _ensure(ch)
        tk = str(r.get("ticker") or "").strip().upper()
        if tk:
            g["tickers"].add(tk)
            all_tickers.add(tk)
        rlz = _to_dec(r.get("realized_pnl"))
        if rlz is None:
            rlz = _to_dec(r.get("total_pnl")) or ZERO
        g["realized_pnl"] += rlz

    items: list[dict[str, Any]] = []
    tot_mv = ZERO
    tot_cost = ZERO
    tot_float = ZERO
    tot_real = ZERO

    for g in sorted(
        groups.values(),
        key=lambda x: (x["market_value"], abs(x["floating_pnl"] + x["realized_pnl"])),
        reverse=True,
    ):
        sub_total = g["floating_pnl"] + g["realized_pnl"]
        tot_mv += g["market_value"]
        tot_cost += g["cost_amount"]
        tot_float += g["floating_pnl"]
        tot_real += g["realized_pnl"]
        items.append({
            "name": g["name"],
            "market_value": serialize_val(g["market_value"]),
            "cost_amount": serialize_val(g["cost_amount"]),
            "floating_pnl": serialize_val(g["floating_pnl"]),
            "realized_pnl": serialize_val(g["realized_pnl"]),
            "total_pnl": serialize_val(sub_total),
            "stock_count": len(g["tickers"]),
        })

    return {
        "items": items,
        "total": {
            "market_value": serialize_val(tot_mv),
            "cost_amount": serialize_val(tot_cost),
            "floating_pnl": serialize_val(tot_float),
            "realized_pnl": serialize_val(tot_real),
            "total_pnl": serialize_val(tot_float + tot_real),
            "stock_count": len(all_tickers),
        },
    }


def _build_person_summary(
    open_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按归属人员汇总全周期（持有中 + 已清仓）总账。"""
    groups: dict[Any, dict[str, Any]] = {}

    def _ensure(p_id: Any, p_name: str) -> dict[str, Any]:
        key = p_id if p_id is not None else p_name
        if key not in groups:
            groups[key] = {
                "person_id": p_id,
                "person_name": p_name or "未知人员",
                "market_value": ZERO,
                "cost_amount": ZERO,
                "floating_pnl": ZERO,
                "realized_pnl": ZERO,
                "total_buy_amount": ZERO,
                "open_tickers": set(),
                "closed_tickers": set(),
                "all_tickers": set(),
            }
        return groups[key]

    for r in open_rows:
        g = _ensure(r.get("person_id"), str(r.get("person_name") or ""))
        tk = str(r.get("ticker") or "").strip().upper()
        if tk:
            g["open_tickers"].add(tk)
            g["all_tickers"].add(tk)
        g["market_value"] += _to_dec(r.get("market_value")) or ZERO
        g["cost_amount"] += _to_dec(r.get("cost_amount")) or ZERO
        g["floating_pnl"] += _to_dec(r.get("total_pnl")) or ZERO
        g["total_buy_amount"] += _to_dec(r.get("total_buy_amount")) or ZERO

    for r in closed_rows:
        g = _ensure(r.get("person_id"), str(r.get("person_name") or ""))
        tk = str(r.get("ticker") or "").strip().upper()
        if tk:
            g["closed_tickers"].add(tk)
            g["all_tickers"].add(tk)
        rlz = _to_dec(r.get("realized_pnl"))
        if rlz is None:
            rlz = _to_dec(r.get("total_pnl")) or ZERO
        g["realized_pnl"] += rlz
        g["total_buy_amount"] += _to_dec(r.get("total_buy_amount")) or ZERO

    out: list[dict[str, Any]] = []
    for g in sorted(groups.values(), key=lambda x: x["market_value"], reverse=True):
        total_pnl = g["floating_pnl"] + g["realized_pnl"]
        denom = g["total_buy_amount"] if g["total_buy_amount"] > ZERO else g["cost_amount"]
        total_rate = ratio(total_pnl, denom) if denom > ZERO else None
        open_cnt = len(g["open_tickers"])
        closed_only_cnt = len(g["closed_tickers"] - g["open_tickers"])
        out.append({
            "person_id": g["person_id"],
            "person_name": g["person_name"],
            "market_value": serialize_val(g["market_value"]),
            "cost_amount": serialize_val(g["cost_amount"]),
            "floating_pnl": serialize_val(g["floating_pnl"]),
            "realized_pnl": serialize_val(g["realized_pnl"]),
            "total_pnl": serialize_val(total_pnl),
            "total_pnl_rate": serialize_val(total_rate),
            "stock_count": len(g["all_tickers"]),
            "open_count": open_cnt,
            "closed_count": closed_only_cnt,
            "stock_count_display": f"{len(g['all_tickers'])} 只 ({open_cnt}持 / {closed_only_cnt}清)",
        })
    return out


def _build_top_contributors(
    merged_rows: list[dict[str, Any]],
    metric: str = "total",
) -> dict[str, Any]:
    """构建累计总盈亏 / 浮动盈亏 / 已实现盈亏的双向 Top N 排行数据。"""
    key_map = {
        "total": "total_pnl",
        "floating": "floating_pnl",
        "realized": "realized_pnl",
    }
    field = key_map.get(metric, "total_pnl")

    gains: list[dict[str, Any]] = []
    losses: list[dict[str, Any]] = []
    total_gain = ZERO
    total_loss = ZERO

    for r in merged_rows:
        val = _to_dec(r.get(field)) or ZERO
        if val == ZERO:
            continue
        item = {
            "ticker": r["ticker"],
            "stock_name": r["stock_name"],
            "status": r["status"],
            "is_cross_channel": r["is_cross_channel"],
            "pnl": serialize_val(val),
            "total_pnl": r["total_pnl"],
            "floating_pnl": r["floating_pnl"],
            "realized_pnl": r["realized_pnl"],
        }
        if val > ZERO:
            gains.append(item)
            total_gain += val
        else:
            losses.append(item)
            total_loss += val

    gains.sort(key=lambda x: Decimal(str(x["pnl"] or 0)), reverse=True)
    losses.sort(key=lambda x: Decimal(str(x["pnl"] or 0)))

    return {
        "metric": metric if metric in key_map else "total",
        "gains": gains,
        "losses": losses,
        "total_gain": serialize_val(total_gain),
        "total_loss": serialize_val(total_loss),
    }


def build_summary_holdings_dashboard(
    analytics,
    open_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    broker: str | None = None,
    status_filter: str = "all",
    industry: str | None = None,
    search: str | None = None,
    pnl_filter: str = "all",
    structure_filter: str = "",
    ticker_filter: str = "",
    top_metric: str = "total",
    person_drill_id: int | None = None,
) -> dict[str, Any]:
    """构建「持仓管理 → 汇总」标签页的完整看板数据载荷。"""
    meta_map = load_instrument_metadata_map(db_path=db_path)

    # 先按原始未筛券商的全量行提取可用下拉筛选选项
    available_brokers = sorted({
        _norm_channel(r.get("channel"))
        for r in (*open_rows, *closed_rows)
        if r.get("channel")
    })
    available_industries = sorted({
        _resolve_category(r, meta_map)
        for r in (*open_rows, *closed_rows)
    })

    # 若指定了券商筛选，则按券商过滤底层明细行后再聚合
    norm_broker = channel_label(broker).casefold() if broker else ""
    scoped_open = [
        r for r in open_rows
        if not norm_broker or _norm_channel(r.get("channel")).casefold() == norm_broker
    ]
    scoped_closed = [
        r for r in closed_rows
        if not norm_broker or _norm_channel(r.get("channel")).casefold() == norm_broker
    ]

    # 若指定了人员下钻
    if person_drill_id is not None:
        drill_open = [r for r in scoped_open if r.get("person_id") == person_drill_id]
        drill_closed = [r for r in scoped_closed if r.get("person_id") == person_drill_id]
    else:
        drill_open = scoped_open
        drill_closed = scoped_closed

    # 全渠道视角用于左侧券商汇总表（点击某券商时仍保留完整券商对照）
    broker_distribution = _build_broker_distribution(open_rows, closed_rows)
    person_summary = _build_person_summary(scoped_open, scoped_closed)

    # 聚合为按 Ticker 合并的行
    all_merged = _aggregate_ticker_rows(drill_open, drill_closed, meta_map)

    # 应用局部筛选（状态 / 股票类型 / 搜索）
    st_key = (status_filter or "all").strip().lower()
    ind_key = (industry or "").strip().casefold()
    search_key = (search or "").strip().casefold()

    filtered_merged: list[dict[str, Any]] = []
    for row in all_merged:
        if st_key == "open" and not row["has_open"]:
            continue
        if st_key == "closed" and row["has_open"]:
            continue
        if st_key == "cross" and not row["is_cross_channel"]:
            continue
        if ind_key and str(row.get("industry") or "").strip().casefold() != ind_key:
            continue
        if search_key:
            haystack = (
                f"{row['ticker']} {row['stock_name']} {row['industry']} "
                f"{row['channels_display']} {' '.join(row['person_names'])}"
            ).casefold()
            if search_key not in haystack:
                continue
        filtered_merged.append(row)

    # 基于 filtered_merged 计算顶部 6 张 KPI、状态与盈亏结构环形图、类型分解、Top N
    tot_mv = ZERO
    tot_cost = ZERO
    tot_float = ZERO
    tot_real = ZERO
    tot_buy = ZERO
    open_cnt = 0
    closed_cnt = 0
    win_cnt = 0
    loss_cnt = 0
    flat_cnt = 0

    struct_counts = {
        "open_win": 0,
        "open_loss": 0,
        "closed_win": 0,
        "closed_loss": 0,
    }
    type_groups: dict[str, dict[str, Any]] = {}

    for row in filtered_merged:
        r_mv = _to_dec(row["market_value"]) or ZERO
        r_cost = _to_dec(row["cost_amount"]) or ZERO
        r_float = _to_dec(row["floating_pnl"]) or ZERO
        r_real = _to_dec(row["realized_pnl"]) or ZERO
        r_total = _to_dec(row["total_pnl"]) or ZERO
        r_buy = _to_dec(row["total_buy_amount"]) or ZERO

        tot_mv += r_mv
        tot_cost += r_cost
        tot_float += r_float
        tot_real += r_real
        tot_buy += r_buy

        if row["has_open"]:
            open_cnt += 1
            if r_total >= ZERO:
                struct_counts["open_win"] += 1
            else:
                struct_counts["open_loss"] += 1
        else:
            closed_cnt += 1
            if r_total >= ZERO:
                struct_counts["closed_win"] += 1
            else:
                struct_counts["closed_loss"] += 1

        if r_total > ZERO:
            win_cnt += 1
        elif r_total < ZERO:
            loss_cnt += 1
        else:
            flat_cnt += 1

        cat = row["industry"]
        tg = type_groups.setdefault(
            cat,
            {"name": cat, "stock_count": 0, "floating_pnl": ZERO, "realized_pnl": ZERO, "market_value": ZERO},
        )
        tg["stock_count"] += 1
        tg["floating_pnl"] += r_float
        tg["realized_pnl"] += r_real
        tg["market_value"] += r_mv

    grand_total_pnl = tot_float + tot_real
    grand_denom = tot_buy if tot_buy > ZERO else tot_cost
    grand_rate = ratio(grand_total_pnl, grand_denom) if grand_denom > ZERO else None
    open_rate = ratio(tot_float, tot_cost) if tot_cost > ZERO else None
    total_stocks = len(filtered_merged)
    win_rate = ratio(Decimal(win_cnt), Decimal(total_stocks)) if total_stocks > 0 else None

    cards = {
        "total_pnl": serialize_val(grand_total_pnl),
        "total_pnl_rate": serialize_val(grand_rate),
        "floating_pnl": serialize_val(tot_float),
        "floating_pnl_rate": serialize_val(open_rate),
        "realized_pnl": serialize_val(tot_real),
        "market_value": serialize_val(tot_mv),
        "cost_amount": serialize_val(tot_cost),
        "stock_count": total_stocks,
        "open_stock_count": open_cnt,
        "closed_stock_count": closed_cnt,
        "win_rate": serialize_val(win_rate),
        "win_count": win_cnt,
        "loss_count": loss_cnt,
        "flat_count": flat_cnt,
    }

    structure = {
        "total": total_stocks,
        "open_win": struct_counts["open_win"],
        "open_loss": struct_counts["open_loss"],
        "closed_win": struct_counts["closed_win"],
        "closed_loss": struct_counts["closed_loss"],
    }

    type_breakdown = [
        {
            "name": tg["name"],
            "stock_count": tg["stock_count"],
            "market_value": serialize_val(tg["market_value"]),
            "floating_pnl": serialize_val(tg["floating_pnl"]),
            "realized_pnl": serialize_val(tg["realized_pnl"]),
            "total_pnl": serialize_val(tg["floating_pnl"] + tg["realized_pnl"]),
        }
        for tg in sorted(
            type_groups.values(),
            key=lambda x: (x["stock_count"], abs(x["floating_pnl"] + x["realized_pnl"])),
            reverse=True,
        )
    ]

    top_contributors = _build_top_contributors(filtered_merged, metric=top_metric)

    # 表格级别的快捷筛选（环形图扇区 / Top N 点击标的 / 全部·总盈利·总亏损胶囊）
    table_base = list(filtered_merged)
    tk_key = (ticker_filter or "").strip().upper()
    if tk_key:
        table_base = [r for r in table_base if r["ticker"] == tk_key]
    if structure_filter == "open_win":
        table_base = [r for r in table_base if r["has_open"] and (_to_dec(r["total_pnl"]) or ZERO) >= ZERO]
    elif structure_filter == "open_loss":
        table_base = [r for r in table_base if r["has_open"] and (_to_dec(r["total_pnl"]) or ZERO) < ZERO]
    elif structure_filter == "closed_win":
        table_base = [r for r in table_base if not r["has_open"] and (_to_dec(r["total_pnl"]) or ZERO) >= ZERO]
    elif structure_filter == "closed_loss":
        table_base = [r for r in table_base if not r["has_open"] and (_to_dec(r["total_pnl"]) or ZERO) < ZERO]

    c_all = len(table_base)
    c_prof = sum(1 for r in table_base if (_to_dec(r["total_pnl"]) or ZERO) > ZERO)
    c_loss = sum(1 for r in table_base if (_to_dec(r["total_pnl"]) or ZERO) < ZERO)

    pnl_key = (pnl_filter or "all").strip().lower()
    if pnl_key == "profit":
        table_rows = [r for r in table_base if (_to_dec(r["total_pnl"]) or ZERO) > ZERO]
    elif pnl_key == "loss":
        table_rows = [r for r in table_base if (_to_dec(r["total_pnl"]) or ZERO) < ZERO]
    else:
        table_rows = table_base

    # 表尾合计行
    t_mv = sum((_to_dec(r["market_value"]) or ZERO for r in table_rows), ZERO)
    t_cost = sum((_to_dec(r["cost_amount"]) or ZERO for r in table_rows), ZERO)
    t_float = sum((_to_dec(r["floating_pnl"]) or ZERO for r in table_rows), ZERO)
    t_real = sum((_to_dec(r["realized_pnl"]) or ZERO for r in table_rows), ZERO)
    t_total = t_float + t_real

    return {
        "cards": cards,
        "broker_distribution": broker_distribution,
        "structure": structure,
        "type_breakdown": type_breakdown,
        "person_summary": person_summary,
        "top_contributors": top_contributors,
        "rows": table_rows,
        "table_counts": {"all": c_all, "profit": c_prof, "loss": c_loss},
        "table_summary": {
            "market_value": serialize_val(t_mv),
            "cost_amount": serialize_val(t_cost),
            "floating_pnl": serialize_val(t_float),
            "realized_pnl": serialize_val(t_real),
            "total_pnl": serialize_val(t_total),
        },
        "available_filters": {
            "brokers": available_brokers,
            "industries": available_industries,
        },
    }
