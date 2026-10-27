import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from portfolio.domain.models import Ledger, QueryScope, TradeFilters
from portfolio.services.trades_service import TradesService
from portfolio.ui.main_window import MainWindow


def scope(**kwargs):
    return QueryScope(date(2026, 1, 10), **kwargs)


def opened(qtbot, path):
    w = MainWindow(settings=False)
    w.navigation.setCurrentRow(0)
    qtbot.addWidget(w)
    w.show()
    w.open_database(path)
    qtbot.waitUntil(lambda: w.current_page is not None)
    return w


def settled(qtbot, w):
    qtbot.waitUntil(lambda: w.current_page is not None and w.current_page.scope == w.make_scope() and w.current_page.filters == w.make_trade_filters())


def test_stats_cover_all_matches_and_never_union_ledgers(demo_path):
    service = TradesService(demo_path)
    first = service.get_trades(scope(), size=1)
    second = service.get_trades(scope(), size=1, page=1)
    assert len(first.rows) == 1 and first.stats == second.stats
    assert (first.stats.matched, first.stats.buys, first.stats.sells) == (3, 2, 1)
    broker = service.get_trades(scope(ledger=Ledger.BROKER))
    assert (broker.stats.matched, broker.stats.buys, broker.stats.sells) == (2, 1, 1)
    assert service.get_trades(scope(person_ids=(2,))).stats.matched == 1


def test_independent_reference_and_reversed_switches(demo_path):
    service = TradesService(demo_path)
    query = scope(ledger=Ledger.BROKER)
    assert service.get_trades(query, filters=TradeFilters(include_reversed=True, include_reference=False)).total == 3
    assert service.get_trades(query, filters=TradeFilters(include_reversed=False, include_reference=True)).total == 3
    both = service.get_trades(query, filters=TradeFilters(include_reversed=True, include_reference=True))
    assert both.total == 4
    assert {r.metadata["record_state"] for r in both.rows} == {"ACTIVE", "REVERSED"}


