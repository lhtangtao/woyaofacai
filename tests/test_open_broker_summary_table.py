import pytest
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QWidget

from portfolio.ui.open_holdings_dashboard import OpenHoldingsChartsRow, RatioProgressBarCell
from portfolio.ui.theme import numeric_font


@pytest.fixture
def chart_row(qtbot):
    row = OpenHoldingsChartsRow()
    row.resize(1200, 320)
    qtbot.addWidget(row)
    row.show()
    return row


def test_broker_summary_normal_and_large_amounts(chart_row, qtbot):
    """测试普通金额、较大金额与负数金额的完整格式、右对齐与无截断列宽分配。"""
    table = chart_row.broker_table
    headers = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
    assert headers == ["券商渠道", "市值占比", "持仓成本", "持仓市值", "浮动盈亏", "持仓数量"]

    # 1. 构造包含普通金额、较大金额（百万级）与深度亏损负数的渠道数据
    payload = {
        "cards": {
            "total_market_value": 3_500_000.50,
            "total_cost_amount": 3_200_000.00,
            "floating_pnl": 300_000.50,
            "stock_count": 24,
        },
        "broker_distribution": [
            {
                "channel": "长桥证券",
                "cost_amount": 2_450_800.75,
                "market_value": 2_890_450.25,
                "floating_pnl": 439_649.50,
                "ratio": 2_890_450.25 / 3_500_000.50,
                "percent": (2_890_450.25 / 3_500_000.50) * 100.0,
                "stock_count": 18,
            },
            {
                "channel": "嘉信理财",
                "cost_amount": 749_199.25,
                "market_value": 609_550.25,
                "floating_pnl": -139_649.00,  # 较大负数金额
                "ratio": 609_550.25 / 3_500_000.50,
                "percent": (609_550.25 / 3_500_000.50) * 100.0,
                "stock_count": 6,
            },
        ],
        "industry_distribution": [],
        "mv_trend": {},
    }

    chart_row.set_charts_data(payload)
    qtbot.wait(50)

    # 验证数据行数：2 行数据 + 1 行合计
    assert table.rowCount() == 3

    # 2. 验证金额列（持仓成本、持仓市值、浮动盈亏）右对齐，保留完整格式
    for r in range(table.rowCount()):
        for col_idx in (2, 3, 4):
            item = table.item(r, col_idx)
            assert item is not None
            # 对齐方式必须为 AlignRight
            assert bool(item.textAlignment() & Qt.AlignmentFlag.AlignRight)

    # 验证第 1 行嘉信理财的负数金额保留完整符号与两位小数
    neg_pnl_item = table.item(1, 4)
    assert neg_pnl_item.text() == "-139,649.00"

    # 验证第 0 行长桥的百万级持仓市值完整无截断
    large_mv_item = table.item(0, 3)
    assert large_mv_item.text() == "2,890,450.25"

    # 验证合计行金额与右对齐
    tot_row = 2
    assert table.item(tot_row, 0).text() == "合计"
    assert table.item(tot_row, 2).text() == "3,200,000.00"
    assert table.item(tot_row, 3).text() == "3,500,000.50"
    assert table.item(tot_row, 4).text() == "+300,000.50"
    for col_idx in (2, 3, 4):
        assert bool(table.item(tot_row, col_idx).textAlignment() & Qt.AlignmentFlag.AlignRight)

    # 3. 验证列宽能够完整容纳最大文本宽度，绝无省略号截断
    for col_idx in (2, 3, 4):
        col_w = table.columnWidth(col_idx)
        for r in range(table.rowCount()):
            it = table.item(r, col_idx)
            fm = QFontMetrics(it.font())
            text_w = fm.horizontalAdvance(it.text())
            assert col_w >= text_w, f"列 {col_idx} 宽度 {col_w} 小于文本 '{it.text()}' 宽度 {text_w}"


