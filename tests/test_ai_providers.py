"""Codex 与 Gemini CLI 双引擎切换的单元测试与集成测试。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from portfolio.services.ai_service import CodexAIService, normalize_ai_provider


class TestAIServiceProviders(unittest.TestCase):
    """验证 ai_service 中 Codex（默认）与 Gemini CLI 的核心逻辑。"""

    def setUp(self):
        CodexAIService._login_status_cache = {}
        CodexAIService._login_status_timestamp = {}

    def test_normalize_ai_provider_defaults_to_codex(self):
        self.assertEqual(normalize_ai_provider(None), "codex")
        self.assertEqual(normalize_ai_provider(""), "codex")
        self.assertEqual(normalize_ai_provider("unknown"), "codex")
        self.assertEqual(normalize_ai_provider("CODEX"), "codex")
        self.assertEqual(normalize_ai_provider(" Gemini "), "gemini")

    def test_check_login_status_gemini_with_agy_and_oauth(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp)
            gemini_dir = fake_home / ".gemini"
            gemini_dir.mkdir(parents=True)
            (gemini_dir / "oauth_creds.json").write_text(json.dumps({"access_token": "tok"}), encoding="utf-8")
            (gemini_dir / "google_accounts.json").write_text(json.dumps({"active": "user@gmail.com"}), encoding="utf-8")

            with (
                patch.object(CodexAIService, "get_agy_cli_path", return_value="/Users/test/.local/bin/agy"),
                patch.object(CodexAIService, "get_gemini_cli_path", return_value="/usr/local/bin/gemini"),
                patch("pathlib.Path.home", return_value=fake_home),
                patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}),
            ):
                status = CodexAIService.check_login_status(force=True, provider="gemini")

        self.assertEqual(status["provider"], "gemini")
        self.assertTrue(status["installed"])
        self.assertTrue(status["logged_in"])
        self.assertIn("user@gmail.com", status["account_type"])
        self.assertIn("user@gmail.com", status["message"])

    def test_check_login_status_gemini_missing_cli(self):
        with (
            patch.object(CodexAIService, "get_agy_cli_path", return_value=None),
            patch.object(CodexAIService, "get_gemini_cli_path", return_value=None),
        ):
            status = CodexAIService.check_login_status(force=True, provider="gemini")
        self.assertEqual(status["provider"], "gemini")
        self.assertFalse(status["installed"])
        self.assertFalse(status["logged_in"])

    def test_ask_gemini_uses_agy_and_parses_json_response(self):
        def _fake_popen(cmd, **kwargs):
            kwargs["stdout"].write(
                "warning: info\n" + json.dumps({"response": "## NVDA 深度分析\n\n核心驱动稳健。"})
            )
            kwargs["stdout"].flush()
            proc = MagicMock()
            proc.poll.return_value = 0
            proc.returncode = 0
            return proc

        with (
            patch.object(CodexAIService, "get_agy_cli_path", return_value="/Users/test/.local/bin/agy"),
            patch.object(CodexAIService, "get_gemini_cli_path", return_value="/usr/local/bin/gemini"),
            patch("portfolio.services.ai_service.subprocess.Popen", side_effect=_fake_popen) as mock_popen,
            patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}),
        ):
            reply = CodexAIService.ask_stock("分析 NVDA", symbol="NVDA", provider="gemini")

        self.assertIn("NVDA 深度分析", reply)
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd[0], "/Users/test/.local/bin/agy")
        self.assertIn("-p", cmd)
        self.assertIn("--mode", cmd)
        self.assertIn("plan", cmd)
        self.assertIn("--sandbox", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("--model", cmd)

    def test_ask_gemini_cli_with_api_key_uses_skip_trust(self):
        def _fake_popen(cmd, **kwargs):
            kwargs["stdout"].write("直接返回的纯文本研报")
            kwargs["stdout"].flush()
            proc = MagicMock()
            proc.poll.return_value = 0
            proc.returncode = 0
            return proc

        with (
            patch.object(CodexAIService, "get_agy_cli_path", return_value=None),
            patch.object(CodexAIService, "get_gemini_cli_path", return_value="/usr/local/bin/gemini"),
            patch("portfolio.services.ai_service.subprocess.Popen", side_effect=_fake_popen) as mock_popen,
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}),
        ):
            reply = CodexAIService.ask("我的组合风险如何？", context="NVDA 50%", provider="gemini")

        self.assertEqual(reply, "直接返回的纯文本研报")
        cmd = mock_popen.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/local/bin/gemini")
        self.assertIn("--skip-trust", cmd)
        self.assertIn("--approval-mode", cmd)

    def test_ask_defaults_to_codex(self):
        with (
            patch.object(CodexAIService, "get_cli_path", return_value="/usr/local/bin/codex"),
            patch.object(CodexAIService, "_run_cli_process", return_value=0) as mock_run,
            patch.object(CodexAIService, "_ask_gemini", return_value="gemini reply") as mock_gemini,
        ):
            CodexAIService.ask("你好")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.kwargs["provider_label"], "Codex")
        mock_gemini.assert_not_called()


class TestDesktopAIPagesProviderSwitch(unittest.TestCase):
    """验证桌面端 AI 投资问答与 AI 股票分析页默认 Codex 且支持切换 Gemini。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def _mock_status(self, force=False, provider="codex", **kwargs):
        return {
            "provider": provider,
            "installed": True,
            "logged_in": True,
            "account_type": "Google OAuth" if provider == "gemini" else "ChatGPT",
            "message": "ok",
        }

    def test_ai_assistant_and_stock_pages_default_codex_and_switch_gemini(self):
        from portfolio.ui.ai_assistant_page import AIAssistantPage
        from portfolio.ui.ai_stock_analysis_page import AIStockAnalysisPage

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("portfolio.ui.ai_assistant_page.QStandardPaths.writableLocation", return_value=tmp),
            patch("portfolio.ui.ai_stock_analysis_page.QStandardPaths.writableLocation", return_value=tmp),
            patch.object(CodexAIService, "check_login_status", side_effect=self._mock_status),
        ):
            chat_page = AIAssistantPage()
            stock_page = AIStockAnalysisPage()

            # 两个页面均默认选中 Codex
            self.assertEqual(chat_page.current_provider(), "codex")
            self.assertEqual(stock_page.current_provider(), "codex")
            self.assertIn("Codex", chat_page.status_badge.text())
            self.assertIn("Codex", stock_page.status_badge.text())

            # 切换到 Gemini 后状态徽章与 current_provider 同步更新
            chat_page.provider_combo.setCurrentIndex(1)
            stock_page.provider_combo.setCurrentIndex(1)
            self.assertEqual(chat_page.current_provider(), "gemini")
            self.assertEqual(stock_page.current_provider(), "gemini")
            self.assertIn("Gemini", chat_page.status_badge.text())
            self.assertIn("Gemini", stock_page.status_badge.text())

            # 发送问题时向后台 Worker 传入当前选中的 provider="gemini"
            with (
                patch("portfolio.ui.ai_assistant_page.AskCodexWorker.start"),
                patch("portfolio.ui.ai_stock_analysis_page.AskStockWorker.start"),
            ):
                chat_page.input_edit.setPlainText("测试组合诊断")
                chat_page.handle_send()
                self.assertEqual(chat_page.worker.provider, "gemini")
                self.assertIn("Gemini", chat_page.status_hint.text())
                chat_page._thinking_timer.stop()

                stock_page.input_edit.setPlainText("测试标的研报")
                stock_page.handle_send()
                self.assertEqual(stock_page.worker.provider, "gemini")
                self.assertIn("Gemini", stock_page.status_hint.text())
                stock_page._thinking_timer.stop()


