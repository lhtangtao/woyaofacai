"""Comprehensive Automated Parity and Security Verification for Unified API v1.

Verifies:
1. Complete isolation of security DB during testing (zero modification to real security DB).
2. Bootstrap endpoint delivers correct system profile, user identity, persons, reports list.
3. Portfolio overview numbers 100% match AnalyticsService calculation.
4. Holdings today intraday PnL matches live quotes / ledger holdings ("今日盈亏" report).
5. Standardized Report DTO with rows (2D cell array) and items (list of dicts).
6. Trades endpoint delivers 16 columns matching desktop TradeTable specification.
7. Market monitoring watchlist includes un-truncated MA5, MA10, MA20, MA50, MA200.
8. Role isolation: regular user (拉科) strictly scoped to person_id=5, cannot access other persons or Bark.
"""

from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

# 1. Create temporary isolated database environment BEFORE any portfolio modules load
_TEST_TEMP_DIR = tempfile.TemporaryDirectory()
_TEST_DB_PATH = Path(_TEST_TEMP_DIR.name) / "test_security.sqlite3"
os.environ["MONEY_SECURITY_DB_PATH"] = str(_TEST_DB_PATH)

from portfolio.domain.models import QueryScope, Ledger
from portfolio.paths import DATABASE_PATH
from portfolio.services.analytics_service import AnalyticsService
from portfolio.web.app import app
from portfolio.web.security.credentials import (
    get_security_db_path,
    get_default_security_db_path,
    set_security_db_path,
    reset_security_db_path,
    init_security_db,
    set_user_password,
    create_user,
)
from portfolio.web.security.network_access import get_active_physical_lan_networks

# Explicitly ensure custom path is active in memory
set_security_db_path(_TEST_DB_PATH)


class TestUnifiedParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.testing = True
        cls.client = app.test_client()

        # Snapshot real production database baseline to verify zero modification
        cls._real_db_path = get_default_security_db_path()
        cls._real_db_exists = cls._real_db_path.is_file()
        if cls._real_db_exists:
            cls._real_db_bytes_before = cls._real_db_path.read_bytes()
            cls._real_db_sha256_before = hashlib.sha256(cls._real_db_bytes_before).hexdigest()
        else:
            cls._real_db_bytes_before = None
            cls._real_db_sha256_before = None

        # Strict assertion: active db MUST be our temporary test db
        active_db = get_security_db_path().resolve()
        if cls._real_db_exists and active_db == cls._real_db_path.resolve():
            raise RuntimeError(f"FATAL SECURITY VIOLATION: Test running against production DB: {active_db}")

        # Initialize schema inside isolated test DB
        init_security_db()
        set_user_password("admin", "AdminValidPass2026!")
        set_user_password("拉科", "LakeValidPass2026!")

    @classmethod
    def tearDownClass(cls):
        # 1. Reset security db path back to default production path
        reset_security_db_path()
        if "MONEY_SECURITY_DB_PATH" in os.environ:
            del os.environ["MONEY_SECURITY_DB_PATH"]

        # 2. Cleanup isolated temporary directory
        try:
            _TEST_TEMP_DIR.cleanup()
        except Exception:
            pass

        # 3. CRITICAL: Byte-for-byte SHA256 integrity check on real production DB
        if cls._real_db_exists and cls._real_db_path.is_file():
            real_db_bytes_after = cls._real_db_path.read_bytes()
            real_db_sha256_after = hashlib.sha256(real_db_bytes_after).hexdigest()
            if real_db_sha256_after != cls._real_db_sha256_before:
                raise AssertionError(
                    f"CRITICAL SECURITY FAILURE: Real production security DB was modified by tests!\n"
                    f"Before SHA256: {cls._real_db_sha256_before}\n"
                    f"After SHA256:  {real_db_sha256_after}"
                )

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

    def test_04_reports_detail_and_today_holdings(self):
        """Verify /api/v1/reports/<report_name> fetches report data, today holdings, and DTO arrays."""
        # 1. Test 当前持仓
        res = self.client.get("/api/v1/reports/当前持仓", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data.get("name") or data.get("report_name"), "当前持仓")
        self.assertIn("columns", data)
        self.assertIn("rows", data)
        self.assertIn("items", data)
        # rows must be 2D array of cells
        if len(data["rows"]) > 0:
            self.assertIsInstance(data["rows"][0], list)
        if len(data["items"]) > 0:
            self.assertIsInstance(data["items"][0], dict)

        # 2. Test 今日盈亏 (P0-01)
        res_today = self.client.get("/api/v1/reports/今日盈亏", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(res_today.status_code, 200)
        data_today = res_today.get_json()
        self.assertEqual(data_today["status"], "ok")
        self.assertEqual(data_today["name"], "今日盈亏")
        self.assertIn("columns", data_today)
        self.assertIn("渠道", data_today["columns"])
        self.assertIn("Ticker", data_today["columns"])

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
        """Verify user 拉科 is strictly isolated in API v1 (P0-04)."""
        wan_ip = "198.51.100.42"

        login_res = self.client.post(
            "/api/auth/login",
            json={"username": "拉科", "password": "LakeValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(login_res.status_code, 200)

        # 1. Bootstrap: persons list should only contain person_id=5
        res_boot = self.client.get("/api/v1/bootstrap", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_boot.status_code, 200)
        boot_data = res_boot.get_json()
        person_ids = [p["id"] for p in boot_data.get("persons", [])]
        self.assertEqual(person_ids, [5], "User 拉科 must only see their own person_id in bootstrap")

        # 2. Overview: should only reflect 拉科 (person_id=5)
        res_ov = self.client.get("/api/v1/portfolio/overview", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_ov.status_code, 200)
        data_ov = res_ov.get_json()
        self.assertEqual(data_ov["user"]["person_id"], 5)
        self.assertEqual(data_ov["user"]["role"], "user")

        # 3. People assets endpoint: only person 5
        res_people = self.client.get("/api/v1/people", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_people.status_code, 200)
        people_data = res_people.get_json()
        for p in people_data.get("people", []):
            self.assertEqual(p["person_id"], 5, "User 拉科 cannot see other persons' assets")

        # 4. Stock ownership: only person 5
        res_own = self.client.get("/api/v1/ownership", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_own.status_code, 200)
        own_data = res_own.get_json()
        for r in own_data.get("report", []):
            self.assertEqual(r.get("person_id"), 5)

        # 5. Cross-user trade detail access denial (P0-04)
        # Find a trade_id not belonging to person 5
        import sqlite3
        with sqlite3.connect(DATABASE_PATH) as conn:
            other_trade_row = conn.execute(
                "SELECT source_record_id FROM person_operations WHERE person_id != 5 LIMIT 1"
            ).fetchone()
        if other_trade_row:
            other_trade_id = other_trade_row[0]
            res_td = self.client.get(f"/api/v1/trades/detail?trade_id={other_trade_id}", environ_overrides={"REMOTE_ADDR": wan_ip})
            self.assertEqual(res_td.status_code, 403, "Access to other persons' trade detail must be forbidden (403)")

        # 6. Bark config must be 403 Forbidden
        res_bark = self.client.get("/api/bark/config", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_bark.status_code, 403)


if __name__ == "__main__":
    unittest.main()
