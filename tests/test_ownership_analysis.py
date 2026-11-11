from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from portfolio.domain.analytics import Report, ReportRow
from portfolio.engine.holdings import CLOSED_COLUMNS, HOLDINGS_COLUMNS, STOCK_COLUMNS
from portfolio.engine.people_pnl import PeopleBundle
from portfolio.services.ownership_analysis import (
    CLOSED_COLUMNS as OWNERSHIP_CLOSED_COLUMNS,
    COLUMNS as OWNERSHIP_COLUMNS,
    analyze,
    choices,
    export_csv,
)
from portfolio.ui.ownership_analysis_page import OwnershipAnalysisPage


def _make_book(as_of, holding_rows=(), stock_rows=(), closed_rows=()):
    reports = {
        "当前持仓": Report("当前持仓", HOLDINGS_COLUMNS, tuple(holding_rows)),
        "股票汇总": Report("股票汇总", STOCK_COLUMNS, tuple(stock_rows)),
        "清仓收益": Report("清仓收益", CLOSED_COLUMNS, tuple(closed_rows)),
    }
    return SimpleNamespace(scope=SimpleNamespace(as_of=as_of), reports=reports)


def _sample_bundle():
    as_of = date(2026, 11, 11)
    # Stock 1: SNXX - held by 个人 (30) & 拉科 (45)
    # Stock 2: AAPL - completely closed by 个人 (+500) & 拉科 (-100)
    # Stock 3: NVDA - held by 个人 (10), closed by 拉科 (+300)
    meta_snxx = {"account_id": 1, "instrument_id": 1, "valuation_date": "2026-11-11"}
    meta_aapl = {"account_id": 1, "instrument_id": 2, "valuation_date": "2026-11-11"}
    meta_nvda = {"account_id": 1, "instrument_id": 3, "valuation_date": "2026-11-11"}

    person1_holdings = [
        ReportRow(("长桥", "股票", "SNXX", Decimal("30"), Decimal("4.255"), Decimal("127.65"), Decimal("24.94"), Decimal("748.15"), Decimal("0"), Decimal("748.15"), Decimal("-620.50"), Decimal("-0.8294"), None, Decimal("0.5")), meta_snxx),
        ReportRow(("长桥", "股票", "NVDA", Decimal("10"), Decimal("180"), Decimal("1800"), Decimal("100"), Decimal("1000"), Decimal("0"), Decimal("1000"), Decimal("800"), Decimal("0.8"), None, Decimal("0.5")), meta_nvda),
    ]
    person1_stocks = [
        ReportRow(("长桥", "股票", "SNXX", "持有中", Decimal("30"), Decimal("4.255"), Decimal("127.65"), Decimal("24.94"), Decimal("748.15"), Decimal("0"), Decimal("748.15"), Decimal("-620.50"), Decimal("-0.8294"), None, Decimal("0.5"), Decimal("0"), Decimal("0"), Decimal("-620.50")), meta_snxx),
        ReportRow(("长桥", "股票", "AAPL", "已清仓", Decimal("0"), Decimal("220"), Decimal("0"), None, Decimal("2000"), Decimal("2500"), Decimal("-500"), None, None, None, None, Decimal("500"), Decimal("0"), Decimal("500")), meta_aapl),
        ReportRow(("长桥", "股票", "NVDA", "持有中", Decimal("10"), Decimal("180"), Decimal("1800"), Decimal("100"), Decimal("1000"), Decimal("0"), Decimal("1000"), Decimal("800"), Decimal("0.8"), None, Decimal("0.5"), Decimal("0"), Decimal("0"), Decimal("800")), meta_nvda),
    ]
    person1_closed = [
        ReportRow(("长桥", "股票", "AAPL", Decimal("20"), Decimal("20"), Decimal("2000"), Decimal("2500"), Decimal("500"), 2, Decimal("100"), Decimal("125"), Decimal("220"), "2026-01-10", "2026-06-10"), meta_aapl),
    ]

    person2_holdings = [
        ReportRow(("长桥", "股票", "SNXX", Decimal("45"), Decimal("4.255"), Decimal("191.48"), Decimal("24.24"), Decimal("1090.99"), Decimal("0"), Decimal("1090.99"), Decimal("-899.51"), Decimal("-0.8245"), None, Decimal("0.5")), meta_snxx),
    ]
    person2_stocks = [
        ReportRow(("长桥", "股票", "SNXX", "持有中", Decimal("45"), Decimal("4.255"), Decimal("191.48"), Decimal("24.24"), Decimal("1090.99"), Decimal("0"), Decimal("1090.99"), Decimal("-899.51"), Decimal("-0.8245"), None, Decimal("0.5"), Decimal("0"), Decimal("0"), Decimal("-899.51")), meta_snxx),
        ReportRow(("长桥", "股票", "AAPL", "已清仓", Decimal("0"), Decimal("220"), Decimal("0"), None, Decimal("1000"), Decimal("900"), Decimal("100"), None, None, None, None, Decimal("-100"), Decimal("0"), Decimal("-100")), meta_aapl),
        ReportRow(("长桥", "股票", "NVDA", "已清仓", Decimal("0"), Decimal("180"), Decimal("0"), None, Decimal("1200"), Decimal("1500"), Decimal("-300"), None, None, None, None, Decimal("300"), Decimal("0"), Decimal("300")), meta_nvda),
    ]
    person2_closed = [
        ReportRow(("长桥", "股票", "AAPL", Decimal("10"), Decimal("10"), Decimal("1000"), Decimal("900"), Decimal("-100"), 2, Decimal("100"), Decimal("90"), Decimal("220"), "2026-02-01", "2026-05-01"), meta_aapl),
        ReportRow(("长桥", "股票", "NVDA", Decimal("15"), Decimal("15"), Decimal("1200"), Decimal("1500"), Decimal("300"), 2, Decimal("80"), Decimal("100"), Decimal("180"), "2026-01-05", "2026-03-10"), meta_nvda),
    ]

    broker_holdings = [
        ReportRow(("长桥", "股票", "SNXX", Decimal("75"), Decimal("4.255"), Decimal("319.125"), Decimal("24.52"), Decimal("1839.14"), Decimal("0"), Decimal("1839.14"), Decimal("-1520.01"), Decimal("-0.8265"), None, Decimal("1")), meta_snxx),
        ReportRow(("长桥", "股票", "NVDA", Decimal("10"), Decimal("180"), Decimal("1800"), Decimal("70"), Decimal("2200"), Decimal("1500"), Decimal("700"), Decimal("1100"), Decimal("1.57"), None, Decimal("1")), meta_nvda),
    ]
    broker_stocks = [
        ReportRow(("长桥", "股票", "SNXX", "持有中", Decimal("75"), Decimal("4.255"), Decimal("319.125"), Decimal("24.52"), Decimal("1839.14"), Decimal("0"), Decimal("1839.14"), Decimal("-1520.01"), Decimal("-0.8265"), None, Decimal("1"), Decimal("0"), Decimal("0"), Decimal("-1520.01")), meta_snxx),
        ReportRow(("长桥", "股票", "AAPL", "已清仓", Decimal("0"), Decimal("220"), Decimal("0"), None, Decimal("3000"), Decimal("3400"), Decimal("-400"), None, None, None, None, Decimal("400"), Decimal("0"), Decimal("400")), meta_aapl),
        ReportRow(("长桥", "股票", "NVDA", "持有中", Decimal("10"), Decimal("180"), Decimal("1800"), Decimal("70"), Decimal("2200"), Decimal("1500"), Decimal("700"), Decimal("1100"), Decimal("1.57"), None, Decimal("1"), Decimal("0"), Decimal("0"), Decimal("1100")), meta_nvda),
    ]
    broker_closed = [
        ReportRow(("长桥", "股票", "AAPL", Decimal("30"), Decimal("30"), Decimal("3000"), Decimal("3400"), Decimal("400"), 4, Decimal("100"), Decimal("113.33333333"), Decimal("220"), "2026-01-10", "2026-06-10"), meta_aapl),
    ]

    bundle = PeopleBundle(
        aggregate=_make_book(as_of, broker_holdings, broker_stocks, broker_closed),
        persons=(
            (1, "个人", _make_book(as_of, person1_holdings, person1_stocks, person1_closed)),
            (2, "拉科", _make_book(as_of, person2_holdings, person2_stocks, person2_closed)),
        ),
        broker=_make_book(as_of, broker_holdings, broker_stocks, broker_closed),
    )
    instruments = {
        1: {"symbol": "SNXX", "market": "US"},
        2: {"symbol": "AAPL", "market": "US"},
        3: {"symbol": "NVDA", "market": "US"},
    }
    return bundle, instruments


