import unittest
import csv
import io
import os
from pathlib import Path
import tempfile
from datetime import date

# 1. Create temporary isolated database environment BEFORE any portfolio modules load
_TEST_TEMP_DIR = tempfile.TemporaryDirectory()
_TEST_DB_PATH = Path(_TEST_TEMP_DIR.name) / "test_security.sqlite3"
os.environ["MONEY_SECURITY_DB_PATH"] = str(_TEST_DB_PATH)

from portfolio.web.security.credentials import (
    set_security_db_path,
    init_security_db,
)
set_security_db_path(_TEST_DB_PATH)
init_security_db()

from portfolio.web.app import app
from portfolio.paths import DATABASE_PATH
from portfolio.domain.models import QueryScope, Ledger
from portfolio.services.trades_service import TradesService
from portfolio.services.analytics_service import AnalyticsService


class TestDateSortingUnified(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["MONEY_SECURITY_DB_PATH"] = str(_TEST_DB_PATH)
        set_security_db_path(_TEST_DB_PATH)
        init_security_db()
        cls.app = app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        _TEST_TEMP_DIR.cleanup()

    def test_trades_date_sorting_asc_desc(self):
        """1. 交易明细：点击「时间」/「交易时间」在升序和降序之间切换，跨页无重复无遗漏"""
        # Descending (default)
        res_desc = self.client.get("/api/v1/trades?page=1&size=10&sort_col=时间&sort_dir=desc")
        self.assertEqual(res_desc.status_code, 200)
        data_desc = res_desc.get_json()
        self.assertEqual(data_desc["status"], "ok")
        self.assertEqual(data_desc["sort_col"], "时间")
        self.assertEqual(data_desc["sort_dir"], "desc")
        trades_desc = data_desc["trades"]
        self.assertGreater(len(trades_desc), 0)

        # Dates should be descending
        dates_desc = [t["trade_date"] for t in trades_desc if t.get("trade_date")]
        self.assertEqual(dates_desc, sorted(dates_desc, reverse=True))

        # Ascending
        res_asc = self.client.get("/api/v1/trades?page=1&size=10&sort_col=时间&sort_dir=asc")
        self.assertEqual(res_asc.status_code, 200)
        data_asc = res_asc.get_json()
        self.assertEqual(data_asc["sort_dir"], "asc")
        trades_asc = data_asc["trades"]
        self.assertGreater(len(trades_asc), 0)

        # Dates should be ascending
        dates_asc = [t["trade_date"] for t in trades_asc if t.get("trade_date")]
        self.assertEqual(dates_asc, sorted(dates_asc, reverse=False))

        # Earliest date in ASC should be <= latest date in DESC
        self.assertLessEqual(dates_asc[0], dates_desc[0])

        # Pagination consistency test: Page 1 + Page 2 in DESC must have disjoint record_ids
        res_p1 = self.client.get("/api/v1/trades?page=1&size=5&sort_col=时间&sort_dir=desc").get_json()
        res_p2 = self.client.get("/api/v1/trades?page=2&size=5&sort_col=时间&sort_dir=desc").get_json()
        ids_p1 = {t["id"] for t in res_p1["trades"]}
        ids_p2 = {t["id"] for t in res_p2["trades"]}
        self.assertEqual(len(ids_p1.intersection(ids_p2)), 0, "Pages must not overlap or repeat records")

    def test_trades_export_respects_sort_and_filter(self):
        """交易明细导出 CSV 遵循排序方向与筛选条件"""
        res_desc = self.client.get("/api/v1/export/trades?sort_col=时间&sort_dir=desc")
        self.assertEqual(res_desc.status_code, 200)
        self.assertIn("text/csv", res_desc.content_type)
        reader_desc = list(csv.reader(io.StringIO(res_desc.data.decode("utf-8-sig"))))
        self.assertGreater(len(reader_desc), 1)  # header + data
        header = reader_desc[0]
        self.assertEqual(header[0], "时间")
        first_date_desc = reader_desc[1][0]

        res_asc = self.client.get("/api/v1/export/trades?sort_col=时间&sort_dir=asc")
        self.assertEqual(res_asc.status_code, 200)
        reader_asc = list(csv.reader(io.StringIO(res_asc.data.decode("utf-8-sig"))))
        first_date_asc = reader_asc[1][0]

        self.assertLessEqual(first_date_asc, first_date_desc)

    def test_daily_pnl_date_sorting(self):
        """2. 每日持仓收益：点击「日期」排序，默认时间倒序"""
        # Default report query: auto detects 日期 and sorts DESC
        res_default = self.client.get("/api/v1/reports/每日持仓收益?page=1&page_size=20")
        self.assertEqual(res_default.status_code, 200)
        data_default = res_default.get_json()
        self.assertEqual(data_default["sort_col"], "日期")
        self.assertEqual(data_default["sort_dir"], "desc")
        rows_desc = data_default["rows"]
        dates_desc = [r[0] for r in rows_desc if r[0]]
        self.assertEqual(dates_desc, sorted(dates_desc, reverse=True))

        # Toggle to ASC
        res_asc = self.client.get("/api/v1/reports/每日持仓收益?page=1&page_size=20&sort_col=日期&sort_dir=asc")
        self.assertEqual(res_asc.status_code, 200)
        data_asc = res_asc.get_json()
        self.assertEqual(data_asc["sort_dir"], "asc")
        rows_asc = data_asc["rows"]
        dates_asc = [r[0] for r in rows_asc if r[0]]
        self.assertEqual(dates_asc, sorted(dates_asc, reverse=False))

    def test_monthly_reports_sorting(self):
        """3, 4, 5. 每月持仓收益、每月微操收益、月度净投入明细：点击「月份」排序"""
        for report_name in ("每月持仓收益", "每月微操收益", "月度净投入明细"):
            res_default = self.client.get(f"/api/v1/reports/{report_name}?page=1&page_size=20")
            self.assertEqual(res_default.status_code, 200)
            data_def = res_default.get_json()
            self.assertEqual(data_def["sort_col"], "月份")
            self.assertEqual(data_def["sort_dir"], "desc")
            months_desc = [r[0] for r in data_def["rows"] if r[0]]
            self.assertEqual(months_desc, sorted(months_desc, reverse=True), f"{report_name} desc failed")

            res_asc = self.client.get(f"/api/v1/reports/{report_name}?page=1&page_size=20&sort_col=月份&sort_dir=asc")
            self.assertEqual(res_asc.status_code, 200)
            data_asc = res_asc.get_json()
            self.assertEqual(data_asc["sort_dir"], "asc")
            months_asc = [r[0] for r in data_asc["rows"] if r[0]]
            self.assertEqual(months_asc, sorted(months_asc, reverse=False), f"{report_name} asc failed")

    def test_trade_replay_and_transfers_sorting(self):
        """6. 交易回放、人员划转、资金事件、资产调整：点击相应业务日期或时间排序"""
        # 交易回放
        res_replay_desc = self.client.get("/api/v1/reports/交易回放?page=1&page_size=20&sort_col=交易时间&sort_dir=desc")
        self.assertEqual(res_replay_desc.status_code, 200)
        times_desc = [r[0] for r in res_replay_desc.get_json()["rows"] if r[0]]
        self.assertEqual(times_desc, sorted(times_desc, reverse=True))

        res_replay_asc = self.client.get("/api/v1/reports/交易回放?page=1&page_size=20&sort_col=交易时间&sort_dir=asc")
        self.assertEqual(res_replay_asc.status_code, 200)
        times_asc = [r[0] for r in res_replay_asc.get_json()["rows"] if r[0]]
        self.assertEqual(times_asc, sorted(times_asc, reverse=False))

        # 资金事件 (business_date)
        res_funds_desc = self.client.get("/api/v1/reports/资金事件?page=1&page_size=20&sort_col=business_date&sort_dir=desc")
        self.assertEqual(res_funds_desc.status_code, 200)
        bdate_desc = [r[1] for r in res_funds_desc.get_json()["rows"] if r[1]]
        self.assertEqual(bdate_desc, sorted(bdate_desc, reverse=True))

        res_funds_asc = self.client.get("/api/v1/reports/资金事件?page=1&page_size=20&sort_col=business_date&sort_dir=asc")
        self.assertEqual(res_funds_asc.status_code, 200)
        bdate_asc = [r[1] for r in res_funds_asc.get_json()["rows"] if r[1]]
        self.assertEqual(bdate_asc, sorted(bdate_asc, reverse=False))

    def test_import_batches_and_exchange_rates_sorting(self):
        """7. 导入批次、汇率：点击对应时间字段排序"""
        # 导入批次 (imported_at_utc)
        res_batches_desc = self.client.get("/api/v1/reports/导入批次?page=1&page_size=20&sort_col=imported_at_utc&sort_dir=desc")
        self.assertEqual(res_batches_desc.status_code, 200)
        b_desc = [r[4] for r in res_batches_desc.get_json()["rows"] if len(r) > 4 and r[4]]
        self.assertEqual(b_desc, sorted(b_desc, reverse=True))

        res_batches_asc = self.client.get("/api/v1/reports/导入批次?page=1&page_size=20&sort_col=imported_at_utc&sort_dir=asc")
        self.assertEqual(res_batches_asc.status_code, 200)
        b_asc = [r[4] for r in res_batches_asc.get_json()["rows"] if len(r) > 4 and r[4]]
        self.assertEqual(b_asc, sorted(b_asc, reverse=False))

        # 汇率 (折算日期)
        res_fx_desc = self.client.get("/api/v1/reports/汇率?page=1&page_size=20&sort_col=折算日期&sort_dir=desc")
        self.assertEqual(res_fx_desc.status_code, 200)
        fx_desc = [r[0] for r in res_fx_desc.get_json()["rows"] if r[0]]
        self.assertEqual(fx_desc, sorted(fx_desc, reverse=True))

        res_fx_asc = self.client.get("/api/v1/reports/汇率?page=1&page_size=20&sort_col=折算日期&sort_dir=asc")
        self.assertEqual(res_fx_asc.status_code, 200)
        fx_asc = [r[0] for r in res_fx_asc.get_json()["rows"] if r[0]]
        self.assertEqual(fx_asc, sorted(fx_asc, reverse=False))

    def test_report_export_csv_respects_sort(self):
        """报表 CSV 导出遵循当前排序方向"""
        res_desc = self.client.get("/api/v1/export/report/每日持仓收益?sort_col=日期&sort_dir=desc")
        self.assertEqual(res_desc.status_code, 200)
        rows_desc = list(csv.reader(io.StringIO(res_desc.data.decode("utf-8-sig"))))[1:]  # skip header
        dates_desc = [r[0] for r in rows_desc if r[0]]
        self.assertEqual(dates_desc, sorted(dates_desc, reverse=True))

        res_asc = self.client.get("/api/v1/export/report/每日持仓收益?sort_col=日期&sort_dir=asc")
        self.assertEqual(res_asc.status_code, 200)
        rows_asc = list(csv.reader(io.StringIO(res_asc.data.decode("utf-8-sig"))))[1:]
        dates_asc = [r[0] for r in rows_asc if r[0]]
        self.assertEqual(dates_asc, sorted(dates_asc, reverse=False))

    def test_monitoring_alerts_sorting(self):
        """8. 行情监控提醒历史：点击「触发时间」排序"""
        res_desc = self.client.get("/api/v1/monitoring/alerts?sort_col=触发时间&sort_dir=desc")
        self.assertEqual(res_desc.status_code, 200)
        data_desc = res_desc.get_json()
        self.assertEqual(data_desc["status"], "ok")
        self.assertEqual(data_desc["sort_dir"], "desc")

        res_asc = self.client.get("/api/v1/monitoring/alerts?sort_col=触发时间&sort_dir=asc")
        self.assertEqual(res_asc.status_code, 200)
        data_asc = res_asc.get_json()
        self.assertEqual(data_asc["sort_dir"], "asc")

    def test_earnings_calendar_sorting(self):
        """财报日历：点击「披露日期」排序"""
        res_desc = self.client.get("/api/v1/earnings-calendar?year=2025&month=1&sort_dir=desc")
        self.assertEqual(res_desc.status_code, 200)
        self.assertEqual(res_desc.get_json()["sort_dir"], "desc")

        res_asc = self.client.get("/api/v1/earnings-calendar?year=2025&month=1&sort_dir=asc")
        self.assertEqual(res_asc.status_code, 200)
        self.assertEqual(res_asc.get_json()["sort_dir"], "asc")

    def test_desktop_frozen_header_and_all_time_columns_sorting(self):
        """桌面端 PySide6 冻结首列（交易时间/日期/月份）与其他时间列表头点击正排/倒排切换"""
        from PySide6.QtCore import Qt, QDate
        from PySide6.QtWidgets import QApplication
        from portfolio.ui.main_window import MainWindow
        from portfolio.ui.report_page import ReportPage
        from portfolio.domain.analytics import Report, ReportRow
        from portfolio.services.earnings_service import EarningsEvent, EarningsMonth
        from datetime import datetime, timezone

        app_qt = QApplication.instance() or QApplication([])
        w = MainWindow(settings=False)
        # 1. Verify TradeTable default descending and frozen header click
        self.assertEqual(w.sort_column, 0)
        self.assertTrue(w.descending)
        self.assertEqual(w.table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual(w.table.frozen.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)

        # Simulate clicking column 0 on the frozen header (which covers column 0)
        w.table.frozen.horizontalHeader().sectionClicked.emit(0)
        self.assertEqual(w.sort_column, 0)
        self.assertFalse(w.descending)
        self.assertEqual(w.table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)
        self.assertEqual(w.table.frozen.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)

        # Click again -> back to descending
        w.table.frozen.horizontalHeader().sectionClicked.emit(0)
        self.assertTrue(w.descending)
        self.assertEqual(w.table.frozen.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)

        # 2. Verify ReportPage with 清仓收益 (最终清仓时间 & 首次买入时间)
        rp = ReportPage(("清仓收益",))
        closed_rep = Report(
            "清仓收益",
            ("渠道", "Ticker", "首次买入时间", "最终清仓时间"),
            (
                ReportRow(("SCHWAB", "AAPL", "2025-01-01", "2025-06-01")),
                ReportRow(("SCHWAB", "TSLA", "2025-03-01", "2025-09-01")),
            ),
        )
        rp.set_reports({"清仓收益": closed_rep})
        # Default sorts by 最终清仓时间 descending
        self.assertEqual(rp.sort_orders.get("清仓收益"), ("最终清仓时间", True))
        self.assertEqual([r.values[1] for r in rp.sorted_rows], ["TSLA", "AAPL"])

        # Click 最终清仓时间 (col 3) -> toggles to ascending directly without menu
        rp.table.horizontalHeader().sectionClicked.emit(3)
        self.assertEqual(rp.sort_orders.get("清仓收益"), ("最终清仓时间", False))
        self.assertEqual([r.values[1] for r in rp.sorted_rows], ["AAPL", "TSLA"])

        # Click 首次买入时间 (col 2) -> defaults to descending first
        rp.table.horizontalHeader().sectionClicked.emit(2)
        self.assertEqual(rp.sort_orders.get("清仓收益"), ("首次买入时间", True))
        self.assertEqual([r.values[1] for r in rp.sorted_rows], ["TSLA", "AAPL"])

        # 3. Verify EarningsCalendarPage details and coverage date sorting
        ep = w.earnings_page
        ep.month.setDate(QDate(2026, 10, 1))
        ep.targets = {"TSLA.US": ("TSLA.US", "TSLA"), "MU.US": ("MU.US", "MU")}
        e1 = EarningsEvent("TSLA.US", "Tesla", date(2026, 10, 21), "盘后", "10-22 04:30 北京时间")
        e2 = EarningsEvent("MU.US", "Micron", date(2026, 10, 25), "盘后", "10-26 04:30 北京时间")
        ep.result = EarningsMonth((e1, e2), datetime.now(timezone.utc))
        ep.draw()

        # Click 日期（当地）(col 0) on details -> descending (2026-10-25 first)
        ep.details.horizontalHeader().sectionClicked.emit(0)
        self.assertEqual(ep.details.item(0, 0).text(), "2026-10-25")
        self.assertIn("↓", ep.details.horizontalHeaderItem(0).text())

        # Click again -> ascending (2026-10-21 first)
        ep.details.horizontalHeader().sectionClicked.emit(0)
        self.assertEqual(ep.details.item(0, 0).text(), "2026-10-21")
        self.assertIn("↑", ep.details.horizontalHeaderItem(0).text())

        w.close()
        w.deleteLater()
        rp.close()
        rp.deleteLater()
        app_qt.processEvents()


if __name__ == "__main__":
    unittest.main()
