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

    # 平台汇总应包含同级别渠道（支付宝、天天基金、同花顺等）
    plat_names = {r["name"] for r in data["platform_summary"]}
    assert "天天基金" in plat_names
    assert "支付宝" in plat_names
    assert "同花顺" in plat_names

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
    assert page.summary_tabs.count() == 4
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
