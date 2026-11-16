"""国内资产总览聚合服务：合并基金（支付宝、天天基金）与同花顺 A 股，按严格净值/收盘价计算 KPI、日历、月度矩阵与汇总。"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from portfolio.domestic.holdings_query import DOMESTIC_ASSETS_DIR, holdings
from portfolio.domestic.performance import DOMESTIC_PERF_START, build_domestic_performance


def _d(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    if isinstance(val, Decimal):
        return val if val.is_finite() else None
    try:
        d = Decimal(str(val))
        return d if d.is_finite() else None
    except Exception:
        return None


def _strict_sum(values) -> Decimal | None:
    vals = list(values)
    if not vals:
        return None
    total = Decimal(0)
    for v in vals:
        dv = _d(v)
        if dv is None:
            return None
        total += dv
    return total


def _sum_known(values) -> Decimal | None:
    known = [_d(v) for v in values]
    known = [v for v in known if v is not None]
    if not known:
        return None
    return sum(known, Decimal(0))


def _ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        return num / den


def _closed_pnl_of(row: dict[str, Any]) -> Decimal | None:
    """统一读取已清仓标的的已实现盈亏（兼容基金 last_confirmed_total_pnl 与同花顺 realized_pnl）。"""
    val = row.get("last_confirmed_total_pnl")
    if val is None:
        val = row.get("realized_pnl")
    if val is None and not row.get("return_issue"):
        val = row.get("pnl")
    return _d(val)


def _classify_asset_group(fund_type: str | None) -> str:
    ft = str(fund_type or "").strip()
    if "股票" in ft and ("A股" in ft or ft == "股票" or "同花顺" in ft):
        return "股票"
    return "基金"


def _matches_filter(
    platform: str | None,
    fund_type: str | None,
    selected_platform: str | None,
    selected_type: str | None,
) -> bool:
    if selected_platform and selected_platform != "全部渠道":
        if (platform or "—") != selected_platform:
            return False
    if selected_type and selected_type != "全部类型":
        ft = str(fund_type or "待分类")
        if selected_type == "基金":
            if ft == "股票":
                return False
        elif selected_type == "股票":
            if ft != "股票":
                return False
        elif ft != selected_type:
            return False
    return True


def build_domestic_overview(
    platform: str | None = None,
    asset_type: str | None = None,
    *,
    root=DOMESTIC_ASSETS_DIR,
    start_date: date = DOMESTIC_PERF_START,
    valuation_date: date | None = None,
) -> dict[str, Any]:
    """构建国内资产总览全部数据（18 KPI 卡片、日历、月度/季度矩阵、平台/分类汇总与月度趋势）。"""
    h_data = holdings(root=root, valuation_date=valuation_date)
    all_holdings = [r for r in h_data.get("rows", []) if not r.get("is_historical_baseline")]

    perf = build_domestic_performance(root=root, start_date=start_date, valuation_date=valuation_date)
    all_daily = perf.daily_rows
    all_monthly = perf.monthly_rows

    platforms = sorted({str(r.get("platform") or "—") for r in all_holdings if r.get("platform")})
    raw_types = sorted({str(r.get("fund_type") or "待分类") for r in all_holdings if r.get("fund_type")})

    # 过滤持仓与明细
    f_holdings = [
        r for r in all_holdings
        if _matches_filter(r.get("platform"), r.get("fund_type"), platform, asset_type)
    ]
    f_daily = [
        r for r in all_daily
        if _matches_filter(r.get("platform"), r.get("fund_type"), platform, asset_type)
    ]
    f_monthly = [
        r for r in all_monthly
        if _matches_filter(r.get("platform"), r.get("fund_type"), platform, asset_type)
    ]

    open_rows = [r for r in f_holdings if r.get("current_position_status") == "持有中"]
    closed_rows = [r for r in f_holdings if r.get("current_position_status") == "已清仓"]

    # 仅过滤掉小于 1 元但处于持有中的微额货币残渣，避免拉低胜率与持仓数量统计
    visible_open = [
        r for r in open_rows
        if (_d(r.get("market_value")) is None or _d(r.get("market_value")) >= Decimal("1"))
    ]

    with localcontext() as ctx:
        ctx.prec = 50
        total_assets = _sum_known(r.get("market_value") for r in open_rows)
        total_cost = _sum_known(r.get("cost") for r in open_rows)
        floating_pnl = _sum_known(
            (_d(r.get("market_value")) - _d(r.get("cost")))
            if (_d(r.get("market_value")) is not None and _d(r.get("cost")) is not None)
            else _d(r.get("pnl"))
            for r in open_rows
        )
        closed_pnl = _sum_known(_closed_pnl_of(r) for r in closed_rows)
        # 回本缺口：在持亏损标的亏损绝对值之和
        loss_gaps = []
        for r in visible_open:
            mv = _d(r.get("market_value"))
            cost = _d(r.get("cost"))
            fl = (mv - cost) if (mv is not None and cost is not None) else _d(r.get("pnl"))
            if fl is not None and fl < 0:
                loss_gaps.append(-fl)
        breakeven_gap = sum(loss_gaps, Decimal(0)) if visible_open else None

        # 持仓胜率
        win_count = 0
        loss_count = 0
        for r in visible_open:
            mv = _d(r.get("market_value"))
            cost = _d(r.get("cost"))
            fl = (mv - cost) if (mv is not None and cost is not None) else _d(r.get("pnl"))
            if fl is not None:
                if fl > 0:
                    win_count += 1
                elif fl < 0:
                    loss_count += 1
        win_rate = _ratio(Decimal(win_count), Decimal(len(visible_open))) if visible_open else None

        # 累计净投入与累计现金分红
        total_net_input = _sum_known(
            (_d(r.get("buy_amount")) or Decimal(0))
            + (_d(r.get("transfer_in_amount")) or Decimal(0))
            - (_d(r.get("sell_amount")) or Decimal(0))
            - (_d(r.get("transfer_out_amount")) or Decimal(0))
            for r in f_holdings
        )
        total_cash_dividend = _sum_known(r.get("cash_dividend") or Decimal(0) for r in f_holdings)

        # 按日汇总（保留缺失不补 0）
        day_agg: dict[str, dict[str, Any]] = {}
        by_day_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in f_daily:
            by_day_rows[r["date"]].append(r)

        sorted_days = sorted(by_day_rows.keys())
        prev_day_end_mv: Decimal | None = None
        for d_str in sorted_days:
            d_rows = by_day_rows[d_str]
            d_pnl = _sum_known(r.get("daily_pnl") for r in d_rows)
            d_end_mv = _sum_known(r.get("market_value") for r in d_rows)
            d_net = _sum_known(r.get("net_cash_in") for r in d_rows)
            if prev_day_end_mv is not None:
                d_begin_mv = prev_day_end_mv
            elif d_end_mv is not None and d_pnl is not None:
                d_begin_mv = d_end_mv - d_pnl - (d_net or Decimal(0))
            else:
                d_begin_mv = None
            if d_end_mv is not None:
                prev_day_end_mv = d_end_mv
            d_base = (d_begin_mv + max(d_net or Decimal(0), Decimal(0))) if d_begin_mv is not None else None
            d_rate = _ratio(d_pnl, d_base)
            day_agg[d_str] = {
                "date": d_str,
                "pnl": d_pnl,
                "begin_mv": d_begin_mv,
                "end_mv": d_end_mv,
                "net_flow": d_net,
                "rate": d_rate,
            }

        latest_day = sorted_days[-1] if sorted_days else ""
        prev_day = sorted_days[-2] if len(sorted_days) >= 2 else ""

        latest_pnl = day_agg[latest_day]["pnl"] if latest_day else None
        latest_rate = day_agg[latest_day]["rate"] if latest_day else None
        prev_pnl = day_agg[prev_day]["pnl"] if prev_day else None

        # 本周盈亏（最新交易日所在自然周）
        week_pnl = None
        week_label = "—"
        if latest_day:
            ld = date.fromisoformat(latest_day)
            monday = date.fromordinal(ld.toordinal() - ld.weekday()).isoformat()
            sunday = date.fromordinal(ld.toordinal() + (6 - ld.weekday())).isoformat()
            week_label = f"{monday[5:]} ~ {ld.isoformat()[5:]}"
            w_vals = [day_agg[d]["pnl"] for d in sorted_days if monday <= d <= sunday]
            week_pnl = _sum_known(w_vals)

        # 按月聚合（结合 f_monthly 或 day_agg）
        by_month_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in f_monthly:
            by_month_rows[r["month"]].append(r)

        month_agg: dict[str, dict[str, Any]] = {}
        all_months = sorted(set(by_month_rows.keys()) | {d[:7] for d in sorted_days})
        cum_running = Decimal(0)
        prev_month_end_mv: Decimal | None = None
        for m_str in all_months:
            m_rows = by_month_rows.get(m_str, [])
            if m_rows:
                m_pnl = _sum_known(r.get("month_pnl") for r in m_rows)
                m_end = _sum_known(r.get("end_mv") for r in m_rows)
                m_buy = _sum_known(r.get("buy_amount") for r in m_rows)
                m_sell = _sum_known(r.get("sell_amount") for r in m_rows)
                m_net = _sum_known(r.get("net_cash_in") for r in m_rows)
            else:
                d_in_m = [day_agg[d] for d in sorted_days if d.startswith(m_str)]
                m_pnl = _sum_known(x["pnl"] for x in d_in_m)
                m_end = d_in_m[-1]["end_mv"] if d_in_m else None
                m_buy = None
                m_sell = None
                m_net = _sum_known(x["net_flow"] for x in d_in_m)

            if prev_month_end_mv is not None:
                m_begin = prev_month_end_mv
            elif m_end is not None and m_pnl is not None:
                m_begin = m_end - m_pnl - (m_net or Decimal(0))
            else:
                m_begin = None
            if m_end is not None:
                prev_month_end_mv = m_end

            if m_pnl is not None:
                cum_running += m_pnl

            m_base = (m_begin + max(m_net or Decimal(0), Decimal(0))) if m_begin is not None else None
            if m_base is not None and m_base <= 0 and m_net is not None and m_net > 0:
                m_base = m_net
            m_rate = _ratio(m_pnl, m_base)
            month_agg[m_str] = {
                "month": m_str,
                "begin_mv": m_begin,
                "end_mv": m_end,
                "buy_amount": m_buy,
                "sell_amount": m_sell,
                "net_flow": m_net,
                "pnl": m_pnl,
                "rate": m_rate,
                "cum_pnl": cum_running if m_pnl is not None else None,
            }

        cur_month = latest_day[:7] if latest_day else ""
        prev_month = ""
        cur_year = latest_day[:4] if latest_day else ""
        prev_year = ""
        if cur_month:
            cy, cm = map(int, cur_month.split("-"))
            prev_month = f"{cy if cm > 1 else cy - 1:04d}-{cm - 1 if cm > 1 else 12:02d}"
            prev_year = str(cy - 1)

        cur_month_pnl = month_agg[cur_month]["pnl"] if cur_month in month_agg else None
        cur_month_rate = month_agg[cur_month]["rate"] if cur_month in month_agg else None
        prev_month_pnl = month_agg[prev_month]["pnl"] if prev_month in month_agg else None

        # 按年聚合
        year_agg: dict[str, dict[str, Any]] = {}
        for y_str in sorted({m[:4] for m in all_months}):
            ms = [m for m in all_months if m.startswith(y_str)]
            y_pnl = _sum_known(month_agg[m]["pnl"] for m in ms)
            first_m = month_agg[ms[0]] if ms else {}
            y_begin = first_m.get("begin_mv")
            y_net = _sum_known(month_agg[m]["net_flow"] for m in ms)
            y_base = (y_begin + max(y_net or Decimal(0), Decimal(0))) if y_begin is not None else None
            # 若年初市值为 0（如首年建仓），用最大累计投入近似作分母
            if y_base is not None and y_base <= 0 and y_net is not None and y_net > 0:
                y_base = y_net
            year_agg[y_str] = {
                "year": y_str,
                "pnl": y_pnl,
                "rate": _ratio(y_pnl, y_base),
            }

        cur_year_pnl = year_agg[cur_year]["pnl"] if cur_year in year_agg else None
        cur_year_rate = year_agg[cur_year]["rate"] if cur_year in year_agg else None
        prev_year_pnl = year_agg[prev_year]["pnl"] if prev_year in year_agg else None

        # 全时期总盈亏：优先与最新累计总盈亏口径一致（所有已清仓 + 当前持仓盈亏之和，若无则用每日累计）
        total_all_pnl = _sum_known(
            [
                closed_pnl,
                floating_pnl,
            ]
        )
        # 同时计算全时期累计收益率（以累计净投入或历史总买入为基数）
        total_buy_base = _sum_known(
            (_d(r.get("buy_amount")) or Decimal(0)) + (_d(r.get("transfer_in_amount")) or Decimal(0))
            for r in f_holdings
        )
        all_rate_base = total_net_input if (total_net_input is not None and total_net_input > 0) else total_buy_base
        total_all_rate = _ratio(total_all_pnl, all_rate_base)

        kpis = [
            {
                "name": "当前总资产",
                "value": total_assets,
                "ratio": False,
                "neutral": True,
                "day": latest_day or "—",
                "formula": "当前在持基金与股票最新严格估值市值之和（不含现金/余额宝）",
            },
            {
                "name": "最新日收益率",
                "value": latest_rate,
                "ratio": True,
                "neutral": False,
                "day": latest_day or "—",
                "formula": "最新已确认交易日收益 ÷ (期初市值 + 当日正净投入)",
            },
            {
                "name": "全时期总盈亏",
                "value": total_all_pnl,
                "ratio": False,
                "neutral": False,
                "day": f"2022-01-01 ~ {latest_day}" if latest_day else "—",
                "formula": "已清仓累计盈亏 + 当前在持浮动盈亏（合并基金与同花顺 A 股）",
            },
            {
                "name": "已清仓收益",
                "value": closed_pnl,
                "ratio": False,
                "neutral": False,
                "day": f"共 {len(closed_rows)} 只清仓",
                "formula": "已清仓基金与同花顺股票的已实现盈亏之和",
            },
            {
                "name": "浮动盈亏",
                "value": floating_pnl,
                "ratio": False,
                "neutral": False,
                "day": f"持仓 {len(visible_open)} 只",
                "formula": "当前在持标的市值 − 持仓成本",
            },
            {
                "name": "回本缺口",
                "value": breakeven_gap,
                "ratio": False,
                "neutral": True,
                "day": f"亏损 {loss_count} 只",
                "formula": "当前在持亏损标的亏损绝对值之和（盈利不抵扣）",
            },
            {
                "name": "上一日盈亏",
                "value": prev_pnl,
                "ratio": False,
                "neutral": False,
                "day": prev_day or "—",
                "formula": "最新交易日前一有数据交易日的日盈亏之和",
            },
            {
                "name": "最新日盈亏",
                "value": latest_pnl,
                "ratio": False,
                "neutral": False,
                "day": latest_day or "—",
                "formula": "最新已确认净值/收盘价日的日盈亏之和（严格估值）",
            },
            {
                "name": "上月盈亏",
                "value": prev_month_pnl,
                "ratio": False,
                "neutral": False,
                "day": prev_month or "—",
                "formula": "上一自然月全部交易日盈亏之和",
            },
            {
                "name": "本月盈亏",
                "value": cur_month_pnl,
                "ratio": False,
                "neutral": False,
                "day": cur_month or "—",
                "formula": "本月已有交易日盈亏之和",
            },
            {
                "name": "去年盈亏",
                "value": prev_year_pnl,
                "ratio": False,
                "neutral": False,
                "day": prev_year or "—",
                "formula": "上一自然年全部交易日盈亏之和",
            },
            {
                "name": "本年盈亏",
                "value": cur_year_pnl,
                "ratio": False,
                "neutral": False,
                "day": cur_year or "—",
                "formula": "本年已有交易日盈亏之和",
            },
            {
                "name": "累计净投入",
                "value": total_net_input,
                "ratio": False,
                "neutral": True,
                "day": latest_day or "—",
                "formula": "全部标的（买入+转入）−（卖出+转出）累计净投入",
            },
            {
                "name": "累计现金分红",
                "value": total_cash_dividend,
                "ratio": False,
                "neutral": False,
                "day": latest_day or "—",
                "formula": "全部基金历史确认现金分红之和",
            },
            {
                "name": "本月收益率",
                "value": cur_month_rate,
                "ratio": True,
                "neutral": False,
                "day": cur_month or "—",
                "formula": "本月盈亏 ÷ (月初市值 + 本月正净投入)",
            },
            {
                "name": "本年收益率",
                "value": cur_year_rate,
                "ratio": True,
                "neutral": False,
                "day": cur_year or "—",
                "formula": "本年盈亏 ÷ (年初市值 + 本年正净投入)",
            },
            {
                "name": "全时期收益率",
                "value": total_all_rate,
                "ratio": True,
                "neutral": False,
                "day": "2022-01-01 起",
                "formula": "全时期总盈亏 ÷ 累计投入基数",
            },
            {
                "name": "当前持仓胜率",
                "value": win_rate,
                "ratio": True,
                "neutral": True,
                "day": f"盈利 {win_count}/{len(visible_open)} 只" if visible_open else "—",
                "formula": "当前在持盈利标的数量 ÷ 在持标的总数",
            },
        ]

        # 汇总：按平台 / 按分类
        def _build_group_summary(group_key: str) -> list[dict[str, Any]]:
            grouped_h: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in f_holdings:
                k = str(r.get(group_key) or "待分类")
                grouped_h[k].append(r)

            grouped_d_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in f_daily:
                if cur_year and r["date"].startswith(cur_year):
                    k = str(r.get(group_key) or "待分类")
                    grouped_d_year[k].append(r)

            out = []
            for name, g_rows in grouped_h.items():
                g_open = [x for x in g_rows if x.get("current_position_status") == "持有中"]
                g_closed = [x for x in g_rows if x.get("current_position_status") == "已清仓"]
                g_vis_open = [
                    x for x in g_open
                    if (_d(x.get("market_value")) is None or _d(x.get("market_value")) >= Decimal("1"))
                ]
                g_asset = _sum_known(x.get("market_value") for x in g_open) or Decimal(0)
                g_cost = _sum_known(x.get("cost") for x in g_open)
                g_float = _sum_known(
                    (_d(x.get("market_value")) - _d(x.get("cost")))
                    if (_d(x.get("market_value")) is not None and _d(x.get("cost")) is not None)
                    else _d(x.get("pnl"))
                    for x in g_open
                )
                g_closed_pnl = _sum_known(_closed_pnl_of(x) for x in g_closed)
                g_total_pnl = _sum_known([g_closed_pnl, g_float])
                g_year_pnl = _sum_known(x.get("daily_pnl") for x in grouped_d_year.get(name, []))

                w_cnt = 0
                l_cnt = 0
                g_gap = Decimal(0)
                for x in g_vis_open:
                    mv = _d(x.get("market_value"))
                    c = _d(x.get("cost"))
                    fl = (mv - c) if (mv is not None and c is not None) else _d(x.get("pnl"))
                    if fl is not None:
                        if fl > 0:
                            w_cnt += 1
                        elif fl < 0:
                            l_cnt += 1
                            g_gap += -fl

                out.append({
                    "name": name,
                    "asset": g_asset,
                    "cost": g_cost,
                    "open_count": len(g_vis_open),
                    "weight": _ratio(g_asset, total_assets),
                    "closed_pnl": g_closed_pnl,
                    "floating_pnl": g_float,
                    "year_pnl": g_year_pnl,
                    "total_pnl": g_total_pnl,
                    "win_count": w_cnt,
                    "loss_count": l_cnt,
                    "gap": g_gap if g_vis_open else None,
                })
            out.sort(key=lambda x: (x["asset"] or Decimal(0)), reverse=True)
            return out

        platform_summary = _build_group_summary("platform")
        category_summary = _build_group_summary("fund_type")

        # 季度矩阵
        quarterly_rows = []
        for y_str in sorted({m[:4] for m in all_months}, reverse=True):
            q_pnls = []
            for q in range(1, 5):
                q_ms = [f"{y_str}-{m:02d}" for m in range((q - 1) * 3 + 1, q * 3 + 1)]
                vals = [month_agg[m]["pnl"] for m in q_ms if m in month_agg]
                q_pnls.append(_sum_known(vals))
            y_info = year_agg.get(y_str, {})
            quarterly_rows.append({
                "year": y_str,
                "q1": q_pnls[0],
                "q2": q_pnls[1],
                "q3": q_pnls[2],
                "q4": q_pnls[3],
                "year_pnl": y_info.get("pnl"),
                "year_rate": y_info.get("rate"),
            })

        # 月度趋势（按月降序）
        monthly_trend = [month_agg[m] for m in sorted(all_months, reverse=True)]

    return {
        "platforms": platforms,
        "asset_types": raw_types,
        "kpis": kpis,
        "day_agg": day_agg,
        "month_agg": month_agg,
        "year_agg": year_agg,
        "months": sorted(all_months, reverse=True),
        "years": sorted({m[:4] for m in all_months}, reverse=True),
        "latest_day": latest_day,
        "platform_summary": platform_summary,
        "category_summary": category_summary,
        "quarterly_rows": quarterly_rows,
        "monthly_trend": monthly_trend,
    }
