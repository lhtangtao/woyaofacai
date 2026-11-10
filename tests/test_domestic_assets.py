from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from portfolio.domestic.alipay import Document, finish_transaction, parse_transactions
from portfolio.domestic.calculate import rebuild
from portfolio.domestic.query import read_transaction, read_view
from portfolio.domestic.service import publish
from portfolio.domestic.storage import CALC_FILE, RAW_FILE, connect


def raw_trade(order, code, kind, day, shares, app_shares="/", cash="10"):
    return [order, f"{day:%Y/%m/%d} 09:30", kind, code, "", code, "/", app_shares, cash, shares, "0.00",
            f"{day:%Y/%m/%d} 00:00"]


def docs():
    proof = Document("opening_asset", "opening.pdf", b"opening", 2, [
        dict(id="snapshot", snapshot_date="2021-11-11", fund_code="000001", fund_name="A",
             total_shares="100.00", unit_nav="2.50", unit_nav_date="2021-11-10",
             market_value="250.00", source_page=2, raw_data="[]")
    ], [], declared_value="250.00")
    edge = raw_trade("1"*32, "000001", "定投买入", date(2021, 11, 11), "5.00")
    sell = raw_trade("2"*32, "000001", "用户跨\nTA转换", date(2021, 11, 12), "12.30", "12.30")
    buy = raw_trade("2"*32, "000002", "用户跨\nTA转换", date(2021, 11, 12), "4.50")
    first_half, second_half = sell.copy(), [""] * 12
    first_half[0], second_half[0] = sell[0][:8], sell[0][8:]
    pages = [SimpleNamespace(extract_tables=lambda: []),
             SimpleNamespace(extract_tables=lambda: [[edge, first_half]]),
             SimpleNamespace(extract_tables=lambda: [[second_half, buy]])]
    transactions = Document("transaction", "trades.pdf", b"trades", 3, [], [])
    parse_transactions(transactions, pages)
    return proof, transactions


def test_cross_page_transfer_and_snapshot_boundary(tmp_path):
    proof, transactions = docs()
    assert not transactions.issues
    assert len(transactions.rows) == 3 and transactions.rows[1]["source_page"] == 2
    report = publish([proof, transactions], root=tmp_path)
    assert report["calculation"]["transfers"] == 1
    assert report["calculation"]["funds_rebuilt"] == 2
    positions = read_view("positions", as_of="2021-11-12", root=tmp_path)
    by_code = {row["fund_code"]: row for row in positions["rows"]}
    assert Decimal(by_code["000001"]["holding_shares"]) == Decimal("87.70")
    assert by_code["000001"]["valuation_status"] == "缺少净值"
    assert by_code["000002"]["holding_shares"] is None
    original = read_view("positions", as_of="2021-11-11", root=tmp_path)["rows"][0]
    assert original["holding_shares"] == "100.00" and original["nav_date"] == "2021-11-10"
    assert original["market_value"] == "250.00"
    with connect(tmp_path / CALC_FILE, readonly=True) as calc:
        summary = calc.execute("SELECT buy_amount,sell_amount,transfer_out_shares FROM calc_fund_monthly_summary WHERE fund_code='000001'").fetchone()
        assert tuple(map(Decimal, summary)) == (Decimal(0), Decimal(0), Decimal("12.30"))
    view = read_view("transactions", root=tmp_path, fund="000002", size=1)
    assert view["total"] == 1 and view["rows"][0]["normalized"] == "买入（转换转入）"
    trace = read_transaction(transactions.rows[1]["id"], root=tmp_path)
    assert trace["source_filename"] == "trades.pdf"
    assert (tmp_path / "imports" / "alipay" / f"{transactions.digest}.pdf").read_bytes() == b"trades"


def test_repeat_import_and_rebuild_are_safe(tmp_path):
    documents = docs()
    publish(documents, root=tmp_path)
    result = publish(documents, root=tmp_path)
    assert [batch["status"] for batch in result["imports"]] == ["already_imported"] * 2
    assert result["calculation"]["status"] == "unchanged"
    with connect(tmp_path / RAW_FILE, readonly=True) as raw:
        assert raw.execute("SELECT COUNT(*) FROM raw_alipay_fund_transactions").fetchone()[0] == 3
    assert list((tmp_path / "backups").glob(f"*/{RAW_FILE}"))
    before = read_view("positions", root=tmp_path)["rows"]
    (tmp_path / CALC_FILE).unlink()
    assert rebuild(tmp_path, full=True)["status"] == "complete"
    after = read_view("positions", root=tmp_path)["rows"]
    assert [row["holding_shares"] for row in before] == [row["holding_shares"] for row in after]


def test_alipay_confirmed_zero_openings_are_limited_to_the_named_funds_and_date(tmp_path):
    proof, transactions = docs()
    day = date(2021, 11, 12)
    for index, fund in enumerate(("002611", "012414", "019172"), start=3):
        row = raw_trade(str(index) * 32, fund, "用户买入", day, "3.50")
        finish_transaction(transactions, [(4, index, row)])
    publish([proof, transactions], root=tmp_path)
    opening = {row["fund_code"]: row["holding_shares"] for row in
               read_view("positions", root=tmp_path, as_of="2021-11-11")["rows"]}
    closing = {row["fund_code"]: row["holding_shares"] for row in
               read_view("positions", root=tmp_path, as_of=day.isoformat())["rows"]}
    for fund in ("002611", "012414", "019172"):
        assert opening[fund] == "0"
        assert closing[fund] == "3.50"
    assert opening["000001"] == "100.00"
    assert opening["000002"] is None and closing["000002"] is None

    other_snapshot = tmp_path / "other_snapshot"
    proof.rows[0]["snapshot_date"] = "2021-11-10"
    publish([proof, transactions], root=other_snapshot)
    assert read_view("positions", root=other_snapshot, as_of=day.isoformat(), fund="002611")["rows"][0]["holding_shares"] is None


def test_non_admin_cannot_read_personal_domestic_ledger():
    flask = pytest.importorskip("flask")
    from portfolio.web.domestic_api import domestic_api
    from portfolio.web.security.auth_context import AuthContext, LAN_VIEWER_CAPABILITIES

    app = flask.Flask(__name__)
    app.register_blueprint(domestic_api)
    with app.test_client() as client:
        assert client.get("/api/v1/domestic/positions").status_code == 401
    lan = AuthContext("DIRECT_PHYSICAL_LAN", "LAN_VIEWER", None, None, None, None, LAN_VIEWER_CAPABILITIES, False)
    assert not lan.has_capability("users:manage")