def test_review_counts_rows_once_and_follow_filters(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET link_status='REVIEW_REQUIRED' WHERE id IN (1,3)")
        db.execute("UPDATE raw_records SET parse_status='REVIEW_REQUIRED' WHERE id=2")
    service = TradesService(demo_path)
    all_rows = service.get_trades(scope(), size=1)
    assert all_rows.stats.review == 2  # operation 3 matches both problems, still one row
    sell = service.get_trades(scope(), filters=TradeFilters(side="SELL"))
    assert (sell.stats.matched, sell.stats.sells, sell.stats.buys, sell.stats.review) == (1, 1, 0, 1)
    assert service.get_trades(scope(person_ids=(2,))).stats.review == 0


def test_transfer_counts_are_not_buys_or_sells(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE person_operations SET side='TRANSFER_IN',operation_kind='EXTERNAL_TRANSFER' WHERE id=1")
    stats = TradesService(demo_path).get_trades(scope()).stats
    assert (stats.matched, stats.buys, stats.sells) == (3, 1, 1)


def test_file_and_link_filters_literal_and_fees_unallocated(demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE import_batches SET source_path='sample_100%.csv'")
        db.execute("UPDATE broker_trades SET fees_e8=123456789 WHERE id=1")
    service = TradesService(demo_path)
    assert service.get_trades(scope(), filters=TradeFilters(source_file="100%")).total == 3
    assert service.get_trades(scope(), filters=TradeFilters(source_file="_1000%")).total == 0
    assert service.get_trades(scope(), filters=TradeFilters(link_status="REVIEW_REQUIRED")).total == 0
    person = service.get_trades(scope(person_ids=(2,))).rows[0]
    assert person.metadata["fees_e8"] is None and person.metadata["broker_fees_e8"] == 123456789
    broker = service.get_trades(scope(ledger=Ledger.BROKER), filters=TradeFilters(side="BUY")).rows[0]
    assert broker.metadata["fees_e8"] == 123456789
    assert len(person.values) == 16


@pytest.mark.parametrize("filters", [{"side": "DROP"}, {"link_status": "DROP"}, {"include_reversed": 1}, {"include_reference": "true"}])
def test_invalid_filters_are_rejected(filters):
    with pytest.raises(ValueError):
        TradeFilters(**filters)


def test_raw_filters_and_sort_do_not_compute_financial_history(qtbot, demo_path, monkeypatch):
    w = opened(qtbot, demo_path)
    calls = []
    monkeypatch.setattr(w.analytics_service, "calculate", lambda *args, **kwargs: calls.append(args))
    assert w.analytics is None and "analytics" not in w.jobs
    assert [c.value.text() for c in w.trade_cards] == ["3", "2", "1", "0"]
    w.side.setCurrentIndex(w.side.findData("SELL"))
    settled(qtbot, w)
    assert [c.value.text() for c in w.trade_cards][:3] == ["1", "0", "1"]
    w.sort(5)
    settled(qtbot, w)
    assert not calls and "analytics" not in w.jobs
    w.clear_filter("side")
    settled(qtbot, w)
    assert w.current_page.total == 3
    w.close()


def test_drawer_tabs_masking_copy_and_close(qtbot, demo_path):
    w = opened(qtbot, demo_path)
    assert w.drawer.isHidden() and w.advanced_panel.isHidden()
    w.table.setCurrentIndex(w.table.model().index(0, 0))
    qtbot.waitUntil(lambda: "已遮盖" in w.detail.toPlainText())
    assert w.drawer.tabs.currentIndex() == 0 and not w.detail.isVisible()
    assert "demo-secret-not-a-real-key" not in w.detail.toPlainText()
    w.drawer.tabs.setCurrentIndex(2)
    assert w.detail.isVisible()
    w.sensitive.setChecked(True)
    qtbot.waitUntil(lambda: "demo-secret-not-a-real-key" in w.detail.toPlainText())
    w.drawer.dismiss()
    assert w.drawer.isHidden()
    w.close()


def test_history_restores_filters_actual_route_and_advanced_values(qtbot, demo_path):
    w = opened(qtbot, demo_path)
    w.side.setCurrentIndex(w.side.findData("SELL"))
    settled(qtbot, w)
    w.navigation.setCurrentRow(1)
    w.side.setCurrentIndex(w.side.findData("BUY"))
    settled(qtbot, w)
    w.navigation.setCurrentRow(13)
    w.move_history(-1)
    settled(qtbot, w)
    assert w.pages.currentIndex() == 1 and w.side.currentData() == "BUY"
    w.move_history(-1)
    settled(qtbot, w)
    assert w.pages.currentIndex() == 0 and w.side.currentData() == "SELL"
    w.move_history(1)
    settled(qtbot, w)
    assert w.pages.currentIndex() == 1
    w.close()


def test_reset_single_query_source_badge_and_column_precision(qtbot, demo_path, monkeypatch):
    w = opened(qtbot, demo_path)
    w.advanced_button.setChecked(True)
    w.side.setCurrentIndex(1)
    w.include_reversed.setChecked(True)
    settled(qtbot, w)
    assert w.advanced_panel.isVisible()
    calls = []
    get = w.service.get_trades
    def tracked(*args, **kwargs):
        calls.append(1)
        return get(*args, **kwargs)
    monkeypatch.setattr(w.service, "get_trades", tracked)
    w.reset_filters()
    settled(qtbot, w)
    qtbot.wait(350)
    assert len(calls) == 1 and w.current_page.total == 3
    assert str(demo_path) not in w.source_label.text() and str(demo_path) in w.source_label.toolTip()
    assert w.table.verticalHeader().defaultSectionSize() == 40
    assert w.table.horizontalHeader().height() == 42
    w.table.setCurrentIndex(w.table.model().index(0, 7))
    w.table.copy_selection()
    assert "480" in QApplication.clipboard().text()
    assert w.table.model().data(w.table.model().index(0, 7)) == "480.00"
    assert w.table.model().value(w.current_page.rows[0], 7) == Decimal(480)
    w.close()


def test_theme_navigation_compact_and_window_sizes(qtbot, demo_path):
    w = opened(qtbot, demo_path)
    assert w.theme == "light"
    for width, height in ((1280, 800), (1440, 900), (1920, 1080)):
        w.resize(width, height)
        qtbot.wait(15)
        assert w.width() == width and w.height() == height
        assert w.search.width() >= 120 and w.table.height() >= 320
        assert w.filters_widget.geometry().right() <= w.centralWidget().width()
    before = w.current_page
    w._set_theme("dark")
    assert w.design_tokens.panel == "#1C2533" and w.current_page is before
    w.green_profit.setChecked(False)
    assert w.design_tokens.profit == "#DC2626"
    w.navigation.toggle()
    assert w.navigation.width() == 64
    w.navigation.toggle()
    assert w.navigation.width() == 212
    w.close()


def test_analytics_failure_keeps_raw_query_and_export_available(qtbot, demo_path):
    w = opened(qtbot, demo_path)
    page = w.current_page
    w._job_error("analytics", "财务数据暂不可用")
    assert w.current_page is page and w.export_button.isEnabled()
    assert w.table.model().rowCount() == page.total
    w._job_error("provenance", "来源暂不可用")
    assert w.current_page is page
    w.close()


def test_drill_back_restores_original_scope_and_metric_target(qtbot, demo_path):
    w = opened(qtbot, demo_path)
    w.navigation.setCurrentRow(2)
    qtbot.waitUntil(lambda: w.analytics is not None)
    original = w.make_scope()
    w.drill_chart("symbol", "DEMO")
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.instrument_ids == (1,))
    assert w.pages.currentIndex() == 10
    w.move_history(-1)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope == original)
    assert w.pages.currentIndex() == 2
    w.drill_chart("metric", "3")
    assert w.performance_page.selector.currentText() == "清仓收益"
    assert w.heading.text() == "清仓收益"
    w.close()


def test_search_resets_pagination_and_cached_finance_not_recomputed(qtbot, demo_path, monkeypatch):
    w = opened(qtbot, demo_path)
    # Match the asset pages' fixed period so this test isolates list-only changes.
    from PySide6.QtCore import QDate
    w.start.setDate(QDate(2025, 1, 1))
    w.start_enabled.setChecked(True)
    settled(qtbot, w)
    w.page_number = 2
    w.search.setText("DEMO")
    qtbot.waitUntil(lambda: w.current_page is not None and w.current_page.scope.search == "DEMO")
    assert w.page_number == 0 and w.current_page.total == 3
    w.navigation.setCurrentRow(2)
    qtbot.waitUntil(lambda: w.analytics is not None)
    analytics = w.analytics
    w.navigation.setCurrentRow(0)
    def unexpected(*args, **kwargs):
        raise AssertionError("list filter or sort recomputed history")
    monkeypatch.setattr(w.analytics_service, "calculate", unexpected)
    w.side.setCurrentIndex(w.side.findData("SELL"))
    settled(qtbot, w)
    w.sort(5)
    settled(qtbot, w)
    assert w.analytics is analytics
    w.close()


def test_ticker_type_suggest_select_and_clear(qtbot, demo_path):
    with sqlite3.connect(demo_path) as db:
        db.execute("UPDATE instruments SET symbol='AAOX' WHERE id=1")
        db.execute("INSERT INTO instruments(id,market,symbol,currency,asset_type,category) VALUES(2,'US','AAOI','USD','STOCK','正股')")
    w = opened(qtbot, demo_path)
    editor = w.stock.lineEdit()
    editor.setFocus()
    qtbot.keyClicks(editor, "aa")
    popup = w.stock.completer().popup()
    qtbot.waitUntil(lambda: popup.isVisible())
    matches = [popup.model().index(i, 0).data() for i in range(popup.model().rowCount())]
    assert matches == ["AAOI · US", "AAOX · US"]
    assert w.current_page.scope.instrument_ids is None  # typing alone is not a committed selection
    chosen = popup.model().index(1, 0)
    qtbot.mouseClick(popup.viewport(), Qt.MouseButton.LeftButton, pos=popup.visualRect(chosen).center())
    qtbot.waitUntil(lambda: w.current_page is not None and w.current_page.scope.instrument_ids == (1,))
    assert w.stock.currentData() == 1 and w.current_page.total == 3
    editor.setFocus()
    editor.selectAll()
    qtbot.keyClick(editor, Qt.Key.Key_Backspace)
    qtbot.waitUntil(lambda: w.current_page is not None and w.current_page.scope.instrument_ids is None)
    assert w.stock.currentData() is None and w.current_page.total == 3
    w.close()


def test_ticker_enter_exact_and_unknown_never_adds_security(qtbot, demo_path):
    w = opened(qtbot, demo_path)
    editor = w.stock.lineEdit()
    editor.setFocus()
    qtbot.keyClicks(editor, "demo")
    qtbot.keyClick(editor, Qt.Key.Key_Down)
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: w.current_page is not None and w.current_page.scope.instrument_ids == (1,))
    count = w.stock.count()
    editor.selectAll()
    qtbot.keyClicks(editor, "NO_SUCH_TICKER")
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.wait(50)
    assert w.stock.count() == count and w.stock.currentData() == 1
    assert w.current_page.scope.instrument_ids == (1,)
    w.reset_filters()
    settled(qtbot, w)
    assert not editor.text() and w.stock.currentData() is None
    w.close()
