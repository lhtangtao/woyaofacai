from decimal import Decimal

from portfolio.domain.analytics import Report, ReportRow
from portfolio.ui.report_page import ReportPage


def test_stock_status_combines_search_sort_and_refresh_without_affecting_other_reports(qtbot):
    page = ReportPage(('股票汇总', '当前持仓'))
    qtbot.addWidget(page)
    page.show()
    report = Report('股票汇总', ('Ticker', '当前状态', '当前市值($)'), tuple(ReportRow(values) for values in (
        ('AAA', '持有中', Decimal(10)), ('AAB', '已清仓', Decimal(0)),
        ('AAC', '持有中', Decimal(30)), ('BBB', '持有中', Decimal(20)),
        ('CCC', '负仓异常', None), ('DDD', '成交待核对', None))))
    other = Report('当前持仓', ('Ticker', '当前市值($)'), (ReportRow(('AAB', Decimal(5))),))
    page.set_reports({'股票汇总': report, '当前持仓': other})
    assert not page.stock_status_filter.isHidden()
    assert page.stock_status_filter.currentData() == '持有中'
    assert len(page.sorted_rows) == 3
    assert all(row.values[1] == '持有中' for row in page.sorted_rows)
    page.stock_status_filter.setCurrentIndex(page.stock_status_filter.findData('持有中'))
    page.local_search.setText('AA')
    page.sort_column(2, True)
    assert [r.values[0] for r in page.sorted_rows] == ['AAC', 'AAA']
    page.set_reports({'股票汇总': report, '当前持仓': other})
    assert [r.values[0] for r in page.sorted_rows] == ['AAC', 'AAA']
    page.selector.setCurrentText('当前持仓')
    assert page.stock_status_filter.isHidden()
    assert [r.values[0] for r in page.sorted_rows] == ['AAB']
    page.selector.setCurrentText('股票汇总')
    page.local_search.clear()
    page.stock_status_filter.setCurrentIndex(page.stock_status_filter.findData('负仓异常'))
    assert [r.values[0] for r in page.sorted_rows] == ['CCC']
    page.stock_status_filter.setCurrentIndex(0)
    assert len(page.sorted_rows) == 6
    assert len(report.rows) == 6  # Filtering does not mutate the report.


def test_stock_status_filters_before_pagination_and_export(qtbot, monkeypatch, tmp_path):
    from portfolio.ui import report_page
    page = ReportPage(('股票汇总',))
    qtbot.addWidget(page)
    report = Report('股票汇总', ('Ticker', '当前状态'), tuple(ReportRow((str(i), '持有中' if i % 2 else '已清仓')) for i in range(500)))
    page.set_reports({'股票汇总': report})
    page.stock_status_filter.setCurrentIndex(page.stock_status_filter.findData('已清仓'))
    assert len(page.sorted_rows) == 250 and len(page.table.finance_model.rows) == 200
    page.turn(1)
    assert len(page.table.finance_model.rows) == 50
    page.stock_status_filter.setCurrentIndex(page.stock_status_filter.findData('持有中'))
    assert page.page == 0 and all(r.values[1] == '持有中' for r in page.sorted_rows)
    monkeypatch.setattr(report_page.QFileDialog, 'getSaveFileName', lambda *args: (str(tmp_path / 'filtered.csv'), 'CSV'))
    with qtbot.waitSignal(page.export_requested) as emitted:
        page.export()
    assert len(emitted.args[0].rows) == 250
    assert all(r.values[1] == '持有中' for r in emitted.args[0].rows)
    page.clear()
    assert page.stock_status_filter.isHidden()
