from datetime import date
from decimal import Decimal as D

import pytest
from test_replay import event, snapshot

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
from portfolio.engine.audit import audit
from portfolio.engine.fx import convert_reports
from portfolio.engine.replay import replay
from portfolio.excel_reference import extract_reference
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.export_service import export_report


def test_opening_excludes_history_and_does_not_split_twice():
    b = dict(id=1, baseline_role="OPENING", baseline_kind="HOLDING", scope_type="PERSON_ACCOUNT", person_id=1,
             account_id=1, instrument_id=1, baseline_date="2026-01-05", currency="USD", quantity_e8=2000000000,
             net_cost_e8=100000000000, cumulative_buy_e8=100000000000, cumulative_sell_e8=0, source_record_id=99)
    a = dict(id=1, instrument_id=1, effective_date="2026-01-03", broker_scope="ALL", ratio_numerator=2, ratio_denominator=1)
    s = snapshot([event(1, "BUY", "10", "1000"), event(2, "SELL", "5", "300", "2026-01-06")], [a], [b])
    positions, traces, captures = replay(s, capture_days=("2026-01-02", "2026-01-05", "2026-01-06"))
    assert not captures["2026-01-02"]
    assert captures["2026-01-05"][(1, 1, 1)].quantity == 20
    assert positions[(1, 1, 1)].quantity == 15 and positions[(1, 1, 1)].net == 700
    assert 1 not in traces and any(i.rule == "DQ014" for i in s.issues)


def test_grain_conflict_blocks_financial_cost_instead_of_silent_dedup():
    s = snapshot([event(1, "BUY", "10", "100", broker_trade_id=1)])
    s.extras["broker_trades"] = [dict(id=n, broker_order_id="ORDER", broker_execution_id="E" if n == 1 else None,
        record_granularity=g, record_state="ACTIVE", accounting_role="ACCOUNT_LEDGER", account_id=1, source_record_id=n)
        for n, g in ((1, "FILL"), (2, "ORDER_TOTAL"))]
    positions, traces, _ = replay(s)
    assert positions[(1, 1, 1)].net is None
    assert any(i.rule == "DQ005" for i in s.issues)
    from portfolio.engine.holdings import position_reports
    row = position_reports(s, positions)["当前持仓"].rows[0]
    assert row.values[3] is None and row.values[5] is None
    assert traces[1] == (None, None, None)


def test_unpaired_transfer_review_and_cash_opening_unknown():
    s = snapshot([])
    s.extras["person_operations"] = [dict(id=1, source_record_id=2, person_id=1, account_id=1, instrument_id=1,
        broker_trade_id=None, business_date="2026-01-02", operation_kind="OWNER_TRANSFER", side="TRANSFER_OUT", link_status="REVIEW_REQUIRED", transfer_group_key="G", quantity_e8=100000000, amount_e8=100000000, counterparty_person_id=2)]
    report = audit(s, {})
    assert {r.values[0] for r in report.rows} >= {"DQ004", "DQ006", "DQ016", "DQ018"}


@pytest.mark.parametrize("suffix", ["csv", "xlsx", "json"])
def test_exports_exact_decimal_and_no_overwrite(tmp_path, suffix):
    report = Report("合成", ("数量", "金额", "备注"), (ReportRow((D("0.00000001"), D("999999999999999.12345678"), "=HYPERLINK(1)")),))
    path = tmp_path / ("精度." + suffix)
    export_report(report, path, parameters={"as_of": "2026-01-01", "mode": "EXCEL_COMPAT"})
    with pytest.raises(FileExistsError):
        export_report(report, path)
    if suffix == "xlsx":
        reference = extract_reference(path)
        assert reference["sheets"][0]["cells"]["B2"]["value"] == "999999999999999.12345678"
        assert reference["sheets"][0]["cells"]["C2"]["formula"] is None
        assert len(reference["sheets"]) == 2
    else:
        assert "999999999999999.12345678" in path.read_text(encoding="utf-8-sig")


def test_fx_exact_date_and_missing_not_forward_filled():
    from portfolio.domain.analytics import Metric
    s = snapshot([])
    s.extras["fx_rates"] = [dict(rate_date="2026-01-02", base_currency="USD", quote_currency="CNY", rate_e8=700000000, provider="TEST", source_record_id=1)]
    reports = {"当前持仓": Report("当前持仓", ("市值($)",), (ReportRow((D(100),), {"valuation_date": "2026-01-02"}), ReportRow((D(200),), {"valuation_date": "2026-01-03"})))}
    metrics = (Metric("总额", D(100), "test", "2026-01-02"),)
    converted = convert_reports(s, reports, metrics, "CNY")
    assert converted[0].value == 700
    assert [r.values[0] for r in reports["当前持仓"].rows] == [700, None]
    assert any(i.rule == "DQ011" for i in s.issues)


def test_cny_scope_keeps_usd_trade_ledger(demo_path):
    # Explicit full-history audit keeps this historical FX guard regression meaningful.
    a = AnalyticsService(demo_path, audit_start=None).calculate(QueryScope(date(2026, 1, 10), reporting_currency="CNY"))
    assert len(a.reports["交易回放"].rows) == 3
    assert a.metrics[0].value is None and any(i.rule == "DQ011" for i in a.issues)
    import sqlite3
    with sqlite3.connect(demo_path) as connection:
        connection.executemany("INSERT INTO fx_rates(rate_date,base_currency,quote_currency,rate_e8,provider,source_record_id) VALUES(?,'USD','CNY',700000000,'SYNTHETIC',6)",
                               [(day,) for day in ("2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09")])
    partial = AnalyticsService(demo_path).calculate(a.scope)
    assert partial.metrics[0].value == 4620
    # An available last-day FX must not hide the missing Jan 3 historical rate.
    assert partial.metrics[9].value is None
    assert partial.reports["每月持仓收益"].rows[0].values[20] is None


def test_wal_identity_invalidates_cached_derived_result(demo_path):
    import sqlite3
    service = AnalyticsService(demo_path)
    scope = QueryScope(date(2026, 1, 10))
    first = service.calculate(scope)
    c = sqlite3.connect(demo_path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("UPDATE market_prices SET price_e8=15000000000 WHERE price_type='DAILY_CLOSE'")
    c.commit()
    second = service.calculate(scope)
    assert first is not second and first.source_identity != second.source_identity
    c.close()
