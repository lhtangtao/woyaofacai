"""国内资产（支付宝 / 天天基金 / 同花顺）只读交易明细与逐笔持仓成本回放查询模块。"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, localcontext
from typing import Any

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .storage import RAW_FILE, connect

_BUY_KEYWORDS = ("买入", "建仓", "买基金", "买新基金", "定投", "定时定额", "认购", "申购", "即充即用", "转入", "强增")
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
        if "转入" in text or "调增" in text:
            return "买入"
        if "转出" in text or "调减" in text:
            return "卖出"
        return "转换"
    if any(k in text for k in _BUY_KEYWORDS):
        return "买入"
    if any(k in text for k in _SELL_KEYWORDS):
        return "卖出"
    return "其他"


def _d(val: Any) -> Decimal | None:
    if val is None or val == "":
        return None
    if isinstance(val, Decimal):
        return val if val.is_finite() else None
    try:
        d = Decimal(str(val))
        return d if d.is_finite() else None
    except Exception:  # noqa: BLE001
        return None


def _to_float(val: Any) -> float | None:
    d = _d(val)
    return float(d) if d is not None else None


def _format_cost_change(change_tag: str | None, diff: Decimal | None) -> str:
    if change_tag in ("建仓", "清仓"):
        return change_tag
    if diff is None:
        return "—"
    if abs(diff) < Decimal("0.00005"):
        return "不变"
    sign = "-" if diff < 0 else "+"
    mag = abs(diff)
    txt = f"{mag:,.2f}"
    if txt == "0.00":
        txt = f"{mag:,.4f}".rstrip("0").rstrip(".")
    return f"{sign}{txt}/份"


def query_domestic_trades(*, root=DOMESTIC_ASSETS_DIR) -> dict[str, Any]:
    """从 raw_fund_transactions 读取全量国内交易明细，并按账户+标的正序回放成交后持仓、摊薄成本价、净投入与成本变化。"""
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

        latest_nav: dict[str, float] = {}
        if "raw_fund_nav_daily" in tables:
            nav_sql = """
                SELECT n.fund_code, n.unit_nav
                FROM raw_fund_nav_daily n
                JOIN (
                    SELECT fund_code, MAX(nav_date) AS max_date
                    FROM raw_fund_nav_daily
                    WHERE unit_nav IS NOT NULL AND unit_nav <> ''
                    GROUP BY fund_code
                ) m ON n.fund_code = m.fund_code AND n.nav_date = m.max_date
            """
            for r in raw.execute(nav_sql):
                nv = _to_float(r["unit_nav"])
                if nv is not None:
                    latest_nav[str(r["fund_code"])] = nv

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
            c_status = str(r["confirmation_status"] or "")
            p_status = str(r["parse_status"] or "正常")
            status = c_status or p_status or "—"
            is_review = p_status != "正常" or c_status in ("失败", "异常", "待核查")

            sh_dec = _d(r["confirmed_shares"])
            amt_dec = _d(r["confirmed_amount"])
            nav_dec = _d(r["confirmed_nav"])
            if nav_dec is None and sh_dec and amt_dec and abs(sh_dec) > 0:
                with localcontext() as ctx:
                    ctx.prec = 28
                    nav_dec = abs(amt_dec / sh_dec)

            rows.append({
                "id": r["id"],
                "account_id": str(r["account_id"] or ""),
                "trade_date": trade_date,
                "transaction_time": (t_time or trade_date).replace("T", " "),
                "confirmation_date": c_date,
                "platform": plat,
                "fund_code": code,
                "fund_name": str(r["fund_name"] or meta.get("fund_name") or code),
                "fund_type": str(r["fund_type"] or meta.get("fund_type") or "待分类"),
                "direction": classify_direction(raw_type),
                "transaction_type_raw": raw_type,
                "current_price": latest_nav.get(code),
                "application_amount": _to_float(r["application_amount"]),
                "confirmed_amount": float(amt_dec) if amt_dec is not None else None,
                "confirmed_shares": float(abs(sh_dec)) if sh_dec is not None else None,
                "confirmed_nav": float(nav_dec) if nav_dec is not None else None,
                "fee": _to_float(r["fee"]),
                "status": status,
                "review_required": is_review,
                "order_id": str(r["order_id"] or ""),
                "_raw_sh": sh_dec,
                "_raw_amt": amt_dec,
            })

    # 按 (account_id, fund_code) 升序回放计算成交后持仓、成交后成本价（摊薄成本，负值按0处理）、成交后净投入、成本变化
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["fund_code"]:
            by_group[(row["account_id"], row["fund_code"])].append(row)

    with localcontext() as ctx:
        ctx.prec = 50
        for _gkey, g_rows in by_group.items():
            # rows 原为倒序，反转为时间正序回放
            chronological = sorted(
                g_rows,
                key=lambda x: (x["trade_date"], x["transaction_time"], str(x["id"] or "")),
            )
            qty = Decimal(0)
            net_cost = Decimal(0)
            prev_cost_price: Decimal | None = None
            for item in chronological:
                sh = item.pop("_raw_sh", None) or Decimal(0)
                amt = item.pop("_raw_amt", None) or Decimal(0)
                kind = item["transaction_type_raw"]
                direction = item["direction"]
                was_zero = qty <= Decimal("0.0001")

                if "红利再投" in kind or "分红转投" in kind:
                    if sh > 0:
                        qty += abs(sh)
                elif direction == "买入":
                    if abs(sh) > 0:
                        qty += abs(sh)
                    if amt > 0:
                        net_cost += amt
                elif direction == "卖出":
                    sell_sh = abs(sh)
                    if sell_sh > 0:
                        qty = max(Decimal(0), qty - sell_sh)
                    if amt > 0:
                        net_cost -= amt
                    if qty <= Decimal("0.0001"):
                        qty = Decimal(0)
                        net_cost = Decimal(0)
                elif direction == "分红":
                    if amt > 0:
                        net_cost -= amt
                elif "缩" in kind or "折算" in kind:
                    qty = max(Decimal(0), qty + sh)

                if qty > Decimal("0.0001"):
                    raw_unit_cost = net_cost / qty
                    unit_cost = max(Decimal(0), raw_unit_cost)
                    if was_zero or prev_cost_price is None:
                        change_tag = "建仓"
                        cost_diff = None
                    else:
                        change_tag = None
                        cost_diff = unit_cost - prev_cost_price
                    prev_cost_price = unit_cost
                    item["post_shares"] = float(qty)
                    item["post_cost"] = float(unit_cost)
                    item["post_net"] = float(net_cost)
                    item["cost_diff"] = float(cost_diff) if cost_diff is not None else None
                    item["cost_change"] = _format_cost_change(change_tag, cost_diff)
                else:
                    change_tag = "清仓" if not was_zero else None
                    prev_cost_price = None
                    item["post_shares"] = 0.0
                    item["post_cost"] = None
                    item["post_net"] = 0.0
                    item["cost_diff"] = None
                    item["cost_change"] = _format_cost_change(change_tag, None)

    for row in rows:
        row.pop("_raw_sh", None)
        row.pop("_raw_amt", None)

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
