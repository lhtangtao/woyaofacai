from pathlib import Path
from types import SimpleNamespace

from portfolio.ui.main_window import MainWindow


def _setup_window(qtbot):
    w = MainWindow(settings=False)
    qtbot.addWidget(w)
    info = SimpleNamespace(
        path=Path("dummy.sqlite3"),
        persons=[{"id": 1, "name": "个人", "enabled": True}],
        accounts=[
            {"id": 101, "broker_code": "LONGBRIDGE", "enabled": True},
            {"id": 102, "broker_code": "SCHWAB", "enabled": True},
        ],
        instruments=[
            {"id": 201, "symbol": "GGLL", "market": "US"},
            {"id": 202, "symbol": "TQQQ", "market": "US"},
        ],
    )
    w.info = info
    w.accounts.populate(info.accounts)
    w.stock.clear()
    w.stock.addItem("全部股票", None)
    for inst in info.instruments:
        w.stock.addItem(f"{inst['symbol']} · {inst['market']}", inst["id"])
    w._populate_persons()
    w.person.setCurrentIndex(w.person.findData(1))
    w.global_asset_filter.populate(info.persons, info.accounts, info.instruments)
    w._sync_global_filter_from_legacy()
    return w


def test_drill_record_from_today_pnl_carries_broker_and_ticker_to_trade_center(qtbot):
    w = _setup_window(qtbot)
    w.drill_record({"account_id": 101, "instrument_id": 201})

    assert w.pages.currentIndex() == 0
    gs = w.global_asset_filter.state()
    assert gs.person == 1
    assert gs.account_ids == (101,)
    assert gs.instrument_id == 201
    assert w.global_asset_filter.account_filter.text() == "长桥"
    assert w.global_asset_filter.ticker_search.currentText() == "GGLL · US"

    tc_scope = w.trade_center.scope()
    assert tc_scope.person_ids == (1,)
    assert tc_scope.account_ids == (101,)
    assert tc_scope.instrument_ids == (201,)


def test_drill_record_switches_channel_for_same_ticker(qtbot):
    w = _setup_window(qtbot)
    w.drill_record({"account_id": 101, "instrument_id": 201})
    assert w.global_asset_filter.account_filter.text() == "长桥"
    w.drill_record({"account_id": 102, "instrument_id": 201})
    assert w.global_asset_filter.account_filter.text() == "嘉信"
    assert w.trade_center.scope().account_ids == (102,)
    assert w.trade_center.scope().instrument_ids == (201,)
