from datetime import date
import sqlite3

import pandas as pd

from portfolio.domestic.nav_collection import Collector, SCHEMA, publish


def test_decimal_facts_are_idempotent_and_conflicts_preserve_original():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    collector = Collector(None, date(2021, 11, 11), 1)
    original = dict(fund_code="000001", nav_date="2021-11-11", source="test", unit_nav="1.123456789",
                    fetched_at="first", raw_payload="{}", field_status="{}")
    key = ("fund_code", "nav_date", "source")
    table = "raw_fund_nav_daily"
    assert publish(db, table, key, [original], collector) == {"inserted": 1}
    assert publish(db, table, key, [dict(original, fetched_at="second")], collector) == {"existing": 1}
    revised = dict(original, unit_nav="1.2")
    assert publish(db, table, key, [revised, revised], collector) == {"conflict": 2}
    assert db.execute("SELECT unit_nav FROM raw_fund_nav_daily").fetchone()[0] == "1.123456789"
    assert db.execute("SELECT count(*) FROM raw_fund_nav_conflicts").fetchone()[0] == 1


def test_nav_gap_and_dividend_per_ten_are_recorded():
    class Fake:
        def fund_open_fund_info_em(self, *, symbol, indicator):
            return pd.DataFrame({
                "单位净值走势": [{"净值日期": "2021-11-11", "单位净值": "1.2345", "日增长率": "0.1"}],
                "累计净值走势": [{"净值日期": "2021-11-11", "累计净值": None}],
                "分红送配详情": [{"除息日": "2021-11-12", "每10份分红": "每10份派现金0.4500元"}],
                "拆分详情": [],
            }[indicator])

    collector = Collector(Fake(), date(2021, 11, 14), 1)
    dates = {date(2021, 11, 11), date(2021, 11, 12)}
    rows = list(collector.nav(dict(fund_code="000001", fund_type="股票型", fund_name="基金",
                                   currency=None, establishment_date="2021-01-01"), dates, {}))
    assert len(rows) == 1 and rows[0]["unit_nav"] == "1.2345" and rows[0]["accumulated_nav"] is None
    assert any(issue["date"] == "2021-11-12" and issue["field"] == "unit_nav" for issue in collector.issues)
    assert not any(issue["date"] == "2021-11-13" for issue in collector.issues)
    events = list(collector.events("000001"))
    assert len(events) == 1 and events[0]["dividend_per_share"] == "0.0450"


def test_recent_gap_check_repairs_only_history_and_skips_complete_funds(tmp_path, monkeypatch):
    import akshare as ak
    from portfolio.domestic import nav_recent
    from portfolio.domestic.storage import RAW_FILE, connect

    with connect(tmp_path / RAW_FILE) as db:
        db.executescript(SCHEMA)
    fund = {"code": "000001", "name": "测试基金", "kind": "混合型"}
    monkeypatch.setattr(nav_recent, "fund_list", lambda _: [fund])
    trading_days = [date(2021, 11, 11), date(2021, 11, 12), date(2021, 11, 15)]
    monkeypatch.setattr(ak, "tool_trade_date_hist_sina", lambda: pd.DataFrame({"trade_date": trading_days}))
    calls = []

    def history(code, first, last):
        calls.append((code, first, last))
        return [
            {"FSRQ": str(day), "DWJZ": "1.2345", "LJJZ": "1.2345", "JZZZL": "0"}
            for day in trading_days
        ]

    monkeypatch.setattr(nav_recent, "mainland_history", history)
    assert not nav_recent.check(tmp_path, today=date(2021, 11, 15))
    assert not nav_recent.check(tmp_path, today=date(2021, 11, 15))
    assert calls == [("000001", "2021-11-11", "2021-11-12")]
    with connect(tmp_path / RAW_FILE, readonly=True) as db:
        assert [r[0] for r in db.execute("SELECT nav_date FROM raw_fund_nav_daily ORDER BY nav_date")] == [
            "2021-11-11", "2021-11-12",
        ]
