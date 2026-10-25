from datetime import date, datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace

from PySide6.QtCore import QDate, Qt

from portfolio.domain.analytics import Report, ReportRow
from portfolio.domain.models import QueryScope
from portfolio.engine.people_pnl import PeopleBundle, people_reports
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.broker_people import BrokerPeoplePage
from portfolio.ui.main_window import MainWindow


def book(quantity, net, opening, sold, daily_gain):
    q, net, opening, sold, daily_gain = map(D, (quantity, net, opening, sold, daily_gain))
    value = q*D(110)
    meta = {'account_id': 1, 'instrument_id': 1, 'valuation_date': '2026-10-16', 'valuation_source': 'DAILY_CLOSE'}
    holding = ReportRow(('LONGBRIDGE', '正股', 'AAA', q, D(110), value, net/q if q else None, net+sold, sold, net, value-net, None, None, None), meta)
    stock = ReportRow(('LONGBRIDGE', '正股', 'AAA', '持有中' if q else '已清仓', q, D(110), value, net/q if q else None, net+sold, sold, net, value-net if q else None, None, None, None, D(0) if q else -net, D(0), value-net), meta)
    flow = [D(0)]*23
    flow[0], flow[5], flow[8], flow[15], flow[17], flow[19] = '2026-10-16', opening/D(100), q, opening, sold, daily_gain
    reports = {'当前持仓': Report('当前持仓', tuple(str(i) for i in range(14)), (holding,) if q else ()), '股票汇总': Report('股票汇总', (), (stock,)), '每日持仓收益': Report('每日持仓收益', (), (ReportRow(tuple(flow), meta),))}
    return SimpleNamespace(scope=QueryScope(date(2026, 10, 16)), reports=reports, latest_profit_day='2026-10-16')


def fixture_bundle():
    a = book('0', '-50', '100', '150', '50')
    b = book('2', '200', '200', '0', '20')
    broker = book('2', '150', '300', '150', '70')
    return PeopleBundle(broker, ((1, '甲', a), (2, '乙', b)), broker)


def render(bundle):
    return people_reports(bundle, {1: {'symbol': 'AAA', 'market': 'US'}}, {}, None, datetime(2026, 10, 19, tzinfo=timezone.utc))


def test_daily_people_include_fully_closed_and_reconcile_without_double_counting():
    summary, details, broker = render(fixture_bundle())
    rows = {r.metadata['person_id']: r for r in summary.rows}
    assert set(rows) == {1, 2}
    assert rows[1].values[1] == rows[1].values[4] == rows[1].values[5] == D(50)
    assert rows[1].values[3] == D(0)
    assert rows[2].values[1] == D(20)
    assert sum(r.values[1] for r in rows.values()) == broker.values[1] == D(70)
    assert sum(r.values[5] for r in rows.values()) == broker.values[5] == D(70)
    assert details.columns[8] == '当日涨幅'
    assert next(r for r in details.rows if r.metadata['person_id'] == 2).values[8] == D('0.1')
    assert next(r for r in details.rows if r.metadata['person_id'] == 1).metadata['daily_active']


def test_unallocated_is_explicit_residual_instead_of_an_owner_assignment():
    bundle = fixture_bundle()
    bundle.persons = bundle.persons[1:]
    summary, details, broker = render(bundle)
    residual = next(r for r in summary.rows if r.metadata['person_id'] == 'residual')
    assert residual.values[1] == residual.values[5] == D(50)
    assert sum(r.values[1] for r in summary.rows) == broker.values[1]
    assert any(r.metadata['person_id'] == 'residual' for r in details.rows)


def test_unallocated_under_half_share_is_ignored():
    bundle = fixture_bundle()
    b = bundle.persons[1][2]
    old_stock = b.reports['股票汇总'].rows[0]
    vals = list(old_stock.values)
    vals[4] = D('2.4')  # person has 2.4 shares while broker has 2.0 shares (0.4 share difference < 0.5)
    b.reports['股票汇总'] = Report('股票汇总', (), (ReportRow(tuple(vals), old_stock.metadata),))
    summary, details, broker = render(bundle)
    assert not any(r.metadata.get('person_id') == 'residual' for r in summary.rows)
    assert not any(r.metadata.get('person_id') == 'residual' for r in details.rows)


