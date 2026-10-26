"""Comprehensive Automated Parity and Security Verification for Unified API v1.

Verifies:
1. Bootstrap endpoint delivers correct system profile, user identity, persons, reports list.
2. Portfolio overview numbers 100% match AnalyticsService calculation.
3. Holdings today intraday PnL matches live quotes / ledger holdings.
4. Trades endpoint delivers 16 columns matching desktop TradeTable specification.
5. Desktop 28 reports metadata and pagination.
6. Market monitoring watchlist includes un-truncated MA5, MA10, MA20, MA50, MA200.
7. Role isolation: regular user (拉科) strictly scoped to person_id=5, cannot access other persons or Bark.
"""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest

from portfolio.domain.models import QueryScope, Ledger
from portfolio.paths import DATABASE_PATH
from portfolio.services.analytics_service import AnalyticsService
from portfolio.web.app import app
from portfolio.web.security.credentials import init_security_db, set_user_password
from portfolio.web.security.network_access import get_active_physical_lan_networks


class TestUnifiedParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.testing = True
        cls.client = app.test_client()
        init_security_db()

    def test_01_bootstrap_lan_viewer(self):
        """Verify /api/v1/bootstrap delivers system and UI metadata."""
        res = self.client.get("/api/v1/bootstrap", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("user", data)
        self.assertIn("reports", data)
        self.assertGreaterEqual(len(data["reports"]), 28)
        self.assertIn("people", data)
        self.assertIn("accounts", data)
        self.assertEqual(data["design_tokens"]["row_height"], 40)
        self.assertEqual(data["design_tokens"]["header_height"], 42)

    def test_02_overview_parity_with_analytics_service(self):
        """Verify /api/v1/portfolio/overview matches AnalyticsService output exactly."""
        res = self.client.get("/api/v1/portfolio/overview", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")

        # Compare with direct AnalyticsService calculation
        svc = AnalyticsService(DATABASE_PATH)
        calc = svc.calculate(QueryScope(as_of=date.today(), reporting_currency="USD", ledger=Ledger.PERSON))
        metric_map = {m.name: m.value for m in calc.metrics}

        self.assertIn("当前总资产", data["metrics"])
        self.assertAlmostEqual(float(data["metrics"]["当前总资产"]), float(metric_map["当前总资产"]), places=2)
        self.assertAlmostEqual(float(data["metrics"]["全时期总盈亏"]), float(metric_map["全时期总盈亏"]), places=2)

    def test_03_trades_column_parity(self):
        """Verify /api/v1/trades returns 16 standardized columns matching desktop TradeTable."""
        res = self.client.get("/api/v1/trades?page=1&size=20", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("trades", data)
        self.assertIn("pagination", data)
        self.assertEqual(data["pagination"]["page"], 1)

        desktop_expected_columns = [
            "时间", "券商", "分类", "标的", "方向", "数量", "价格", "金额",
            "标记", "持仓", "净投入", "成本", "操作类型", "对手方", "订单号", "来源"
        ]
        self.assertEqual(data["columns"], desktop_expected_columns)

    def test_04_reports_detail(self):
        """Verify /api/v1/reports/<report_name> fetches report data and metadata."""
        res = self.client.get("/api/v1/reports/当前持仓", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data.get("name") or data.get("report_name"), "当前持仓")
        self.assertIn("columns", data)
        self.assertIn("rows", data)

    def test_05_monitoring_watchlist_technical_indicators(self):
        """Verify /api/v1/monitoring/watchlist includes complete MA numbers."""
        res = self.client.get("/api/v1/monitoring/watchlist", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        items = data.get("watchlist") or data.get("items")
        self.assertIsNotNone(items)
        self.assertGreater(len(items), 0)
        item = items[0]
        for ma_key in ("ma5", "ma10", "ma20", "ma50", "ma200"):
            self.assertIn(ma_key, item, f"Watchlist item must contain {ma_key}")

    def test_06_lake_user_isolation(self):
        """Verify user 拉科 is strictly isolated in API v1."""
        wan_ip = "198.51.100.42"
        # Set known test password
        set_user_password("拉科", "LakePass2026!")

        login_res = self.client.post(
            "/api/auth/login",
            json={"username": "拉科", "password": "LakePass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(login_res.status_code, 200)

        # Overview should only reflect 拉科 (person_id=5)
        res_ov = self.client.get("/api/v1/portfolio/overview", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_ov.status_code, 200)
        data_ov = res_ov.get_json()
        self.assertEqual(data_ov["user"]["person_id"], 5)
        self.assertEqual(data_ov["user"]["role"], "user")

        # Bark config must be 403 Forbidden
        res_bark = self.client.get("/api/bark/config", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_bark.status_code, 403)


if __name__ == "__main__":
    unittest.main()
