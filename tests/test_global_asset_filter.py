"""资产管理全局筛选组件 GlobalAssetFilter 综合验收测试。"""
from datetime import date

from PySide6.QtCore import Qt

from portfolio.domain.models import Ledger, QueryScope
from portfolio.ui.global_asset_filter import GlobalAssetFilter, GlobalAssetFilterState
from portfolio.ui.main_window import MainWindow
from portfolio.ui.sidebar import GROUPS


def _open_demo(qtbot, demo_path, *, settings=False):
    w = MainWindow(settings=settings)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: w.analytics is not None and w.global_asset_filter.isEnabled(), timeout=15000)
    return w


def test_single_instance_and_page_switching_preserves_state(qtbot, demo_path):
    """验收 1, 2, 3, 4, 5, 15：唯一 GlobalAssetFilter 实例跨资产总览/今日盈亏/持仓管理共享且状态完全同步。"""
    w = _open_demo(qtbot, demo_path)

    # 15. 整个主窗口中只有一个 GlobalAssetFilter 实例，且位于 QStackedWidget 外部
    instances = w.findChildren(GlobalAssetFilter)
    assert len(instances) == 1
    gaf = instances[0]
    assert gaf is w.global_asset_filter
    assert w.pages.indexOf(gaf) == -1
    gaf_id = id(gaf)

    # 1. 在资产总览选择人员 A（person_id=2），切换今日盈亏与持仓管理，仍然选择人员 A
    w.navigation.select(2, None)
    assert not gaf.isHidden()
    assert w.filters_widget.isHidden()
    gaf.set_person(2, emit=True)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,), timeout=15000)

    w.navigation.select(3, "今日盈亏")
    assert id(w.global_asset_filter) == gaf_id
    assert not gaf.isHidden()
    assert gaf.state().person == 2
    assert w.analytics.scope.person_ids == (2,)

    w.navigation.select(22, None)
    assert id(w.global_asset_filter) == gaf_id
    assert not gaf.isHidden()
    assert gaf.state().person == 2
    assert w.holdings_page._selected_person_filter == 2

    # 2. 在今日盈亏选择两个人员（多选），切换其他两个页面，多选状态不变
    w.navigation.select(3, "今日盈亏")
    gaf.set_person((1, 2), emit=True)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (1, 2), timeout=15000)

    w.navigation.select(2, None)
    assert gaf.state().person == (1, 2)
    assert w.analytics.scope.person_ids == (1, 2)

    w.navigation.select(22, None)
    assert gaf.state().person == (1, 2)
    assert w.holdings_page._selected_person_filter == (1, 2)

    # 3. 在持仓管理选择指定渠道，切换资产总览，渠道条件保持一致
    first_acc_id = next(iter(gaf.account_filter.actions_by_id))
    gaf.set_accounts((first_acc_id,), emit=True)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.account_ids == (first_acc_id,), timeout=15000)
    w.navigation.select(2, None)
    assert gaf.state().account_ids == (first_acc_id,)
    assert w.analytics.scope.account_ids == (first_acc_id,)

    # 4 & 5. 同时选择人员、渠道、股票代码，三个页面查询范围一致
    first_inst_id = gaf.ticker_search.itemData(1)
    assert first_inst_id is not None
    gaf.set_state(GlobalAssetFilterState.normalized(person=1, account_ids=(first_acc_id,), instrument_id=first_inst_id), emit=True)
    qtbot.waitUntil(
        lambda: (
            w.analytics is not None
            and w.analytics.scope.person_ids == (1,)
            and w.analytics.scope.account_ids == (first_acc_id,)
            and w.analytics.scope.instrument_ids == (first_inst_id,)
        ),
        timeout=15000,
    )
    for nav_args in ((3, "今日盈亏"), (22, None), (2, None)):
        w.navigation.select(*nav_args)
        assert w.analytics.scope.person_ids == (1,)
        assert w.analytics.scope.account_ids == (first_acc_id,)
        assert w.analytics.scope.instrument_ids == (first_inst_id,)
        assert id(w.global_asset_filter) == gaf_id


def test_unconfirmed_ticker_text_not_applied(qtbot, demo_path):
    """未确认的 Ticker 输入内容不得被当作生效的筛选条件。"""
    w = _open_demo(qtbot, demo_path)
    w.navigation.select(2, None)
    gaf = w.global_asset_filter
    gaf.ticker_search.lineEdit().setText("NOT_EXIST_TICKER")
    assert gaf.state().instrument_id is None
    assert w.make_scope().instrument_ids is None


