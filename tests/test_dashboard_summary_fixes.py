"""回归测试：资产总览「渠道汇总」盘中防重复累加、隐藏 JSON 详情框及渠道中文化显示。"""
from decimal import Decimal as D

from PySide6.QtCore import Qt

from test_live_metrics import fixture
from portfolio.domain.analytics import Report, ReportRow
from portfolio.engine.dashboard import SUMMARY_COLUMNS
from portfolio.engine.live_valuation import live_summaries
from portfolio.ui.dashboard_page import DashboardPage
from portfolio.ui.report_page import ReportPage


def test_live_summary_does_not_compound_annual_gain_across_repeated_refreshes():
    """连续多轮盘中刷新写回 analytics.reports 后，本年盈亏不得重复累加。"""
    a, live, session, now = fixture()
    base = ('LONGBRIDGE', D(1000), D(900), 1, D(1), D(0), D(100), D(200), D(100), 1, 0, D(0), D(123))
    a.reports['渠道汇总'] = Report('渠道汇总', SUMMARY_COLUMNS, (ReportRow(base),))
    a.reports['当前持仓'] = Report(
        a.reports['当前持仓'].name,
        a.reports['当前持仓'].columns,
        (ReportRow(('LONGBRIDGE', *a.reports['当前持仓'].rows[0].values[1:]), a.reports['当前持仓'].rows[0].metadata),),
    )
    live = Report(
        live.name,
        live.columns,
        (ReportRow(('LONGBRIDGE', *live.rows[0].values[1:]), live.rows[0].metadata),),
        live.note,
    )

    first = live_summaries(a, live, D(1100), session, now)
    assert first['渠道汇总'].rows[0].values[7] == D(300)
    assert first['渠道汇总'].rows[0].metadata['baseline_annual_gain'] == D(200)

    # 模拟 live_quotes.py 每 10 秒写回 self.analytics.reports 后再次触发 live_summaries
    for _ in range(5):
        a.reports = {**a.reports, '实时持仓': live, **first}
        again = live_summaries(a, live, D(1100), session, now)
        assert again['渠道汇总'].rows[0].values[7] == D(300)
        assert again['渠道汇总'].rows[0].values[12] == D(123)
        first = again


def test_dashboard_hides_json_details_and_renders_chinese_channel_names(qtbot):
    """资产总览应隐藏底部 JSON 详情框，并将渠道代码映射为中文展示。"""
    dash = DashboardPage()
    qtbot.addWidget(dash)
    assert dash.summaries.details.isHidden()

    report = Report(
        '渠道汇总',
        SUMMARY_COLUMNS,
        (
            ReportRow(('BBAE', D(100), D(90), 1, D('0.25'), D(0), D(10), D(10), D(10), 1, 0, D(0), None)),
            ReportRow(('FIRSTRADE', D(100), D(90), 1, D('0.25'), D(0), D(10), D(10), D(10), 1, 0, D(0), None)),
            ReportRow(('LONGBRIDGE', D(100), D(90), 1, D('0.25'), D(0), D(10), D(10), D(10), 1, 0, D(0), D(141))),
            ReportRow(('SCHWAB', D(100), D(90), 1, D('0.25'), D(0), D(10), D(10), D(10), 1, 0, D(0), D(159))),
        ),
    )
    dash.summaries.set_reports({'渠道汇总': report})
    model = dash.summaries.table.finance_model
    channels = [model.data(model.index(r, 0), Qt.ItemDataRole.DisplayRole) for r in range(model.rowCount())]
    assert channels == ['BBAE', '第一证券', '长桥', '嘉信']
    # 原值保留英文代码，避免影响后续引擎汇总匹配或导出校验
    assert [r.values[0] for r in model.rows] == ['BBAE', 'FIRSTRADE', 'LONGBRIDGE', 'SCHWAB']

    # 其他 ReportPage 默认仍保留详情面板
    generic_page = ReportPage(('渠道汇总',))
    qtbot.addWidget(generic_page)
    assert not generic_page.details.isHidden()
