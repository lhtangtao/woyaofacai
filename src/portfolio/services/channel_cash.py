"""Fetch live account cash balances for supported brokers (Longbridge, Schwab)."""
from datetime import date, datetime, time, timezone
from decimal import Decimal
import logging
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

from portfolio.domain.models import from_e8
from portfolio.domain.trade_time import event_time_utc

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
PERSONAL_PERSON_ID = 1
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc


def _parse_anchor_utc(anchor_text: str) -> tuple[str, datetime] | None:
    """将用户输入的锚点时间（北京时间 YYYY-MM-DD HH:MM:SS 或纯日期 YYYY-MM-DD）解析为 (日期字符串, UTC时刻)。"""
    text = str(anchor_text or "").strip()
    if not text:
        return None
    if len(text) == 10:
        d = date.fromisoformat(text)
        dt = datetime.combine(d, time(23, 59, 59), SHANGHAI)
        return d.isoformat(), dt.astimezone(UTC)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI).date().isoformat(), dt.astimezone(UTC)


def _trade_utc(trade: dict[str, Any], broker_code: str) -> datetime | None:
    """复用 domain/trade_time.event_time_utc 解析单笔成交的准确 UTC 时刻。"""
    bday = str(trade.get("business_date") or "")[:10]
    raw_time = str(trade.get("book_time") or bday or "").strip()
    if not raw_time:
        return None
    ev = SimpleNamespace(
        day=bday,
        time=raw_time,
        metadata={
            "executed_at_utc": trade.get("executed_at_utc"),
            "broker_code": broker_code,
            "trade_clock_timezone": trade.get("trade_clock_timezone"),
        },
    )
    try:
        return event_time_utc(ev)
    except Exception:
        return None


def is_cash_visible_scope(scope) -> bool:
    """现金信息仅在「我个人」和「全部人员」视角展示，其他人员不展示。"""
    if scope is None:
        return False
    person_ids = getattr(scope, "person_ids", None)
    if person_ids is None:
        return True
    try:
        ids = tuple(int(p) for p in person_ids)
    except Exception:
        return False
    return ids == (PERSONAL_PERSON_ID,)


def _trade_cash_delta(trade: dict[str, Any]) -> Decimal | None:
    """计算单笔 broker_trades 成交对账户现金的净变动（买入为负，卖出为正）。"""
    side = str(trade.get("side") or "").upper()
    net_e8 = trade.get("net_amount_e8")
    if net_e8 is not None:
        net = from_e8(net_e8)
        if side == "BUY" and net > ZERO:
            return -net
        if side == "SELL" and net < ZERO:
            return -net
        return net

    calc_e8 = trade.get("calculation_amount_e8")
    gross_e8 = trade.get("gross_amount_e8")
    if calc_e8 is not None:
        gross = abs(from_e8(calc_e8))
    elif gross_e8 is not None:
        gross = abs(from_e8(gross_e8))
    else:
        q_e8 = trade.get("quantity_e8")
        p_e8 = trade.get("price_e8")
        if q_e8 is None or p_e8 is None:
            return None
        gross = abs(from_e8(q_e8) * from_e8(p_e8))

    fees_e8 = trade.get("fees_e8")
    fee = abs(from_e8(fees_e8)) if fees_e8 is not None else ZERO
    if side == "BUY":
        return -(gross + fee)
    if side == "SELL":
        return gross - fee
    return ZERO


def compute_anchored_channel_cash(
    snapshot,
    anchor: dict[str, Any],
) -> dict[str, Any] | None:
    """基于某个时刻（支持精确到时分秒）的手动现金锚点，叠加之后该渠道的真实成交与现金流水推算当前现金。"""
    channel = str(anchor.get("channel") or "").upper()
    raw_anchor_text = str(anchor.get("anchor_date") or "").strip()
    anchor_amt = anchor.get("amount")
    if not channel or not raw_anchor_text or anchor_amt is None:
        return None
    parsed_anchor = _parse_anchor_utc(raw_anchor_text)
    if parsed_anchor is None:
        return None
    anchor_day, anchor_utc = parsed_anchor
    base_amount = anchor_amt if isinstance(anchor_amt, Decimal) else Decimal(str(anchor_amt))
    as_of = snapshot.scope.as_of.isoformat()
    if as_of < anchor_day:
        return None

    accounts = getattr(snapshot, "accounts", {}) or {}
    channel_acc_ids = {
        acc_id
        for acc_id, acc in accounts.items()
        if str(acc.get("broker_code") or "").upper() == channel
    }

    extras = getattr(snapshot, "extras", {}) or {}
    current = base_amount
    trades_applied = 0
    events_applied = 0

    for trade in extras.get("broker_trades", []):
        if trade.get("record_state") != "ACTIVE" or trade.get("accounting_role") != "ACCOUNT_LEDGER":
            continue
        if channel_acc_ids and trade.get("account_id") not in channel_acc_ids:
            continue
        if not channel_acc_ids:
            acc = accounts.get(trade.get("account_id"), {})
            if str(acc.get("broker_code") or "").upper() != channel:
                continue
        bday = str(trade.get("business_date") or "")[:10]
        if not bday or bday > as_of:
            continue
        t_utc = _trade_utc(trade, channel)
        if t_utc is not None:
            if t_utc <= anchor_utc:
                continue
        elif bday <= anchor_day:
            continue
        delta = _trade_cash_delta(trade)
        if delta is not None:
            current += delta
            trades_applied += 1

    for ev in extras.get("cash_events", []):
        if ev.get("record_state") != "ACTIVE":
            continue
        if ev.get("accounting_role") in ("REVIEW_REQUIRED", "INFORMATIONAL"):
            continue
        if ev.get("broker_trade_id") and ev.get("event_type") in ("FEE", "TAX"):
            continue
        if channel_acc_ids and ev.get("account_id") not in channel_acc_ids:
            continue
        bday = str(ev.get("business_date") or "")[:10]
        if not (anchor_day < bday <= as_of):
            continue
        amt_e8 = ev.get("amount_e8")
        if amt_e8 is not None:
            current += from_e8(amt_e8)
            events_applied += 1

    return {
        "channel": channel,
        "amount": current,
        "source": "锚点推算",
        "anchor_date": raw_anchor_text,
        "anchor_amount": base_amount,
        "trades_applied": trades_applied,
        "events_applied": events_applied,
        "updated_at": str(anchor.get("updated_at") or ""),
        "note": str(anchor.get("note") or ""),
    }