def test_holdings_drill_and_mode_switch_and_reset(qtbot, demo_path):
    """验收 6, 7, 11：持仓管理下钻反向更新全局筛选；内部视图切换不丢全局条件；重置恢复默认状态。"""
    w = _open_demo(qtbot, demo_path)
    gaf = w.global_asset_filter
    w.navigation.select(22, None)

    # 6. 在持仓管理下钻到某个人员后，全局筛选栏同步更新
    w.sync_person_from_holdings(2)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,), timeout=15000)
    assert gaf.state().person == 2

    # 7. 在持仓管理切换仪表板、持仓明细、已清仓时，全局条件不丢失
    w.holdings_page.switch_view_mode("dashboard")
    assert gaf.state().person == 2
    w.holdings_page.switch_view_mode("detail")
    assert gaf.state().person == 2
    w.holdings_page.open_closed_positions()
    assert gaf.state().person == 2

    # 11. 点击重置按钮，三个条件恢复默认状态（个人 / 全部渠道 / 全部股票）
    first_acc_id = next(iter(gaf.account_filter.actions_by_id))
    first_inst_id = gaf.ticker_search.itemData(1)
    gaf.set_accounts((first_acc_id,), emit=True)
    gaf.set_instrument(first_inst_id, emit=True)
    gaf.reset_button.click()
    expected_pid = (gaf.default_person_data(),) if gaf.default_person_data() is not None else None
    qtbot.waitUntil(
        lambda: (
            w.analytics is not None
            and w.analytics.scope.person_ids == expected_pid
            and w.analytics.scope.account_ids is None
            and w.analytics.scope.instrument_ids is None
        ),
        timeout=15000,
    )
    assert gaf.state().account_ids is None
    assert gaf.state().instrument_id is None


def test_special_pages_hide_global_filter_and_stay_independent(qtbot, demo_path):
    """验收 8, 9, 10：进入股票归属和财报日历时隐藏 GlobalAssetFilter，不清空状态且不受全局筛选影响；返回后恢复。"""
    w = _open_demo(qtbot, demo_path)
    gaf = w.global_asset_filter

    # 在资产总览设置特定筛选条件（单选 person=2 + 指定渠道）
    w.navigation.select(2, None)
    first_acc_id = next(iter(gaf.account_filter.actions_by_id))
    gaf.set_person(2, emit=True)
    gaf.set_accounts((first_acc_id,), emit=True)
    qtbot.waitUntil(
        lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,) and w.analytics.scope.account_ids == (first_acc_id,),
        timeout=15000,
    )

    # 9. 进入股票归属：隐藏 GlobalAssetFilter，但不销毁/清空，且查询全部人员不受全局人员筛选影响
    w.navigation.select(3, "股票归属")
    assert gaf.isHidden()
    assert gaf.state().person == 2
    assert gaf.state().account_ids == (first_acc_id,)
    qtbot.waitUntil(lambda: w.asset_page.ownership_page.bundle is not None, timeout=15000)
    assert w.asset_page.ownership_page.bundle.aggregate.scope.person_ids is None

    # 10. 进入持仓财报日历：隐藏 GlobalAssetFilter，独立查询全部启用人员的有效持仓且不漏掉其他人持仓
    w.navigation.select(14, None)
    assert gaf.isHidden()
    assert gaf.state().person == 2
    assert gaf.state().account_ids == (first_acc_id,)
    qtbot.waitUntil(lambda: w.earnings_page.analytics is not None, timeout=15000)
    assert w.earnings_page.analytics.scope.person_ids is None
    assert w.earnings_page.analytics.scope.account_ids is None
    assert w.earnings_page.analytics.scope.instrument_ids is None

    # 8. 返回资产总览：GlobalAssetFilter 重新显示且原条件完整保留
    w.navigation.select(2, None)
    assert not gaf.isHidden()
    assert gaf.state().person == 2
    assert gaf.state().account_ids == (first_acc_id,)


def test_history_navigation_and_calculation_parity(qtbot, demo_path):
    """验收 12, 14：前进/后退不回退过期全局筛选，无重复信号或循环刷新；相同筛选下计算结果与直接计算一致。"""
    w = _open_demo(qtbot, demo_path)
    gaf = w.global_asset_filter
    emissions = []
    gaf.filterChanged.connect(lambda st: emissions.append(st))

    w.navigation.select(2, None)
    w.navigation.select(3, "今日盈亏")
    gaf.set_person(2, emit=True)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,), timeout=15000)
    assert len(emissions) == 1

    # 后退到资产总览，全局筛选不应回退为旧的 person=1
    w.move_history(-1)
    assert gaf.state().person == 2
    assert w.make_scope().person_ids == (2,)
    assert len(emissions) == 1  # 导航不触发重复 filterChanged

    # 前进回今日盈亏
    w.move_history(1)
    assert gaf.state().person == 2
    assert w.make_scope().person_ids == (2,)

    # 14. 计算结果与直接调用 analytics_service.calculate 一致
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,), timeout=15000)
    direct = w.analytics_service.calculate(w.analytics.scope)
    assert w.analytics.reports["当前持仓"].rows == direct.reports["当前持仓"].rows
    assert w.analytics.metrics == direct.metrics


