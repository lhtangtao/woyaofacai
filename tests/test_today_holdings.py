from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal as D
from decimal import localcontext
from types import SimpleNamespace

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.live_quotes import LiveQuote, us_session
from portfolio.domain.models import QueryScope
from portfolio.engine.live_valuation import live_holdings
from portfolio.engine.today_holdings import today_holdings, today_summary
from portfolio.ui.report_page import ReportPage
from portfolio.ui.sidebar import GROUPS


def data():
    meta = {'account_id': 1, 'instrument_id': 1, 'valuation_source': 'DAILY_CLOSE', 'valuation_date': '2026-10-16'}
    holding = ReportRow(('LONGBRIDGE', '正股', 'AAA', D(5), D(100), D(500), D(90), D(900), D(400), D(500), D(0), D(0), None, D(1)), meta)
    daily = [D(0)] * 23
    daily[0], daily[5], daily[8], daily[15], daily[17] = '2026-10-19', D(10), D(5), D(1000), D(510)
    reports = {'当前持仓': Report('当前持仓', tuple(str(i) for i in range(14)), (holding,)),
               '每日持仓收益': Report('每日持仓收益', (), (ReportRow(tuple(daily), meta),))}
    a = SimpleNamespace(reports=reports, scope=QueryScope(date(2026, 10, 19)), latest_profit_day='2026-10-16')
    now = datetime.fromisoformat('2026-10-19T02:30:00+00:00')
    session = us_session(now, {date(2026, 10, 19)})
    quotes = {('AAA.US', 'Overnight'): LiveQuote('AAA.US', D(110), now, 'Overnight')}
    live, *_ = live_holdings(a, {1: {'symbol': 'AAA', 'market': 'US'}}, quotes, session, now)
    return a, live, session


def test_current_holdings_pnl_includes_partial_sale_and_keeps_history_unchanged():
    a, live, session = data()
    before = repr(a.reports)
    report = today_holdings(a, live, session)
    assert report.rows[0].values[:7] == ('长桥', 'AAA', D(5), D(100), D(110), D('.1'), D(60))
    assert report.rows[0].values[9] == live.rows[0].values[6]
    assert report.rows[0].values[11] == live.rows[0].values[5]
    assert '2026-10-19' in report.note and '夜盘' in report.note
    with localcontext() as ctx:
        ctx.prec = 50
        expected = D(60) / D(490)
    assert today_summary(report.rows)[:4] == (D(60), expected, 1, 0)
    assert repr(a.reports) == before


def test_missing_quote_does_not_turn_reference_price_into_zero_profit():
    a, live, session = data()
    row = replace(live.rows[0], metadata={**live.rows[0].metadata, 'live_session': '收盘参考'})
    report = today_holdings(a, replace(live, rows=(row,)), session)
    assert report.rows[0].values[4:7] == (None, None, None)
    assert report.rows[0].values[9] == live.rows[0].values[6]
    assert report.rows[0].values[11] == live.rows[0].values[5]
    assert today_summary(report.rows)[0] is None
    assert today_summary(report.rows)[-1] == 1


