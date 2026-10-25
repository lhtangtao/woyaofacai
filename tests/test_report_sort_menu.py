from decimal import Decimal

from PySide6.QtCore import QPoint, Qt

from portfolio.domain.analytics import Report, ReportRow
from portfolio.ui.report_page import ReportPage


def test_header_menu_sorts_exact_numbers_and_keeps_missing_last(qtbot):
    page = ReportPage(('实时持仓',))
    qtbot.addWidget(page)
    page.resize(1000, 650)
    page.show()
    report = Report('实时持仓', ('Ticker', '持仓市值($)'), tuple(ReportRow((str(i), value)) for i, value in enumerate((Decimal('10.001'), None, Decimal('2'), Decimal('-9'), Decimal('10.002')))))
    page.set_reports({report.name: report})
    header = page.table.horizontalHeader()
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=header.viewport().rect().topLeft() + QPoint(header.sectionViewportPosition(1) + 20, 20))
    assert page.sort_menu.isVisible()
    actions = page.sort_menu.actions()
    assert [a.text() for a in actions[1:]] == ['正序（从小到大）', '倒序（从大到小）']
    actions[1].trigger()
    assert [r.values[1] for r in page.sorted_rows] == [Decimal('-9'), Decimal('2'), Decimal('10.001'), Decimal('10.002'), None]
    page.open_sort_menu(1)
    assert page.sort_menu.actions()[1].isChecked()
    page.sort_menu.actions()[2].trigger()
    assert [r.values[1] for r in page.sorted_rows] == [Decimal('10.002'), Decimal('10.001'), Decimal('2'), Decimal('-9'), None]
    page.sort_menu.close()


def test_sort_applies_before_pagination_and_survives_refresh(qtbot):
    page = ReportPage(('实时持仓',))
    qtbot.addWidget(page)
    report = Report('实时持仓', ('Ticker', '持仓市值($)'), tuple(ReportRow(('股票', Decimal(i))) for i in range(250)))
    page.set_reports({report.name: report})
    page.sort_column(1, True)
    assert page.table.finance_model.rows[0].values[1] == 249
    assert len(page.table.finance_model.rows) == 200
    page.local_search.setText('股票')
    assert page.sorted_rows[0].values[1] == 249
    refreshed = Report(report.name, report.columns, (*report.rows, ReportRow(('股票', Decimal('1000')))))
    page.set_reports({refreshed.name: refreshed})
    assert page.sorted_rows[0].values[1] == 1000
    assert page.table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
