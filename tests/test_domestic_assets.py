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


def tiantian_statement(content=b"tiantian"):
    from portfolio.domestic.tiantian import DIVIDEND_HEADER, TRADE_HEADER, parse_pages

    day = "2015-09-16"
    row = [day, "000001", "示例基金", "强减", "成功", "10.30", "0.00", "0.00", "0.0000", "示例银行 | 1234"]
    dividend = [day, "000001", "示例基金", "红利再投资", "--", "0.04", "1.0000"]
    pages = [
        SimpleNamespace(extract_text=lambda: "天天基金对账单 历史交易明细(2015-01-01~2015-12-31)",
                        extract_tables=lambda: [[TRADE_HEADER, row]]),
        SimpleNamespace(extract_tables=lambda: [[row], [DIVIDEND_HEADER, dividend]]),
    ]
    return parse_pages("statement.pdf", content, pages)


def test_tiantian_migrates_legacy_and_leaves_calc_and_alipay_scope_unchanged(tmp_path):
    from portfolio.domestic.fund_transactions import import_tiantian, preview_tiantian, verify_alipay_migration

    publish(docs(), root=tmp_path)
    calc_before = (tmp_path / CALC_FILE).read_bytes()
    with connect(tmp_path / RAW_FILE) as raw:
        old_rows = [tuple(row) for row in raw.execute("SELECT * FROM raw_alipay_fund_transactions ORDER BY id")]
        raw.executescript("""
            DROP TRIGGER raw_mirror_alipay_trade;
            DROP TABLE raw_fund_transaction_sources;
            DROP TABLE raw_fund_transactions;
            DROP TABLE raw_fund_source_files;
            DROP TABLE raw_schema_migrations;
        """)
    statement = tiantian_statement()
    raw_before = (tmp_path / RAW_FILE).read_bytes()
    preview = preview_tiantian(tmp_path, [statement])[0]
    assert preview["records"] == 3 and preview["review"] == 2 and not preview["issues"]
    assert preview["same_fields_retained"] == 1 and preview["duplicate_rows"] == 0
    assert (tmp_path / RAW_FILE).read_bytes() == raw_before
    result = import_tiantian([statement], root=tmp_path)
    assert result["verified_alipay"] == 3 and result["batches"][0]["inserted"] == 3
    assert (tmp_path / CALC_FILE).read_bytes() == calc_before
    assert read_view("transactions", root=tmp_path)["total"] == 3
    assert read_view("imports", root=tmp_path)["total"] == 2
    assert not read_view("positions", root=tmp_path)["stale"]
    assert rebuild(tmp_path)["status"] == "unchanged"
    with connect(tmp_path / RAW_FILE, readonly=True) as raw:
        assert verify_alipay_migration(raw) == 3
        assert [tuple(row) for row in raw.execute("SELECT * FROM raw_alipay_fund_transactions ORDER BY id")] == old_rows
        rows = raw.execute("""SELECT source_page, order_id, confirmed_amount, parse_status
            FROM raw_fund_transactions WHERE account_id='TIANTIAN_FUND'
            ORDER BY source_page,source_record_no""").fetchall()
        assert [tuple(row) for row in rows] == [(1, None, "0.00", "待核对"), (2, None, "0.00", "待核对"),
                                                (2, None, None, "正常")]
    assert list((tmp_path / "backups").glob(f"*/{RAW_FILE}"))


def test_tiantian_deduplicates_repeat_and_overlap_with_source_trace(tmp_path):
    from portfolio.domestic.fund_transactions import import_tiantian, preview_tiantian

    statement = tiantian_statement()
    overlap = tiantian_statement(b"different PDF bytes")
    import_tiantian([statement], root=tmp_path)
    assert preview_tiantian(tmp_path, [statement])[0]["already_imported"]
    assert import_tiantian([statement], root=tmp_path)["batches"][0]["status"] == "already_imported"
    assert preview_tiantian(tmp_path, [overlap])[0]["duplicate_rows"] == 3
    result = import_tiantian([overlap], root=tmp_path)["batches"][0]
    assert (result["inserted"], result["duplicates"], result["overlapping_batches"]) == (0, 3, 1)
    with connect(tmp_path / RAW_FILE, readonly=True) as raw:
        assert raw.execute("SELECT COUNT(*) FROM raw_fund_transactions").fetchone()[0] == 3
        assert raw.execute("SELECT COUNT(*) FROM raw_fund_transaction_sources").fetchone()[0] == 6
        assert raw.execute("SELECT COUNT(*) FROM raw_import_batches").fetchone()[0] == 2
    assert not (tmp_path / CALC_FILE).exists()