def test_today_page_preset_sort_summary_and_sidebar_order(qtbot):
    a, live, session = data()
    report = today_holdings(a, live, session)
    row = report.rows[0]
    negative = replace(row, values=('BBAE', 'BBB', D(5), D(100), D(90), D('-.1'), D(-50), '夜盘', 'now', D(100), D(-50), D(450), D('0.11111111')))
    # The engine excludes zero holdings even if an upstream report accidentally includes one.
    extra = replace(live.rows[0], values=(*live.rows[0].values[:3], D(0), *live.rows[0].values[4:]))
    assert len(today_holdings(a, replace(live, rows=(*live.rows, extra)), session).rows) == 1
    report = replace(report, rows=(negative, row))
    page = ReportPage(('今日盈亏', '当前持仓'))
    qtbot.addWidget(page)
    page.show()
    page.set_reports({'今日盈亏': report, '当前持仓': a.reports['当前持仓']})
    assert not page.today_overview.isHidden() and not page.today_sort.isHidden()
    assert page.sorted_rows[0].values[1] == 'AAA'
    assert page.table.finance_model.index(0, 5).data() == '10.00%'
    assert page.table.horizontalHeader().logicalIndex(3) == 9
    assert page.table.finance_model.columns[9] == '成本价($)'
    assert page.table.finance_model.index(0, 9).data() == '90.00'
    assert page.table.horizontalHeader().logicalIndex(6) == 12
    assert page.table.finance_model.columns[12] == '回本涨幅'
    assert page.table.horizontalHeader().logicalIndex(7) == 11
    assert page.table.finance_model.index(0, 11).data() == '550.00'
    page.today_sort.setCurrentIndex(2)  # Biggest loss first.
    assert page.sorted_rows[0].values[1] == 'BBB'
    page.set_reports({'今日盈亏': report, '当前持仓': a.reports['当前持仓']})
    assert page.sorted_rows[0].values[1] == 'BBB'
    assert '+10.00' in page.today_cards[0].text()
    page.selector.setCurrentText('当前持仓')
    assert page.today_overview.isHidden() and page.today_sort.isHidden()
    names = [entry[0] for _, entries in GROUPS for entry in entries]
    assert names.index('今日盈亏') + 1 == names.index('当前持仓')


def test_channel_assets_include_reference_values_without_faking_pnl():
    from portfolio.engine.today_holdings import channel_summary
    a, live, session = data()
    report = today_holdings(a, live, session)
    row = report.rows[0]
    missing = replace(row, values=('BBAE', 'BBB', D(2), None, None, None, None, '等待报价', '—', None, None, D('200.25')), metadata={'current_asset': D('200.25')})
    assert channel_summary((row, row, missing)) == (('BBAE', None, D('200.25')), ('长桥', D(120), D(1100)))
    assert channel_summary(()) == ()


def test_default_page_is_today_and_channel_panel_tracks_filters(qtbot):
    from portfolio.ui.main_window import MainWindow
    window = MainWindow(settings=False)
    qtbot.addWidget(window)
    assert window.navigation.currentRow() == 3
    assert window.asset_page.selector.currentText() == '今日盈亏'
    a, live, session = data()
    report = today_holdings(a, live, session)
    page = window.asset_page
    page.set_reports({'今日盈亏': report, '当前持仓': a.reports['当前持仓']})
    assert not page.channel_overview.isHidden()
    assert page.channel_overview.channels == (('长桥', D(60), D(550)),)
    page.local_search.setText('DOES_NOT_EXIST')
    assert page.channel_overview.channels == ()
    page.selector.setCurrentText('当前持仓')
    assert page.channel_overview.isHidden()


def test_asset_dates_are_fixed_and_other_pages_keep_custom_dates(qtbot):
    from PySide6.QtCore import QDate

    from portfolio.ui.main_window import MainWindow
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w._filter_batch = True
    w.start_enabled.setChecked(True)
    w.start.setDate(QDate(2026, 3, 4))
    w.as_of.setDate(QDate(2026, 6, 8))
    for page, target in ((2, None), (3, '今日盈亏'), (3, '股票汇总'), (3, '人员资产'), (3, '渠道汇总')):
        w.navigation.select(page, target)
        assert w.date_button.isHidden()
        assert w.make_scope().start_date == date(2025, 1, 1)
        assert w.make_scope().as_of == QDate.currentDate().toPython()
    w.navigation.setCurrentRow(0)
    assert not w.date_button.isHidden()
    assert w.make_scope().start_date == date(2026, 3, 4)
    assert w.make_scope().as_of == date(2026, 6, 8)


