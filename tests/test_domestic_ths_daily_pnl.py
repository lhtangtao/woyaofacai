"""验证同花顺股票/ETF 每日持仓、收盘价与每日/每月盈亏计算。"""
from datetime import date
from pathlib import Path

from portfolio.domestic.fund_transactions import SCHEMA as FT_SCHEMA
from portfolio.domestic.nav_collection import SCHEMA as NAV_SCHEMA
from portfolio.domestic.performance import build_domestic_performance
from portfolio.domestic.storage import CALC_FILE, CALC_SCHEMA, RAW_FILE, RAW_SCHEMA, connect
from portfolio.domestic.ths_import import import_ths_trades


def _seed_navs(root: Path, nav_rows: list[tuple[str, str, str]]) -> None:
    with connect(root / RAW_FILE) as raw:
        raw.executescript(RAW_SCHEMA + FT_SCHEMA + NAV_SCHEMA)
        for code, nav_date, close in nav_rows:
            raw.execute(
                """INSERT INTO raw_fund_nav_daily (
                    fund_code, nav_date, source, unit_nav, accumulated_nav,
                    daily_return_rate, income_per_10k, yield_7d, currency,
                    fetched_at, raw_payload, field_status
                ) VALUES (?, ?, 'AKShare日线不复权', ?, ?, NULL, NULL, NULL, 'CNY', '2026-11-15T00:00:00+08:00', '{}', '正常')""",
                (code, nav_date, close, close),
            )
    with connect(root / CALC_FILE) as calc:
        calc.executescript(CALC_SCHEMA)


def test_ths_daily_pnl_tracks_float_dividend_tax_and_close(tmp_path: Path):
    records = [
        {
            "fund_code": "600529",
            "fund_name": "山东药玻",
            "fund_type": "股票-主板",
            "transaction_time": "2024-01-02 10:00:00",
            "confirmation_date": "2024-01-02",
            "transaction_type_raw": "建仓",
            "confirmed_nav": "10.000",
            "confirmed_shares": "100",
            "confirmed_amount": "1000.00",
            "fee": "5.00",
        },
        {
            "fund_code": "600529",
            "fund_name": "山东药玻",
            "fund_type": "股票-主板",
            "transaction_time": "2024-01-03 16:00:00",
            "confirmation_date": "2024-01-03",
            "transaction_type_raw": "分红",
            "confirmed_nav": "10.500",
            "confirmed_shares": "0",
            "confirmed_amount": "20.00",
            "fee": "0.00",
        },
        {
            "fund_code": "600529",
            "fund_name": "山东药玻",
            "fund_type": "股票-主板",
            "transaction_time": "2024-01-04 14:00:00",
            "confirmation_date": "2024-01-04",
            "transaction_type_raw": "清仓",
            "confirmed_nav": "11.000",
            "confirmed_shares": "-100",
            "confirmed_amount": "1100.00",
            "fee": "6.00",
        },
        {
            "fund_code": "600529",
            "fund_name": "山东药玻",
            "fund_type": "股票-主板",
            "transaction_time": "2024-01-05 00:00:00",
            "confirmation_date": "2024-01-05",
            "transaction_type_raw": "股息红利税补缴",
            "confirmed_nav": "0.000",
            "confirmed_shares": "0",
            "confirmed_amount": "2.00",
            "fee": "0.00",
        },
    ]
    _seed_navs(
        tmp_path,
        [
            ("600529", "2024-01-02", "10.20"),
            ("600529", "2024-01-03", "10.50"),
            ("600529", "2024-01-04", "11.00"),
            ("600529", "2024-01-05", "11.00"),
        ],
    )
    import_ths_trades(records, root=tmp_path, create_backup=False)

    perf = build_domestic_performance(
        root=tmp_path,
        start_date=date(2024, 1, 2),
        valuation_date="2024-01-05",
    )
    by_day = {r["date"]: r for r in perf.daily_rows if r["fund_code"] == "600529"}

    # 2024-01-02: 市值 1020 - 投入 1005 = +15
    assert by_day["2024-01-02"]["market_value"] == 1020.0
    assert by_day["2024-01-02"]["daily_pnl"] == 15.0
    assert by_day["2024-01-02"]["cum_pnl"] == 15.0

    # 2024-01-03: 市值 1050 - (1005 - 分红20) = +65, 日盈亏 +50
    assert by_day["2024-01-03"]["market_value"] == 1050.0
    assert by_day["2024-01-03"]["daily_pnl"] == 50.0
    assert by_day["2024-01-03"]["cum_pnl"] == 65.0

    # 2024-01-04: 清仓市值 0 - (1005 - 20 - 1094) = +109, 日盈亏 +44
    assert by_day["2024-01-04"]["market_value"] == 0.0
    assert by_day["2024-01-04"]["daily_pnl"] == 44.0
    assert by_day["2024-01-04"]["cum_pnl"] == 109.0

    # 2024-01-05: 补缴红利税 2 元, 累计盈亏 +107, 日盈亏 -2
    assert by_day["2024-01-05"]["daily_pnl"] == -2.0
    assert by_day["2024-01-05"]["cum_pnl"] == 107.0
    assert perf.monthly_rows[0]["month_pnl"] == 107.0
