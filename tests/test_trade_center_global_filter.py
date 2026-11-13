"""验证交易明细侧边栏位置迁移及接入顶部 GlobalAssetFilter 全局筛选器。"""

from portfolio.ui.global_asset_filter import GlobalAssetFilterState
from portfolio.ui.main_window import MainWindow
from portfolio.ui.sidebar import GROUPS


def test_trade_detail_sidebar_moved_between_monthly_and_ownership():
    """交易明细应位于资产管理分组的每月持仓收益与股票归属之间，并从交易与收益移除。"""
    asset_titles = [entry[0] for entry in GROUPS[0][1]]
    assert "交易明细" in asset_titles
    assert asset_titles.index("每月持仓收益") + 1 == asset_titles.index("交易明细")
    assert asset_titles.index("交易明细") + 1 == asset_titles.index("股票归属")

    trade_titles = [entry[0] for entry in GROUPS[2][1]]
    assert "交易明细" not in trade_titles


def test_trade_detail_uses_global_asset_filter_and_syncs_scope(qtbot, demo_path):
    """切换到交易明细时应显示全局筛选器、隐藏重复本地筛选框并同步 scope 触发刷新。"""
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    w.show()
    w.open_database(demo_path)
    qtbot.waitUntil(lambda: w.analytics is not None and w.global_asset_filter.isEnabled(), timeout=15000)

    gaf = w.global_asset_filter
    first_acc_id = next(iter(gaf.account_filter.actions_by_id))

    # 切到交易明细 (page == 0)
    w.navigation.select(0)
    qtbot.waitUntil(lambda: w.trade_center.data is not None, timeout=15000)

    assert not gaf.isHidden()
    assert len(w.trade_center._global_shadow_boxes) == 3
    assert all(box.isHidden() for box in w.trade_center._global_shadow_boxes)
    # 去掉原交易明细日期/关键词/已选条件/查询/重置区域，默认隐藏 filter_form，高级筛选移至顶部重置按钮旁
    assert w.trade_center.local_filter_bar.isHidden()
    assert w.trade_center.filter_form.isHidden()
    assert w.trade_center.note.isHidden()
    assert not gaf.trailing_host.isHidden()
    assert not w.trade_center.top_advanced_button.isHidden()
    assert w.trade_center.top_advanced_button.text() == "高级筛选 ▾"

    # 点击顶部「高级筛选」可展开/收起高级筛选面板
    w.trade_center.top_advanced_button.click()
    assert not w.trade_center.filter_form.isHidden()
    assert not w.trade_center.advanced.isHidden()
    assert w.trade_center.top_advanced_button.text() == "高级筛选 ▴"
    w.trade_center.top_advanced_button.click()
    assert w.trade_center.filter_form.isHidden()
    assert w.trade_center.advanced.isHidden()
    assert w.trade_center.top_advanced_button.text() == "高级筛选 ▾"

    # 在交易明细页修改全局筛选器：应同步到 TradeCenterPage.scope()
    gaf.set_state(
        GlobalAssetFilterState(person=(1,), account_ids=(first_acc_id,), instrument_id=1),
        emit=True,
    )
    scope = w.trade_center.scope()
    assert scope.person_ids == (1,)
    assert scope.account_ids == (first_acc_id,)
    assert scope.instrument_ids == (1,)

    # 切到资产总览修改全局筛选器，再切回交易明细：应自动同步并触发重新查询
    w.navigation.select(2)
    assert gaf.trailing_host.isHidden()
    query_calls = []
    orig_query = w.trade_center.query
    w.trade_center.query = lambda: (query_calls.append(True), orig_query())[1]

    gaf.set_state(
        GlobalAssetFilterState(person="OTHERS", account_ids=None, instrument_id=None),
        emit=True,
    )
    w.navigation.select(0)

    assert len(query_calls) == 1
    scope_after = w.trade_center.scope()
    assert scope_after.person_ids is not None
    assert scope_after.account_ids is None
    assert scope_after.instrument_ids is None