def test_persistence_across_restart(qtbot, demo_path, tmp_path):
    """验收 13：关闭并重新打开程序后，全局筛选状态按照原有持久化规则恢复。"""
    from PySide6.QtCore import QSettings

    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)

    w1 = _open_demo(qtbot, demo_path, settings=True)
    first_acc_id = next(iter(w1.global_asset_filter.account_filter.actions_by_id))
    w1.global_asset_filter.set_person(2, emit=True)
    w1.global_asset_filter.set_accounts((first_acc_id,), emit=True)
    qtbot.waitUntil(lambda: w1.analytics is not None and w1.analytics.scope.person_ids == (2,), timeout=15000)
    w1.close()

    w2 = MainWindow(settings=True)
    qtbot.addWidget(w2)
    w2.show()
    w2.open_database(demo_path)
    qtbot.waitUntil(lambda: w2.analytics is not None and w2.global_asset_filter.isEnabled(), timeout=15000)
    assert w2.global_asset_filter.state().person == 2
    assert w2.global_asset_filter.state().account_ids == (first_acc_id,)
    w2.close()


def test_five_pages_share_global_filter_and_sidebar_order(qtbot, demo_path):
    """验证左侧导航顺序及 5 个资产管理页面共用同一个 GlobalAssetFilter 实例、联动筛选、局部重置与前进后退。"""
    asset_titles = [entry[0] for entry in GROUPS[0][1]]
    assert asset_titles == [
        "资产总览",
        "今日盈亏",
        "持仓管理",
        "每日持仓收益",
        "每月持仓收益",
        "股票归属",
        "持仓财报日历",
    ]
    trade_titles = [entry[0] for entry in GROUPS[2][1]]
    assert "每日持仓收益" not in trade_titles
    assert "每月持仓收益" not in trade_titles

    w = _open_demo(qtbot, demo_path)
    gaf = w.global_asset_filter
    gaf_id = id(gaf)

    # 在每日持仓收益(23) 设置多选人员 + 渠道 + Ticker，验证 5 个页面均共享同一实例与状态
    w.navigation.select(23, "每日持仓收益")
    assert not gaf.isHidden()
    assert w.daily_performance_page.person_combo.isHidden()
    assert w.daily_performance_page.broker_combo.isHidden()
    assert w.daily_performance_page.ticker_input.isHidden()

    first_acc_id = next(iter(gaf.account_filter.actions_by_id))
    first_inst_id = gaf.ticker_search.itemData(1)
    gaf.set_state(
        GlobalAssetFilterState.normalized(person=(1, 2), account_ids=(first_acc_id,), instrument_id=first_inst_id),
        emit=True,
    )
    qtbot.waitUntil(
        lambda: (
            w.analytics is not None
            and w.analytics.scope.person_ids == (1, 2)
            and w.analytics.scope.account_ids == (first_acc_id,)
            and w.analytics.scope.instrument_ids == (first_inst_id,)
        ),
        timeout=15000,
    )

    # 局部重置不清空全局筛选
    w.daily_performance_page.reset_filters()
    assert gaf.state().person == (1, 2)
    assert gaf.state().account_ids == (first_acc_id,)
    assert gaf.state().instrument_id == first_inst_id

    for nav_args in ((24, "每月持仓收益"), (2, None), (3, "今日盈亏"), (22, None), (23, "每日持仓收益")):
        w.navigation.select(*nav_args)
        assert id(w.global_asset_filter) == gaf_id
        assert not gaf.isHidden()
        assert gaf.state().person == (1, 2)
        assert gaf.state().account_ids == (first_acc_id,)
        assert gaf.state().instrument_id == first_inst_id
        assert w.analytics.scope.person_ids == (1, 2)
        assert w.analytics.scope.account_ids == (first_acc_id,)
        assert w.analytics.scope.instrument_ids == (first_inst_id,)

    # 每月持仓收益隐藏重复局部控件，且局部重置不清空全局筛选
    w.navigation.select(24, "每月持仓收益")
    assert w.monthly_performance_page.person_combo.isHidden()
    assert w.monthly_performance_page.broker_combo.isHidden()
    assert w.monthly_performance_page.ticker_input.isHidden()
    w.monthly_performance_page.on_reset_clicked()
    assert gaf.state().person == (1, 2)

    # 统一重置按钮对 5 个页面均生效
    gaf.reset_button.click()
    expected_pid = (gaf.default_person_data(),) if gaf.default_person_data() is not None else None
    qtbot.waitUntil(
        lambda: (
            w.analytics is not None
            and w.analytics.scope.person_ids == expected_pid
            and w.analytics.scope.account_ids is None
            and w.analytics.scope.instrument_ids is None
        ),
        timeout=15000,
    )

    # 后退/前进不回退全局筛选
    gaf.set_person(2, emit=True)
    qtbot.waitUntil(lambda: w.analytics is not None and w.analytics.scope.person_ids == (2,), timeout=15000)
    w.move_history(-1)
    assert gaf.state().person == 2
    w.move_history(1)
    assert gaf.state().person == 2
