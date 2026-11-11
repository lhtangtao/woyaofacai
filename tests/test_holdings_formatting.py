from decimal import Decimal
from PySide6.QtCore import Qt

from portfolio.domain.analytics import ReportRow
from portfolio.ui.holdings_page import HoldingsPage, HoldingsTableModel


def test_holdings_table_model_omits_dollar_sign_for_usd():
    model = HoldingsTableModel()
    model.columns = ("Ticker", "持仓市值", "本金", "累计盈亏", "收益率")
    model.replace([
        ReportRow(("TQQQ", Decimal("20804.12"), Decimal("16673.45"), Decimal("4130.67"), Decimal("0.2477")), {}),
        ReportRow(("GGLL", Decimal("2733.28"), Decimal("-1771.00"), Decimal("-500.12"), Decimal("-0.1200")), {}),
    ])

    assert model.data(model.index(0, 1)) == "20,804.12"
    assert model.data(model.index(0, 2)) == "16,673.45"
    assert model.data(model.index(0, 3)) == "+4,130.67"
    assert model.data(model.index(0, 4)) == "+24.77%"
    assert model.data(model.index(1, 2)) == "-1,771.00"
    assert model.data(model.index(1, 3)) == "-500.12"


def test_holdings_page_disables_elide_on_summary_tables(qtbot):
    page = HoldingsPage()
    qtbot.addWidget(page)
    assert page.p_table.textElideMode() == Qt.TextElideMode.ElideNone
    assert page.b_table.textElideMode() == Qt.TextElideMode.ElideNone
    assert page.s_table.textElideMode() == Qt.TextElideMode.ElideNone
