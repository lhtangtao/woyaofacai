"""拉科专属 Gemini 写作空间权限、服务与 Web API 回归测试。"""
from flask import Flask, g

from portfolio.services.ai_service import CodexAIService
from portfolio.web import api_v1 as api_module
from portfolio.web.security.auth_context import build_auth_context


def _make_client(auth_ctx):
    app = Flask(__name__)
    app.register_blueprint(api_module.api_v1)

    @app.before_request
    def _inject_auth():
        g.auth_context = auth_ctx

    return app.test_client()


def test_auth_context_lake_writer_permissions():
    """仅拉科（username 或 person_name）与管理员具备拉科专属写作权限。"""
    admin_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 1, "username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"},
    )
    lake_by_username = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "拉科", "role": "user", "person_id": 5, "person_name": "测试"},
    )
    lake_by_person = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 3, "username": "lake_user", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    other_user = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 4, "username": "zhangsan", "role": "user", "person_id": 6, "person_name": "张三"},
    )
    lan_viewer = build_auth_context("DIRECT_PHYSICAL_LAN", session_user=None)
    unauth = build_auth_context("REMOTE_PUBLIC", session_user=None)

    assert admin_ctx.can_access_lake_writer() is True
    assert admin_ctx.to_dict()["can_view_lake_writer"] is True
    assert lake_by_username.can_access_lake_writer() is True
    assert lake_by_username.to_dict()["can_view_lake_writer"] is True
    assert lake_by_person.can_access_lake_writer() is True
    assert lake_by_person.to_dict()["can_view_lake_writer"] is True

    assert other_user.can_access_lake_writer() is False
    assert other_user.to_dict()["can_view_lake_writer"] is False
    assert lan_viewer.can_access_lake_writer() is False
    assert lan_viewer.to_dict()["can_view_lake_writer"] is False
    assert unauth.can_access_lake_writer() is False
    assert unauth.to_dict()["can_view_lake_writer"] is False


def test_ask_article_writer_uses_gemini_and_keeps_history(monkeypatch):
    """验证 ask_article_writer 强制使用 Gemini 通道、不注入账本上下文并携带多轮历史。"""
    captured = {}

    def fake_ask(cls, prompt, context="", model=None, cancel_event=None, provider="gemini"):
        captured["prompt"] = prompt
        captured["context"] = context
        captured["model"] = model
        captured["provider"] = provider
        return "# 文章标题\n\n这是修改后的正文。"

    monkeypatch.setattr(CodexAIService, "ask", classmethod(fake_ask))

    result = CodexAIService.ask_article_writer(
        "请把开头改得更有悬念",
        history=[
            {"role": "user", "content": "写一篇关于深夜咖啡馆的短文"},
            {"role": "assistant", "content": "凌晨两点的街角还亮着一盏灯。"},
        ],
        model="gemini-4-neon-high",
        provider="codex",  # 非 Gemini 引擎应自动回退为 gemini
    )

    assert "文章标题" in result
    assert captured["context"] == ""
    assert captured["provider"] == "gemini"
    assert captured["model"] == "gemini-4-neon-high"
    assert "凌晨两点的街角还亮着一盏灯" in captured["prompt"]
    assert "请把开头改得更有悬念" in captured["prompt"]


def test_lake_writer_api_access_control_and_session_isolation(monkeypatch):
    """验证拉科专属 API 的 403 拦截、多轮写作持久化与按账号历史隔离。"""
    store: dict[str, dict] = {}

    def fake_load(filename: str):
        return dict(store.get(filename, {}))

    def fake_save(filename: str, data: dict):
        store[filename] = dict(data)

    monkeypatch.setattr(api_module, "load_ai_sessions", fake_load)
    monkeypatch.setattr(api_module, "save_ai_sessions", fake_save)

    seen_calls = []

    def fake_writer(cls, prompt, history=None, model=None, cancel_event=None, provider="gemini"):
        seen_calls.append({"prompt": prompt, "history_len": len(history or []), "model": model, "provider": provider})
        return f"成稿：{prompt}"

    monkeypatch.setattr(api_module.AIService, "ask_article_writer", classmethod(fake_writer))

    # 1. 其他普通用户访问应返回 403
    other_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 4, "username": "zhangsan", "role": "user", "person_id": 6, "person_name": "张三"},
    )
    other_client = _make_client(other_ctx)
    assert other_client.get("/api/v1/ai/lake-writer/history").status_code == 403
    assert other_client.post("/api/v1/ai/lake-writer/chat", json={"prompt": "帮我写文章"}).status_code == 403
    assert other_client.post("/api/v1/ai/lake-writer/clear", json={}).status_code == 403

    # 2. 拉科用户可正常写稿、多轮对话并清空
    lake_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "lake", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    lake_client = _make_client(lake_ctx)

    r1 = lake_client.post(
        "/api/v1/ai/lake-writer/chat",
        json={"prompt": "写一篇旅行随笔", "model": "gemini-4-hydrogen-high", "provider": "codex"},
    )
    assert r1.status_code == 200
    assert r1.get_json()["answer"] == "成稿：写一篇旅行随笔"
    assert seen_calls[-1]["provider"] == "gemini"
    assert seen_calls[-1]["history_len"] == 0

    r2 = lake_client.post(
        "/api/v1/ai/lake-writer/chat",
        json={"prompt": "再精简 200 字", "model": "gemini-4-hydrogen-high"},
    )
    assert r2.status_code == 200
    assert seen_calls[-1]["history_len"] == 2

    hist_lake = lake_client.get("/api/v1/ai/lake-writer/history").get_json()
    assert hist_lake["status"] == "ok"
    assert len(hist_lake["messages"]) == 4

    # 3. 管理员也可访问，但与拉科的写作历史相互隔离
    admin_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 1, "username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"},
    )
    admin_client = _make_client(admin_ctx)
    hist_admin = admin_client.get("/api/v1/ai/lake-writer/history").get_json()
    assert hist_admin["status"] == "ok"
    assert hist_admin["messages"] == []

    # 4. 拉科清空自己的历史
    clr = lake_client.post("/api/v1/ai/lake-writer/clear", json={})
    assert clr.status_code == 200
    assert lake_client.get("/api/v1/ai/lake-writer/history").get_json()["messages"] == []
    # 不污染普通 AI 投资问答历史文件
    assert "ai_chat_sessions.json" not in store
