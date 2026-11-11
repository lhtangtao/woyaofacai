import sqlite3
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QWidget

from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.holdings_management_service import HoldingsManagementService, channel_label
from portfolio.ui.holdings_page import HoldingsPage


@pytest.fixture
def multi_broker_ledger(demo_path, monkeypatch):
    """构造包含 BBAE / FIRSTRADE / LONGBRIDGE / SCHWAB 四家券商持仓的测试账本。"""
    monkeypatch.setattr(HoldingsManagementService, "get_latest_quotes_map", staticmethod(lambda: {}))
    with sqlite3.connect(demo_path) as db:
        db.execute("DELETE FROM person_operations")
        db.execute("DELETE FROM broker_trades")
        db.execute("DELETE FROM broker_accounts")
        db.execute("DELETE FROM instruments")

        accounts = [
            (1, "BBAE", "LEGACY_DEFAULT", "BBAE", "USD", "Asia/Shanghai", "08:00"),
            (2, "FIRSTRADE", "LEGACY_DEFAULT", "FIRSTRADE", "USD", "America/New_York", "00:00"),
            (3, "LONGBRIDGE", "LEGACY_DEFAULT", "LONGBRIDGE", "USD", "Asia/Shanghai", "08:00"),
            (4, "SCHWAB", "LEGACY_DEFAULT", "SCHWAB", "USD", "America/New_York", "00:00"),
        ]
        db.executemany(
            "INSERT INTO broker_accounts(id,broker_code,account_key,display_name,base_currency,book_timezone,day_cutoff,enabled,source_record_id) "
            "VALUES(?,?,?,?,?,?,?,1,1)",
            accounts,
        )

        instruments = [
            (1, "US", "AAPL", "Apple", "USD", "STOCK", "正股"),
            (2, "US", "MSFT", "Microsoft", "USD", "STOCK", "正股"),
            (3, "US", "NVDA", "Nvidia", "USD", "STOCK", "正股"),
            (4, "US", "TQQQ", "ProShares", "USD", "ETF", "杠杆"),
        ]
        db.executemany(
            "INSERT INTO instruments(id,market,symbol,name,currency,asset_type,category) VALUES(?,?,?,?,?,?,?)",
            instruments,
        )

        # 每个券商持仓 1 只不同股票
        entries = [
            (1, 1, 1, "BUY", 10, 100, 2),  # BBAE -> AAPL
            (1, 2, 2, "BUY", 5, 200, 3),   # FIRSTRADE -> MSFT
            (1, 3, 3, "BUY", 8, 150, 4),   # LONGBRIDGE -> NVDA
            (1, 4, 4, "BUY", 20, 50, 5),   # SCHWAB -> TQQQ
        ]
        for n, (person, account, instrument, side, quantity, price, day) in enumerate(entries, 1):
            stamp = f"2026-01-{day:02d}"
            db.execute(
                """INSERT INTO person_operations
                (id,operation_key,person_id,account_id,instrument_id,operation_kind,side,business_date,book_time,sequence_no,
                 quantity_e8,price_e8,amount_e8,amount_basis,currency,link_status,source_record_id)
                VALUES(?,?,?,?,?,'MARKET_TRADE',?,?,?,?,?,?,?,'GROSS','USD','LEGACY_UNLINKED',1)""",
                (
                    n,
                    str(n),
                    person,
                    account,
                    instrument,
                    side,
                    stamp,
                    f"{stamp} 10:00:00",
                    n,
                    int(D(quantity) * 10**8),
                    price * 10**8,
                    int(D(quantity) * price * 10**8),
                ),
            )

    service = AnalyticsService(demo_path)
    return service, service.calculate(QueryScope(date(2026, 1, 9)))


def test_broker_summary_chinese_and_detail_filtering(multi_broker_ledger):
    """验证仪表板券商汇总中文显示及明细按中英文渠道精确筛选。"""
    service, analytics = multi_broker_ledger
    dash = HoldingsManagementService.get_dashboard_data(analytics)
    channels = [r["channel"] for r in dash["broker_summary"]]
    assert set(channels) == {"BBAE", "第一证券", "长桥", "嘉信"}

    expected = {
        ("BBAE", "BBAE"): ("BBAE", "AAPL"),
        ("FIRSTRADE", "第一证券"): ("第一证券", "MSFT"),
        ("LONGBRIDGE", "长桥"): ("长桥", "NVDA"),
        ("SCHWAB", "嘉信"): ("嘉信", "TQQQ"),
    }
    for (raw_code, zh_name), (exp_channel, exp_ticker) in expected.items():
        for query in (raw_code, zh_name):
            rows = HoldingsManagementService.get_holdings_detail_rows(
                analytics, status="持有中", broker=query, db_path=service.path
            )
            assert len(rows) == 1, f"query={query} 应只返回 1 条持仓，实际 {len(rows)}"
            assert rows[0]["channel"] == exp_channel
            assert rows[0]["ticker"] == exp_ticker


def test_mac_broker_table_click_and_chip_clear(multi_broker_ledger, qtbot):
    """验证 Mac 端点击券商汇总任意一行跳转明细页均能带上对应中文渠道筛选并可清除。"""
    service, analytics = multi_broker_ledger
    host = QWidget()
    host.service = service
    host.analytics_service = service
    host.accounts = SimpleNamespace(selected_ids=lambda: None, current_broker_name=lambda: "")
    host.quote_store = None
    host.info = SimpleNamespace(persons=(), instruments=())
    qtbot.addWidget(host)

    page = HoldingsPage(host)
    page.resize(1400, 800)
    host.show()
    page.show()
    page.set_analytics(analytics)

    # 仪表板券商表格中所有渠道列均为中文
    displayed_brokers = [
        r.values[0] for r in page.b_model.rows if not r.metadata.get("is_total") and r.values[0] != "合计"
    ]
    assert set(displayed_brokers) == {"BBAE", "第一证券", "长桥", "嘉信"}

    # 逐行点击券商汇总表跳转持仓明细，均能带入对应筛选
    for idx, row in enumerate(page.b_model.rows):
        if row.metadata.get("is_total") or row.values[0] == "合计":
            continue
        ch = row.metadata.get("channel") or row.values[0]
        page.switch_view_mode("dashboard")
        page._on_broker_table_clicked(page.b_model.index(idx, 0))
        assert page.stack.currentIndex() == 1
        assert page.broker_combo.currentText() == ch
        assert not page.active_broker_chip.isHidden()
        assert ch in page.active_broker_chip.text()
        assert len(page.detail_model.rows) == 1
        assert page.detail_model.rows[0].values[1] == ch

    # 测试通过原始英文 broker_code 调用 set_view 也能映射为中文并正确过滤
    for raw_code in ("FIRSTRADE", "LONGBRIDGE", "SCHWAB", "BBAE"):
        exp_zh = channel_label(raw_code)
        page.set_view("detail", dimension="broker", broker=raw_code, status="持有中")
        assert page.broker_combo.currentText() == exp_zh
        assert len(page.detail_model.rows) == 1
        assert page.detail_model.rows[0].values[1] == exp_zh

    # 点击筛选标签清除后恢复全部 4 条持仓
    page.active_broker_chip.click()
    assert page.broker_combo.currentIndex() == 0
    assert page.active_broker_chip.isHidden()
    assert len(page.detail_model.rows) == 4
