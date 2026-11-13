"""验证每日持仓收益在最新交易日尚未收盘时使用最近可用交易报价进行实时估值叠加。"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.live_quotes import LiveQuote
from portfolio.engine.performance import DAILY_COLUMNS
from portfolio.ui.daily_performance_page import apply_live_daily_overlay


class _StubQuoteStore:
    def __init__(self, quotes):
        self._quotes = quotes

    def load(self, now):
        return self._quotes, set(), set(), None


def test_apply_live_daily_overlay_fills_unclosed_day_from_latest_quote():
    """最新日期收盘价与当日收益全为 None 时，取最近有效交易报价实时计算市值与当日收益；正式收盘日不覆盖。"""
    def _row(day, broker, sym, qty, close, mv, open_mv, bought, sold, pnl):
        vals = [
            day, broker, "大盘", sym, "持仓",
            qty, Decimal("0"), Decimal("0"), qty,
            Decimal("50"), close, mv, Decimal("0.5"),
            None, None, open_mv, bought, sold, 0, pnl,
            Decimal("500"), Decimal("0"), Decimal("500"),
        ]
        return ReportRow(tuple(vals), {"instrument_id": 1 if sym == "TQQQ" else 2, "account_id": 1})

    r_prev = _row("2026-11-12", "LONGBRIDGE", "TQQQ", Decimal("10"), Decimal("58"), Decimal("580"), Decimal("550"), Decimal("0"), Decimal("0"), Decimal("30"))
    r_curr_open = _row("2026-11-13", "LONGBRIDGE", "TQQQ", Decimal("10"), None, None, Decimal("580"), Decimal("0"), Decimal("0"), None)
    r_curr_closed = _row("2026-11-13", "SCHWAB", "NVDA", Decimal("0"), None, None, Decimal("200"), Decimal("0"), Decimal("215"), None)

    analytics = SimpleNamespace(
        reports={"每日持仓收益": Report("每日持仓收益", DAILY_COLUMNS, (r_prev, r_curr_open, r_curr_closed))}
    )
    instruments = {
        1: {"symbol": "TQQQ", "market": "US"},
        2: {"symbol": "NVDA", "market": "US"},
    }
    now = datetime(2026, 11, 13, 18, 12, tzinfo=UTC)
    quotes = {
        ("TQQQ.US", "Regular"): LiveQuote("TQQQ.US", Decimal("60.5"), now, "Regular", "live"),
    }

    rows, max_day, is_live, live_count, total_count = apply_live_daily_overlay(
        analytics, quote_store=_StubQuoteStore(quotes), instruments=instruments, now=now
    )
    assert max_day == "2026-11-13"
    assert is_live is True
    assert live_count == 2
    assert total_count == 2
    # 持仓中的 TQQQ：市值 = 10 * 60.5 = 605；当日收益 = 605 - 580 = +25
    assert rows[1][10] == Decimal("60.5")
    assert rows[1][11] == Decimal("605.0")
    assert rows[1][19] == Decimal("25.0")
    # 当日清仓的 NVDA：市值 = 0；当日收益 = 0 - 200 - 0 + 215 = +15
    assert rows[2][11] == Decimal("0")
    assert rows[2][19] == Decimal("15")