def test_missing_daily_value_propagates_and_common_day_does_not_reuse_old_owner_day():
    bundle = fixture_bundle()
    a = bundle.persons[0][2]
    a.reports['每日持仓收益'] = Report('每日持仓收益', (), ())
    a.latest_profit_day = '2026-08-01'
    summary, _, _ = render(bundle)
    assert next(r for r in summary.rows if r.metadata['person_id'] == 1).values[1] == D(0)
    b = bundle.persons[1][2]
    old = b.reports['每日持仓收益'].rows[0]
    values = list(old.values)
    values[19] = None
    b.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(values), old.metadata),))
    summary, _, _ = render(bundle)
    assert next(r for r in summary.rows if r.metadata['person_id'] == 2).values[1] is None
    assert next(r for r in summary.rows if r.metadata['person_id'] == 'residual').values[1] is None


def test_people_chart_modes_sort_and_click_drill(qtbot):
    page = BrokerPeoplePage()
    qtbot.addWidget(page)
    page.resize(1400, 950)
    page.show()
    page.bundle = fixture_bundle()
    page.result = render(page.bundle)
    page.show_result()
    qtbot.waitUntil(lambda: len(page.chart.hits) == 2)
    assert page.summary.finance_model.rows[0].values[1] == D(50)
    assert any(r.metadata['person_id'] == 1 for r in page.detail.finance_model.rows)
    qtbot.mouseClick(page.chart, Qt.MouseButton.LeftButton, pos=page.chart.hits[1].center().toPoint())
    assert all(r.metadata['person_id'] == 1 for r in page.detail.finance_model.rows)
    page.show_result()  # price refresh keeps the selected person
    assert page.chart.person == 1
    qtbot.mouseClick(page.chart, Qt.MouseButton.LeftButton, pos=page.chart.hits[1].center().toPoint())
    assert len(page.detail.finance_model.rows) == 2
    page.mode.setCurrentIndex(1)
    assert '累计盈亏' in page.cards[0].text()
    page.order.setCurrentIndex(1)
    assert page.summary.finance_model.rows[0].values[5] == D(20)


def test_people_service_and_broker_navigation_ignore_single_owner_filter(qtbot, demo_path):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)
    w.person.setCurrentIndex(w.person.findData(1))
    w.holding_date.setDate(QDate(2026, 1, 9))
    w.navigation.select(3, '渠道汇总')
    qtbot.waitUntil(lambda: w.asset_page.broker_page.bundle is not None, timeout=20000)
    page = w.asset_page.broker_page
    assert page.bundle.aggregate.scope.person_ids is None
    assert not w.holding_date.isHidden() and w.person.isHidden()
    assert len(page.bundle.persons) == 2
    assert all(a.scope.person_ids == (identifier,) for identifier, _, a in page.bundle.persons)
    assert page.result is not None and not page.isHidden()
    assert w.asset_page.scroll.isHidden()
    w.navigation.select(3, '今日盈亏')
    assert not w.person.isHidden() and page.isHidden()
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (1,), timeout=15000)
    w.navigation.select(3, '渠道汇总')
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids is None, timeout=15000)
    assert w.live_panel.analytics is page.bundle.aggregate
    w.close()


def test_people_calculation_detects_database_changes(demo_path, monkeypatch):
    import pytest
    service = AnalyticsService(demo_path)
    original = service.identity
    count = [0]
    def changed():
        count[0] += 1
        return original() if count[0] == 1 else (*original(), 'changed')
    monkeypatch.setattr(service, 'identity', changed)
    with pytest.raises(ValueError, match='源库'):
        service.calculate_people(QueryScope(date(2026, 1, 9)))


