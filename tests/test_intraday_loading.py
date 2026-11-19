from datetime import datetime, timezone

from portfolio.ui.intraday_pnl import IntradayPnlChart


def test_intraday_chart_shows_loading_until_data_arrives_and_pauses_when_hidden(qtbot):
    chart = IntradayPnlChart()
    qtbot.addWidget(chart)
    chart.resize(1200, 460)
    chart.day = "2026-11-19"
    chart.has_report = True
    chart.history = ()
    chart.history_state = {
        "state": "NOT_GENERATED",
        "pipeline": {
            "running": True,
            "state": "FETCHING",
            "message": "正在获取缺失的真实五分钟K线；已有本地数据保留。",
        },
    }
    chart.show()
    chart.draw()

    assert "正在加载当日五分钟走势" in chart.canvas.loading_message
    assert "正在获取缺失的真实五分钟K线" in chart.canvas.loading_message
    assert chart.canvas.loading_timer.isActive()
    assert chart.status.isHidden()

    # 隐藏时停止动画定时器，避免后台空耗
    chart.hide()
    assert not chart.canvas.loading_timer.isActive()
    chart.show()
    assert chart.canvas.loading_timer.isActive()

    # 折线点数据到达后，立即结束加载态并正常绘制折线
    chart.history = (
        (
            datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
            "History5",
            [{"channel": "长桥", "ticker": "NVDA", "pnl": "123.45", "person_id": 1}],
        ),
    )
    chart.draw()
    assert chart.canvas.loading_message == ""
    assert not chart.canvas.loading_timer.isActive()


def test_intraday_chart_does_not_spin_on_filtered_empty_or_terminal_states(qtbot):
    chart = IntradayPnlChart()
    qtbot.addWidget(chart)
    chart.day = "2026-11-19"
    chart.has_report = True
    chart.history = (
        (
            datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
            "History5",
            [{"channel": "长桥", "ticker": "NVDA", "pnl": "123.45", "person_id": 1}],
        ),
    )
    chart.show()

    # 搜索不到匹配标的时不显示加载中
    chart.draw(search="NONEXISTENT")
    assert chart.canvas.loading_message == ""
    assert chart.canvas.empty_message == "当前筛选条件下暂无当日走势数据"

    # 明确无美股持仓或缺少凭证时不显示加载中
    chart.history = ()
    chart.history_state = {
        "state": "AVAILABLE",
        "pipeline": {"running": True, "state": "NO_HOLDINGS", "message": "无持仓"},
    }
    chart.draw()
    assert chart.canvas.loading_message == ""
    assert "美股持仓" in chart.canvas.empty_message