def test_choices_and_analyze_by_holding_status():
    bundle, instruments = _sample_bundle()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

    # 1. 默认持仓中：只包含 SNXX 和 NVDA
    holding_choices = choices(bundle, status="holding")
    assert [c["symbol"] for c in holding_choices] == ["NVDA", "SNXX"]
    res_nvda_holding = analyze(bundle, 3, instruments, {}, None, now, status="holding")
    assert not res_nvda_holding["is_closed"]
    assert [r["person_name"] for r in res_nvda_holding["rows"]] == ["个人"]

    # 2. 已清仓模式：只包含彻底清仓的 AAPL，展示每个人已清仓收益归属
    closed_choices = choices(bundle, status="closed")
    assert [c["symbol"] for c in closed_choices] == ["AAPL"]
    res_aapl = analyze(bundle, 2, instruments, {}, None, now, status="closed")
    assert res_aapl["is_closed"] is True
    assert [(r["person_name"], r["pnl"]) for r in res_aapl["rows"]] == [("个人", Decimal("500")), ("拉科", Decimal("-100"))]
    assert res_aapl["summary"]["pnl"] == Decimal("400")
    assert res_aapl["summary"]["buy_total"] == Decimal("3000")
    assert res_aapl["summary"]["sell_total"] == Decimal("3400")
    assert res_aapl["reconciliation"]["ok"] is True
    assert res_aapl["reconciliation"]["pnl_difference"] == Decimal("0")

    csv_closed = export_csv(res_aapl)
    assert "历史买入股数" in csv_closed
    assert "清仓收益" in csv_closed

    # 3. 全部模式：包含 SNXX、NVDA 和 AAPL（已清仓），且 NVDA 下同时展示持仓中的个人和已清仓的拉科
    all_choices = choices(bundle, status="all")
    assert [c["symbol"] for c in all_choices] == ["AAPL", "NVDA", "SNXX"]
    assert all_choices[0]["is_closed"] is True
    assert "已清仓" in all_choices[0]["label"]

    res_nvda_all = analyze(bundle, 3, instruments, {}, None, now, status="all")
    assert [(r["person_name"], r["quantity"], r["is_closed"], r["pnl"]) for r in res_nvda_all["rows"]] == [
        ("个人", Decimal("10"), False, Decimal("800")),
        ("拉科", Decimal("0"), True, Decimal("300")),
    ]