class TestWebAIProviderRoutes(unittest.TestCase):
    """验证 Web API /api/v1/ai/* 默认 codex 并支持切换 gemini。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp_dir = tempfile.TemporaryDirectory()
        cls._db_path = Path(cls._tmp_dir.name) / "test_ai_web_security.sqlite3"
        os.environ["MONEY_SECURITY_DB_PATH"] = str(cls._db_path)

        from portfolio.web.app import app, CSRF_COOKIE_NAME
        from portfolio.web.security.credentials import init_security_db, set_security_db_path, set_user_password
        from portfolio.web.security.csrf import generate_csrf_token

        set_security_db_path(cls._db_path)
        init_security_db()
        set_user_password("admin", "AdminValidPass2026!")
        app.testing = True
        cls.client = app.test_client()

        token = generate_csrf_token()
        cls.client.set_cookie(CSRF_COOKIE_NAME, token)
        cls.csrf_headers = {"X-CSRF-Token": token}

        login_res = cls.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminValidPass2026!"},
            headers=cls.csrf_headers,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert login_res.status_code == 200, login_res.get_data(as_text=True)

    @classmethod
    def tearDownClass(cls):
        from portfolio.web.security.credentials import reset_security_db_path

        reset_security_db_path()
        os.environ.pop("MONEY_SECURITY_DB_PATH", None)
        cls._tmp_dir.cleanup()

    def test_status_chat_and_stock_chat_routes(self):
        fake_sessions = {}

        def _load(name):
            return dict(fake_sessions.get(name, {}))

        def _save(name, data):
            fake_sessions[name] = dict(data)

        def _status(force=False, provider="codex", **kwargs):
            return {
                "provider": provider,
                "installed": True,
                "logged_in": True,
                "account_type": "Google OAuth" if provider == "gemini" else "ChatGPT",
                "message": "ok",
            }

        with (
            patch("portfolio.web.api_v1.AIService.check_login_status", side_effect=_status),
            patch("portfolio.web.api_v1.AIService.ask", return_value="mocked answer") as mock_ask,
            patch("portfolio.web.api_v1.AIService.ask_stock", return_value="mocked stock answer") as mock_ask_stock,
            patch("portfolio.web.api_v1.load_ai_sessions", side_effect=_load),
            patch("portfolio.web.api_v1.save_ai_sessions", side_effect=_save),
            patch("portfolio.web.api_v1.get_analytics_service", side_effect=RuntimeError("offline")),
            patch("portfolio.web.api_v1.get_monitor_store", return_value=None),
        ):
            env = {"REMOTE_ADDR": "127.0.0.1"}

            # 1. 状态接口返回默认 codex 及全部 providers
            st_res = self.client.get("/api/v1/ai/status?provider=gemini", environ_overrides=env)
            self.assertEqual(st_res.status_code, 200)
            st_data = st_res.get_json()
            self.assertEqual(st_data["provider"], "gemini")
            self.assertIn("codex", st_data["providers"])
            self.assertIn("gemini", st_data["providers"])

            # 2. 投资问答接口不传 provider 默认走 codex；传 gemini 走 gemini
            c_default = self.client.post("/api/v1/ai/chat", json={"prompt": "默认引擎测试"}, headers=self.csrf_headers, environ_overrides=env)
            self.assertEqual(c_default.status_code, 200)
            self.assertEqual(c_default.get_json()["provider"], "codex")
            self.assertEqual(mock_ask.call_args.kwargs["provider"], "codex")

            c_gem = self.client.post(
                "/api/v1/ai/chat",
                json={"prompt": "Gemini 引擎测试", "provider": "gemini"},
                headers=self.csrf_headers,
                environ_overrides=env,
            )
            self.assertEqual(c_gem.status_code, 200)
            self.assertEqual(c_gem.get_json()["provider"], "gemini")
            self.assertEqual(mock_ask.call_args.kwargs["provider"], "gemini")

            # 3. 股票分析接口同样默认 codex、支持切换 gemini
            s_default = self.client.post(
                "/api/v1/ai/stock/chat",
                json={"symbol": "NVDA", "prompt": "分析"},
                headers=self.csrf_headers,
                environ_overrides=env,
            )
            self.assertEqual(s_default.status_code, 200)
            self.assertEqual(s_default.get_json()["provider"], "codex")
            self.assertEqual(mock_ask_stock.call_args.kwargs["provider"], "codex")

            s_gem = self.client.post(
                "/api/v1/ai/stock/chat",
                json={"symbol": "NVDA", "prompt": "分析", "provider": "gemini"},
                headers=self.csrf_headers,
                environ_overrides=env,
            )
            self.assertEqual(s_gem.status_code, 200)
            self.assertEqual(s_gem.get_json()["provider"], "gemini")
            self.assertEqual(mock_ask_stock.call_args.kwargs["provider"], "gemini")


if __name__ == "__main__":
    unittest.main()