def test_holdings_valuation_uses_query_date_and_distinguishes_uncovered_position(tmp_path):
    from portfolio.domestic.holdings_query import holdings
    from portfolio.domestic.nav_collection import SCHEMA

    proof, transactions = docs()
    publish([proof, transactions], root=tmp_path)
    with connect(tmp_path / CALC_FILE) as calc:
        calc.execute("INSERT INTO calc_alipay_statement_coverage VALUES (?,?,?)",
                     (transactions.digest, "2021-11-11", "2026-11-11"))
    with connect(tmp_path / RAW_FILE) as raw:
        raw.executescript(SCHEMA)
        raw.execute("""INSERT INTO raw_fund_nav_daily
            (fund_code,nav_date,source,unit_nav,accumulated_nav,fetched_at,raw_payload,field_status)
            VALUES ('000001','2026-11-10','AKShare/Eastmoney','1.5','99','x','{}','{}')""")
    rebuild(tmp_path)
    row = holdings("ALIPAY_FUND", "000001", root=tmp_path, valuation_date="2026-11-11")["rows"][0]
    assert (row["position_as_of"], row["valuation_date"], row["nav_date"]) == (
        "2026-11-11", "2026-11-11", "2026-11-10")
    assert Decimal(row["market_value"]) == Decimal("87.70") * Decimal("1.5")
    pending = holdings("ALIPAY_FUND", "000001", root=tmp_path, valuation_date="2026-11-12")["rows"][0]
    assert pending["shares"] is None and pending["market_value"] is None
    assert pending["last_confirmed_shares"] == "87.70"

    from portfolio.domestic.position_confirmation import SCHEMA as CONFIRMATION_SCHEMA

    with connect(tmp_path / CALC_FILE) as calc:
        calc.execute(CONFIRMATION_SCHEMA)
        calc.executemany("INSERT INTO calc_fund_position_confirmations VALUES (?,?,?,?,NULL)", (
            ("ALIPAY_FUND", "000001", "2026-11-12", "持有中"),
            ("ALIPAY_FUND", "000002", "2026-11-12", "已清仓"),
        ))
    rows = {r["fund_code"]: r for r in holdings(root=tmp_path, valuation_date="2026-11-12")["rows"]}
    assert rows["000001"]["status"] == "持有中" and rows["000001"]["shares"] == "87.70"
    assert Decimal(rows["000001"]["market_value"]) == Decimal("87.70") * Decimal("1.5")
    closed = rows["000002"]
    assert closed["status"] == "已清仓" and closed["market_value"] is None and closed["shares"] is None
    assert closed["last_confirmed_status"] == "无法确认" and closed["review_reason"]
    assert closed["confirmation_note"] == "用户确认；历史交易或收益尚未完整核算"
    historical = holdings("ALIPAY_FUND", "000002", root=tmp_path, valuation_date="2026-11-11")["rows"][0]
    assert historical["status"] == "份额待核对" and historical["user_confirmation_date"] is None


def test_holdings_preserve_confirmed_close_across_gap_and_reopen(tmp_path):
    from portfolio.domestic.holdings_query import holdings
    from portfolio.domestic.fund_transactions import SCHEMA as RAW_SCHEMA
    from portfolio.domestic.tiantian_positions import SCHEMA
    from portfolio.domestic.tiantian_snapshot import SCHEMA as SNAPSHOT_SCHEMA

    proof, transactions = docs()
    publish([proof, transactions], root=tmp_path)
    account = "TIANTIAN_TEST"
    code = "000001"
    with connect(tmp_path / RAW_FILE) as raw:
        raw.executescript(RAW_SCHEMA)
        raw.execute("INSERT INTO raw_asset_accounts VALUES (?,?,?,?,?,?)",
                    (account, "对账账户", "天天基金", "FUND", "CNY", "2026-11-12"))
    with connect(tmp_path / CALC_FILE) as calc:
        calc.executescript(SCHEMA)
        calc.execute("INSERT INTO calc_tiantian_run VALUES (1,'test','test','2026-11-12')")
        calc.execute("INSERT INTO calc_tiantian_funds VALUES (?,?,?,?,?,?,?,?)",
                     (account, code, "2026-11-06", "2", "对账单缺档 2026-11-04", "2", "0", "0"))
        calc.executemany("INSERT INTO calc_tiantian_positions VALUES (?,?,?,?)", (
            (account, code, "2026-11-01", "3"),
            (account, code, "2026-11-02", "0.00"),
            (account, code, "2026-11-03", "0"),
            (account, code, "2026-11-04", None),
            (account, code, "2026-11-05", "1"),
            (account, code, "2026-11-06", "2"),
        ))
        calc.executescript(SNAPSHOT_SCHEMA)
        calc.executemany("INSERT INTO calc_tiantian_statement_positions VALUES (?,?,?,'测试基金',NULL,?,NULL,NULL,'test')",
                         ((account, "2026-11-02", code, "0"), (account, "2026-11-05", code, "1")))

    def row(day):
        return holdings(account, code, root=tmp_path, valuation_date=day)["rows"][0]

    closed = row("2026-11-03")
    assert closed["current_position_status"] == "已清仓" and closed["market_value"] == "0"
    assert closed["last_confirmed_close_date"] == "2026-11-02" and closed["review_reason"] is None
    gap = row("2026-11-04")
    assert gap["status"] == "历史已清仓 · 当前待核对"
    assert gap["position_status"] == "历史交易缺档"
    assert gap["shares"] is None and gap["market_value"] is None and gap["last_confirmed_shares"] == "0"
    from portfolio.domestic.position_confirmation import SCHEMA as CONFIRMATION_SCHEMA

    with connect(tmp_path / CALC_FILE) as calc:
        calc.execute(CONFIRMATION_SCHEMA)
        calc.execute("INSERT INTO calc_fund_position_confirmations VALUES (?,?,?,'已清仓',NULL)",
                     (account, code, "2026-11-04"))
    acknowledged = row("2026-11-04")
    assert acknowledged["status"] == "已清仓" and acknowledged["market_value"] is None
    assert acknowledged["last_confirmed_shares"] == "0" and "缺档" in acknowledged["review_reason"]
    reopened = row("2026-11-06")
    assert reopened["current_position_status"] == "持有中" and reopened["shares"] == "2"
    assert reopened["last_confirmed_close_date"] is None and reopened["nav_status"] == "净值缺失"
