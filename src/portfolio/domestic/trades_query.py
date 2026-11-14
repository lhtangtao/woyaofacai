"""国内资产（支付宝 / 天天基金）只读交易明细查询模块。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .storage import RAW_FILE, connect

_BUY_KEYWORDS = ("买入", "建仓", "买基金", "买新基金", "定投", "定时定额", "认购", "申购", "即充即用", "转入")
_SELL_KEYWORDS = ("卖出", "清仓", "卖基金", "赎回", "取现", "转出")
_DIVIDEND_KEYWORDS = ("分红", "红利")
_TRANSFER_KEYWORDS = ("转换", "调增", "调减", "转卡")


def classify_direction(raw_type: str | None) -> str:
    text = str(raw_type or "").strip()
    if not text:
        return "其他"
    if any(k in text for k in _DIVIDEND_KEYWORDS):
        return "分红"
    if any(k in text for k in _TRANSFER_KEYWORDS):
        if "转入" in text:
            return "买入"
        if "转出" in text:
            return "卖出"
        return "转换"
    if any(k in text for k in _BUY_KEYWORDS):
        return "买入"
    if any(k in text for k in _SELL_KEYWORDS):
        return "卖出"
    return "其他"


def _to_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(Decimal(str(val)))
    except Exception:  # noqa: BLE001
        return None


def query_domestic_trades(*, root=DOMESTIC_ASSETS_DIR) -> dict[str, Any]:
    """从 raw_fund_transactions 读取全量国内基金交易明细（只读）。"""
    db_path = root / RAW_FILE
    if not db_path.is_file():
        return {"rows": [], "platforms": [], "directions": [], "raw_types": [], "fund_types": []}

    rows: list[dict[str, Any]] = []
    with connect(db_path, readonly=True) as raw:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "raw_fund_transactions" not in tables:
            return {"rows": [], "platforms": [], "directions": [], "raw_types": [], "fund_types": []}

        fund_meta: dict[str, dict[str, str]] = {}
        if "raw_fund_info" in tables:
            for r in raw.execute("SELECT fund_code, fund_name, fund_type FROM raw_fund_info"):
                fund_meta[r[0]] = {"fund_name": r[1] or "", "fund_type": r[2] or ""}

        sql = """
            SELECT id, account_id, source_platform, order_id, transaction_time,
                   transaction_type_raw, fund_code, fund_name, fund_type,
                   application_amount, application_shares, confirmed_amount, confirmed_shares,
                   fee, confirmation_date, confirmed_nav, confirmation_status, parse_status
            FROM raw_fund_transactions
            ORDER BY COALESCE(confirmation_date, substr(transaction_time, 1, 10), '') DESC,
                     COALESCE(transaction_time, '') DESC,
                     id DESC
        """
        for r in raw.execute(sql):
            code = str(r["fund_code"] or "")
            meta = fund_meta.get(code, {})
            plat = (
                "支付宝" if r["source_platform"] == "ALIPAY"
                else "天天基金" if r["source_platform"] == "TIANTIAN"
                else "同花顺" if r["source_platform"] == "THS"
                else str(r["source_platform"] or "—")
            )
            raw_type = str(r["transaction_type_raw"] or "—")
            t_time = str(r["transaction_time"] or "")
            c_date = str(r["confirmation_date"] or "")
            trade_date = c_date or (t_time[:10] if len(t_time) >= 10 else "")
            status = str(r["confirmation_status"] or r["parse_status"] or "—")

            rows.append({
                "id": r["id"],
                "trade_date": trade_date,
                "transaction_time": t_time or trade_date,
                "confirmation_date": c_date,
                "platform": plat,
                "fund_code": code,
                "fund_name": str(r["fund_name"] or meta.get("fund_name") or code),
                "fund_type": str(r["fund_type"] or meta.get("fund_type") or "待分类"),
                "direction": classify_direction(raw_type),
                "transaction_type_raw": raw_type,
                "application_amount": _to_float(r["application_amount"]),
                "confirmed_amount": _to_float(r["confirmed_amount"]),
                "confirmed_shares": _to_float(r["confirmed_shares"]),
                "confirmed_nav": _to_float(r["confirmed_nav"]),
                "fee": _to_float(r["fee"]),
                "status": status,
                "order_id": str(r["order_id"] or ""),
            })

    platforms = sorted({r["platform"] for r in rows if r["platform"]})
    directions = sorted({r["direction"] for r in rows if r["direction"]})
    raw_types = sorted({r["transaction_type_raw"] for r in rows if r["transaction_type_raw"]})
    fund_types = sorted({r["fund_type"] for r in rows if r["fund_type"]})
    return {
        "rows": rows,
        "platforms": platforms,
        "directions": directions,
        "raw_types": raw_types,
        "fund_types": fund_types,
    }
