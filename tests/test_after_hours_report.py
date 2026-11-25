"""美股盘后复盘日报引擎、定时判断、Web API 与桌面端页面回归测试。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Flask, g

from portfolio.services.after_hours_report import (
    AfterHoursReportStore,
    generate_after_hours_report,
    is_after_hours_ready,
)
from portfolio.ui.after_hours_report_page import AfterHoursReportPage
from portfolio.web import api_v1 as api_module
from portfolio.web.security.auth_context import build_auth_context

NY_TZ = ZoneInfo("America/New_York")


def _make_client(auth_ctx):
    app = Flask(__name__)
    app.register_blueprint(api_module.api_v1)

    @app.before_request
    def _inject_auth():
        g.auth_context = auth_ctx

    return app.test_client()


def test_is_after_hours_ready_respects_ny_time_and_weekend():
    """验证美东交易日盘后 20:10 触发窗口与周末跳过逻辑。"""
    # 周三美东 19:59 -> 未到盘后结束
    ready_before, _ = is_after_hours_ready(datetime(2026, 11, 25, 19, 59, tzinfo=NY_TZ))
    assert ready_before is False

    # 周三美东 20:10 -> 到达自动生成窗口
    ready_after, target_day = is_after_hours_ready(datetime(2026, 11, 25, 20, 10, tzinfo=NY_TZ))
    assert ready_after is True
    assert target_day == date(2026, 11, 25)

    # 周六美东 21:00 -> 休市跳过
    ready_sat, _ = is_after_hours_ready(datetime(2026, 11, 28, 21, 0, tzinfo=NY_TZ))
    assert ready_sat is False


def test_generate_after_hours_report_and_desktop_viewer(demo_path, tmp_path, qtbot):
    """验证自动同步衔接、盘后行情+新闻采集、AI 总结落盘以及桌面端只读/展示页面。"""
    store = AfterHoursReportStore(tmp_path / "after_hours_reports.json")
    captured = {}

    def fake_sync():
        return {"ran": True, "ok": True, "message": "同步完成 2 笔流水", "finished_at": "2026-11-25T21:00:00+08:00"}

    def fake_quotes(symbols):
        return {
            "DEMO": {
                "symbol": "DEMO",
                "regular_close": 112.5,
                "prev_close": 110.0,
                "regular_change_pct": 0.0227,
                "after_hours_price": 115.0,
                "after_hours_change_pct": 0.0222,
                "total_change_pct": 0.0455,
                "source": "longbridge",
            },
            "SPY": {
                "symbol": "SPY",
                "regular_close": 600.0,
                "prev_close": 595.0,
                "regular_change_pct": 0.0084,
                "after_hours_price": 601.2,
                "after_hours_change_pct": 0.002,
                "total_change_pct": 0.0104,
                "source": "longbridge",
            },
        }

    def fake_news(symbols):
        return {
            "DEMO": [
                {
                    "title": "Demo Corp beats Q4 earnings estimates",
                    "publisher": "Reuters",
                    "published_bj": "11-25 06:30",
                    "link": "https://example.com/news/1",
                }
            ]
        }

    def fake_ai(prompt, provider, model):
        captured["prompt"] = prompt
        captured["provider"] = provider
        return "## 一、今日总览与大盘对比\n\n今日组合表现稳健，DEMO 盘后走强。"

    report = generate_after_hours_report(
        trade_date=date(2026, 1, 2),
        person_key="ALL",
        run_sync=True,
        provider="gemini",
        model="gemini-4-hydrogen-high",
        trigger="auto",
        db_path=demo_path,
        store=store,
        sync_runner=fake_sync,
        quote_fetcher=fake_quotes,
        news_fetcher=fake_news,
        ai_asker=fake_ai,
    )

    assert report["id"] == "2026-01-02::ALL"
    assert report["sync_status"]["ok"] is True
    assert "Demo Corp beats Q4 earnings estimates" in captured["prompt"]
    assert "SPY" in captured["prompt"]
    assert store.has_report("2026-01-02", "ALL") is True

    # 桌面端页面可直接加载同一份本地存储
    page = AfterHoursReportPage(store=store, db_path=demo_path)
    qtbot.addWidget(page)
    assert page.report_list.count() == 1
    assert "DEMO 盘后走强" in page.viewer.toPlainText()


def test_after_hours_web_api_scope_isolation(tmp_path, monkeypatch):
    """验证 Web API 权限隔离：普通隔离用户仅能看自己人员日报，管理员可看全部范围。"""
    store = AfterHoursReportStore(tmp_path / "after_hours_reports.json")
    store.save_report({
        "id": "2026-11-25::个人",
        "trade_date": "2026-11-25",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-25T21:10:00+08:00",
        "trigger": "auto",
        "provider": "gemini",
        "model": "gemini-4-hydrogen-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {"summary": {"day_pnl": 520.5, "total_market_value": 50000, "trade_count": 2, "holding_count": 3}},
        "markdown": "# 个人盘后总结",
    })
    store.save_report({
        "id": "2026-11-25::拉科",
        "trade_date": "2026-11-25",
        "person_key": "拉科",
        "person_id": 5,
        "person_name": "拉科",
        "generated_at": "2026-11-25T21:12:00+08:00",
        "trigger": "manual",
        "provider": "gemini",
        "model": "gemini-4-hydrogen-high",
        "sync_status": {"ran": False, "ok": True, "message": "skip"},
        "snapshot": {"summary": {"day_pnl": -120.0, "total_market_value": 10000, "trade_count": 1, "holding_count": 2}},
        "markdown": "# 拉科盘后总结",
    })
    monkeypatch.setattr(api_module, "get_after_hours_store", lambda: store)

    # 1. 拉科只能看到自己的日报，跨人查看「个人」返回 403
    lake_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "拉科", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    lake_client = _make_client(lake_ctx)
    r_list = lake_client.get("/api/v1/ai/after-hours/reports").get_json()
    assert r_list["status"] == "ok"
    assert r_list["can_sync"] is False
    assert [x["id"] for x in r_list["reports"]] == ["2026-11-25::拉科"]
    assert lake_client.get("/api/v1/ai/after-hours/report/2026-11-25::个人").status_code == 403
    assert lake_client.get("/api/v1/ai/after-hours/report/2026-11-25::拉科").status_code == 200

    # 2. 管理员可看到全部人员的日报且允许触发同步
    admin_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 1, "username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"},
    )
    admin_client = _make_client(admin_ctx)
    a_list = admin_client.get("/api/v1/ai/after-hours/reports").get_json()
    assert a_list["status"] == "ok"
    assert a_list["can_sync"] is True
    assert len(a_list["reports"]) == 2
