"""拉科专属 Gemini 写作空间（多标签、软清空归档、管理员/客户端查看）测试。"""
import json

from flask import Flask, g

from portfolio.services.ai_service import CodexAIService
from portfolio.services.lake_writer_store import LakeWriterStore
from portfolio.ui.lake_writer_archive_page import LakeWriterArchivePage
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
    assert lake_by_person.can_access_lake_writer() is True

    assert other_user.can_access_lake_writer() is False
    assert other_user.to_dict()["can_view_lake_writer"] is False
    assert lan_viewer.can_access_lake_writer() is False
    assert unauth.can_access_lake_writer() is False


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
        provider="codex",
    )

    assert "文章标题" in result
    assert captured["context"] == ""
    assert captured["provider"] == "gemini"
    assert captured["model"] == "gemini-4-neon-high"
    assert "凌晨两点的街角还亮着一盏灯" in captured["prompt"]
    assert "请把开头改得更有悬念" in captured["prompt"]


def test_lake_writer_multi_tab_soft_clear_and_admin_archives(tmp_path, monkeypatch, qtbot):
    """验证多标签隔离上下文、软清空不物理删除、管理员接口及桌面端只读查看。"""
    store_file = tmp_path / "ai_lake_writer_sessions.json"
    # 预埋一条 v1 旧格式数据测试无损迁移
    store_file.write_text(
        json.dumps(
            {
                "lake_writer::拉科": [
                    {"role": "user", "content": "旧版随笔主题", "time_str": "10:00:00", "time": "2026-11-25T10:00:00"}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_module, "get_lake_writer_store", lambda: LakeWriterStore(store_file))

    seen_calls = []

    def fake_writer(cls, prompt, history=None, model=None, cancel_event=None, provider="gemini"):
        seen_calls.append({"prompt": prompt, "history_len": len(history or []), "provider": provider})
        return f"成稿：{prompt}"

    monkeypatch.setattr(api_module.AIService, "ask_article_writer", classmethod(fake_writer))

    # 1. 普通非拉科用户全部返回 403
    other_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 4, "username": "zhangsan", "role": "user", "person_id": 6, "person_name": "张三"},
    )
    other_client = _make_client(other_ctx)
    assert other_client.get("/api/v1/ai/lake-writer/history").status_code == 403
    assert other_client.post("/api/v1/ai/lake-writer/tabs", json={}).status_code == 403
    assert other_client.post("/api/v1/ai/lake-writer/chat", json={"prompt": "test"}).status_code == 403

    # 2. 拉科用户读取迁移后的默认标签，并新建第二个独立标签
    lake_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "拉科", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    lake_client = _make_client(lake_ctx)

    hist0 = lake_client.get("/api/v1/ai/lake-writer/history").get_json()
    assert hist0["status"] == "ok"
    assert len(hist0["tabs"]) == 1
    tab1_id = hist0["active_tab_id"]
    assert len(hist0["messages"]) == 1  # 迁移来的旧消息

    # 拉科不能调管理员存档接口
    assert lake_client.get("/api/v1/ai/lake-writer/admin/archives").status_code == 403

    # 新建第二个标签，上下文应为 0，节约 Token
    created = lake_client.post("/api/v1/ai/lake-writer/tabs", json={}).get_json()
    tab2_id = created["active_tab_id"]
    assert tab2_id != tab1_id
    assert len(created["tabs"]) == 2

    r_tab2 = lake_client.post(
        "/api/v1/ai/lake-writer/chat",
        json={"prompt": "写一篇科技评论文章", "tab_id": tab2_id, "model": "gemini-4-hydrogen-high"},
    )
    assert r_tab2.status_code == 200
    assert seen_calls[-1]["history_len"] == 0  # 不受 tab1 历史影响

    # 重命名 tab2
    ren = lake_client.post(f"/api/v1/ai/lake-writer/tabs/{tab2_id}/rename", json={"title": "科技评论稿"})
    assert ren.status_code == 200
    assert any(t["title"] == "科技评论稿" for t in ren.get_json()["tabs"])

    # 3. 软清空 tab2：拉科端显示已清空，但后台归档保留原消息
    clr = lake_client.post("/api/v1/ai/lake-writer/clear", json={"tab_id": tab2_id})
    assert clr.status_code == 200
    assert clr.get_json()["archived_count"] == 2
    assert lake_client.get(f"/api/v1/ai/lake-writer/history?tab_id={tab2_id}").get_json()["messages"] == []

    # 软清空后再发消息，Gemini 只看到清空后的新上下文（0 条）
    lake_client.post("/api/v1/ai/lake-writer/chat", json={"prompt": "重新写一版", "tab_id": tab2_id})
    assert seen_calls[-1]["history_len"] == 0

    # 软删除（关闭）tab2：拉科活跃列表只剩 tab1，但后台档案依然存在
    deleted = lake_client.post(f"/api/v1/ai/lake-writer/tabs/{tab2_id}/delete", json={}).get_json()
    assert len(deleted["tabs"]) == 1
    assert deleted["active_tab_id"] == tab1_id

    # 4. 管理员可通过接口查看包括已清空、已关闭在内的全部记录
    admin_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 1, "username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"},
    )
    admin_client = _make_client(admin_ctx)
    arch_resp = admin_client.get("/api/v1/ai/lake-writer/admin/archives?owner=拉科").get_json()
    assert arch_resp["status"] == "ok"
    assert arch_resp["total_tabs"] == 2
    tab2_arch = next(r for r in arch_resp["records"] if r["id"] == tab2_id)
    assert tab2_arch["status"] == "deleted"
    assert tab2_arch["archived_batch_count"] == 1
    assert tab2_arch["archived_batches"][0]["messages"][0]["content"] == "写一篇科技评论文章"
    assert tab2_arch["messages"][0]["content"] == "重新写一版"

    # 5. Mac 桌面端 LakeWriterArchivePage 可只读展示全部存档
    page = LakeWriterArchivePage(store=LakeWriterStore(store_file))
    qtbot.addWidget(page)
    assert page.tab_list.count() == 2
    html_text = page.viewer.toPlainText()
    assert "旧版随笔主题" in html_text or "重新写一版" in html_text
