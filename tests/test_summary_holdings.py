"""持仓管理「汇总」标签页跨渠道聚合服务与 UI 回归测试。"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

from portfolio.services.summary_holdings_analytics_service import (
    build_summary_holdings_dashboard,
)


def _sample_rows():
    open_rows = [
        {
            "person_id": 1,
            "person_name": "个人",
            "account_id": 101,
            "instrument_id": 1001,
            "channel": "长桥",
            "ticker": "AAOX",
            "category": "杠杆",
            "quantity": 300.0,
            "cost_price": 18.50,
            "cost_amount": 5550.00,
            "current_price": 17.10,
            "market_value": 5130.00,
            "total_pnl": -420.00,
            "total_buy_amount": 7420.00,
            "total_sell_amount": 2000.00,
            "buy_count": 4,
            "sell_count": 1,
            "status": "持有中",
        },
        {
            "person_id": 1,
            "person_name": "个人",
            "account_id": 101,
            "instrument_id": 1002,
            "channel": "长桥",
            "ticker": "QQQM",
            "category": "大盘",
            "quantity": 100.0,
            "cost_price": 200.00,
            "cost_amount": 20000.00,
            "current_price": 212.00,
            "market_value": 21200.00,
            "total_pnl": 1200.00,
            "total_buy_amount": 20000.00,
            "total_sell_amount": 0.0,
            "buy_count": 2,
            "sell_count": 0,
            "status": "持有中",
        },
    ]
    closed_rows = [
        {
            "person_id": 1,
            "person_name": "个人",
            "account_id": 102,
            "instrument_id": 1001,
            "channel": "BBAE",
            "ticker": "AAOX",
            "category": "杠杆",
            "quantity": 0.0,
            "current_price": 17.10,
            "market_value": 0.0,
            "realized_pnl": 1280.50,
            "total_pnl": 1280.50,
            "total_buy_amount": 6200.00,
            "total_sell_amount": 7480.50,
            "buy_count": 3,
            "sell_count": 3,
            "status": "已清仓",
        },
        {
            "person_id": 2,
            "person_name": "家属",
            "account_id": 102,
            "instrument_id": 1003,
            "channel": "BBAE",
            "ticker": "TSLL",
            "category": "杠杆",
            "quantity": 0.0,
            "current_price": 14.80,
            "market_value": 0.0,
            "realized_pnl": -500.00,
            "total_pnl": -500.00,
            "total_buy_amount": 3000.00,
            "total_sell_amount": 2500.00,
            "buy_count": 1,
            "sell_count": 1,
            "status": "已清仓",
        },
    ]
    return open_rows, closed_rows


def test_cross_channel_merge_and_conservation():
    """验证 AAOX 跨渠道（长桥持有 + BBAE 清仓）合并为一行，且全局金额守恒。"""
    open_rows, closed_rows = _sample_rows()
    payload = build_summary_holdings_dashboard(None, open_rows, closed_rows)

    rows_by_tk = {r["ticker"]: r for r in payload["rows"]}
    assert set(rows_by_tk.keys()) == {"AAOX", "QQQM", "TSLL"}

    aaox = rows_by_tk["AAOX"]
    assert aaox["status"] == "持有中"
    assert aaox["is_cross_channel"] is True
    assert aaox["channels_display"] == "长桥(持) · BBAE(清)"
    assert Decimal(str(aaox["quantity"])) == Decimal("300")
    assert Decimal(str(aaox["floating_pnl"])) == Decimal("-420.0")
    assert Decimal(str(aaox["realized_pnl"])) == Decimal("1280.5")
    assert Decimal(str(aaox["total_pnl"])) == Decimal("860.5")
    assert aaox["trade_counts_display"] == "7买 / 4卖"
    assert len(aaox["channel_breakdown"]) == 2

    # 守恒核对：汇总浮点/已实现/总盈亏 == 持仓中 + 已清仓
    cards = payload["cards"]
    assert Decimal(str(cards["floating_pnl"])) == Decimal("780.0")   # -420 + 1200
    assert Decimal(str(cards["realized_pnl"])) == Decimal("780.5")   # 1280.5 - 500
    assert Decimal(str(cards["total_pnl"])) == Decimal("1560.5")     # 780 + 780.5
    assert cards["stock_count"] == 3
    assert cards["open_stock_count"] == 2
    assert cards["closed_stock_count"] == 1
    assert cards["win_count"] == 2
    assert cards["loss_count"] == 1


def test_summary_filters_and_top_metrics():
    """验证跨渠道筛选、状态筛选、券商筛选与 Top N 口径切换。"""
    open_rows, closed_rows = _sample_rows()

    cross_only = build_summary_holdings_dashboard(None, open_rows, closed_rows, status_filter="cross")
    assert [r["ticker"] for r in cross_only["rows"]] == ["AAOX"]

    closed_only = build_summary_holdings_dashboard(None, open_rows, closed_rows, status_filter="closed")
    assert [r["ticker"] for r in closed_only["rows"]] == ["TSLL"]

    bbae_only = build_summary_holdings_dashboard(None, open_rows, closed_rows, broker="BBAE")
    assert {r["ticker"] for r in bbae_only["rows"]} == {"AAOX", "TSLL"}
    bbae_aaox = next(r for r in bbae_only["rows"] if r["ticker"] == "AAOX")
    assert bbae_aaox["status"] == "已全清仓"
    assert Decimal(str(bbae_aaox["realized_pnl"])) == Decimal("1280.5")
    assert Decimal(str(bbae_aaox["floating_pnl"])) == Decimal("0")

    top_float = build_summary_holdings_dashboard(None, open_rows, closed_rows, top_metric="floating")
    assert [x["ticker"] for x in top_float["top_contributors"]["gains"]] == ["QQQM"]
    assert [x["ticker"] for x in top_float["top_contributors"]["losses"]] == ["AAOX"]


def test_summary_dashboard_widgets_render_offscreen():
    """离屏渲染校验 SummaryHoldingsKpiBar、SummaryChartsRow、SummaryTopContributorsCard 与 StockDrawerWidget。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from portfolio.ui.holdings_page import StockDrawerWidget
    from portfolio.ui.summary_holdings_dashboard import (
        SummaryChartsRow,
        SummaryHoldingsKpiBar,
        SummaryTopContributorsCard,
    )

    app = QApplication.instance() or QApplication([])
    open_rows, closed_rows = _sample_rows()
    payload = build_summary_holdings_dashboard(None, open_rows, closed_rows)

    kpi = SummaryHoldingsKpiBar()
    kpi.set_cards_data(payload["cards"])
    assert "+1,560.50" in kpi.card_total.val_lbl.text()

    charts = SummaryChartsRow()
    charts.set_data(payload)
    assert charts.broker_table.rowCount() >= 2

    top_card = SummaryTopContributorsCard()
    top_card.set_data(payload["top_contributors"])
    assert "Top 5" in top_card.win_badge.text()

    drawer = StockDrawerWidget()
    drawer.set_data({
        "summary": {
            "ticker": "AAOX",
            "company_name": "AAOX",
            "tags": ["美股", "标的"],
            "current_quantity": 300.0,
            "cost_price": 18.5,
            "current_price": 17.1,
            "market_value": 5130.0,
            "floating_pnl": -420.0,
            "floating_pnl_rate": -0.0757,
            "cumulative_pnl": 860.5,
            "cumulative_pnl_rate": 0.0632,
        },
        "show_channel_breakdown": True,
        "channel_breakdown": [
            {"channel": "长桥", "status": "持有中", "quantity": 300.0, "floating_pnl": -420.0, "realized_pnl": 0.0, "subtotal_pnl": -420.0},
            {"channel": "BBAE", "status": "已清仓", "quantity": 0.0, "floating_pnl": 0.0, "realized_pnl": 1280.5, "subtotal_pnl": 1280.5},
        ],
        "trades": [],
        "holding_analysis": {},
        "pnl_analysis": {},
        "monthly_performance": [],
    })
    assert not drawer.channel_breakdown_box.isHidden()
    assert drawer.channel_breakdown_table.rowCount() == 3  # 长桥 + BBAE + 合并合计
    app.processEvents()
