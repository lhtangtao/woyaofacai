"""国内资产（支付宝 / 天天基金）每日与每月持仓收益计算引擎。"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .holdings_history import daily_values
from .holdings_query import holdings
from .storage import ACCOUNT, CALC_FILE, RAW_FILE, connect
from .tiantian_positions import BUY, SELL, movement, subscription_key

DOMESTIC_PERF_START = date(2022, 1, 1)


@dataclass
class DomesticPerformanceData:
    daily_rows: list[dict[str, Any]]
    monthly_rows: list[dict[str, Any]]
    platforms: list[str]
    fund_types: list[str]
    as_of: str | None


def _collect_daily_cash_flows(rows, cutoff: str, *, root=DOMESTIC_ASSETS_DIR):
    """按 (trade_date, account_id, fund_code) 统计每日买入、卖出(含分红)与操作笔数。"""
    flows: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not (root / RAW_FILE).is_file():
        return flows

    from contextlib import ExitStack
    with ExitStack() as stack:
        raw = stack.enter_context(connect(root / RAW_FILE, readonly=True))
        raw_tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        calc_tables = set()
        calc = None
        if (root / CALC_FILE).is_file():
            calc = stack.enter_context(connect(root / CALC_FILE, readonly=True))
            calc_tables = {r[0] for r in calc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for row in rows:
            if row.get("is_historical_baseline"):
                continue
            key = row["account_id"], row["fund_code"]
            if key[0] == ACCOUNT and calc is not None and "calc_fund_transactions" in calc_tables:
                for t in calc.execute(
                    """SELECT effective_date, transaction_type, confirmed_amount, status
                       FROM calc_fund_transactions
                       WHERE account_id=? AND fund_code=? AND effective_date IS NOT NULL
                       AND effective_date<=?
                       ORDER BY effective_date, raw_transaction_id""",
                    (*key, cutoff),
                ):
                    if t[3] != "正常" or t[2] is None:
                        continue
                    amt = Decimal(t[2])
                    fkey = (t[0], key[0], key[1])
                    entry = flows.setdefault(fkey, {"buy": Decimal(0), "sell": Decimal(0), "count": 0})
                    if t[1] in ("BUY", "TRANSFER_IN"):
                        entry["buy"] += amt
                        entry["count"] += 1
                    elif t[1] in ("SELL", "TRANSFER_OUT", "CASH_DIVIDEND"):
                        entry["sell"] += amt
                        entry["count"] += 1
            elif row.get("platform") == "天天基金" and "raw_fund_transactions" in raw_tables:
                trades = [dict(r) for r in raw.execute(
                    """SELECT * FROM raw_fund_transactions
                       WHERE account_id=? AND fund_code=? AND source_platform='TIANTIAN'
                       AND confirmation_date IS NOT NULL AND confirmation_date<=?
                       ORDER BY confirmation_date, transaction_time, id""",
                    (*key, cutoff),
                )]
                confirmations = Counter(
                    subscription_key(r) for r in trades
                    if r["transaction_type_raw"] == "认购结果"
                    and r["confirmation_status"] == "成功" and r["parse_status"] == "正常"
                )
                for t in trades:
                    delta = movement(t, confirmations)
                    if delta is None:
                        continue
                    kind = t["transaction_type_raw"]
                    amt = Decimal(t["confirmed_amount"] or 0)
                    day = t["confirmation_date"]
                    fkey = (day, key[0], key[1])
                    if kind in BUY | {"超级转换-转入"} and delta > 0:
                        entry = flows.setdefault(fkey, {"buy": Decimal(0), "sell": Decimal(0), "count": 0})
                        entry["buy"] += amt
                        entry["count"] += 1
                    elif kind in SELL | {"超级转换-转出", "现金分红"}:
                        entry = flows.setdefault(fkey, {"buy": Decimal(0), "sell": Decimal(0), "count": 0})
                        entry["sell"] += amt
                        entry["count"] += 1
    return flows


def build_domestic_performance(
    *,
    root=DOMESTIC_ASSETS_DIR,
    start_date: date = DOMESTIC_PERF_START,
    valuation_date: date | None = None,
) -> DomesticPerformanceData:
    """构建国内资产每日与每月持仓收益明细（自 2022-01-01 起）。"""
    data = holdings(root=root, valuation_date=valuation_date)
    rows = [r for r in data.get("rows", []) if not r.get("is_historical_baseline")]
    if not rows:
        return DomesticPerformanceData([], [], [], [], None)

    cutoff = rows[0]["valuation_date"]
    dv = daily_values(rows, root=root)
    cash_flows = _collect_daily_cash_flows(rows, cutoff, root=root)

    meta_by_key = {
        (r["account_id"], r["fund_code"]): {
            "platform": r.get("platform") or "—",
            "fund_code": r["fund_code"],
            "fund_name": r.get("fund_name") or r["fund_code"],
            "fund_type": r.get("fund_type") or "待分类",
        }
        for r in rows
    }

    # 所有有累计盈亏记录的交易日（含 start_date 之前最后一个基线日，用于首日差分）
    all_days = sorted(dv.cum_pnl)
    pre_days = [d for d in all_days if d < start_date]
    target_days = [d for d in all_days if d >= start_date]
    if not target_days:
        return DomesticPerformanceData([], [], [], [], cutoff)

    prev_cum_by_key: dict[tuple[str, str], Decimal] = {}
    if pre_days:
        base_day = pre_days[-1]
        for k, val in dv.cum_pnl.get(base_day, {}).items():
            prev_cum_by_key[k] = val

    # 预先构建按基金的累计净投入基线
    cum_net_by_key: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for (d_str, acc_id, f_code), cf in cash_flows.items():
        if date.fromisoformat(d_str) < start_date:
            cum_net_by_key[(acc_id, f_code)] += cf["buy"] - cf["sell"]

    daily_rows: list[dict[str, Any]] = []
    # 按 (month, key) 聚合月度数据
    monthly_acc: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    prev_month_cum_by_key: dict[tuple[str, str], Decimal] = dict(prev_cum_by_key)
    current_month = None
    month_last_cum: dict[tuple[str, str], Decimal] = {}

    with localcontext() as ctx:
        ctx.prec = 60
        for day_obj in target_days:
            day_str = day_obj.isoformat()
            month_str = day_str[:7]
            if current_month is not None and month_str != current_month:
                prev_month_cum_by_key.update(month_last_cum)
                month_last_cum.clear()
            current_month = month_str

            day_cum_map = dv.cum_pnl.get(day_obj, {})
            day_mv_map = dv.get(day_obj, {})

            # 合并当日有累计盈亏或有资金流水的基金
            active_keys = set(day_cum_map.keys())
            for (d_str, acc_id, f_code) in cash_flows:
                if d_str == day_str and (acc_id, f_code) in meta_by_key:
                    active_keys.add((acc_id, f_code))

            for key in sorted(active_keys):
                meta = meta_by_key.get(key)
                if not meta:
                    continue
                cf = cash_flows.get((day_str, key[0], key[1]), {"buy": Decimal(0), "sell": Decimal(0), "count": 0})
                buy_amt = cf["buy"]
                sell_amt = cf["sell"]
                net_in = buy_amt - sell_amt
                cum_net_by_key[key] += net_in

                cum_pnl = day_cum_map.get(key)
                mv = day_mv_map.get(key)
                if cum_pnl is not None:
                    prev_cum = prev_cum_by_key.get(key)
                    daily_pnl = cum_pnl - prev_cum if prev_cum is not None else cum_pnl
                    prev_cum_by_key[key] = cum_pnl
                    month_last_cum[key] = cum_pnl
                else:
                    daily_pnl = Decimal(0)

                # 只记录有持仓、有盈亏波动或有交易流水的行（过滤掉长期已清仓且无变动的零行）
                has_activity = (
                    (mv is not None and mv != 0)
                    or daily_pnl != 0
                    or buy_amt != 0
                    or sell_amt != 0
                    or cf["count"] > 0
                )
                if has_activity:
                    daily_rows.append({
                        "date": day_str,
                        "month": month_str,
                        "platform": meta["platform"],
                        "fund_code": meta["fund_code"],
                        "fund_name": meta["fund_name"],
                        "fund_type": meta["fund_type"],
                        "market_value": float(mv) if mv is not None else 0.0,
                        "daily_pnl": float(daily_pnl),
                        "cum_pnl": float(cum_pnl) if cum_pnl is not None else 0.0,
                        "buy_amount": float(buy_amt),
                        "sell_amount": float(sell_amt),
                        "net_cash_in": float(net_in),
                        "cum_net_cash_in": float(cum_net_by_key[key]),
                        "trade_count": int(cf["count"]),
                    })

                m_item = monthly_acc.get((month_str, key))
                if m_item is None:
                    m_item = {
                        "month": month_str,
                        "platform": meta["platform"],
                        "fund_code": meta["fund_code"],
                        "fund_name": meta["fund_name"],
                        "fund_type": meta["fund_type"],
                        "end_mv": float(mv) if mv is not None else 0.0,
                        "month_pnl": Decimal(0),
                        "cum_pnl": float(cum_pnl) if cum_pnl is not None else 0.0,
                        "buy_amount": Decimal(0),
                        "sell_amount": Decimal(0),
                        "net_cash_in": Decimal(0),
                        "cum_net_cash_in": float(cum_net_by_key[key]),
                        "trade_count": 0,
                        "start_prev_cum": prev_month_cum_by_key.get(key),
                        "last_cum": cum_pnl,
                    }
                    monthly_acc[(month_str, key)] = m_item
                if mv is not None:
                    m_item["end_mv"] = float(mv)
                if cum_pnl is not None:
                    m_item["cum_pnl"] = float(cum_pnl)
                    m_item["last_cum"] = cum_pnl
                m_item["buy_amount"] += buy_amt
                m_item["sell_amount"] += sell_amt
                m_item["net_cash_in"] += net_in
                m_item["cum_net_cash_in"] = float(cum_net_by_key[key])
                m_item["trade_count"] += int(cf["count"])

    monthly_rows: list[dict[str, Any]] = []
    for (_, _key), item in sorted(monthly_acc.items()):
        last_c = item["last_cum"]
        prev_c = item["start_prev_cum"]
        m_pnl = (last_c - prev_c) if (last_c is not None and prev_c is not None) else (last_c or Decimal(0))
        has_activity = (
            item["end_mv"] != 0
            or m_pnl != 0
            or item["buy_amount"] != 0
            or item["sell_amount"] != 0
            or item["trade_count"] > 0
        )
        if not has_activity:
            continue
        monthly_rows.append({
            "month": item["month"],
            "platform": item["platform"],
            "fund_code": item["fund_code"],
            "fund_name": item["fund_name"],
            "fund_type": item["fund_type"],
            "end_mv": float(item["end_mv"]),
            "month_pnl": float(m_pnl),
            "cum_pnl": float(item["cum_pnl"]),
            "buy_amount": float(item["buy_amount"]),
            "sell_amount": float(item["sell_amount"]),
            "net_cash_in": float(item["net_cash_in"]),
            "cum_net_cash_in": float(item["cum_net_cash_in"]),
            "trade_count": int(item["trade_count"]),
        })

    platforms = sorted({r["platform"] for r in rows if r.get("platform") and r.get("platform") != "—"})
    fund_types = sorted({r["fund_type"] for r in rows if r.get("fund_type")})
    return DomesticPerformanceData(
        daily_rows=daily_rows,
        monthly_rows=monthly_rows,
        platforms=platforms,
        fund_types=fund_types,
        as_of=target_days[-1].isoformat(),
    )