def test_live_people_cashflows_include_today_full_sale_and_missing_quotes_stay_missing():
    from dataclasses import replace

    from portfolio.domain.live_quotes import LiveQuote, us_session
    bundle = fixture_bundle()
    now = datetime.fromisoformat('2026-10-19T02:30:00+00:00')
    session = us_session(now, {date(2026, 10, 19)})
    books = (bundle.persons[0][2], bundle.persons[1][2], bundle.broker)
    for analytics, opening in zip(books, (D(110), D(220), D(330))):
        analytics.scope = replace(analytics.scope, as_of=date(2026, 10, 19))
        row = analytics.reports['每日持仓收益'].rows[0]
        values = list(row.values)
        values[0], values[15] = '2026-10-19', opening
        analytics.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(values), row.metadata),))
    quotes = {('AAA.US', 'Overnight'): LiveQuote('AAA.US', D(120), now, 'Overnight')}
    instruments = {1: {'symbol': 'AAA', 'market': 'US'}}
    summary, _, broker = people_reports(bundle, instruments, quotes, session, now)
    owned = {r.metadata['person_id']: r for r in summary.rows}
    assert owned[1].values[1] == owned[1].values[4] == D(40)
    assert owned[2].values[1] == D(20)
    assert broker.values[1] == D(60)
    assert broker.values[5] == D(90)
    assert broker.values[6] == D(50) and broker.values[7] == D(40)
    missing, _, broker_missing = people_reports(bundle, instruments, {}, session, now)
    assert next(r for r in missing.rows if r.metadata['person_id'] == 2).values[1] is None
    assert broker_missing.values[1] is None


def test_closed_owners_do_not_force_broker_to_an_old_profit_day():
    from dataclasses import replace
    bundle = fixture_bundle()
    closed = bundle.persons[0][2]
    old = closed.reports['每日持仓收益'].rows[0]
    values = list(old.values)
    values[0] = '2026-08-01'
    closed.reports['每日持仓收益'] = Report('每日持仓收益', (), (ReportRow(tuple(values), old.metadata),))
    closed.latest_profit_day = '2026-08-01'
    bundle.aggregate = closed
    bundle.persons = bundle.persons[:1]
    summary, _, broker = render(bundle)
    assert '收盘收益日 2026-10-16' in summary.note
    assert next(r for r in summary.rows if r.metadata['person_id'] == 1).values[1] == D(0)
    assert broker.values[1] == D(70)
    bundle.aggregate.scope = replace(bundle.aggregate.scope, as_of=date(2026, 10, 17))
    summary, _, _ = render(bundle)
    assert '休市' in summary.note and '2026-10-16' in summary.note


def test_stock_ownership_uses_shares_and_reconciles_without_profit_residual():
    from portfolio.engine.stock_ownership import ownership_choices, ownership_report
    bundle = fixture_bundle()
    report, broker, assigned, difference = ownership_report(bundle, 1)
    assert ownership_choices(bundle) == [(1, 'AAA')]
    assert broker == assigned == D(2) and difference == 0
    assert len(report.rows) == 1  # fully closed owner excluded despite realized profit
    assert report.rows[0].values == ('乙', D(2), D(1))
    bundle.broker = book('3', '150', '300', '150', '70')
    report, broker, assigned, difference = ownership_report(bundle, 1)
    assert (broker, assigned, difference) == (D(3), D(2), D(1))
    assert report.rows[-1].metadata['person_id'] == 'residual'
    assert sum(r.values[1] for r in report.rows) == broker

    # When only person 1 / 个人 holds the stock, it is excluded from ownership choices
    b_zero = book('0', '0', '0', '0', '0')
    a_held = book('2', '100', '100', '0', '10')
    bundle_owner_only = PeopleBundle(bundle.broker, ((1, '个人', a_held), (2, '他人', b_zero)), bundle.broker)
    assert ownership_choices(bundle_owner_only) == []



