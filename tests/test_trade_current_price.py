from decimal import Decimal
from portfolio.paths import DATABASE_PATH
from portfolio.services.trades_service import TradesService
from portfolio.domain.models import QueryScope, Ledger
from portfolio.ui.trade_table import TradeTable, TradeModel, COLUMNS
from PySide6.QtCore import Qt


def test_trades_service_attaches_current_price():
    svc = TradesService(DATABASE_PATH)
    from datetime import date
    scope = QueryScope(as_of=date(2026, 10, 27), ledger=Ledger.PERSON)
    page = svc.get_trades(scope, page=0, size=10)
    assert page.rows, "Should retrieve trade rows"
    # Verify that rows have 'current_price' in metadata
    has_price = [r.metadata.get("current_price") for r in page.rows]
    assert any(p is not None for p in has_price), "At least some trades should resolve current market prices"
    for r in page.rows:
        cp = r.metadata.get("current_price")
        if cp is not None:
            assert isinstance(cp, Decimal) and cp > 0


def test_trade_table_columns_and_visual_order(qtbot):
    table = TradeTable()
    model = table.finance_model
    # COLUMNS contains '当前价($)'
    assert "当前价($)" in COLUMNS
    col_idx = COLUMNS.index("当前价($)")
    assert col_idx == 20
    assert model.headerData(20, Qt.Orientation.Horizontal) == "当前价($)"

    # Check visual order: visualIndex(20) must be directly before visualIndex(6) ('成交均价($)')
    hdr = table.horizontalHeader()
    idx_curr = hdr.visualIndex(20)
    idx_exec = hdr.visualIndex(6)
    assert idx_curr == idx_exec - 1, f"当前价 (visual {idx_curr}) 必须紧邻在成交均价 (visual {idx_exec}) 前面"
