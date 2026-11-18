"""国内资产 · 资产总览单元测试与 UI 离屏验证。"""
from decimal import Decimal
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from portfolio.domestic.overview import build_domestic_overview
from portfolio.ui.domestic_overview_page import DomesticOverviewPage
from portfolio.ui.sidebar import GROUPS


def test_sidebar_has_domestic_overview_first():
    domestic_group = next(items for name, items in GROUPS if name == "国内资产")
    assert domestic_group[0] == ("资产总览", 31, "grid", None)


def test_build_domestic_overview_aggregates_and_filters():
    data = build_domestic_overview()
    assert len(data["kpis"]) == 18
    names = [k["name"] for k in data["kpis"]]
    assert names[0] == "当前总资产"
    assert "累计净投入" in names and "累计现金分红" in names
    assert "本月微操收益" not in names

    kpis = {k["name"]: k for k in data["kpis"]}
    for required in ("当前总资产", "最新日收益率", "最新日盈亏", "本月盈亏", "上月盈亏", "本年盈亏", "去年盈亏", "本月收益率", "本年收益率"):
        assert kpis[required]["value"] is not None, f"{required} should not be None"
    assert data["monthly_trend"] and data["monthly_trend"][0]["pnl"] is not None

    # 平台汇总应包含同级别渠道（支付宝、天天基金、同花顺等），且均包含已清仓收益
    plat_map = {r["name"]: r for r in data["platform_summary"]}
    assert {"天天基金", "支付宝", "同花顺"}.issubset(plat_map.keys())
    for p_name in ("天天基金", "支付宝", "同花顺"):
        assert plat_map[p_name]["closed_pnl"] is not None, f"{p_name} closed_pnl should not be None"

    # 平台汇总之和与顶部 KPI 严格一致（误差 < 0.01）
    assert abs(sum((r["asset"] or Decimal(0) for r in data["platform_summary"]), Decimal(0)) - kpis["当前总资产"]["value"]) < Decimal("0.01")
    assert abs(sum((r["closed_pnl"] or Decimal(0) for r in data["platform_summary"]), Decimal(0)) - kpis["已清仓收益"]["value"]) < Decimal("0.01")
    assert abs(sum((r["floating_pnl"] or Decimal(0) for r in data["platform_summary"]), Decimal(0)) - kpis["浮动盈亏"]["value"]) < Decimal("0.01")
    assert abs(sum((r["total_pnl"] or Decimal(0) for r in data["platform_summary"]), Decimal(0)) - kpis["全时期总盈亏"]["value"]) < Decimal("0.01")
    assert abs(sum((r["year_pnl"] or Decimal(0) for r in data["platform_summary"]), Decimal(0)) - kpis["本年盈亏"]["value"]) < Decimal("0.01")

    # 季度合计与全年盈亏严格一致
    for q_row in data["quarterly_rows"]:
        q_sum = sum((q_row[k] for k in ("q1", "q2", "q3", "q4") if q_row[k] is not None), Decimal(0))
        assert abs(q_sum - q_row["year_pnl"]) < Decimal("0.01")

    # 资产总览的当前总资产、浮动盈亏必须与持仓管理页面完全一致（均采用摊薄成本）
    from portfolio.domestic.holdings_query import holdings
    from portfolio.ui.domestic_holdings_dashboard import holding_metrics
    from portfolio.ui.domestic_holdings_page import show_in_overview

    open_rows = [r for r in holdings()["rows"] if show_in_overview(r) and r.get("current_position_status") == "持有中"]
    _, h_stats = holding_metrics(open_rows)
    assert abs(h_stats["market"] - kpis["当前总资产"]["value"]) < Decimal("0.01")
    assert abs(h_stats["floating"] - kpis["浮动盈亏"]["value"]) < Decimal("0.01")

    # 按渠道过滤联动
    ths_only = build_domestic_overview(platform="同花顺")
    assert len(ths_only["kpis"]) == 18
    assert {r["name"] for r in ths_only["platform_summary"]} == {"同花顺"}


def test_domestic_overview_page_renders_and_emits_jump_to_day(qtbot):
    app = QApplication.instance() or QApplication([])
    assert app is not None

    class _Host:
        def _submit(self, _key, fn, cb):
            cb(fn(lambda: False))

    page = DomesticOverviewPage(_Host())
    qtbot.addWidget(page)
    page.resize(1300, 900)
    page.reload()

    assert len(page.cards) == 18
    assert page.platform_combo.count() >= 2
    assert page.type_combo.count() >= 2
    assert page.summaries.selector.count() == 4
    assert page.summaries.table.finance_model.center_aligned is True
    idx0 = page.summaries.table.finance_model.index(0, 0)
    idx1 = page.summaries.table.finance_model.index(0, 1)
    assert page.summaries.table.finance_model.data(idx0, Qt.ItemDataRole.TextAlignmentRole) == int(Qt.AlignmentFlag.AlignCenter)
    assert page.summaries.table.finance_model.data(idx1, Qt.ItemDataRole.TextAlignmentRole) == int(Qt.AlignmentFlag.AlignCenter)
    assert page.calendar_table.rowCount() >= 4
    assert page.monthly_table.rowCount() == 4

    # 查找一个带有日期 UserRole 的日历单元格并验证点击联动信号
    emitted = []
    page.jump_to_day.connect(emitted.append)
    found = None
    for r in range(page.calendar_table.rowCount()):
        for c in range(5):
            item = page.calendar_table.item(r, c)
            if item and item.data(Qt.ItemDataRole.UserRole):
                found = (r, c, item.data(Qt.ItemDataRole.UserRole))
                break
        if found:
            break
    assert found is not None
    page._on_calendar_clicked(found[0], found[1])
    assert emitted == [found[2]]