def test_ownership_page_status_switch_ui(qtbot):
    bundle, instruments = _sample_bundle()
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

    page = OwnershipAnalysisPage(None)
    qtbot.addWidget(page)
    page.bundle = bundle
    page.catalog = instruments
    page.quotes = {}
    page.session = None
    page.now = now

    # 默认状态为持仓中
    assert page.holding_status == "holding"
    page._populate_stock_combo(choices(bundle, status="holding"))
    page.redraw()
    assert page.table.headerItem().text(1) == OWNERSHIP_COLUMNS[1]

    # 切换到已清仓：下拉切为 AAPL，表头切为已清仓口径，卡片/明细显示清仓收益分配
    page.status_buttons["closed"].click()
    assert page.holding_status == "closed"
    assert [page.stock.itemData(i) for i in range(page.stock.count())] == [2]
    assert page.table.headerItem().text(1) == OWNERSHIP_CLOSED_COLUMNS[1]
    assert page.table.headerItem().text(8) == "清仓收益"
    assert "400.00" in page.cards[4].text()
    assert page.table.topLevelItem(0).text(0) == "个人"
    assert "500.00" in page.table.topLevelItem(0).text(8)
    assert page.table.topLevelItem(1).text(0) == "拉科"
    assert "-100.00" in page.table.topLevelItem(1).text(8)

    # 切换到全部：选中 NVDA 时同时展示持仓中的个人和已清仓的拉科
    page.status_buttons["all"].click()
    assert page.holding_status == "all"
    idx_nvda = page.stock.findData(3)
    assert idx_nvda >= 0
    page.stock.setCurrentIndex(idx_nvda)
    assert page.table.topLevelItem(0).text(0) == "个人"
    assert page.table.topLevelItem(1).text(0) == "拉科（已清仓）"


def test_ownership_api_supports_holding_status(monkeypatch):
    from portfolio.web import api_v1
    from portfolio.web.app import app

    bundle, instruments = _sample_bundle()
    monkeypatch.setattr(api_v1, "get_analytics_service", lambda: SimpleNamespace(calculate_people=lambda scope: bundle))
    monkeypatch.setattr(api_v1, "instrument_catalog", lambda conn: instruments, raising=False)
    monkeypatch.setattr(
        "portfolio.db.repositories.analytics.instrument_catalog",
        lambda conn: instruments,
    )
    monkeypatch.setattr(
        api_v1,
        "get_today_service",
        lambda: SimpleNamespace(quote_snapshot=lambda agg, inst, live, now: ("历史收盘参考", None, {})),
    )

    client = app.test_client()
    r_hold = client.get("/api/v1/ownership", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r_hold.status_code == 200
    d_hold = r_hold.get_json()
    assert d_hold["holding_status"] == "holding"
    assert [c["symbol"] for c in d_hold["choices"]] == ["NVDA", "SNXX"]

    r_closed = client.get("/api/v1/ownership?holding_status=closed", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r_closed.status_code == 200
    d_closed = r_closed.get_json()
    assert d_closed["holding_status"] == "closed"
    assert d_closed["is_closed"] is True
    assert [c["symbol"] for c in d_closed["choices"]] == ["AAPL"]
    assert [(r["person_name"], float(r["pnl"])) for r in d_closed["rows"]] == [("个人", 500.0), ("拉科", -100.0)]

    r_all = client.get("/api/v1/ownership?holding_status=all&instrument_id=3", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r_all.status_code == 200
    d_all = r_all.get_json()
    assert d_all["holding_status"] == "all"
    assert [r["person_name"] for r in d_all["rows"]] == ["个人", "拉科"]