def test_today_expands_all_rows_and_other_reports_keep_paging(qtbot):
    from PySide6.QtCore import Qt
    a, live, session = data()
    report = today_holdings(a, live, session)
    report = replace(report, rows=report.rows * 215)
    other = replace(report, name='其他报表')
    page = ReportPage(('今日盈亏', '其他报表'))
    qtbot.addWidget(page)
    page.resize(1250, 650)
    page.show()
    page.set_reports({'今日盈亏': report, '其他报表': other})
    qtbot.wait(30)
    assert page.table.finance_model.rowCount() == 215
    assert page.table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.table.height() > 215 * 30
    assert page.scroll.verticalScrollBar().maximum() > 0
    assert all(b.isHidden() for b in page.paging_buttons)
    page.scroll.verticalScrollBar().setValue(150)
    page.show_page()
    qtbot.wait(20)
    assert page.scroll.verticalScrollBar().value() == 150
    page.selector.setCurrentText('其他报表')
    assert page.table.finance_model.rowCount() == 200
    assert page.table.maximumHeight() == 16777215
    assert page.table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert all(not b.isHidden() for b in page.paging_buttons)


def test_asset_bar_click_toggles_channel_and_survives_refresh(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor

    from portfolio.ui.theme import tokens
    a, live, session = data()
    report = today_holdings(a, live, session)
    row = report.rows[0]
    other = replace(row, values=('BBAE', 'BBB', *row.values[2:]), metadata={**row.metadata, 'current_asset': D(200)})
    report = replace(report, rows=(row, other))
    page = ReportPage(('今日盈亏',))
    qtbot.addWidget(page)
    page.resize(1400, 800)
    page.show()
    page.set_reports({'今日盈亏': report})
    chart = page.channel_overview
    qtbot.waitUntil(lambda: len(chart.asset_hits) == 2)
    qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.asset_hits['长桥'].center().toPoint())
    assert chart.selected_channel == '长桥'
    assert len(page.sorted_rows) == 1 and page.sorted_rows[0].values[0] == '长桥'
    assert len(chart.channels) == 2  # All bars stay available while filtering the table.
    qtbot.wait(20)
    image = chart.grab().toImage()
    assert image.pixelColor(int(chart.width()*.58+108), int(chart.asset_hits['长桥'].center().y())) == QColor(tokens(chart).subtle)
    page.set_reports({'今日盈亏': report})
    assert chart.selected_channel == '长桥' and len(page.sorted_rows) == 1
    qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.asset_hits['BBAE'].center().toPoint())
    assert chart.selected_channel == 'BBAE' and page.sorted_rows[0].values[0] == 'BBAE'
    qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.asset_hits['BBAE'].center().toPoint())
    assert chart.selected_channel is None and len(page.sorted_rows) == 2
    qtbot.wait(20)
    image = chart.grab().toImage()
    assert image.pixelColor(int(chart.width()*.58+108), int(chart.asset_hits['长桥'].center().y())) == QColor(tokens(chart).brand)
    qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.asset_hits['长桥'].center().toPoint())
    page.local_search.setText('BBB')
    assert chart.selected_channel is None and len(page.sorted_rows) == 1


def test_pnl_bar_and_asset_bar_share_selection_and_restore_colors(qtbot):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QColor

    from portfolio.ui.theme import tokens
    a, live, session = data()
    report = today_holdings(a, live, session)
    row = report.rows[0]
    loss = replace(row, values=('BBAE', 'BBB', *row.values[2:6], D(-30), *row.values[7:]))
    report = replace(report, rows=(row, loss))
    page = ReportPage(('今日盈亏',))
    qtbot.addWidget(page)
    page.resize(1400, 800)
    page.show()
    page.set_reports({'今日盈亏': report})
    chart = page.channel_overview
    qtbot.waitUntil(lambda: len(chart.pnl_hits) == 2)
    # Positive/negative columns share one selected channel with the asset chart.
    for channel, normal_color in (('长桥', tokens(chart).profit), ('BBAE', tokens(chart).loss)):
        hit = chart.pnl_hits[channel]
        sample = QPoint(int(hit.center().x()), 110 if channel == '长桥' else 245)
        qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=sample)
        assert chart.selected_channel == channel
        assert len(page.sorted_rows) == 1 and page.sorted_rows[0].values[0] == channel
        qtbot.wait(20)
        assert chart.grab().toImage().pixelColor(sample) == QColor(tokens(chart).subtle)
        page.set_reports({'今日盈亏': report})
        assert chart.selected_channel == channel
        # Cancel via the other chart, then verify the P/L bar restores its sign color.
        qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.asset_hits[channel].center().toPoint())
        assert chart.selected_channel is None and len(page.sorted_rows) == 2
        qtbot.wait(20)
        assert chart.grab().toImage().pixelColor(sample) == QColor(normal_color)
    qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.pnl_hits['长桥'].center().toPoint())
    qtbot.mouseClick(chart, Qt.MouseButton.LeftButton, pos=chart.pnl_hits['长桥'].center().toPoint())
    assert chart.selected_channel is None and len(page.sorted_rows) == 2