def test_broker_summary_total_coverage_and_no_quote(chart_row, qtbot):
    """测试合计行占比在有效完整覆盖下显示 100.0%，在无有效市值时显示 '—'。"""
    table = chart_row.broker_table

    # 场景 A: 存在有效总市值且各渠道完整覆盖
    payload_valid = {
        "cards": {
            "total_market_value": 100_000.0,
            "total_cost_amount": 90_000.0,
            "floating_pnl": 10_000.0,
            "stock_count": 8,
        },
        "broker_distribution": [
            {
                "channel": "长桥",
                "cost_amount": 50_000.0,
                "market_value": 60_000.0,
                "floating_pnl": 10_000.0,
                "ratio": 0.6,
                "percent": 60.0,
                "stock_count": 5,
            },
            {
                "channel": "嘉信",
                "cost_amount": 40_000.0,
                "market_value": 40_000.0,
                "floating_pnl": 0.0,
                "ratio": 0.4,
                "percent": 40.0,
                "stock_count": 3,
            },
        ],
        "industry_distribution": [],
        "mv_trend": {},
    }
    chart_row.set_charts_data(payload_valid)
    qtbot.wait(30)

    tot_row = table.rowCount() - 1
    assert table.item(tot_row, 1).data(Qt.ItemDataRole.UserRole) == "100.0%"
    tot_cell: RatioProgressBarCell = table.cellWidget(tot_row, 1)
    assert tot_cell is not None
    assert tot_cell.pbar.value() == 1000
    assert tot_cell.lbl is not None
    assert tot_cell.lbl.text() == "100.0%"
    # 验证百分比标签宽度已自适应
    fm = QFontMetrics(tot_cell.lbl.font())
    assert tot_cell.lbl.width() >= fm.horizontalAdvance("100.0%")

    # 场景 B: 无有效总市值（如行情未获取到，total_mv <= 0）
    payload_no_mv = {
        "cards": {
            "total_market_value": 0.0,
            "total_cost_amount": 50_000.0,
            "floating_pnl": -50_000.0,
            "stock_count": 2,
        },
        "broker_distribution": [
            {
                "channel": "长桥",
                "cost_amount": 50_000.0,
                "market_value": 0.0,
                "floating_pnl": -50_000.0,
                "ratio": 0.0,
                "percent": 0.0,
                "stock_count": 2,
            }
        ],
        "industry_distribution": [],
        "mv_trend": {},
    }
    chart_row.set_charts_data(payload_no_mv)
    qtbot.wait(30)

    tot_row_b = table.rowCount() - 1
    # 验证按规则不硬编码 100.0%，而是显示 "—"
    assert table.item(tot_row_b, 1).data(Qt.ItemDataRole.UserRole) == "—"
    tot_cell_b: RatioProgressBarCell = table.cellWidget(tot_row_b, 1)
    assert tot_cell_b is not None
    assert tot_cell_b.pbar.value() == 0
    assert tot_cell_b.lbl.text() == "—"


def test_broker_summary_window_resizing_and_scroll(chart_row, qtbot):
    """测试窗口缩放时自适应布局：小宽度优先缩短进度条且允许横向滚动保证金额完整，大宽度展开。"""
    payload = {
        "cards": {
            "total_market_value": 1_250_000.0,
            "total_cost_amount": 1_100_000.0,
            "floating_pnl": 150_000.0,
            "stock_count": 10,
        },
        "broker_distribution": [
            {
                "channel": "长桥证券",
                "cost_amount": 1_100_000.0,
                "market_value": 1_250_000.0,
                "floating_pnl": 150_000.0,
                "ratio": 1.0,
                "percent": 100.0,
                "stock_count": 10,
            }
        ],
        "industry_distribution": [],
        "mv_trend": {},
    }
    chart_row.set_charts_data(payload)
    qtbot.wait(30)

    table = chart_row.broker_table
    # 验证横向滚动条策略为 ScrollBarAsNeeded
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded

    # 1. 缩小至紧凑尺寸（模拟小窗口宽度）
    chart_row.resize(600, 320)
    qtbot.wait(30)

    # 验证金额列在紧凑窗口下依旧保留能完整展示金额的列宽，绝不被压小至截断
    for col_idx in (2, 3, 4):
        item = table.item(0, col_idx)
        fm = QFontMetrics(item.font())
        assert table.columnWidth(col_idx) >= fm.horizontalAdvance(item.text())

    # 2. 扩大至宽屏尺寸
    chart_row.resize(1600, 320)
    qtbot.wait(30)

    # 验证大窗口下进度条所在列（Col 1）拉宽展开
    wide_col1_w = table.columnWidth(1)
    assert wide_col1_w >= 80
