from datetime import date
from decimal import Decimal

from portfolio.domain.analytics import Position, Snapshot
from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.report_page import ReportPage


def test_person_assets_show_channel_and_exact_quantity_status(qtbot):
    positions = {}
    for instrument, quantity in enumerate(('10', '0', '-1', '.00001', '4'), 1):
        key = (1, 1, instrument)
        positions[key] = Position(key, quantity=Decimal(quantity), buy=Decimal(100), sell=Decimal(20), source_ids=[instrument])
    positions[(1, 1, 5)].warnings.add('成交粒度冲突')
    snapshot = Snapshot(QueryScope(date(2026, 10, 19)), [],
                        {i: {'symbol': f'TICK{i}'} for i in range(1, 6)},
                        {1: {'broker_code': 'LONGBRIDGE', 'display_name': 'Longbridge original account'}},
                        {1: {'name': '人员甲'}}, [], [], [], extras={'positions': positions})
    original = repr(positions)
    reports = {}
    AnalyticsService._data_center(snapshot, reports)
    report = reports['人员资产']
    assert report.columns == (
        '人员', '渠道', 'Ticker', '数量', '当前状态',
        '持仓成本/清仓成本($)', '最新价($)', '当前市值($)', '持仓盈亏/清仓收益($)', '盈亏比例', '回本涨幅', '占比',
        '净投入($)'
    )
    assert {r.values[1] for r in report.rows} == {'长桥'}
    assert [r.values[4] for r in report.rows] == ['持有中', '已清仓', '负仓异常', '已清仓', '成交待核对']
    assert report.rows[-1].values[3] is None
    assert report.rows[1].values[12] == Decimal(80)  # Zero shares retains actual net cash.
    assert repr(positions) == original
    assert all(r.metadata['account_id'] == 1 for r in report.rows)
    page = ReportPage(('人员资产',))
    qtbot.addWidget(page)
    page.show()
    page.set_reports(reports)
    assert not page.stock_status_filter.isHidden()
    page.stock_status_filter.setCurrentIndex(page.stock_status_filter.findData('已清仓'))
    assert len(page.sorted_rows) == 2 and all(row.values[3] == 0 for row in page.sorted_rows)
    page.stock_status_filter.setCurrentIndex(page.stock_status_filter.findData('持有中'))
    assert len(page.sorted_rows) == 1
    assert all(r.values[3] > 0 for r in page.sorted_rows)