def test_stock_ownership_combines_accounts_retains_tiny_shares_and_unknowns():
    from portfolio.engine.stock_ownership import ownership_report
    bundle = fixture_bundle()
    owner = bundle.persons[1][2]
    original = owner.reports['股票汇总'].rows[0]
    values = list(original.values)
    values[4] = D('0.00000001')
    tiny = ReportRow(tuple(values), {**original.metadata, 'account_id': 2})
    owner.reports['股票汇总'] = Report('股票汇总', (), (original, tiny))
    report, _, assigned, difference = ownership_report(bundle, 1)
    assert assigned == D('2.00000001') and difference == D('-0.00000001')
    assert report.rows[-1].values[1] < 0
    values[4] = None
    owner.reports['股票汇总'] = Report('股票汇总', (), (ReportRow(tuple(values), original.metadata),))
    report, _, assigned, difference = ownership_report(bundle, 1)
    assert assigned is None and difference is None
    assert all(r.values[1] is None for r in report.rows)


def test_stock_ownership_ui_switches_and_keeps_fractional_shares(qtbot):
    from portfolio.engine.stock_ownership import ownership_report
    page = BrokerPeoplePage()
    qtbot.addWidget(page)
    page.bundle = fixture_bundle()
    page.result = render(page.bundle)
    page.mode.setCurrentIndex(2)
    assert page.stock.currentText() == 'AAA'
    assert page.stock.isHidden() is False and page.detail.isHidden()
    expected, *_ = ownership_report(page.bundle, 1)
    assert page.summary.finance_model.rows == expected.rows
    assert '2 股' in page.cards[0].text()
    assert page.chart.quantity_mode
    tiny = ReportRow(('小数股', D('0.00000001'), D(0)), {'person_id': 3})
    page.chart.set_rows((tiny,), 1)
    assert page.chart.rows == (tiny,)
    page.mode.setCurrentIndex(0)
    assert page.stock.isHidden() and not page.detail.isHidden()
    assert not page.chart.quantity_mode


def test_stock_ownership_sidebar_menu_and_routing(demo_path, qtbot):
    from portfolio.ui.main_window import MainWindow
    from portfolio.ui.sidebar import GROUPS

    asset_entries = [entry[0] for entry in GROUPS[0][1]]
    assert '券商资产' in asset_entries
    assert '股票归属' in asset_entries
    assert asset_entries.index('券商资产') + 1 == asset_entries.index('股票归属')
    assert asset_entries.index('股票归属') + 1 == asset_entries.index('持仓财报日历')

    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: w.analytics is not None, timeout=15000)

    # 1. Select 股票归属 from sidebar
    w.navigation.select(3, '股票归属')
    qtbot.waitUntil(lambda: w.asset_page.broker_page.bundle is not None, timeout=20000)
    assert w.pages.currentIndex() == 3
    assert w.asset_page.selector.currentText() == '股票归属'
    assert w.asset_page.broker_page.mode.currentText() == '股票归属'
    assert w.heading.text() == '股票归属'
    assert '各人员的持有股数与占比' in w.description.text()
    assert w.asset_page.broker_page.title_label.text() == '股票归属 · 人员占比'
    assert not w.asset_page.broker_page.stock.isHidden()

    # 2. Select 券商资产 from sidebar
    w.navigation.select(3, '渠道汇总')
    assert w.asset_page.selector.currentText() == '渠道汇总'
    assert w.asset_page.broker_page.mode.currentText() != '股票归属'
    assert w.heading.text() == '券商资产 · 人员盈亏'
    assert w.asset_page.broker_page.title_label.text() == '全部人员 · 权益账本'

    # 3. Mode combo switches to 股票归属 inside page syncs sidebar
    w.asset_page.broker_page.mode.setCurrentText('股票归属')
    assert w.navigation._route == (3, '股票归属')
    assert w.heading.text() == '股票归属'

    # 4. Mode combo switches back to 当日盈亏 inside page syncs sidebar
    w.asset_page.broker_page.mode.setCurrentText('当日盈亏')
    assert w.navigation._route == (3, '渠道汇总')
    assert w.heading.text() == '券商资产 · 人员盈亏'
    w.close()

