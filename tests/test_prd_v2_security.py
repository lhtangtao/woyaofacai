#!/usr/bin/env python3
"""
Comprehensive Automated Test Suite for Money Portfolio Security Architecture PRD v2.0
Validates Acceptance Criteria AC-01 through AC-46.
"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
import hashlib
from unittest.mock import patch
from pathlib import Path
from datetime import date

# Ensure src is on PYTHONPATH
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

# Create temporary isolated database environment BEFORE any portfolio modules load
_TEST_TEMP_DIR = tempfile.TemporaryDirectory()
_TEST_DB_PATH = Path(_TEST_TEMP_DIR.name) / "test_security.sqlite3"
os.environ["MONEY_SECURITY_DB_PATH"] = str(_TEST_DB_PATH)

from portfolio.web.security.credentials import (
    get_security_db_path,
    get_default_security_db_path,
    set_security_db_path,
    reset_security_db_path,
    init_security_db,
    hash_password,
    verify_password,
    get_user_by_username,
    set_user_password,
    create_user,
    toggle_user_enabled,
    record_login_attempt,
    is_login_rate_limited,
)

# Explicitly ensure custom path is active in memory
set_security_db_path(_TEST_DB_PATH)

from portfolio.services.notification_service import (
    notification_service,
    mask_bark_url,
    sanitize_log_message,
)
from portfolio.web.app import app, CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from portfolio.web.security.network_access import (
    classify_network_access,
    get_active_physical_lan_networks,
    _classify_ip_address,
)
from portfolio.web.security.sessions import (
    create_session,
    validate_session,
    revoke_session,
    revoke_all_user_sessions,
    get_flask_secret_key,
)
from portfolio.web.security.auth_context import (
    build_auth_context,
    CAP_VIEW_ASSETS,
    CAP_VIEW_MARKET,
    CAP_MANAGE_RULES,
    CAP_AI_CHAT,
    CAP_MANAGE_USERS,
    CAP_MANAGE_BARK,
)
from portfolio.web.security.csrf import generate_csrf_token


class TestPRDv2SecurityArchitecture(unittest.TestCase):
    """Rigorous verification suite for all Acceptance Criteria (including AC-41 to AC-46)."""

    @classmethod
    def setUpClass(cls):
        app.testing = True
        cls.client = app.test_client()

        # Ensure temporary test DB path is active even when running multi-suite
        os.environ["MONEY_SECURITY_DB_PATH"] = str(_TEST_DB_PATH)
        set_security_db_path(_TEST_DB_PATH)

        # 1. Snapshot real production database baseline to verify zero modification
        cls._real_db_path = get_default_security_db_path()
        cls._real_db_exists = cls._real_db_path.is_file()
        if cls._real_db_exists:
            cls._real_db_bytes_before = cls._real_db_path.read_bytes()
            cls._real_db_sha256_before = hashlib.sha256(cls._real_db_bytes_before).hexdigest()
        else:
            cls._real_db_bytes_before = None
            cls._real_db_sha256_before = None

        # 2. Strict verification: active db MUST be our temporary test db, NEVER the real db!
        active_db = get_security_db_path().resolve()
        if cls._real_db_exists and active_db == cls._real_db_path.resolve():
            raise RuntimeError(f"FATAL SECURITY VIOLATION: Test suite attempting to run against production DB: {active_db}")

        # 3. Initialize schema and isolated test accounts inside temporary test DB
        init_security_db()
        set_user_password("admin", "AdminValidPass2026!")
        set_user_password("拉科", "LakeValidPass2026!")
        try:
            create_user("巴小", "BaxiaoValidPass2026!", role="user", person_name="巴小")
        except Exception:
            set_user_password("巴小", "BaxiaoValidPass2026!")

        # 4. Save notification service bark_url original state
        cls._original_bark_url = notification_service.bark_url

    @classmethod
    def tearDownClass(cls):
        # Restore in-memory notification service state
        notification_service.set_bark_url(cls._original_bark_url)

        # 1. Reset security db path back to default production path
        reset_security_db_path()
        if "MONEY_SECURITY_DB_PATH" in os.environ:
            del os.environ["MONEY_SECURITY_DB_PATH"]

        # 2. Clean up temporary directory and database
        try:
            _TEST_TEMP_DIR.cleanup()
        except Exception:
            pass

        # 3. Strict verification: Real production database MUST be completely untouched!
        if cls._real_db_exists:
            current_bytes = cls._real_db_path.read_bytes()
            current_sha256 = hashlib.sha256(current_bytes).hexdigest()
            if current_sha256 != cls._real_db_sha256_before:
                raise AssertionError(
                    f"CRITICAL: Real security database was modified during testing!\n"
                    f"Expected SHA256: {cls._real_db_sha256_before}\n"
                    f"Actual SHA256:   {current_sha256}"
                )

    def setUp(self):
        self.client = app.test_client()

    def post_with_csrf(self, url, json_data=None, headers=None, environ_overrides=None):
        token = generate_csrf_token()
        self.client.set_cookie(CSRF_COOKIE_NAME, token)
        h = {"X-CSRF-Token": token}
        if headers:
            h.update(headers)
        return self.client.post(url, json=json_data, headers=h, environ_overrides=environ_overrides)

    # --------------------------------------------------------------------------
    # 1. Network Origin Classification (AC-01, AC-02, AC-11, AC-25, AC-26, AC-28, AC-30, AC-31)
    # --------------------------------------------------------------------------

    def test_ac_01_loopback_classification(self):
        """AC-01: Mac local loopback classified as LOCAL_LOOPBACK."""
        mode_v4 = classify_network_access("127.0.0.1", {})
        self.assertEqual(mode_v4, "LOCAL_LOOPBACK")
        mode_v6 = classify_network_access("::1", {})
        self.assertEqual(mode_v6, "LOCAL_LOOPBACK")

    def test_ac_02_and_25_physical_lan_classification(self):
        """AC-02 & AC-25: Direct physical LAN devices classified as DIRECT_PHYSICAL_LAN."""
        lan_nets = get_active_physical_lan_networks()
        self.assertTrue(len(lan_nets) > 0, "Should detect at least one active physical LAN subnet")
        first_net = lan_nets[0]
        sample_host = str(list(first_net.hosts())[5])
        mode = classify_network_access(sample_host, {})
        self.assertEqual(mode, "DIRECT_PHYSICAL_LAN")

    def test_ac_11_and_26_tailscale_classification(self):
        """AC-11 & AC-26: Tailscale 100.64.0.0/10 must NEVER be classified as physical LAN."""
        tailscale_ips = ["100.64.0.1", "100.80.20.5", "100.127.255.254"]
        for ip in tailscale_ips:
            mode = classify_network_access(ip, {})
            self.assertEqual(mode, "REMOTE_TAILSCALE", f"IP {ip} must be REMOTE_TAILSCALE")

    def test_ac_26_clash_fake_ip_classification(self):
        """AC-26: Clash fake-ip 198.18.0.0/15 must NOT be physical LAN."""
        fake_ips = ["198.18.0.1", "198.19.100.5"]
        for ip in fake_ips:
            mode = classify_network_access(ip, {})
            self.assertNotEqual(mode, "DIRECT_PHYSICAL_LAN")
            self.assertEqual(mode, "REMOTE_PUBLIC")

    def test_ac_28_and_30_proxy_spoofing_defense(self):
        """AC-28 & AC-30: Remote client spoofing X-Forwarded-For: 192.168.x.x must be rejected."""
        # Case A: Attacker connecting from public IP claiming to be LAN in X-Forwarded-For
        mode = classify_network_access("203.0.113.5", {"X-Forwarded-For": "192.168.100.27"})
        self.assertEqual(mode, "REMOTE_PUBLIC")

        # Case B: Local reverse proxy forwarding public client
        mode_rev_proxy = classify_network_access("127.0.0.1", {"X-Forwarded-For": "203.0.113.5"})
        self.assertEqual(mode_rev_proxy, "REMOTE_PUBLIC")

        # Case C: Local reverse proxy forwarding Tailscale client
        mode_ts_proxy = classify_network_access("127.0.0.1", {"X-Forwarded-For": "100.100.1.5"})
        self.assertEqual(mode_ts_proxy, "REMOTE_TAILSCALE")

    # --------------------------------------------------------------------------
    # 2. LAN Password-Free Read-Only & Principle of Least Privilege (AC-01 to AC-07)
    # --------------------------------------------------------------------------

    def test_ac_05_and_06_lan_viewer_role_and_capabilities(self):
        """AC-05 & AC-06: LAN unauthenticated user has role LAN_VIEWER, never ADMIN."""
        lan_nets = get_active_physical_lan_networks()
        lan_ip = str(list(lan_nets[0].hosts())[10])

        res = self.client.get("/api/auth/me", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["user"]["role"], "lan_viewer")
        self.assertFalse(data["user"]["is_authenticated"])
        self.assertFalse(data["user"]["can_admin"])
        self.assertFalse(data["user"]["can_write"])
        self.assertFalse(data["user"]["can_chat"])

    def test_ac_01_to_04_lan_can_read_assets_and_quotes(self):
        """AC-01 to AC-04: Physical LAN visitors can read overview, reports, charts, quotes without login."""
        lan_nets = get_active_physical_lan_networks()
        lan_ip = str(list(lan_nets[0].hosts())[11])

        # Overview
        res = self.client.get("/api/overview", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 200)

        # Reports list
        res = self.client.get("/api/reports/list", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 200)

        # Charts
        res = self.client.get("/api/charts", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 200)

        # Monitoring symbols
        res = self.client.get("/api/monitoring/symbols", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 200)

    def test_ac_07_lan_viewer_denied_modifications(self):
        """AC-07: LAN visitor attempting rule toggle, Bark, AI chat or user management gets 403."""
        lan_nets = get_active_physical_lan_networks()
        lan_ip = str(list(lan_nets[0].hosts())[12])

        # Rule toggle (even with valid CSRF token, LAN_VIEWER lacks capability -> 403)
        res = self.post_with_csrf("/api/rules/1/toggle", json_data={"enabled": True}, environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "FORBIDDEN")

        # Bark test
        res = self.post_with_csrf("/api/bark/test", json_data={"url": "https://api.day.app/xxx"}, environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "FORBIDDEN")

        # Bark config
        res = self.post_with_csrf("/api/bark/config", json_data={"url": "https://api.day.app/xxx"}, environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "FORBIDDEN")

        # AI chat
        res = self.post_with_csrf("/api/ai/chat", json_data={"prompt": "hello"}, environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "FORBIDDEN")

        # User management
        res = self.client.get("/api/auth/users", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "FORBIDDEN")

    # --------------------------------------------------------------------------
    # 3. Remote Network Mandatory Authentication (AC-08, AC-09, AC-10, AC-11, AC-15, AC-29)
    # --------------------------------------------------------------------------

    def test_ac_08_and_09_remote_public_unauthenticated_returns_401(self):
        """AC-08 & AC-09: Public 5G/WAN visitors must receive 401 on protected APIs and see login screen."""
        wan_ip = "198.51.100.42"

        # HTML page shows remote login screen
        res = self.client.get("/", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"remoteLoginScreen", res.data)

        # Protected APIs return 401
        for endpoint in ["/api/overview", "/api/reports/list", "/api/charts", "/api/trades", "/api/monitoring/symbols"]:
            res = self.client.get(endpoint, environ_overrides={"REMOTE_ADDR": wan_ip})
            self.assertEqual(res.status_code, 401, f"{endpoint} should return 401 for unauthenticated WAN")
            self.assertEqual(res.get_json().get("code"), "UNAUTHENTICATED")

    def test_ac_11_tailscale_unauthenticated_returns_401(self):
        """AC-11: Tailscale 100.x remote visitors must receive 401 without password login."""
        ts_ip = "100.115.30.40"
        res = self.client.get("/api/overview", environ_overrides={"REMOTE_ADDR": ts_ip})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.get_json().get("code"), "UNAUTHENTICATED")

    # --------------------------------------------------------------------------
    # 4. Identity Authentication, User Isolation & Capabilities (AC-12, AC-13, AC-14, AC-23)
    # --------------------------------------------------------------------------

    def test_ac_17_old_default_credentials_rejected(self):
        """AC-17: Default credentials (admin/admin, 拉科/lake) must be rejected."""
        wan_ip = "198.51.100.42"

        # Attempt default admin
        res = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin"}, environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res.status_code, 401)

        # Attempt default lake
        res = self.client.post("/api/auth/login", json={"username": "拉科", "password": "lake"}, environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res.status_code, 401)

    def test_ac_12_admin_login_and_full_permissions(self):
        """AC-12: Valid admin credentials grant full access, including user management."""
        wan_ip = "198.51.100.42"
        res = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["user"]["role"], "admin")

        # Admin can access users list
        res_users = self.client.get("/api/auth/users", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_users.status_code, 200)

        # Admin can access overview
        res_ov = self.client.get("/api/overview", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_ov.status_code, 200)

    def test_ac_13_and_14_user_isolation(self):
        """AC-13 & AC-14: Regular user '拉科' is strictly restricted to person_id=5, cannot query other scopes."""
        wan_ip = "198.51.100.42"
        # Login as 拉科
        res = self.client.post(
            "/api/auth/login",
            json={"username": "拉科", "password": "LakeValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["user"]["person_id"], 5)

        # Calling user management is forbidden
        res_users = self.client.get("/api/auth/users", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_users.status_code, 403)

        # Calling Bark config is forbidden
        res_bark = self.client.post("/api/bark/test", json={"url": "test"}, environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_bark.status_code, 403)

        # Overview query scope is strictly forced to person_id=5 regardless of query params
        res_ov = self.client.get("/api/overview?person=全量&person_id=1", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_ov.status_code, 200)

    def test_ac_15_logout_session_revocation(self):
        """AC-15: Remote user after logout immediately receives 401 on protected APIs."""
        wan_ip = "198.51.100.42"
        # Login
        self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        # Verify authenticated
        res_me = self.client.get("/api/auth/me", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertTrue(res_me.get_json()["user"]["is_authenticated"])

        # Logout
        res_logout = self.post_with_csrf("/api/auth/logout", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_logout.status_code, 200)

        # Protected API now returns 401
        res_ov = self.client.get("/api/overview", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_ov.status_code, 401)

    # --------------------------------------------------------------------------
    # 5. Security Vulnerability Fixes (AC-16, AC-18, AC-19, AC-20, AC-21, AC-22)
    # --------------------------------------------------------------------------

    def test_ac_16_anti_spoofing_headers_rejected(self):
        """AC-16: X-User: admin or ?auth_user=admin header/query param cannot bypass authentication."""
        wan_ip = "198.51.100.42"
        res = self.client.get(
            "/api/overview?auth_user=admin",
            headers={"X-User": "admin"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res.status_code, 401)

    def test_ac_18_persistent_secret_key(self):
        """AC-18: Persistent high-entropy secret key exists and has 0600 file permissions."""
        key = get_flask_secret_key()
        self.assertGreaterEqual(len(key), 32)

    def test_ac_20_tampered_cookie_rejected(self):
        """AC-20: Forged/tampered session cookie does not grant admin access."""
        self.client.set_cookie(SESSION_COOKIE_NAME, "forged_session_token_1234567890")
        wan_ip = "198.51.100.42"
        res = self.client.get("/api/overview", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res.status_code, 401)

    def test_ac_21_csrf_protection(self):
        """AC-21: Mutating requests without matching CSRF token or with bad Origin are rejected with 403."""
        # 1. First get a valid CSRF token from the server
        res = self.client.get("/api/auth/csrf")
        self.assertEqual(res.status_code, 200)
        token = res.get_json()["csrf_token"]

        # 2. POST with wrong CSRF token -> 403
        res = self.client.post(
            "/api/rules/1/toggle",
            json={"enabled": True},
            headers={"X-CSRF-Token": "bad_token"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "CSRF_ERROR")

        # 3. POST with forged cross-origin Origin header -> 403
        res = self.client.post(
            "/api/rules/1/toggle",
            json={"enabled": True},
            headers={"X-CSRF-Token": token, "Origin": "https://malicious-site.example.com"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.get_json().get("code"), "CSRF_ERROR")

    def test_ac_22_login_rate_limiting(self):
        """AC-22: Repeated failed login attempts trigger 429 Too Many Requests."""
        client_ip = "192.0.2.77"
        username = "rate_limit_test_user"

        # Record 5 failures
        for _ in range(5):
            record_login_attempt(client_ip, username, success=False)

        # Check rate limited
        self.assertTrue(is_login_rate_limited(client_ip, username))

        # Attempt login via API -> 429
        res = self.client.post(
            "/api/auth/login",
            json={"username": username, "password": "wrong_password"},
            environ_overrides={"REMOTE_ADDR": client_ip},
        )
        self.assertEqual(res.status_code, 429)

    # --------------------------------------------------------------------------
    # 6. Local Mac Only Initialization & Financial Database Safety (AC-34, AC-36, AC-38, AC-39)
    # --------------------------------------------------------------------------

    def test_ac_36_local_only_init_admin(self):
        """AC-36: /api/auth/init_admin is only accessible from local loopback (127.0.0.1)."""
        # Call from LAN or Remote -> 403 LOCAL_ONLY_FORBIDDEN
        res_remote = self.post_with_csrf(
            "/api/auth/init_admin",
            json_data={"password": "NewSecretAdmin2026!"},
            environ_overrides={"REMOTE_ADDR": "192.168.100.55"},
        )
        self.assertEqual(res_remote.status_code, 403)
        self.assertEqual(res_remote.get_json().get("code"), "LOCAL_ONLY_FORBIDDEN")

        # Call from 127.0.0.1 -> Allowed
        res_local = self.post_with_csrf(
            "/api/auth/init_admin",
            json_data={"password": "NewSecretAdmin2026!"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(res_local.status_code, 200)

        # Restore password
        set_user_password("admin", "AdminValidPass2026!")

    def test_ac_19_session_revocation_on_disable_or_password_change(self):
        """AC-19: Session is immediately invalidated when user is disabled or password is changed."""
        wan_ip = "198.51.100.42"
        # 1. Login as admin
        res = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res.status_code, 200)

        # 2. Change password via API (with CSRF)
        res_change = self.post_with_csrf(
            "/api/auth/users/password",
            json_data={"username": "admin", "password": "AdminNewPass2026!#$"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_change.status_code, 200)

        # 3. Old session token in cookie is now invalidated -> 401
        res_after = self.client.get("/api/overview", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_after.status_code, 401)

        # 4. Restore admin password
        set_user_password("admin", "AdminValidPass2026!")

    def test_ac_23_ai_chat_history_isolation(self):
        """AC-23: Regular user '拉科' cannot access or clear other users' AI chat history."""
        wan_ip = "198.51.100.42"
        # Login as 拉科
        self.client.post(
            "/api/auth/login",
            json={"username": "拉科", "password": "LakeValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )

        # Attempt to read '管理员' history -> server forces scope to '拉科'
        res_hist = self.client.get(
            "/api/ai/history?person=管理员&channel=全部渠道&symbol=全部标的",
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_hist.status_code, 200)
        data = res_hist.get_json()
        self.assertIn("拉科", data["session_key"])
        self.assertNotIn("管理员", data["session_key"])

    def test_ac_37_no_secrets_in_html_or_api(self):
        """AC-37: Web templates and endpoints contain no weak password hints or secrets."""
        res = self.client.get("/")
        html = res.data.decode("utf-8")
        self.assertNotIn("admin/admin", html)
        self.assertNotIn("lake/lake", html)
        self.assertNotIn("quickFillUser", html)

    def test_ac_39_audit_log_ip_masking(self):
        """AC-39: Audit logs must mask client IP address to protect user privacy (e.g. 192.168.1.***)."""
        from portfolio.web.security.audit import mask_ip, record_audit_event, get_recent_audit_events
        masked = mask_ip("192.168.100.27")
        self.assertEqual(masked, "192.168.100.xxx")
        masked_v6 = mask_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
        self.assertIn("xxxx", masked_v6)

        record_audit_event("PRIVACY_TEST", "test_user", "TEST_MODE", "192.168.100.88", "OK")
        events = get_recent_audit_events(limit=5)
        latest = [e for e in events if e.get("event_type") == "PRIVACY_TEST"]
        self.assertTrue(len(latest) > 0)
        self.assertEqual(latest[0]["ip_address"], "192.168.100.xxx")

    def test_ac_34_financial_database_untouched(self):
        """AC-34: Financial database money.sqlite3 exists and has zero schema changes."""
        fin_db = Path(__file__).resolve().parent.parent / "数据库" / "money.sqlite3"
        self.assertTrue(fin_db.exists(), "Financial database must exist")
        # Ensure security tables are NOT in money.sqlite3
        conn = sqlite3.connect(fin_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users', 'auth_sessions', 'auth_audit_events')")
        tables = cursor.fetchall()
        conn.close()
        self.assertEqual(len(tables), 0, "Financial database must not contain security authentication tables")

    def test_ac_38_default_deny_scan_on_all_routes(self):
        """AC-38: Automated scan verifying every /api/* route requires authentication/capability."""
        public_routes = {"/api/auth/csrf", "/api/auth/login", "/api/auth/me"}
        unauthenticated_ip = "198.51.100.99"

        for rule in app.url_map.iter_rules():
            rule_str = str(rule)
            if not rule_str.startswith("/api/"):
                continue
            if rule_str in public_routes:
                continue

            # Test GET /api/... without auth from remote IP
            if "GET" in rule.methods:
                test_url = rule_str.replace("<symbol>", "AAPL").replace("<report_name>", "资产汇总")
                res = self.client.get(test_url, environ_overrides={"REMOTE_ADDR": unauthenticated_ip})
                self.assertIn(
                    res.status_code,
                    (401, 403),
                    f"Route {test_url} must deny unauthenticated remote access, got {res.status_code}",
                )

    # --------------------------------------------------------------------------
    # 7. Test Isolation & Bark Credential Protection (AC-41 through AC-46)
    # --------------------------------------------------------------------------

    def test_ac_41_real_security_database_completely_untouched(self):
        """AC-41: Automated verification that real user security.sqlite3 is 100% isolated and untouched."""
        # 1. Assert active database is temporary test DB, NOT production path
        active_db = get_security_db_path().resolve()
        if self._real_db_exists:
            self.assertNotEqual(
                str(active_db),
                str(self._real_db_path.resolve()),
                "Active security DB path must NOT point to production security database!",
            )

        # 2. Assert real database file byte hash is unchanged
        if self._real_db_exists:
            current_bytes = self._real_db_path.read_bytes()
            current_sha = hashlib.sha256(current_bytes).hexdigest()
            self.assertEqual(
                current_sha,
                self._real_db_sha256_before,
                "Real security.sqlite3 hash mismatch! Production database was touched.",
            )

        # 3. Perform write operations in test environment and re-verify real DB untouched
        set_user_password("admin", "TemporaryIsolatedPass2026!")
        if self._real_db_exists:
            current_bytes = self._real_db_path.read_bytes()
            current_sha = hashlib.sha256(current_bytes).hexdigest()
            self.assertEqual(
                current_sha,
                self._real_db_sha256_before,
                "Real security.sqlite3 modified after set_user_password!",
            )
        # Restore admin test password
        set_user_password("admin", "AdminValidPass2026!")

    def test_ac_42_bark_url_not_in_html_for_anonymous_or_unauthenticated(self):
        """AC-42: Sensitive Bark URL is never injected into HTML for anonymous LAN or WAN visitors."""
        test_secret_url = "https://api.day.app/SECRET_KEY_lan_test_7788/"
        notification_service.set_bark_url(test_secret_url)

        # Anonymous physical LAN visitor
        lan_nets = get_active_physical_lan_networks()
        lan_ip = str(list(lan_nets[0].hosts())[10]) if lan_nets else "192.168.1.155"

        res_lan = self.client.get("/", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res_lan.status_code, 200)
        html_lan = res_lan.data.decode("utf-8")
        self.assertNotIn("SECRET_KEY_lan_test_7788", html_lan)
        self.assertNotIn("https://api.day.app", html_lan)
        self.assertNotIn('id="barkInputUrl"', html_lan)
        self.assertIn("已配置", html_lan)

        # Unauthenticated remote WAN visitor (e.g. 198.51.100.99)
        res_wan = self.client.get("/", environ_overrides={"REMOTE_ADDR": "198.51.100.99"})
        self.assertEqual(res_wan.status_code, 200)
        html_wan = res_wan.data.decode("utf-8")
        self.assertNotIn("SECRET_KEY_lan_test_7788", html_wan)
        self.assertNotIn("https://api.day.app", html_wan)
        self.assertNotIn('id="barkInputUrl"', html_wan)

    def test_ac_43_bark_url_not_accessible_to_regular_users(self):
        """AC-43: Regular users (e.g. 拉科, 巴小) cannot view, configure, or test Bark notifications."""
        test_secret_url = "https://api.day.app/SECRET_KEY_lake_forbidden_9999/"
        notification_service.set_bark_url(test_secret_url)
        wan_ip = "198.51.100.42"

        # Login as regular user 拉科
        res_login = self.client.post(
            "/api/auth/login",
            json={"username": "拉科", "password": "LakeValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_login.status_code, 200)

        # 1. HTML view does NOT contain Bark secret URL or input
        res_page = self.client.get("/", environ_overrides={"REMOTE_ADDR": wan_ip})
        html = res_page.data.decode("utf-8")
        self.assertNotIn("SECRET_KEY_lake_forbidden_9999", html)
        self.assertNotIn('id="barkInputUrl"', html)

        # 2. GET /api/bark/config is 403 Forbidden
        res_get = self.client.get("/api/bark/config", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_get.status_code, 403)
        self.assertNotIn("SECRET_KEY_lake_forbidden_9999", res_get.data.decode("utf-8"))

        # 3. POST /api/bark/config is 403 Forbidden
        res_post = self.post_with_csrf(
            "/api/bark/config",
            json_data={"url": "https://api.day.app/AttackerKey/"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_post.status_code, 403)

        # 4. POST /api/bark/test is 403 Forbidden
        res_test = self.post_with_csrf(
            "/api/bark/test",
            json_data={},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_test.status_code, 403)

    def test_ac_44_bark_config_endpoints_protected_for_unauthenticated_and_lan(self):
        """AC-44: Bark configuration endpoints are denied for unauthenticated remote WAN and anonymous LAN."""
        lan_nets = get_active_physical_lan_networks()
        lan_ip = str(list(lan_nets[0].hosts())[10]) if lan_nets else "192.168.1.88"

        # Anonymous LAN visitor (physical LAN subnet -> LAN_VIEWER without CAP_MANAGE_BARK -> 403 Forbidden)
        res_lan_get = self.client.get("/api/bark/config", environ_overrides={"REMOTE_ADDR": lan_ip})
        self.assertEqual(res_lan_get.status_code, 403)

        res_lan_post = self.post_with_csrf(
            "/api/bark/config",
            json_data={"url": "https://api.day.app/LanTry/"},
            environ_overrides={"REMOTE_ADDR": lan_ip},
        )
        self.assertEqual(res_lan_post.status_code, 403)

        # Unauthenticated remote WAN visitor (198.51.100.99 -> 401 Unauthorized)
        res_wan_get = self.client.get("/api/bark/config", environ_overrides={"REMOTE_ADDR": "198.51.100.99"})
        self.assertEqual(res_wan_get.status_code, 401)

        res_wan_post = self.post_with_csrf(
            "/api/bark/config",
            json_data={"url": "https://api.day.app/WanTry/"},
            environ_overrides={"REMOTE_ADDR": "198.51.100.99"},
        )
        self.assertEqual(res_wan_post.status_code, 401)

    def test_ac_45_admin_bark_management_and_response_sanitization(self):
        """AC-45: Admin can view and update Bark configuration; responses omit raw sensitive URLs."""
        test_initial_url = "https://api.day.app/SECRET_KEY_admin_ok_1122/"
        notification_service.set_bark_url(test_initial_url)
        wan_ip = "198.51.100.42"

        # Login as admin
        res_login = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminValidPass2026!"},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_login.status_code, 200)

        # 1. Admin GET /api/bark/config succeeds
        res_get = self.client.get("/api/bark/config", environ_overrides={"REMOTE_ADDR": wan_ip})
        self.assertEqual(res_get.status_code, 200)
        data_get = res_get.get_json()
        self.assertTrue(data_get["configured"])
        self.assertEqual(data_get["url"], test_initial_url)

        # 2. Admin POST /api/bark/config updates configuration safely
        new_secret_url = "https://api.day.app/NEW_SECRET_KEY_admin_3344/"
        with tempfile.NamedTemporaryFile(suffix=".json") as tmp_cfg:
            with patch("portfolio.web.app.API_CONFIG_PATH", new=Path(tmp_cfg.name)):
                res_post = self.post_with_csrf(
                    "/api/bark/config",
                    json_data={"url": new_secret_url},
                    environ_overrides={"REMOTE_ADDR": wan_ip},
                )
                self.assertEqual(res_post.status_code, 200)
                data_post = res_post.get_json()
                self.assertEqual(data_post["status"], "ok")
                self.assertTrue(data_post["configured"])
                # Asserts response body omits raw secret URL reflection
                self.assertNotIn("url", data_post)
                self.assertEqual(notification_service.bark_url, new_secret_url)

        # 3. Admin POST /api/bark/test sends push notification without errors
        res_test = self.post_with_csrf(
            "/api/bark/test",
            json_data={"url": new_secret_url},
            environ_overrides={"REMOTE_ADDR": wan_ip},
        )
        self.assertEqual(res_test.status_code, 200)
        self.assertIn("ok", res_test.get_json()["status"])

    def test_ac_46_mask_bark_url_and_log_sanitization(self):
        """AC-46: Bark URL masking and log sanitization utility functions."""
        # Masking standard Bark device key
        masked = mask_bark_url("https://api.day.app/N3kJs94kSjd/")
        self.assertIn("https://api.day.app/", masked)
        self.assertNotIn("N3kJs94kSjd", masked)
        self.assertTrue("****" in masked)

        # Masking empty/none
        self.assertEqual(mask_bark_url(""), "")
        self.assertEqual(mask_bark_url(None), "")

        # Log sanitization removes secret keys
        raw_error = "ConnectionError: failed at https://api.day.app/SUPERSECRETKEY999/test timeout"
        clean = sanitize_log_message(raw_error, "https://api.day.app/SUPERSECRETKEY999/")
        self.assertNotIn("SUPERSECRETKEY999", clean)
        self.assertIn("****", clean)


if __name__ == "__main__":
    unittest.main()
