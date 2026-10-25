"""Fetch live account cash balances for supported brokers (Longbridge, Schwab)."""
from decimal import Decimal
import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
