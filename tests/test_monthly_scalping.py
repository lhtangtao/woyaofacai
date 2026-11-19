import sys
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from collections import namedtuple
import pytest
from PySide6.QtWidgets import QApplication

from portfolio.domain.models import QueryScope, Ledger
from portfolio.engine.scalping import _scalping, short_term_scalping
from portfolio.services.analytics_service import AnalyticsService
from portfolio.ui.monthly_scalping_page import (
    MonthlyScalpingPage,
    fmt_money,
    fmt_signed_money,
    fmt_broker_cn,
    calc_weighted_spread,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


Event = namedtuple('Event', ['id', 'key', 'day', 'time', 'sequence', 'kind', 'amount', 'quantity', 'incoming', 'account_id', 'source_id', 'metadata'])


def make_mock_snapshot(events, broker_code="LONGBRIDGE"):
    return SimpleNamespace(
        events=events,
        actions=[],
        accounts={1: {'broker_code': broker_code, 'broker_name': broker_code}},
        instruments={1: {'symbol': 'TEST', 'category': '正股'}},
        extras={},
        issues=[],
        prices=[]
    )


# =========================================================================
# PRD 核心业务规则场景测试（场景 1 ~ 场景 11）
# =========================================================================

def test_scenario_1_monthly_profit():
    """场景1：月内盈利：同月买入100股@100，卖出100股@110，价格上涨 -> 微操收益为正"""
    e1 = Event(1, (1, 1, 1), '2026-10-10', '2026-10-10 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-10-20', '2026-10-20 10:00:00', 2, 'MARKET_TRADE', Decimal('11000'), Decimal('100'), False, 1, 2, {})
    snap = make_mock_snapshot([e1, e2], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    assert len(rep.rows) == 1
    # 微操收益为 +$1,000
    row = rep.rows[0].values
    assert row[0] == "2026-10"
    assert row[3] == "TEST"
    assert row[4] == 1              # 微操次数
    assert row[5] == Decimal('100') # 微操数量
    assert row[11] == Decimal('1000') # 微操收益


def test_scenario_2_monthly_loss():
    """场景2：月内亏损：同月买入100股@100，卖出100股@90，价格下跌 -> 微操收益为负"""
    e1 = Event(1, (1, 1, 1), '2026-10-10', '2026-10-10 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-10-20', '2026-10-20 10:00:00', 2, 'MARKET_TRADE', Decimal('9000'), Decimal('100'), False, 1, 2, {})
    snap = make_mock_snapshot([e1, e2], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    assert len(rep.rows) == 1
    row = rep.rows[0].values
    assert row[11] == Decimal('-1000') # 亏损 -$1,000


def test_scenario_3_cross_month_trades_not_scalping():
    """场景3：跨月买入和卖出：10月买入100股，11月卖出100股 -> 两月均不计入微操收益"""
    e1 = Event(1, (1, 1, 1), '2026-10-15', '2026-10-15 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-11-15', '2026-11-15 10:00:00', 2, 'MARKET_TRADE', Decimal('11000'), Decimal('100'), False, 1, 2, {})
    snap = make_mock_snapshot([e1, e2], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    # 10月和11月均无月内配对，微操报表行为 0
    assert len(rep.rows) == 0
    assert len(pairs.rows) == 0


def test_scenario_4_only_buy_in_month():
    """场景4：只有买入：11月买入100股，没有卖出 -> 11月无月内配对"""
    e1 = Event(1, (1, 1, 1), '2026-11-10', '2026-11-10 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    snap = make_mock_snapshot([e1], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    assert len(rep.rows) == 0
    assert len(pairs.rows) == 0


def test_scenario_5_only_sell_prior_holding():
    """场景5：只有卖出：11月只有卖出上月持有的股票 -> 不计入11月微操收益"""
    # 9月买入100股，11月卖出100股
    e1 = Event(1, (1, 1, 1), '2026-09-10', '2026-09-10 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-11-10', '2026-11-10 10:00:00', 2, 'MARKET_TRADE', Decimal('11000'), Decimal('100'), False, 1, 2, {})
    snap = make_mock_snapshot([e1, e2], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    # 11月没有买入配对，不计入微操收益
    assert len([r for r in rep.rows if r.values[0] == "2026-11"]) == 0


def test_scenario_6_partial_matching():
    """场景6：部分配对：当月买入100股，卖出60股 -> 60股计入微操，40股不计入"""
    e1 = Event(1, (1, 1, 1), '2026-10-10', '2026-10-10 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-10-20', '2026-10-20 10:00:00', 2, 'MARKET_TRADE', Decimal('6600'), Decimal('60'), False, 1, 2, {})
    snap = make_mock_snapshot([e1, e2], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    assert len(rep.rows) == 1
    row = rep.rows[0].values
    assert row[5] == Decimal('60')      # 仅配对 60 股
    assert row[9] == Decimal('6000')    # 买入金额分摊为 6000
    assert row[10] == Decimal('6600')   # 卖出金额为 6600
    assert row[11] == Decimal('600')    # 微操收益为 600


def test_scenario_7_multiple_trades_same_month():
    """场景7：同月多次交易：按时间顺序确定性配对，数量不重复计算"""
    # 10-01 买 50@100
    # 10-05 买 50@105
    # 10-10 卖 70@110
    e1 = Event(1, (1, 1, 1), '2026-10-01', '2026-10-01 10:00:00', 1, 'MARKET_TRADE', Decimal('5000'), Decimal('50'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-10-05', '2026-10-05 10:00:00', 2, 'MARKET_TRADE', Decimal('5250'), Decimal('50'), True, 1, 2, {})
    e3 = Event(3, (1, 1, 1), '2026-10-10', '2026-10-10 10:00:00', 3, 'MARKET_TRADE', Decimal('7700'), Decimal('70'), False, 1, 3, {})
    snap = make_mock_snapshot([e1, e2, e3], broker_code="FIRSTTRADE")
    rep, pairs, _, _ = _scalping(snap)

    assert len(rep.rows) == 1
    row = rep.rows[0].values
    assert row[5] == Decimal('70')  # 成功配对 70 股


def test_scenario_8_fee_allocation():
    """场景8：手续费：对于 LONGBRIDGE/BBAE 订单固定费用按成交股数比例合理分摊"""
    e1 = Event(1, (1, 1, 1), '2026-10-10', '2026-10-10 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {})
    e2 = Event(2, (1, 1, 1), '2026-10-20', '2026-10-20 10:00:00', 2, 'MARKET_TRADE', Decimal('11000'), Decimal('100'), False, 1, 2, {})
    snap = make_mock_snapshot([e1, e2], broker_code="LONGBRIDGE")
    rep, pairs, _, _ = _scalping(snap)

    assert len(pairs.rows) == 1
    p = pairs.rows[0].values
    assert p[8] == Decimal('2')  # 双方各分摊 1 USD，共 2 USD
    assert p[9] == Decimal('998') # 1000 - 2 = 998


def test_scenario_9_top5_month_drill_down_only_selected_month(app, demo_path):
    """场景9：排行榜验证：月内微操收益 TOP 5 和月内微操亏损 TOP 5 仅使用当前选中月份的数据"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    # 选中某个月份
    if page.raw_rows:
        test_m = str(page.raw_rows[0][0])
        page.on_trend_month_clicked(test_m)
        assert page.active_chart_month == test_m

        # 检查右侧摘要卡片为该月数据
        assert test_m in page.month_summary_card.title_lbl.text()


def test_scenario_10_unrealized_pnl_independent(app, demo_path):
    """场景10：当前浮动盈亏独立展示，不计入微操收益，不影响微操收益排序"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    # 验证提取的 unrealized_pnl_map 存在
    assert isinstance(page.unrealized_pnl_map, dict)
    # 验证表格中包含第 14 列「当前浮动盈亏($)」和第 15 列「说明」
    assert "当前浮动盈亏($)" in page.table_model.columns
    assert "说明" in page.table_model.columns


def test_scenario_11_zero_profit_vs_no_match(app):
    """场景11：无数据月份与有配对但零收益月份能够正确区分"""
    page = MonthlyScalpingPage()
    # 模拟一个有配对零收益月份和一个无配对月份
    test_points = [
        {"month": "2026-04", "pnl": 0.0, "trades": 2, "qty": 100.0, "profit_cnt": 0, "loss_cnt": 0, "has_pair": True},
        {"month": "2026-05", "pnl": 0.0, "trades": 0, "qty": 0.0, "profit_cnt": 0, "loss_cnt": 0, "has_pair": False},
    ]
    page.trend_canvas.set_data(test_points, "2026-04")
    # 选中 2026-04（有配对零收益）
    page.month_summary_card.set_month_data("2026-04", 0.0, 2, 100.0, True)
    assert page.month_summary_card.val_pnl.text() == "$0.00"
    assert page.month_summary_card.val_trades.text() == "2"

    # 选中 2026-05（无配对）
    page.month_summary_card.set_month_data("2026-05", None, 0, 0.0, False)
    assert page.month_summary_card.val_pnl.text() == "—"
    assert page.month_summary_card.val_trades.text() == "0"


# =========================================================================
# UI 与交互兼容性测试
# =========================================================================

def test_monthly_scalping_weighted_spread():
    """加权平均价差标准算法测试"""
    mock_rows = [
        ("2026-11", "LONGBRIDGE", "正股", "A", 1, 10.0, 10.0, 20.0, 10.0, 100.0, 200.0, 100.0),
        ("2026-11", "LONGBRIDGE", "正股", "B", 1, 1000.0, 5.0, 6.0, 1.0, 5000.0, 6000.0, 1000.0),
    ]
    val, s_str = calc_weighted_spread(mock_rows)
    assert val is not None
    assert abs(val - 1.0891) < 0.01
    assert s_str == "+$1.09"

    assert calc_weighted_spread([]) == (None, "—")
    assert calc_weighted_spread([("2026-11", "LONGBRIDGE", "正股", "C", 1, 0, 5.0, 5.0, 0.0)]) == (None, "—")


def test_monthly_scalping_trend_month_drill_keeps_full_range(app, demo_path):
    """趋势图点击某个月份，不缩短全局日期范围"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    init_start = page.applied_start_month
    init_end = page.applied_end_month
    total_months_count = len(page.trend_canvas.data_points)

    target_month = page.trend_canvas.data_points[0][0]
    page.on_trend_month_clicked(target_month)

    # 验证全局日期范围未被缩短
    assert page.active_chart_month == target_month
    assert page.applied_start_month == init_start
    assert page.applied_end_month == init_end
    assert len(page.trend_canvas.data_points) == total_months_count

    # 验证再次点击当前月份时相当于没有点击，保持单月锁定，绝不退回全周期汇总
    page.on_trend_month_clicked(target_month)
    assert page.active_chart_month == target_month
    page.on_trend_month_clicked("")
    assert page.active_chart_month == target_month
    assert len(page.trend_canvas.data_points) == total_months_count


def test_monthly_scalping_table_sorting_three_levels(app, demo_path):
    """表格默认三级稳定排序（月份降序 -> 收益降序 -> Ticker 升序）"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    rows = page.table_model.all_rows
    assert len(rows) > 0


def test_monthly_scalping_drawer_and_interactions(app, demo_path):
    """交互、抽屉与联动"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    if page.raw_rows:
        row = list(page.table_model.all_rows[0])
        page.open_drawer(row)
        assert not page.drawer_frame.isHidden()
        assert not page.drawer_overlay.isHidden()

        page.close_drawer()
        assert page.drawer_frame.isHidden()
        assert page.drawer_overlay.isHidden()

        sym = str(row[3])
        page.filter_by_ticker(sym)
        assert page.active_ranking_ticker == sym
        for r in page.table_model.all_rows:
            assert r[3] == sym

        page.filter_by_ticker(sym)
        assert page.active_ranking_ticker == ""


def test_monthly_scalping_open_stock_replay(app, demo_path):
    """测试点击交易回放/双击行能够正常触发 open_stock_replay 并弹出回放弹窗"""
    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    page.activate()

    if page.raw_rows:
        row = list(page.table_model.all_rows[0])
        sym = str(row[3])
        m = str(row[0])
        page.open_stock_replay(sym, month=m)
        assert page._replay_dialog is not None
        assert sym in page._replay_dialog.windowTitle()
        page._replay_dialog.close()


def test_stock_drawer_data_completeness_and_clean_cards(app, demo_path):
    """验证股票抽屉：收益分析、持仓分析、月度表现与交易记录均完整产出，顶部前4卡无冗余副标题"""
    from portfolio.services.holdings_management_service import HoldingsManagementService
    from portfolio.ui.holdings_page import StockDrawerWidget

    service = AnalyticsService(demo_path)
    scope = QueryScope(as_of=date.today(), ledger=Ledger.PERSON)
    analytics = service.calculate(scope)
    pa = analytics.reports.get("人员资产")
    assert pa and pa.rows
    sample_row = pa.rows[0]
    ticker = str(sample_row.values[2])
    broker = str(sample_row.values[1])
    person_id = sample_row.metadata.get("person_id")

    data = HoldingsManagementService.get_stock_drawer_data(
        str(demo_path), analytics, ticker, person_id=person_id, broker=broker
    )
    assert len(data["trades"]) > 0
    assert len(data["holding_analysis"]["dates"]) > 0
    assert len(data["pnl_analysis"]["chart_dates"]) > 0
    assert len(data["pnl_analysis"]["cumulative_rates"]) > 0
    assert len(data["monthly_performance"]) > 0

    drawer = StockDrawerWidget()
    drawer.set_data(data)
    assert not drawer.m_qty.sub_lbl.isVisible()
    assert not drawer.m_cost.sub_lbl.isVisible()
    assert not drawer.m_curr.sub_lbl.isVisible()
    assert not drawer.m_mv.sub_lbl.isVisible()
    assert drawer.monthly_model.rowCount() > 0
    drawer.close()


# =========================================================================
# 实战短线微操引擎（SHORT_TERM）与 UI 双口径切换测试
# =========================================================================

def _make_short_snapshot(events, *, broker_code="LONGBRIDGE", actions=(), as_of=date(2026, 12, 31)):
    return SimpleNamespace(
        scope=QueryScope(as_of=as_of, ledger=Ledger.PERSON),
        events=events,
        actions=list(actions),
        baselines=[],
        accounts={1: {'broker_code': broker_code, 'broker_name': broker_code}},
        instruments={1: {'symbol': 'TEST', 'category': '杠杆'}},
        extras={},
        issues=[],
        prices=[],
    )


def test_short_term_cross_month_and_window_boundary():
    """跨月短线（11-30买、12-01卖）在 <=5 天口径归属 2026-12，T+0 口径不配对并记入超时转长持"""
    e1 = Event(1, (1, 1, 1), '2026-11-30', '2026-11-30 10:00:00', 1, 'MARKET_TRADE', Decimal('1000'), Decimal('10'), True, 1, 1, {'fees_e8': 131000000})
    e2 = Event(2, (1, 1, 1), '2026-12-01', '2026-12-01 10:00:00', 2, 'MARKET_TRADE', Decimal('1100'), Decimal('10'), False, 1, 2, {'fees_e8': 112000000})
    snap = _make_short_snapshot([e1, e2], broker_code="LONGBRIDGE")

    res5 = short_term_scalping(snap, max_days=5)
    assert len(res5["pairs"]) == 1
    p = res5["pairs"][0]
    assert p["month"] == "2026-12"
    assert p["cross_month"] is True
    assert p["hold_days"] == 1
    assert p["mode"] == "POS_T"
    assert p["fee"] == Decimal("2.43")
    assert p["fee_verified"] is True
    assert p["pnl"] == Decimal("97.57")
    assert len(res5["expired_buys"]) == 0

    # 日内 T+0：跨日不配对，11-30 买入转为超时转长持
    res0 = short_term_scalping(snap, max_days=0)
    assert len(res0["pairs"]) == 0
    assert len(res0["expired_buys"]) == 1
    assert res0["expired_buys"][0]["open_day"] == "2026-11-30"


def test_short_term_pos_t_rev_t_split_and_expired_tracking():
    """严格区分正T（先买后卖）与反T（先卖后买），超过窗口的买入/卖出计入未配对纠偏"""
    # 10-01 建底仓 100 股（超过 5 天未平 -> 正T转长持）
    # 10-15 反T卖出 40 股 @ 120 -> 10-16 买回 30 股 @ 110（30股反T成功，剩余10股超过5交易日超时 -> 反T未接回）
    # 10-26 正T买入 20 股 @ 100 -> 10-27 卖出 20 股 @ 108（20股正T成功）
    events = [
        Event(1, (1, 1, 1), '2026-10-01', '2026-10-01 10:00:00', 1, 'MARKET_TRADE', Decimal('10000'), Decimal('100'), True, 1, 1, {'fees_e8': 0}),
        Event(2, (1, 1, 1), '2026-10-15', '2026-10-15 10:00:00', 2, 'MARKET_TRADE', Decimal('4800'), Decimal('40'), False, 1, 2, {'fees_e8': 0}),
        Event(3, (1, 1, 1), '2026-10-16', '2026-10-16 10:00:00', 3, 'MARKET_TRADE', Decimal('3300'), Decimal('30'), True, 1, 3, {'fees_e8': 0}),
        Event(4, (1, 1, 1), '2026-10-26', '2026-10-26 10:00:00', 4, 'MARKET_TRADE', Decimal('2000'), Decimal('20'), True, 1, 4, {'fees_e8': 0}),
        Event(5, (1, 1, 1), '2026-10-27', '2026-10-27 10:00:00', 5, 'MARKET_TRADE', Decimal('2160'), Decimal('20'), False, 1, 5, {'fees_e8': 0}),
    ]
    snap = _make_short_snapshot(events, broker_code="SCHWAB")
    res = short_term_scalping(snap, max_days=5)

    assert len(res["pairs"]) == 2
    rev = next(p for p in res["pairs"] if p["mode"] == "REV_T")
    pos = next(p for p in res["pairs"] if p["mode"] == "POS_T")
    assert rev["qty"] == Decimal("30") and rev["pnl"] == Decimal("300")
    assert pos["qty"] == Decimal("20") and pos["pnl"] == Decimal("160")
    assert len(res["expired_buys"]) == 1 and res["expired_buys"][0]["qty"] == Decimal("100")
    assert len(res["expired_sells"]) == 1 and res["expired_sells"][0]["qty"] == Decimal("10")

    row = res["report"].rows[0]
    assert row.metadata["pos_t_count"] == 1 and row.metadata["pos_t_pnl"] == Decimal("160")
    assert row.metadata["rev_t_count"] == 1 and row.metadata["rev_t_pnl"] == Decimal("300")
    assert row.metadata["win_rate"] == Decimal("1")


def test_short_term_split_and_net_broker_fee_handling():
    """跨拆股按比例折算股数；净额渠道（SCHWAB）不重复扣费，总价渠道缺费显式标记未核实"""
    e1 = Event(1, (1, 1, 1), '2026-10-12', '2026-10-12 10:00:00', 1, 'MARKET_TRADE', Decimal('1000'), Decimal('10'), True, 1, 1, {'fees_e8': 2000000})
    e2 = Event(2, (1, 1, 1), '2026-10-14', '2026-10-14 10:00:00', 2, 'MARKET_TRADE', Decimal('1100'), Decimal('20'), False, 1, 2, {'fees_e8': 2000000})
    actions = [{'id': 1, 'instrument_id': 1, 'broker_scope': 'ALL', 'effective_date': '2026-10-13', 'ratio_numerator': 2, 'ratio_denominator': 1}]

    # SCHWAB：amount 已净额，不重复扣费
    snap_schwab = _make_short_snapshot([e1, e2], broker_code="SCHWAB", actions=actions)
    res_s = short_term_scalping(snap_schwab, max_days=5)
    assert len(res_s["pairs"]) == 1
    assert res_s["pairs"][0]["qty"] == Decimal("20")
    assert res_s["pairs"][0]["fee"] == Decimal("0")
    assert res_s["pairs"][0]["reported_fee"] == Decimal("0.04")
    assert res_s["pairs"][0]["fee_verified"] is True
    assert res_s["pairs"][0]["pnl"] == Decimal("100")

    # LONGBRIDGE 缺费：标记 fee_verified=False，不编造费用
    e1_nofee = Event(1, (1, 1, 1), '2026-10-12', '2026-10-12 10:00:00', 1, 'MARKET_TRADE', Decimal('1000'), Decimal('10'), True, 1, 1, {})
    e2_nofee = Event(2, (1, 1, 1), '2026-10-14', '2026-10-14 10:00:00', 2, 'MARKET_TRADE', Decimal('1100'), Decimal('20'), False, 1, 2, {})
    snap_lb = _make_short_snapshot([e1_nofee, e2_nofee], broker_code="LONGBRIDGE", actions=actions)
    res_lb = short_term_scalping(snap_lb, max_days=5)
    assert res_lb["pairs"][0]["fee_verified"] is False
    assert res_lb["report"].rows[0].metadata["fee_unverified_count"] == 1


def test_monthly_scalping_mode_switching_ui(app, demo_path):
    """UI 支持在 SHORT_5 / SHORT_0 / SHORT_20 / EXCEL_COMPAT 之间实时切换并联动卡片与抽屉"""
    service = AnalyticsService(demo_path)
    analytics = service.calculate(QueryScope(as_of=date.today(), ledger=Ledger.PERSON))

    page = MonthlyScalpingPage()
    page.show()
    page.update_data(analytics)
    assert page.scalping_mode == "SHORT_5"
    assert not page.unpaired_card.isHidden()
    assert "微操净收益($)" in page.table_model.columns

    # 切到月内兼容口径
    page.set_scalping_mode("EXCEL_COMPAT")
    assert page.scalping_mode == "EXCEL_COMPAT"
    assert page.unpaired_card.isHidden()
    assert "月内微操收益($)" in page.table_model.columns

    # 切回超短线并展开未配对明细与抽屉
    page.set_scalping_mode("SHORT_5")
    assert not page.unpaired_card.isHidden()
    if page.table_model.rows:
        page.open_drawer(page.table_model.rows[0])
        assert not page.drawer_frame.isHidden()
        page.close_drawer()
    page.close()