def resolve_channel_cash_details(snapshot_or_analytics) -> dict[str, dict[str, Any]]:
    """统一解析当前视角下各券商渠道的现金余额及来源（长桥/嘉信 API 实时 + BBAE/第一证券锚点推算）。

    仅在「个人」或「全部人员」视角返回；其他人员视角返回空字典。
    """
    scope = getattr(snapshot_or_analytics, "scope", None)
    if not is_cash_visible_scope(scope):
        return {}

    extras = getattr(snapshot_or_analytics, "extras", {}) or {}
    api_cash = extras.get("channel_cash", {}) or {}
    api_updated = extras.get("channel_cash_updated_at", {}) or {}
    anchors = extras.get("manual_cash_anchors", {}) or {}

    resolved: dict[str, dict[str, Any]] = {}

    # 1. API 实时现金（长桥 / 嘉信）
    for ch, amt in api_cash.items():
        ch_u = str(ch).upper()
        if amt is None:
            continue
        val = amt if isinstance(amt, Decimal) else Decimal(str(amt))
        resolved[ch_u] = {
            "channel": ch_u,
            "amount": val,
            "source": "API实时",
            "anchor_date": None,
            "anchor_amount": None,
            "trades_applied": 0,
            "events_applied": 0,
            "updated_at": str(api_updated.get(ch_u) or ""),
            "note": "券商开放接口实时拉取",
        }

    # 2. 手动现金锚点 + 后续交易流水推算（BBAE / 第一证券等无实时 API 券商）
    #    若 snapshot 具备完整 broker_trades 则动态推算，否则直接使用已预计算的结果或锚点原值
    precomputed = extras.get("resolved_channel_cash", {}) or {}
    for ch, item in precomputed.items():
        ch_u = str(ch).upper()
        if ch_u not in resolved and item and item.get("amount") is not None:
            resolved[ch_u] = dict(item)

    has_trades = "broker_trades" in extras
    for ch, anchor in anchors.items():
        ch_u = str(ch).upper()
        if ch_u in resolved:
            continue
        if has_trades:
            est = compute_anchored_channel_cash(snapshot_or_analytics, anchor)
            if est is not None:
                resolved[ch_u] = est
        else:
            amt = anchor.get("amount")
            if amt is not None:
                resolved[ch_u] = {
                    "channel": ch_u,
                    "amount": amt if isinstance(amt, Decimal) else Decimal(str(amt)),
                    "source": "锚点推算",
                    "anchor_date": str(anchor.get("anchor_date") or "").strip(),
                    "anchor_amount": amt if isinstance(amt, Decimal) else Decimal(str(amt)),
                    "trades_applied": 0,
                    "events_applied": 0,
                    "updated_at": str(anchor.get("updated_at") or ""),
                    "note": str(anchor.get("note") or ""),
                }

    return resolved


def fetch_longbridge_cash() -> Optional[Decimal]:
    """Fetch USD available cash from Longbridge OpenAPI."""
    try:
        from portfolio.sync.longbridge_credentials import load_longbridge_credentials
        from longbridge.openapi import Config, TradeContext
        token, key, secret = load_longbridge_credentials()
        cfg = Config.from_apikey(app_key=key, app_secret=secret, access_token=token)
        ctx = TradeContext(cfg)
        balances = ctx.account_balance()
        if balances:
            for c in balances[0].cash_infos:
                if getattr(c, "currency", "").upper() == "USD":
                    return Decimal(str(c.available_cash))
    except Exception as exc:
        logger.warning("获取长桥账户现金失败：%s", exc)
    return None


def fetch_schwab_cash() -> Optional[Decimal]:
    """Fetch USD net cash from Schwab API."""
    try:
        from portfolio.sync.schwab_api import get_service
        srv = get_service()
        acc = srv.account()
        if acc:
            balances = acc.get("securitiesAccount", {}).get("currentBalances", {})
            cash = Decimal(str(balances.get("cashBalance", 0.0)))
            margin = Decimal(str(balances.get("marginBalance", 0.0)))
            money_market = Decimal(str(balances.get("moneyMarketFund", 0.0)))
            return cash + margin + money_market
    except Exception as exc:
        logger.warning("获取嘉信账户现金失败：%s", exc)
    return None


def fetch_all_channel_cash() -> dict[str, Decimal]:
    """Fetch live cash balances for both Longbridge and Schwab."""
    results = {}
    lb = fetch_longbridge_cash()
    if lb is not None:
        results["LONGBRIDGE"] = lb
    sw = fetch_schwab_cash()
    if sw is not None:
        results["SCHWAB"] = sw
    return results
