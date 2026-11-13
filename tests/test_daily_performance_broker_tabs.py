from PySide6.QtWidgets import QLabel

from portfolio.ui.daily_performance_page import DailyPerformancePage


def _make_daily_row(day: str, broker: str, ticker: str, pnl: float):
    row = [None] * 23
    row[0] = day
    row[1] = broker
    row[3] = ticker
    row[19] = pnl
    return tuple(row)


def _broker_pnl_map(page: DailyPerformancePage) -> dict[str, str]:
    result = {}
    for i in range(page.broker_list_layout.count()):
        frame = page.broker_list_layout.itemAt(i).widget()
        if frame is None or not frame.layout():
            continue
        labels = [w for w in frame.findChildren(QLabel)]
        if len(labels) >= 3:
            result[labels[0].text()] = labels[-1].text()
    return result


def test_daily_performance_broker_overview_daily_and_cumulative_tabs(qtbot):
    """验证每日持仓收益右侧渠道概览支持『当日渠道收益 / 累计渠道收益』切换且默认选中当日。"""
    page = DailyPerformancePage()
    qtbot.addWidget(page)

    rows = [
        _make_daily_row("2026-11-12", "LONGBRIDGE", "TQQQ", 100.0),
        _make_daily_row("2026-11-12", "SCHWAB", "NVDA", -40.0),
        _make_daily_row("2026-11-13", "LONGBRIDGE", "TQQQ", 25.0),
        _make_daily_row("2026-11-13", "SCHWAB", "NVDA", 60.0),
    ]
    page.raw_rows = rows
    page.current_filtered_rows = rows
    page.max_as_of_date = "2026-11-13"
    page.selected_date = "2026-11-13"

    # 默认应选中「当日渠道收益」
    assert page._broker_overview_mode == "daily"
    assert page.broker_tab_daily.isChecked()
    assert not page.broker_tab_cum.isChecked()

    page.update_broker_overview(rows)
    assert page.th_b_pnl.text() == "当日收益"
    assert page.broker_scope_lbl.text() == "2026-11-13"
    assert _broker_pnl_map(page) == {
        "嘉信": "+60.00",
        "长桥": "+25.00",
    }

    # 切换选中日期到 2026-11-12，当日渠道收益应联动刷新
    page.select_date("2026-11-12")
    assert page.broker_scope_lbl.text() == "2026-11-12"
    assert _broker_pnl_map(page) == {
        "长桥": "+100.00",
        "嘉信": "-40.00",
    }

    # 切换到「累计渠道收益」，展示全期间累计
    page.broker_tab_cum.click()
    assert page._broker_overview_mode == "cum"
    assert page.broker_tab_cum.isChecked()
    assert page.th_b_pnl.text() == "累计收益"
    assert page.broker_scope_lbl.text() == "(期间累计)"
    assert _broker_pnl_map(page) == {
        "长桥": "+125.00",
        "嘉信": "+20.00",
    }

    # 切回「当日渠道收益」
    page.broker_tab_daily.click()
    assert page._broker_overview_mode == "daily"
    assert page.th_b_pnl.text() == "当日收益"
    assert _broker_pnl_map(page) == {
        "长桥": "+100.00",
        "嘉信": "-40.00",
    }