def test_ticker_search_fuzzy_and_hidden_quote_columns(qtbot):
    a, live, session = data()
    report = today_holdings(a, live, session)
    row = report.rows[0]
    extra = replace(row, values=('BBAE', 'TQQQ', *row.values[2:]))
    report = replace(report, rows=(row, extra))
    page = ReportPage(('今日盈亏',))
    qtbot.addWidget(page)
    page.show()
    page.set_reports({'今日盈亏': report})

    # Verify 报价时间 and 行情状态 are hidden
    cols = [page.table.finance_model.columns[i] for i in range(len(page.table.finance_model.columns))]
    if '报价时间' in cols:
        assert page.table.isColumnHidden(cols.index('报价时间'))
    if '行情状态' in cols:
        assert page.table.isColumnHidden(cols.index('行情状态'))

    # Verify Ticker fuzzy search
    page.local_search.setText('tq')
    assert len(page.sorted_rows) == 1
    assert page.sorted_rows[0].values[1] == 'TQQQ'

    page.local_search.clear()
    assert len(page.sorted_rows) == 2


def test_holdings_recovery_order_and_hidden_columns(qtbot):
    from portfolio.engine.holdings import HOLDINGS_COLUMNS
    assert '回本涨幅' in HOLDINGS_COLUMNS
    assert '回本所需涨幅' not in HOLDINGS_COLUMNS

    a, live, session = data()
    meta = {'account_id': 1, 'instrument_id': 1}
    row_loss = ReportRow(('长桥', '大盘', 'TQQQ', D(10), D(50), D(500), D(100), D(1000), D(0), D(1000), D(-500), D('-0.5'), D(1), D('0.5')), meta)
    row_profit = ReportRow(('长桥', '大盘', 'AAPL', D(10), D(150), D(1500), D(100), D(1000), D(0), D(1000), D(500), D('0.5'), None, D('0.5')), meta)
    holdings_rep = Report('当前持仓', HOLDINGS_COLUMNS, (row_loss, row_profit))

    live_cols = (*HOLDINGS_COLUMNS, '行情时段', '报价时间', '行情状态')
    live_row_loss = ReportRow((*row_loss.values, '美股盘中', '09:30:00', '实时'), meta)
    live_row_profit = ReportRow((*row_profit.values, '美股盘中', '09:30:00', '实时'), meta)
    live_rep = Report('实时持仓', live_cols, (live_row_loss, live_row_profit))

    page = ReportPage(('当前持仓', '实时持仓'))
    qtbot.addWidget(page)
    page.show()
    page.set_reports({'当前持仓': holdings_rep, '实时持仓': live_rep})

    header = page.table.horizontalHeader()
    # Test 当前持仓
    page.selector.setCurrentText('当前持仓')
    assert page.table.finance_model.columns[12] == '回本涨幅'
    v_price = header.visualIndex(4)
    v_rec = header.visualIndex(12)
    assert v_rec == v_price + 1
    assert not page.table.isColumnHidden(12)
    assert page.table.finance_model.index(0, 12).data() == '100.00%'
    assert page.table.finance_model.index(1, 12).data() == '—'

    # Test 实时持仓
    page.selector.setCurrentText('实时持仓')
    v_price = header.visualIndex(4)
    v_rec = header.visualIndex(12)
    assert v_rec == v_price + 1
    assert not page.table.isColumnHidden(12)
    # Check quote columns are hidden
    for col_name in ('行情时段', '报价时间', '行情状态'):
        idx = live_cols.index(col_name)
        assert page.table.isColumnHidden(idx)

