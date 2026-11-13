"""美股「持仓管理 → 持有中」独立只读统计与聚合查询服务。

设计原则与严格边界：
1. 仅针对美股「持有中」持仓进行只读聚合与元数据补充，不修改任何美股核心计算逻辑（replay / holdings / performance）。
2. 所有核心指标（持仓总市值、持仓总成本、浮动盈亏、持仓收益率、持仓股票数量、前五大持仓集中度）严格沿用现有美股计算结果。
3. 股票名称、股票类型、行业均读取自真实数据库（instruments / raw_records / verified_earnings / earnings_calendar_cache），绝不猜测或虚构数据。
4. 持仓市值趋势严格使用数据库「每日持仓收益」中的真实历史日末市值，绝不用当前持股数反推历史市值；若某时间段存在不完整快照则显式标记。
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

from portfolio.domain.analytics import ratio
from portfolio.paths import DATABASE_PATH, quote_cache_path
from portfolio.services.earnings_links import INDEX_FUNDS, SINGLE_STOCK_LINKS
from portfolio.services.holdings_management_service import (
    HoldingsManagementService,
    channel_label,
    serialize_val,
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


def _shift_months(ref: date, months: int) -> date:
    """安全按自然月偏移日期。"""
    total_months = ref.year * 12 + (ref.month - 1) + months
    year, month_idx = divmod(total_months, 12)
    month = month_idx + 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(ref.day, max_day))


def resolve_mv_trend_bounds(
    window_key: str | None,
    *,
    as_of: date | None = None,
) -> tuple[str | None, str | None]:
    """将市值趋势时间窗口（1m / 3m / 6m / ytd / all）解析为 [start_date, end_date] 闭区间 ISO 字符串。"""
    key = (window_key or "ytd").strip().lower()
    ref = as_of or date.today()
    if key in ("", "all"):
        return None, ref.isoformat()
    if key == "1m":
        return _shift_months(ref, -1).isoformat(), ref.isoformat()
    if key == "3m":
        return _shift_months(ref, -3).isoformat(), ref.isoformat()
    if key == "6m":
        return _shift_months(ref, -6).isoformat(), ref.isoformat()
    if key == "ytd":
        return f"{ref.year}-01-01", ref.isoformat()
    return None, ref.isoformat()


def _load_verified_and_cached_names(quote_db_path: str | Path | None = None) -> dict[str, str]:
    """从仓库内 verified_earnings.json 与本地行情缓存 earnings_calendar_cache 提取已核实的真实公司名称。"""
    names: dict[str, str] = {}

    # 1. 本地财报日历缓存中的官方公司名称
    try:
        qc = Path(quote_db_path).expanduser().resolve() if quote_db_path else quote_cache_path().expanduser().resolve()
        if qc.exists():
            conn = sqlite3.connect(f"{qc.as_uri()}?mode=ro", uri=True, timeout=5.0)
            conn.execute("PRAGMA busy_timeout = 5000")
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='earnings_calendar_cache'")
            if cur.fetchone():
                cur.execute("SELECT payload_json FROM earnings_calendar_cache")
                for (pj,) in cur.fetchall():
                    if not pj:
                        continue
                    data = json.loads(pj)
                    items = data if isinstance(data, list) else data.get("events", [])
                    for item in items:
                        if isinstance(item, dict):
                            sym = str(item.get("symbol") or "").upper().replace(".US", "").strip()
                            nm = str(item.get("name") or "").strip()
                            if sym and nm and sym not in names:
                                names[sym] = nm
            conn.close()
    except Exception as e:
        logger.debug(f"[持有中元数据] 读取本地财报缓存名称跳过: {e}")

    # 2. 仓库已核实财报资源文件（优先覆盖中文名称）
    try:
        ve_path = Path(__file__).resolve().parent.parent / "resources" / "verified_earnings.json"
        if ve_path.exists():
            entries = json.loads(ve_path.read_text(encoding="utf-8"))
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and not entry.get("is_macro"):
                        sym = str(entry.get("symbol") or "").upper().replace(".US", "").strip()
                        nm = str(entry.get("name") or "").strip()
                        if sym and nm:
                            names[sym] = nm
    except Exception as e:
        logger.debug(f"[持有中元数据] 读取 verified_earnings.json 跳过: {e}")

    return names


def load_instrument_metadata_map(
    db_path: str | Path | None = None,
    quote_db_path: str | Path | None = None,
) -> dict[str, dict[str, str]]:
    """只读查询数据库 instruments 与关联 raw_records，提取真实的股票名称、股票类型、行业及币种。

    严禁猜测或虚构行业：
    - 若数据库 instruments 表或原始导入记录中存在显式的 industry / sector / 行业 字段，优先直接采用；
    - 否则采用数据库中已有的真实标的分类（category：正股 / 杠杆 / 大盘 / 待确认，缺失时标记为“未分类”）。
    """
    target_path = Path(db_path).expanduser().resolve() if db_path else DATABASE_PATH.expanduser().resolve()
    known_names = _load_verified_and_cached_names(quote_db_path=quote_db_path)
    meta_by_symbol: dict[str, dict[str, str]] = {}

    if not target_path.exists():
        return meta_by_symbol

    try:
        conn = sqlite3.connect(f"{target_path.as_uri()}?mode=ro", uri=True, timeout=10.0)
        conn.execute("PRAGMA busy_timeout = 10000")
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(instruments)")
        inst_cols = {str(r[1]).lower() for r in cur.fetchall()}
        has_industry_col = "industry" in inst_cols
        has_sector_col = "sector" in inst_cols
        has_stock_type_col = "stock_type" in inst_cols

        select_parts = [
            "i.id",
            "i.symbol",
            "i.name",
            "i.currency",
            "i.asset_type",
            "i.category",
            "i.industry" if has_industry_col else "NULL AS industry",
            "i.sector" if has_sector_col else "NULL AS sector",
            "i.stock_type" if has_stock_type_col else "NULL AS stock_type",
            "r.payload_json",
        ]
        cur.execute(
            f"""
            SELECT {", ".join(select_parts)}
            FROM instruments i
            LEFT JOIN raw_records r ON i.source_record_id = r.id
            WHERE i.market = 'US' OR i.market IS NULL
            """
        )
        rows = cur.fetchall()
        conn.close()

        for (
            inst_id,
            raw_sym,
            inst_name,
            currency,
            asset_type,
            category,
            db_industry,
            db_sector,
            db_stock_type,
            payload_json,
        ) in rows:
            sym = str(raw_sym or "").strip().upper()
            if not sym:
                continue

            raw_fields: dict[str, Any] = {}
            if payload_json:
                try:
                    parsed = json.loads(payload_json)
                    if isinstance(parsed, dict) and isinstance(parsed.get("fields"), dict):
                        raw_fields = parsed["fields"]
                except Exception:
                    raw_fields = {}

            cat_str = str(category or raw_fields.get("分类") or "").strip()
            desc_str = str(raw_fields.get("说明") or "").strip()
            atype_str = str(asset_type or "").strip().upper()

            # 1. 股票名称（真实名称优先，其次为官方关联说明，无则显示 "—"）
            name_val = str(
                inst_name
                or raw_fields.get("股票名称")
                or raw_fields.get("名称")
                or raw_fields.get("name")
                or known_names.get(sym)
                or ""
            ).strip()
            if not name_val:
                us_sym = f"{sym}.US"
                if us_sym in SINGLE_STOCK_LINKS:
                    underlying = SINGLE_STOCK_LINKS[us_sym][0].replace(".US", "")
                    u_name = known_names.get(underlying)
                    name_val = f"{u_name or underlying} 关联ETF" if u_name else (desc_str or f"{underlying} 关联ETF")
                elif us_sym in INDEX_FUNDS or desc_str:
                    name_val = desc_str if desc_str else "指数 ETF"
                else:
                    name_val = "—"

            # 2. 股票类型（基于数据库真实 asset_type / 说明 / 分类）
            explicit_type = str(
                db_stock_type
                or raw_fields.get("股票类型")
                or raw_fields.get("stock_type")
                or ""
            ).strip()
            if explicit_type:
                stock_type_val = explicit_type
            elif atype_str == "ETF" or "ETF" in desc_str.upper() or cat_str in ("杠杆", "大盘"):
                stock_type_val = "ETF"
            elif atype_str in ("STOCK", "EQUITY") or cat_str == "正股":
                stock_type_val = "正股"
            elif cat_str:
                stock_type_val = cat_str
            else:
                stock_type_val = "其他"

            # 3. 行业（严格使用数据库已有真实字段，无单独行业字段时使用真实分类 category，绝不猜测）
            explicit_ind = str(
                db_industry
                or db_sector
                or raw_fields.get("行业")
                or raw_fields.get("所属行业")
                or raw_fields.get("industry")
                or raw_fields.get("sector")
                or ""
            ).strip()
            if explicit_ind:
                industry_val = explicit_ind
            elif cat_str:
                industry_val = cat_str
            else:
                industry_val = "未分类"

            curr_val = str(currency or raw_fields.get("币种") or "USD").strip().upper() or "USD"

            info = {
                "instrument_id": str(inst_id),
                "symbol": sym,
                "stock_name": name_val,
                "stock_type": stock_type_val,
                "industry": industry_val,
                "category": cat_str or "未分类",
                "currency": curr_val,
            }
            meta_by_symbol[sym] = info
            meta_by_symbol[f"ID:{inst_id}"] = info
    except Exception as e:
        logger.exception(f"[持有中元数据] 读取数据库标的元数据异常: {e}")

    return meta_by_symbol


def enrich_open_holdings_rows(
    raw_rows: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    quote_db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """为现有持有中明细行补充真实的股票名称、股票类型、行业与币种字段，不改变任何原有数值口径。"""
    meta_map = load_instrument_metadata_map(db_path=db_path, quote_db_path=quote_db_path)
    enriched: list[dict[str, Any]] = []
    for r in raw_rows:
        item = dict(r)
        tk = str(item.get("ticker") or "").strip().upper()
        inst_id = item.get("instrument_id")
        meta = meta_map.get(f"ID:{inst_id}") or meta_map.get(tk) or {}

        cat = str(item.get("category") or meta.get("category") or "").strip()
        stock_name = str(item.get("stock_name") or meta.get("stock_name") or "—").strip() or "—"
        stock_type = str(item.get("stock_type") or meta.get("stock_type") or "").strip()
        if not stock_type:
            if cat in ("杠杆", "大盘"):
                stock_type = "ETF"
            elif cat == "正股":
                stock_type = "正股"
            else:
                stock_type = cat or "其他"
        industry = str(item.get("industry") or meta.get("industry") or cat or "未分类").strip() or "未分类"
        currency = str(item.get("currency") or meta.get("currency") or "USD").strip().upper() or "USD"

        item["stock_name"] = stock_name
        item["stock_type"] = stock_type
        item["industry"] = industry
        item["category"] = cat or industry
        item["currency"] = currency
        item["channel"] = channel_label(str(item.get("channel") or "").split(" · #")[0].strip())

        # 计算离回本还需涨幅（breakeven_needed_rate）：
        # 当当前价 < 持仓均价（即处于浮亏状态）时，离回本需涨 (持仓均价 - 当前价) / 当前价；
        # 若当前价 >= 持仓均价（已回本/盈利），则无需再涨，记为 0。
        cost_p = _to_dec(item.get("cost_price"))
        curr_p = _to_dec(item.get("current_price"))
        if cost_p is not None and curr_p is not None and curr_p > ZERO:
            if curr_p < cost_p:
                be_rate = ratio(cost_p - curr_p, curr_p)
            else:
                be_rate = ZERO
            item["breakeven_needed_rate"] = serialize_val(be_rate)
        else:
            item["breakeven_needed_rate"] = None

        enriched.append(item)
    return enriched


def filter_open_holdings_rows(
    rows: list[dict[str, Any]],
    *,
    broker: str | None = None,
    stock_type: str | None = None,
    industry: str | None = None,
    currency: str | None = None,
    search: str | None = None,
    pnl_filter: str | None = None,
) -> list[dict[str, Any]]:
    """组合业务筛选条件过滤持有中记录，并按当前筛选范围重算持仓占比（weight）。"""
    broker_norm = channel_label(broker).casefold() if (broker and broker.strip()) else ""
    type_norm = (stock_type or "").strip().casefold()
    ind_norm = (industry or "").strip().casefold()
    curr_norm = (currency or "").strip().upper()
    search_norm = (search or "").strip().upper()
    pnl_norm = (pnl_filter or "all").strip().lower()

    scoped: list[dict[str, Any]] = []
    for r in rows:
        ch = channel_label(str(r.get("channel") or "")).casefold()
        if broker_norm and broker_norm not in ("全部", "全部渠道", "all") and ch != broker_norm:
            continue

        st = str(r.get("stock_type") or "").strip().casefold()
        cat = str(r.get("category") or "").strip().casefold()
        if type_norm and type_norm not in ("全部", "全部类型", "all") and type_norm not in (st, cat):
            continue

        ind = str(r.get("industry") or "").strip().casefold()
        if ind_norm and ind_norm not in ("全部", "全部行业", "all") and ind != ind_norm:
            continue

        curr = str(r.get("currency") or "USD").strip().upper()
        if curr_norm and curr_norm not in ("全部", "全部币种", "ALL") and curr != curr_norm:
            continue

        if search_norm:
            haystack = " ".join(
                str(r.get(k) or "")
                for k in ("ticker", "stock_name", "person_name", "channel", "stock_type", "industry")
            ).upper()
            if search_norm not in haystack:
                continue

        scoped.append(dict(r))

    # 重算当前业务筛选范围内的持仓占比 (weight = 市值 / 筛选范围总市值)
    total_mv = sum(
        (_to_dec(it.get("market_value")) or ZERO for it in scoped if (_to_dec(it.get("market_value")) or ZERO) > ZERO),
        ZERO,
    )
    for it in scoped:
        mv_dec = _to_dec(it.get("market_value")) or ZERO
        w_dec = (mv_dec / total_mv) if (total_mv > ZERO and mv_dec > ZERO) else ZERO
        it["weight"] = serialize_val(w_dec)

    # 盈利 / 亏损状态切换仅改变列表展示范围
    if pnl_norm in ("profit", "win"):
        return [it for it in scoped if (_to_dec(it.get("total_pnl")) or ZERO) > ZERO]
    if pnl_norm == "loss":
        return [it for it in scoped if (_to_dec(it.get("total_pnl")) or ZERO) < ZERO]
    return scoped


def compute_open_mv_trend(
    analytics,
    *,
    meta_map: dict[str, dict[str, str]] | None = None,
    person_id: int | tuple[int, ...] | None = None,
    broker: str | None = None,
    stock_type: str | None = None,
    industry: str | None = None,
    currency: str | None = None,
    search: str | None = None,
    trend_window: str = "ytd",
) -> dict[str, Any]:
    """从「每日持仓收益」报表中提取真实历史日末市值趋势。

    严格遵守：
    1. 绝不使用当前持仓数量反推历史市值。
    2. 若某日存在持仓数量 > 0 但缺失日末市值或收盘价快照，明确记录并标记数据不完整，绝不补造。
    """
    if not analytics or not hasattr(analytics, "reports"):
        return {
            "window": trend_window or "ytd",
            "points": [],
            "has_incomplete_data": False,
            "incomplete_days_count": 0,
            "incomplete_note": "",
        }

    daily_rep = analytics.reports.get("每日持仓收益")
    if not daily_rep or not getattr(daily_rep, "rows", None):
        return {
            "window": trend_window or "ytd",
            "points": [],
            "has_incomplete_data": False,
            "incomplete_days_count": 0,
            "incomplete_note": "",
        }

    meta_lookup = meta_map or {}
    as_of = getattr(getattr(analytics, "scope", None), "as_of", None) or date.today()
    start_d, end_d = resolve_mv_trend_bounds(trend_window, as_of=as_of)

    broker_norm = channel_label(broker).casefold() if (broker and broker.strip()) else ""
    type_norm = (stock_type or "").strip().casefold()
    ind_norm = (industry or "").strip().casefold()
    curr_norm = (currency or "").strip().upper()
    search_norm = (search or "").strip().upper()

    # 当传入了特定 person_id 且当前 analytics.scope.person_ids 未限定为该人员时，
    # 利用「交易回放」按日追踪该人员在各 (account_id, instrument_id) 的真实持仓股数，再乘以当日真实收盘价
    target_pids: tuple[int, ...] | None = None
    if isinstance(person_id, int):
        target_pids = (person_id,)
    elif isinstance(person_id, (tuple, list)) and person_id:
        target_pids = tuple(int(x) for x in person_id)

    scope_pids = getattr(getattr(analytics, "scope", None), "person_ids", None)
    need_person_replay_filter = bool(target_pids is not None and scope_pids != target_pids)

    person_trades_by_day: dict[str, list[tuple[Any, Any, Decimal]]] = defaultdict(list)
    if need_person_replay_filter:
        replay = analytics.reports.get("交易回放")
        if replay and getattr(replay, "rows", None):
            for r in reversed(replay.rows):
                rmeta = getattr(r, "metadata", {}) or {}
                if rmeta.get("person_id") in target_pids:
                    day_str = str(rmeta.get("business_date") or (r.values[0] if r.values else ""))[:10]
                    post_q = _to_dec(r.values[9] if len(r.values) > 9 else 0) or ZERO
                    person_trades_by_day[day_str].append(
                        (rmeta.get("account_id"), rmeta.get("instrument_id"), post_q)
                    )

    daily_rows_by_date: dict[str, list[Any]] = defaultdict(list)
    for r in daily_rep.rows:
        vals = getattr(r, "values", ())
        if len(vals) < 12:
            continue
        day_str = str(vals[0] or "")[:10]
        if not day_str:
            continue
        daily_rows_by_date[day_str].append(r)

    all_dates = sorted(set(daily_rows_by_date.keys()) | set(person_trades_by_day.keys()))
    running_person_qty: dict[tuple[Any, Any], Decimal] = defaultdict(lambda: ZERO)

    daily_mv: dict[str, Decimal] = {}
    daily_pnl_map: dict[str, Decimal | None] = {}
    daily_cum_pnl_map: dict[str, Decimal | None] = {}
    incomplete_days: set[str] = set()
    running_cum_pnl = ZERO
    has_any_valid_pnl = False

    for d_str in all_dates:
        if need_person_replay_filter and d_str in person_trades_by_day:
            for acc_id, inst_id, pq in person_trades_by_day[d_str]:
                running_person_qty[(acc_id, inst_id)] = pq

        if d_str not in daily_rows_by_date:
            continue

        day_sum = ZERO
        day_valid_mv_count = 0
        day_missing_mv_count = 0
        day_pnl_sum = ZERO
        day_pnl_valid_count = 0
        day_pnl_missing_count = 0

        for r in daily_rows_by_date[d_str]:
            vals = r.values
            rmeta = getattr(r, "metadata", {}) or {}
            raw_ch = str(vals[1] or "")
            ch_cn = channel_label(raw_ch)
            cat_str = str(vals[2] or "").strip()
            tk = str(vals[3] or "").strip().upper()
            inst_id = rmeta.get("instrument_id")
            acc_id = rmeta.get("account_id")

            m_info = meta_lookup.get(f"ID:{inst_id}") or meta_lookup.get(tk) or {}
            st_val = str(m_info.get("stock_type") or ("ETF" if cat_str in ("杠杆", "大盘") else "正股")).strip()
            ind_val = str(m_info.get("industry") or cat_str or "未分类").strip()
            curr_val = str(rmeta.get("currency") or m_info.get("currency") or "USD").strip().upper()
            sname_val = str(m_info.get("stock_name") or "").strip()

            if broker_norm and broker_norm not in ("全部", "全部渠道", "all") and ch_cn.casefold() != broker_norm:
                continue
            if type_norm and type_norm not in ("全部", "全部类型", "all") and type_norm not in (st_val.casefold(), cat_str.casefold()):
                continue
            if ind_norm and ind_norm not in ("全部", "全部行业", "all") and ind_val.casefold() != ind_norm:
                continue
            if curr_norm and curr_norm not in ("全部", "全部币种", "ALL") and curr_val != curr_norm:
                continue
            if search_norm:
                haystack = f"{tk} {sname_val} {ch_cn} {st_val} {ind_val}".upper()
                if search_norm not in haystack:
                    continue

            end_qty = _to_dec(vals[8]) or ZERO
            close_p = _to_dec(vals[10])
            row_mv = _to_dec(vals[11])
            row_pnl = _to_dec(vals[19]) if len(vals) > 19 else None

            if need_person_replay_filter:
                p_qty = running_person_qty.get((acc_id, inst_id), ZERO)
                if p_qty <= ZERO:
                    continue
                if close_p is not None and close_p > ZERO:
                    day_sum += p_qty * close_p
                    day_valid_mv_count += 1
                elif end_qty > ZERO and row_mv is not None:
                    day_sum += row_mv * (p_qty / end_qty)
                    day_valid_mv_count += 1
                else:
                    day_missing_mv_count += 1
                    if (not start_d or d_str >= start_d) and (not end_d or d_str <= end_d):
                        incomplete_days.add(d_str)
                # 独立人员回放模式不混用全员每日收益
                day_pnl_missing_count += 1
            else:
                # 累计当日收益（包含当日已清仓但有当日收益的记录，先于市值跳过判断）
                if row_pnl is not None:
                    day_pnl_sum += row_pnl
                    day_pnl_valid_count += 1
                elif end_qty > ZERO or (len(vals) > 16 and ((_to_dec(vals[16]) or ZERO) > ZERO or (_to_dec(vals[17]) or ZERO) > ZERO)):
                    day_pnl_missing_count += 1

                if end_qty <= ZERO and (row_mv is None or row_mv <= ZERO):
                    continue
                if end_qty > ZERO and row_mv is None:
                    day_missing_mv_count += 1
                    if (not start_d or d_str >= start_d) and (not end_d or d_str <= end_d):
                        incomplete_days.add(d_str)
                elif row_mv is not None:
                    day_sum += row_mv
                    day_valid_mv_count += 1

        # 真实累计盈亏必须基于完整历史数据逐日累计，再按时间范围截取显示区间
        day_pnl_val: Decimal | None = None
        day_cum_val: Decimal | None = None
        if not need_person_replay_filter and day_pnl_valid_count > 0 and day_pnl_missing_count == 0:
            day_pnl_val = day_pnl_sum
            running_cum_pnl += day_pnl_sum
            has_any_valid_pnl = True
            day_cum_val = running_cum_pnl
        elif not need_person_replay_filter and day_pnl_missing_count > 0:
            if (not start_d or d_str >= start_d) and (not end_d or d_str <= end_d):
                incomplete_days.add(d_str)

        if start_d and d_str < start_d:
            continue
        if end_d and d_str > end_d:
            continue

        # 仅当当日存在真实有效的历史市值快照时才加入折线点，绝不将缺失收盘价的交易日补造为 0
        if day_valid_mv_count > 0:
            daily_mv[d_str] = day_sum
            daily_pnl_map[d_str] = day_pnl_val
            daily_cum_pnl_map[d_str] = day_cum_val

    points = [
        {
            "date": d,
            "market_value": serialize_val(daily_mv[d]),
            "daily_pnl": serialize_val(daily_pnl_map.get(d)),
            "cumulative_pnl": serialize_val(daily_cum_pnl_map.get(d)),
            "incomplete": d in incomplete_days,
        }
        for d in sorted(daily_mv.keys())
    ]

    has_incomplete = len(incomplete_days) > 0
    missing_dates_sorted = sorted(incomplete_days)
    incomplete_note = (
        f"历史快照提示：{', '.join(missing_dates_sorted[:3])}{' 等 ' if len(missing_dates_sorted) > 3 else ' '}共 {len(missing_dates_sorted)} 个交易日缺少完整收盘价快照，未补造数据"
        if has_incomplete
        else ""
    )
    return {
        "window": trend_window or "ytd",
        "start_date": start_d,
        "end_date": end_d,
        "points": points,
        "has_incomplete_data": has_incomplete,
        "incomplete_days_count": len(incomplete_days),
        "missing_dates": missing_dates_sorted,
        "incomplete_note": incomplete_note,
    }


def compute_open_ticker_top10(scoped_rows: list[dict[str, Any]], *, top_n: int = 5) -> dict[str, Any]:
    """按 Ticker 汇总当前有效筛选范围内持有中股票的浮动盈亏，分别取盈利与亏损 Top N。"""
    by_ticker: dict[str, dict[str, Any]] = {}
    total_profit = ZERO
    total_loss = ZERO

    for r in scoped_rows:
        tk = str(r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        pnl = _to_dec(r.get("total_pnl")) or ZERO
        mv = _to_dec(r.get("market_value")) or ZERO
        cost = _to_dec(r.get("cost_amount"))
        if cost is None:
            cost = mv - pnl
        qty = _to_dec(r.get("quantity")) or ZERO

        if pnl > ZERO:
            total_profit += pnl
        elif pnl < ZERO:
            total_loss += pnl

        raw_ch = str(r.get("channel") or r.get("broker") or "").split(" · #")[0].strip()
        ch_name = channel_label(raw_ch) if raw_ch else ""

        entry = by_ticker.get(tk)
        if entry is None:
            entry = {
                "ticker": tk,
                "net_pnl": ZERO,
                "market_value": ZERO,
                "cost_amount": ZERO,
                "quantity": ZERO,
                "record_count": 0,
                "channels": set(),
            }
            by_ticker[tk] = entry
        entry["net_pnl"] += pnl
        entry["market_value"] += mv
        entry["cost_amount"] += cost
        entry["quantity"] += qty
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
            "floating_pnl": serialize_val(item["net_pnl"]),
            "market_value": serialize_val(item["market_value"]),
            "cost_amount": serialize_val(item["cost_amount"]),
            "quantity": serialize_val(item["quantity"]),
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
        "total_floating_profit": serialize_val(total_profit),
        "total_floating_loss": serialize_val(total_loss),
        "scope_profit_total": serialize_val(total_profit),
        "scope_loss_total": serialize_val(total_loss),
        "scope_net_pnl": serialize_val(total_profit + total_loss),
    }


def build_open_holdings_dashboard(
    analytics,
    raw_open_rows: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    quote_db_path: str | Path | None = None,
    person_id: int | tuple[int, ...] | None = None,
    broker: str | None = None,
    stock_type: str | None = None,
    industry: str | None = None,
    currency: str | None = None,
    search: str | None = None,
    pnl_filter: str = "all",
    trend_window: str = "ytd",
    top_n: int = 5,
) -> dict[str, Any]:
    """构建美股「持有中」页面的全部统计卡片、券商分布、行业分布、市值趋势及明细列表数据。"""
    meta_map = load_instrument_metadata_map(db_path=db_path, quote_db_path=quote_db_path)
    enriched_all = enrich_open_holdings_rows(raw_open_rows, db_path=db_path, quote_db_path=quote_db_path)

    # 1. 提取当前全局人员范围内可用的筛选项（基于未过滤业务条件的持有中记录）
    available_brokers = sorted({r["channel"] for r in enriched_all if r.get("channel")})
    available_stock_types = sorted({r["stock_type"] for r in enriched_all if r.get("stock_type")})
    available_industries = sorted({r["industry"] for r in enriched_all if r.get("industry")})
    available_currencies = sorted({r["currency"] for r in enriched_all if r.get("currency")}) or ["USD"]

    # 2. 按业务筛选条件（券商、股票类型、行业、币种、搜索词）过滤，作为卡片与图表的统计基准
    scoped_rows = filter_open_holdings_rows(
        enriched_all,
        broker=broker,
        stock_type=stock_type,
        industry=industry,
        currency=currency,
        search=search,
        pnl_filter="all",
    )

    # 3. 顶部 6 张核心统计卡片（严格复用现有美股计算口径）
    total_mv = ZERO
    total_cost = ZERO
    total_pnl = ZERO
    ticker_mv_map: dict[str, Decimal] = defaultdict(lambda: ZERO)

    broker_groups: dict[str, dict[str, Any]] = {}
    industry_groups: dict[str, dict[str, Any]] = {}
    person_groups: dict[Any, dict[str, Any]] = {}

    profit_count = 0
    loss_count = 0
    flat_count = 0

    for r in scoped_rows:
        mv = _to_dec(r.get("market_value")) or ZERO
        cost = _to_dec(r.get("cost_amount"))
        pnl = _to_dec(r.get("total_pnl")) or ZERO
        if cost is None:
            cost = mv - pnl

        total_mv += mv
        total_cost += cost
        total_pnl += pnl

        tk = str(r.get("ticker") or "").strip().upper()
        if tk and mv > ZERO:
            ticker_mv_map[tk] += mv

        if pnl > ZERO:
            profit_count += 1
        elif pnl < ZERO:
            loss_count += 1
        else:
            flat_count += 1

        # 按人员分组（用于全局人员选择「全部」或「他人」时展示每个人的持仓情况）
        p_id = r.get("person_id")
        p_name = str(r.get("person_name") or "未知人员")
        p_key = p_id if p_id is not None else p_name
        pg = person_groups.get(p_key)
        if pg is None:
            pg = {
                "person_id": p_id,
                "person_name": p_name,
                "market_value": ZERO,
                "cost_amount": ZERO,
                "floating_pnl": ZERO,
                "stock_count": 0,
                "profit_count": 0,
                "loss_count": 0,
            }
            person_groups[p_key] = pg
        pg["market_value"] += mv
        pg["cost_amount"] += cost
        pg["floating_pnl"] += pnl
        pg["stock_count"] += 1
        if pnl > ZERO:
            pg["profit_count"] += 1
        elif pnl < ZERO:
            pg["loss_count"] += 1

        # 按券商分组
        ch = str(r.get("channel") or "未知渠道")
        bg = broker_groups.get(ch)
        if bg is None:
            bg = {"channel": ch, "market_value": ZERO, "cost_amount": ZERO, "floating_pnl": ZERO, "stock_count": 0, "tickers": set()}
            broker_groups[ch] = bg
        bg["market_value"] += mv
        bg["cost_amount"] += cost
        bg["floating_pnl"] += pnl
        bg["stock_count"] += 1
        if tk:
            bg["tickers"].add(tk)

        # 按行业分组
        ind = str(r.get("industry") or "未分类")
        ig = industry_groups.get(ind)
        if ig is None:
            ig = {"industry": ind, "market_value": ZERO, "stock_count": 0, "tickers": set()}
            industry_groups[ind] = ig
        ig["market_value"] += mv
        ig["stock_count"] += 1
        if tk:
            ig["tickers"].add(tk)

    return_rate = ratio(total_pnl, total_cost) if total_cost > ZERO else None
    stock_count = len(scoped_rows)
    unique_ticker_count = len(ticker_mv_map)

    # 前五大持仓集中度（按个股市值前 5 名之和 ÷ 持仓总市值）
    sorted_holding_mvs = sorted(
        ((_to_dec(r.get("market_value")) or ZERO) for r in scoped_rows if (_to_dec(r.get("market_value")) or ZERO) > ZERO),
        reverse=True,
    )
    top5_mv = sum(sorted_holding_mvs[:5], ZERO)
    top5_concentration = ratio(top5_mv, total_mv) if total_mv > ZERO else ZERO

    cards = {
        "total_market_value": serialize_val(total_mv),
        "total_cost_amount": serialize_val(total_cost),
        "floating_pnl": serialize_val(total_pnl),
        "return_rate": serialize_val(return_rate),
        "stock_count": stock_count,
        "unique_ticker_count": unique_ticker_count,
        "top5_concentration": serialize_val(top5_concentration),
    }

    # 4. 券商持仓分布环形图数据
    broker_distribution = []
    for ch, bg in sorted(broker_groups.items(), key=lambda x: (-x[1]["market_value"], x[0])):
        mv = bg["market_value"]
        c_val = bg.get("cost_amount", ZERO)
        r_val = ratio(mv, total_mv) if total_mv > ZERO else ZERO
        broker_distribution.append({
            "channel": ch,
            "name": ch,
            "cost_amount": serialize_val(c_val),
            "floating_pnl": serialize_val(bg.get("floating_pnl", ZERO)),
            "market_value": serialize_val(mv),
            "ratio": serialize_val(r_val),
            "percent": round(float(r_val or ZERO) * 100.0, 2),
            "stock_count": bg["stock_count"],
            "unique_ticker_count": len(bg["tickers"]),
        })

    # 5. 行业持仓分布环形图数据
    industry_distribution = []
    for ind, ig in sorted(industry_groups.items(), key=lambda x: (-x[1]["market_value"], x[0])):
        mv = ig["market_value"]
        r_val = ratio(mv, total_mv) if total_mv > ZERO else ZERO
        industry_distribution.append({
            "industry": ind,
            "name": ind,
            "market_value": serialize_val(mv),
            "ratio": serialize_val(r_val),
            "percent": round(float(r_val or ZERO) * 100.0, 2),
            "stock_count": ig["stock_count"],
            "unique_ticker_count": len(ig["tickers"]),
        })

    # 5.5 人员持仓汇总数据（按持仓市值倒序排列）
    person_summary = []
    for _, pg in sorted(
        person_groups.items(),
        key=lambda x: (-x[1]["market_value"], str(x[1]["person_name"])),
    ):
        p_mv = pg["market_value"]
        p_cost = pg["cost_amount"]
        p_pnl = pg["floating_pnl"]
        p_rate = ratio(p_pnl, p_cost) if p_cost > ZERO else None
        p_weight = ratio(p_mv, total_mv) if total_mv > ZERO else ZERO
        person_summary.append({
            "person_id": pg["person_id"],
            "person_name": pg["person_name"],
            "name": pg["person_name"],
            "market_value": serialize_val(p_mv),
            "cost_amount": serialize_val(p_cost),
            "floating_pnl": serialize_val(p_pnl),
            "return_rate": serialize_val(p_rate),
            "weight": serialize_val(p_weight),
            "stock_count": pg["stock_count"],
            "profit_count": pg["profit_count"],
            "loss_count": pg["loss_count"],
        })

    # 6. 持仓市值趋势
    mv_trend = compute_open_mv_trend(
        analytics,
        meta_map=meta_map,
        person_id=person_id,
        broker=broker,
        stock_type=stock_type,
        industry=industry,
        currency=currency,
        search=search,
        trend_window=trend_window,
    )

    # 7. 明细表盈利/亏损状态切换（只改变列表对应记录的展示范围）
    table_rows = filter_open_holdings_rows(
        scoped_rows,
        pnl_filter=pnl_filter,
    )
    table_mv = sum((_to_dec(r.get("market_value")) or ZERO for r in table_rows), ZERO)
    table_cost = sum(
        (
            (_to_dec(r.get("cost_amount")) if _to_dec(r.get("cost_amount")) is not None else ((_to_dec(r.get("market_value")) or ZERO) - (_to_dec(r.get("total_pnl")) or ZERO)))
            for r in table_rows
        ),
        ZERO,
    )
    table_pnl = sum((_to_dec(r.get("total_pnl")) or ZERO for r in table_rows), ZERO)
    table_rate = ratio(table_pnl, table_cost) if table_cost > ZERO else None
    ticker_top10 = compute_open_ticker_top10(scoped_rows, top_n=top_n)

    return {
        "cards": cards,
        "broker_distribution": broker_distribution,
        "industry_distribution": industry_distribution,
        "person_summary": person_summary,
        "mv_trend": mv_trend,
        "ticker_top10": ticker_top10,
        "pnl_counts": {
            "all": stock_count,
            "profit": profit_count,
            "loss": loss_count,
            "flat": flat_count,
        },
        "table_counts": {
            "all": stock_count,
            "profit": profit_count,
            "loss": loss_count,
            "flat": flat_count,
        },
        "table_summary": {
            "market_value": serialize_val(table_mv),
            "cost_amount": serialize_val(table_cost),
            "floating_pnl": serialize_val(table_pnl),
            "return_rate": serialize_val(table_rate),
            "count": len(table_rows),
        },
        "available_filters": {
            "brokers": available_brokers,
            "stock_types": available_stock_types,
            "industries": available_industries,
            "currencies": available_currencies,
        },
        "rows": table_rows,
    }
