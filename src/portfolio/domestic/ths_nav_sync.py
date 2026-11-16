"""同花顺（THS）国内股票/ETF 历史日线不复权收盘价同步工具。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .storage import RAW_FILE, backup, connect
from .ths_analytics import THS_SOURCE

THS_NAV_SOURCE = "AKShare日线不复权"
BEIJING = ZoneInfo("Asia/Shanghai")


def _sina_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
    return f"{prefix}{code}"


def _fetch_daily_closes(code: str, fund_type: str, start_day: str, end_day: str) -> list[tuple[str, str, dict]]:
    """拉取指定 A 股或场内 ETF 的不复权日线收盘价列表 [(nav_date, close_str, payload)]。"""
    import akshare as ak

    start_compact = start_day.replace("-", "")
    end_compact = end_day.replace("-", "")
    is_etf = "ETF" in (fund_type or "").upper() or code.startswith(("15", "51", "56", "58"))
    records: list[tuple[str, str, dict]] = []

    if is_etf:
        # 优先使用新浪场内 ETF 历史日线（不复权、不触发东方财富 push2 限流）
        try:
            df_sina = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))
        except Exception:
            df_sina = None
        if df_sina is not None and not df_sina.empty:
            for row in df_sina.to_dict("records"):
                d_str = str(row.get("date") or "")[:10]
                close = row.get("close")
                if d_str and start_day <= d_str <= end_day and close is not None:
                    records.append((d_str, str(Decimal(str(close))), {"close": str(close), "api": "fund_etf_hist_sina"}))
            if records:
                return records
        try:
            df = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=start_compact,
                end_date=end_compact,
                adjust="",
            )
        except Exception:
            df = None
        if df is not None and not df.empty:
            for row in df.to_dict("records"):
                d_str = str(row.get("日期") or "")[:10]
                close = row.get("收盘")
                if d_str and close is not None:
                    records.append((d_str, str(Decimal(str(close))), {"close": str(close), "api": "fund_etf_hist_em"}))
        return records

    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_compact,
            end_date=end_compact,
            adjust="",
        )
    except Exception:
        df = None

    if df is not None and not df.empty:
        for row in df.to_dict("records"):
            d_str = str(row.get("日期") or "")[:10]
            close = row.get("收盘")
            if d_str and close is not None:
                records.append((d_str, str(Decimal(str(close))), {"close": str(close), "api": "stock_zh_a_hist"}))
        return records

    # 兜底尝试新浪历史日线（兼容部分退市股）
    try:
        df_sina = ak.stock_zh_a_daily(
            symbol=_sina_symbol(code),
            start_date=start_compact,
            end_date=end_compact,
            adjust="",
        )
    except Exception:
        df_sina = None

    if df_sina is not None and not df_sina.empty:
        for row in df_sina.to_dict("records"):
            d_str = str(row.get("date") or "")[:10]
            close = row.get("close")
            if d_str and close is not None:
                records.append((d_str, str(Decimal(str(close))), {"close": str(close), "api": "stock_zh_a_daily"}))
    return records


def sync_ths_nav_daily(
    *,
    root=DOMESTIC_ASSETS_DIR,
    create_backup: bool = True,
    end_date: str | None = None,
    open_only: bool = False,
    recent_days: int | None = None,
) -> dict[str, object]:
    """为所有同花顺标的补充持仓期间的历史不复权收盘价到 raw_fund_nav_daily。"""
    db_path = root / RAW_FILE
    if create_backup and db_path.is_file():
        backup(root)

    cutoff = end_date or datetime.now(BEIJING).date().isoformat()
    inserted = 0
    skipped_existing = 0
    failed_codes: list[str] = []

    with connect(db_path) as db:
        rows = db.execute(
            """
            SELECT t.fund_code,
                   COALESCE(i.fund_name, t.fund_name) AS fund_name,
                   COALESCE(i.fund_type, t.fund_type) AS fund_type,
                   MIN(t.confirmation_date) AS min_day,
                   MAX(t.confirmation_date) AS max_day,
                   SUM(CAST(COALESCE(t.confirmed_shares, '0') AS REAL)) AS net_shares
            FROM raw_fund_transactions t
            LEFT JOIN raw_fund_info i ON i.fund_code = t.fund_code
            WHERE t.source_platform = ? AND t.confirmation_date IS NOT NULL
            GROUP BY t.fund_code
            ORDER BY min_day
            """,
            (THS_SOURCE,),
        ).fetchall()

        now_iso = datetime.now(BEIJING).isoformat(timespec="seconds")
        for r in rows:
            code = r["fund_code"]
            fund_type = r["fund_type"] or ""
            is_open = abs(float(r["net_shares"] or 0.0)) > 1e-6
            if open_only and not is_open:
                continue
            start_day = r["min_day"]
            if recent_days is not None and recent_days > 0:
                from datetime import timedelta
                recent_start = (date.fromisoformat(cutoff) - timedelta(days=recent_days)).isoformat()
                if recent_start > start_day:
                    start_day = recent_start
            # 若仍有持仓或后续还有净值需要，延展至 cutoff
            target_end = cutoff if is_open else (r["max_day"] or cutoff)
            if target_end < start_day:
                target_end = start_day

            # 已有场外正式净值覆盖（例如 161725 白酒基金LOF）的日期不重复写入，避免来源冲突
            existing_days = {
                row[0]
                for row in db.execute(
                    "SELECT nav_date FROM raw_fund_nav_daily WHERE fund_code=? AND nav_date BETWEEN ? AND ?",
                    (code, start_day, target_end),
                )
            }
            if code == "161725" and existing_days:
                skipped_existing += len(existing_days)
                continue

            try:
                closes = _fetch_daily_closes(code, fund_type, start_day, target_end)
            except Exception:
                closes = []

            if not closes:
                failed_codes.append(f"{code}({r['fund_name']})")
                continue

            for d_str, close_str, payload in closes:
                if d_str in existing_days:
                    skipped_existing += 1
                    continue
                try:
                    db.execute(
                        """
                        INSERT OR IGNORE INTO raw_fund_nav_daily (
                            fund_code, nav_date, source, unit_nav, accumulated_nav,
                            daily_return_rate, income_per_10k, yield_7d, currency,
                            fetched_at, raw_payload, field_status
                        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, 'CNY', ?, ?, '正常')
                        """,
                        (
                            code,
                            d_str,
                            THS_NAV_SOURCE,
                            close_str,
                            close_str,
                            now_iso,
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                    if db.total_changes:
                        inserted += 1
                except sqlite3.Error:
                    continue

    return {
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "failed_codes": failed_codes,
        "total_instruments": len(rows),
    }
