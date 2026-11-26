"""美股盘后复盘日报 2.0（结构化驾驶舱、旧版兜底解析、行情估算、清单持久化、追问与桌面端）测试。"""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Flask, g

from portfolio.services.after_hours_report import (
    AfterHoursReportStore,
    ensure_structured,
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
    ready_before, _ = is_after_hours_ready(datetime(2026, 11, 25, 19, 59, tzinfo=NY_TZ))
    assert ready_before is False

    ready_after, target_day = is_after_hours_ready(datetime(2026, 11, 25, 20, 10, tzinfo=NY_TZ))
    assert ready_after is True
    assert target_day == date(2026, 11, 25)

    ready_sat, _ = is_after_hours_ready(datetime(2026, 11, 28, 21, 0, tzinfo=NY_TZ))
    assert ready_sat is False


def test_generate_structured_report_quote_fallback_and_desktop_2_0(demo_path, tmp_path, qtbot):
    """验证双轨 JSON+Markdown 输出解析、无账本日盈亏时的行情估算补齐及桌面端 2.0 交互。"""
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
                "regular_change_pct": 0.022727,
                "after_hours_price": 115.0,
                "after_hours_change_pct": 0.022222,
                "total_change_pct": 0.04545,
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
            "QQQ": {
                "symbol": "QQQ",
                "regular_close": 500.0,
                "prev_close": 505.0,
                "regular_change_pct": -0.0099,
                "after_hours_price": 502.0,
                "after_hours_change_pct": 0.004,
                "total_change_pct": -0.0059,
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
        structured_json = {
            "regime": {"label": "多头突围 · 显著跑赢", "tone": "success"},
            "tldr": "在 QQQ 收跌背景下组合逆势收涨，DEMO 财报超预期推动盘后走强。",
            "mover_reasons": {"DEMO": "Q4 业绩超预期带动常规与盘后共振走高。"},
            "news_sentiments": {"Demo Corp beats Q4 earnings estimates": "利好"},
            "news_digest": [
                {
                    "symbol": "DEMO",
                    "original_title": "Demo Corp beats Q4 earnings estimates",
                    "zh_title": "Demo Corp 四季度业绩全面超预期",
                    "summary": "四季度营收与利润均超市场一致预期，直接推动盘后买盘走强。",
                    "sentiment": "利好",
                }
            ],
            "risks": [{"level": "提示", "title": "单一持仓集中度较高", "detail": "关注明日盘前获利回吐风险。"}],
            "checklist": [
                {"id": "c1", "ticker": "DEMO", "level": "$115 关口", "text": "观察盘前是否站稳 $115 上方"},
                {"id": "c2", "ticker": "QQQ", "level": "$500 支撑", "text": "跟踪纳指期货能否企稳"},
            ],
            "trades_summary": "今日交易节奏稳健。",
            "trade_reviews": [{"symbol": "DEMO", "side": "BUY", "verdict": "精准低吸", "tone": "success", "comment": "买在起涨前。"}],
            "followups": ["明日盘前如果高开 3% 要不要先兑现一半？"],
        }
        return f"```json\n{json.dumps(structured_json, ensure_ascii=False)}\n```\n\n## 一、今日总览与大盘对比\n\nDEMO 盘后走强。"

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
    assert report["structured"]["regime"]["tone"] == "success"
    assert report["structured"]["checklist"][0]["ticker"] == "DEMO"
    assert len(report["structured"]["news_digest"]) == 1
    assert report["structured"]["news_digest"][0]["zh_title"] == "Demo Corp 四季度业绩全面超预期"
    assert report["structured"]["news_digest"][0]["publisher"] == "Reuters"
    assert "Demo Corp beats Q4 earnings estimates" in report["structured"]["news_zh_map"]
    # 2026-01-02 账本第 19 列「当日收益($)」为 0.0，叠加 10 股盘后变动 +25.0 = 25.0
    assert report["snapshot"]["summary"]["day_pnl"] == 25.0
    assert report["snapshot"]["summary"]["regular_day_pnl"] == 0.0
    assert report["snapshot"]["summary"]["after_hours_day_pnl"] == 25.0
    assert report["snapshot"]["summary"]["ledger_day_pnl"] == 0.0
    assert report["snapshot"]["summary"]["day_pnl_basis"] == "ledger_plus_after_hours"
    assert report["snapshot"]["summary"]["alpha_vs_qqq"] is not None

    # 离线/无行情时回退到账本精确口径 (0.0)
    offline_report = generate_after_hours_report(
        trade_date=date(2026, 1, 2),
        person_key="ALL",
        run_sync=False,
        db_path=demo_path,
        store=store,
        quote_fetcher=lambda _syms: {},
        news_fetcher=fake_news,
        ai_asker=fake_ai,
    )
    assert offline_report["snapshot"]["summary"]["day_pnl"] == 0.0
    assert offline_report["snapshot"]["summary"]["day_pnl_basis"] == "ledger"

    # 2026-01-05 账本第 19 列「当日收益($)」为 -30.0，叠加 6 股盘后 +15.0 = -15.0
    est_report = generate_after_hours_report(
        trade_date=date(2026, 1, 5),
        person_key="ALL",
        run_sync=False,
        db_path=demo_path,
        store=store,
        quote_fetcher=fake_quotes,
        news_fetcher=fake_news,
        ai_asker=fake_ai,
    )
    assert est_report["snapshot"]["summary"]["day_pnl"] == -15.0
    assert est_report["snapshot"]["summary"]["regular_day_pnl"] == -30.0
    assert est_report["snapshot"]["summary"]["after_hours_day_pnl"] == 15.0
    assert est_report["snapshot"]["summary"]["day_pnl_basis"] == "ledger_plus_after_hours"

    # 对于账本完全没有该日收盘记录的日期 (2026-02-02)，回退使用行情估算常规盘 + 盘后 (15.0 + 15.0 = 30.0)
    quote_only_report = generate_after_hours_report(
        trade_date=date(2026, 2, 2),
        person_key="ALL",
        run_sync=False,
        db_path=demo_path,
        store=store,
        quote_fetcher=fake_quotes,
        news_fetcher=fake_news,
        ai_asker=fake_ai,
    )
    assert quote_only_report["snapshot"]["summary"]["day_pnl"] == 30.0
    assert quote_only_report["snapshot"]["summary"]["regular_day_pnl"] == 15.0
    assert quote_only_report["snapshot"]["summary"]["after_hours_day_pnl"] == 15.0
    assert quote_only_report["snapshot"]["summary"]["day_pnl_basis"] == "quote_after_hours"

    # 桌面端 2.0 页面可渲染并勾选清单落盘，且 1280x800 下绝无横向溢出
    from PySide6.QtCore import Qt

    page = AfterHoursReportPage(store=store, db_path=demo_path)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    qtbot.wait(50)

    assert page.report_list.count() == 3
    assert "DEMO 财报超预期" in page.tldr_lbl.text()
    assert page.cl_progress_lbl.text() == "0 / 2 已核查"
    assert page.right_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.right_container.width() <= page.right_scroll.viewport().width() + 2
    assert page.kpi_card_4.isVisible()

    page._on_checklist_toggled("c1")
    assert page.cl_progress_lbl.text() == "1 / 2 已核查"
    reloaded = store.get_report("2026-02-02::ALL")
    assert reloaded["checked_ids"] == ["c1"]


def test_legacy_markdown_report_upgrades_via_ensure_structured():
    """验证没有 structured 字段的老版纯 Markdown 日报可自动解析升级为 2.0 视图。"""
    legacy = {
        "id": "2026-11-23::拉科",
        "trade_date": "2026-11-23",
        "person_key": "拉科",
        "person_name": "拉科",
        "snapshot": {
            "summary": {"holding_count": 2, "trade_count": 0, "day_pnl": -120.0, "leverage_pct": 0.56},
            "holdings": [{"symbol": "TQQQ"}],
            "trades": [],
        },
        "markdown": (
            "## 一、今日总览与大盘对比\n\n"
            "- 受高杠杆拖累回撤较大，控仓为主。\n\n"
            "## 四、仓位结构与风险提示\n\n"
            "- 三只杠杆 ETF 合计占比达 56%，存在波动率损耗风险。\n\n"
            "## 五、次日盘前关注清单\n\n"
            "1. 关注 TQQQ 在 $46.19 的支撑表现。\n"
        ),
    }
    upgraded = ensure_structured(legacy)
    assert upgraded["structured"]["regime"]["tone"] == "error"
    assert "控仓为主" in upgraded["structured"]["tldr"]
    assert len(upgraded["structured"]["risks"]) >= 1
    assert len(upgraded["structured"]["checklist"]) >= 1
    assert upgraded["structured"]["checklist"][0]["ticker"] == "TQQQ"


def test_after_hours_web_api_scope_checklist_and_followup(tmp_path, monkeypatch):
    """验证 Web API 权限隔离、清单持久化与上下文追问接口。"""
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
        "markdown": "## 一、今日总览\n\n个人总结",
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
        "markdown": "## 一、今日总览\n\n拉科总结",
    })
    monkeypatch.setattr(api_module, "get_after_hours_store", lambda: store)
    monkeypatch.setattr(
        "portfolio.services.after_hours_report.CodexAIService.ask",
        classmethod(lambda cls, prompt, context="", model=None, cancel_event=None, provider="gemini": "建议控制杠杆仓位。"),
    )

    lake_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "拉科", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    lake_client = _make_client(lake_ctx)

    # 1. 拉科只能看和改自己的日报，操作「个人」返回 403
    assert lake_client.get("/api/v1/ai/after-hours/report/2026-11-25::个人").status_code == 403
    assert lake_client.post(
        "/api/v1/ai/after-hours/report/2026-11-25::个人/checklist",
        json={"checked_ids": ["c1"]},
    ).status_code == 403
    assert lake_client.post(
        "/api/v1/ai/after-hours/report/2026-11-25::个人/followup",
        json={"question": "怎么调仓？"},
    ).status_code == 403

    # 2. 拉科勾选清单与追问正常持久化
    chk = lake_client.post(
        "/api/v1/ai/after-hours/report/2026-11-25::拉科/checklist",
        json={"checked_ids": ["c1"]},
    )
    assert chk.status_code == 200
    assert chk.get_json()["checked_ids"] == ["c1"]

    fq = lake_client.post(
        "/api/v1/ai/after-hours/report/2026-11-25::拉科/followup",
        json={"question": "明天怎么应对？"},
    )
    assert fq.status_code == 200
    assert fq.get_json()["answer"] == "建议控制杠杆仓位。"
    assert len(fq.get_json()["followups_history"]) == 1

    # 3. 管理员可看到全部人员且带 structured 字段
    admin_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 1, "username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"},
    )
    admin_client = _make_client(admin_ctx)
    detail_resp = admin_client.get("/api/v1/ai/after-hours/report/2026-11-25::拉科").get_json()
    assert detail_resp["report"]["checked_ids"] == ["c1"]
    assert "structured" in detail_resp["report"]


def test_fetch_after_hours_quotes_unpacks_longbridge_tuple_credentials(monkeypatch):
    """验证 load_longbridge_credentials 返回三元组 (token, key, secret) 时能正确调用长桥 QuoteContext。"""
    import sys
    import types

    from portfolio.services import after_hours_report as svc
    from portfolio.sync import longbridge_credentials

    captured = {}

    class _FakePost:
        last_done = "102.50"

    class _FakeQuote:
        symbol = "QQQ.US"
        last_done = "100.00"
        prev_close = "98.00"
        post_market_quote = _FakePost()

    class _FakeConfig:
        @classmethod
        def from_apikey(cls, app_key, app_secret, access_token, **_kwargs):
            captured["cfg"] = (app_key, app_secret, access_token)
            return object()

    class _FakeQuoteContext:
        def __init__(self, cfg):
            pass

        def quote(self, symbols):
            captured["symbols"] = list(symbols)
            return [_FakeQuote()]

    fake_mod = types.ModuleType("longbridge.openapi")
    fake_mod.Config = _FakeConfig
    fake_mod.QuoteContext = _FakeQuoteContext
    fake_pkg = types.ModuleType("longbridge")
    fake_pkg.openapi = fake_mod

    monkeypatch.setitem(sys.modules, "longbridge", fake_pkg)
    monkeypatch.setitem(sys.modules, "longbridge.openapi", fake_mod)
    monkeypatch.setattr(longbridge_credentials, "load_longbridge_credentials", lambda: ("tok-1", "key-2", "sec-3"))

    quotes = svc.fetch_after_hours_quotes(["QQQ"])
    assert captured["cfg"] == ("key-2", "sec-3", "tok-1")
    assert captured["symbols"] == ["QQQ.US"]
    assert quotes["QQQ"]["source"] == "longbridge"
    assert quotes["QQQ"]["after_hours_price"] == 102.5


def test_after_hours_generate_async_task_polling_and_isolation(tmp_path, monkeypatch):
    """验证盘后日报 async=True 立刻返回 202 任务号，可轮询获取结果且严格隔离人员权限。"""
    import time

    from portfolio.services.after_hours_report import AfterHoursReportStore

    store = AfterHoursReportStore(tmp_path / "reports.json")
    monkeypatch.setattr(api_module, "get_after_hours_store", lambda: store)
    with api_module._AFTER_HOURS_TASKS_LOCK:
        api_module._AFTER_HOURS_TASKS.clear()

    def _fake_generate(**kwargs):
        return {
            "id": "2026-11-25::拉科",
            "trade_date": "2026-11-25",
            "person": kwargs["person_key"],
            "model": kwargs.get("model") or "",
            "markdown": "# 复盘\n\n## 一、核心结论\n测试生成成功。",
        }

    import portfolio.services.after_hours_report as svc

    monkeypatch.setattr(svc, "generate_after_hours_report", _fake_generate)

    lake_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "拉科", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    other_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 3, "username": "华", "role": "user", "person_id": 2, "person_name": "华"},
    )
    lake_client = _make_client(lake_ctx)
    other_client = _make_client(other_ctx)

    resp = lake_client.post(
        "/api/v1/ai/after-hours/generate",
        json={"trade_date": "2026-11-25", "person": "拉科", "async": True},
    )
    assert resp.status_code == 202
    task_id = resp.get_json()["task_id"]

    # 其他隔离人员查询该任务应返回 403
    assert other_client.get(f"/api/v1/ai/after-hours/task/{task_id}").status_code == 403

    # 拉科轮询直至任务完成
    final = None
    for _ in range(30):
        st = lake_client.get(f"/api/v1/ai/after-hours/task/{task_id}")
        assert st.status_code == 200
        task = st.get_json()["task"]
        if task["status"] == "done":
            final = task
            break
        time.sleep(0.05)

    assert final is not None
    assert final["report"]["id"] == "2026-11-25::拉科"
    assert "structured" in final["report"]


def test_after_hours_cash_balance_and_missing_scope_never_fakes_zero(demo_path, tmp_path, monkeypatch):
    """验证现金余额正常汇总与无独立现金账本范围保持 None（绝不伪造 0）。"""
    from portfolio.services import after_hours_report as svc

    monkeypatch.setattr(
        svc,
        "resolve_channel_cash_details",
        lambda _res: {
            "LONGBRIDGE": {"channel": "长桥", "amount": 1200.5, "source": "ledger"},
            "BBAE": {"channel": "BBAE", "amount": 300.0, "source": "ledger"},
        },
    )
    snap_with_cash = svc.build_after_hours_snapshot(
        trade_date=date(2026, 1, 5),
        person_key="ALL",
        person_id=None,
        person_name="全部人员",
        db_path=demo_path,
        quote_fetcher=lambda _syms: {},
        news_fetcher=lambda _syms: {},
    )
    assert snap_with_cash["summary"]["cash_balance"] == 1500.5
    assert snap_with_cash["summary"]["cash_pct"] is not None
    assert len(snap_with_cash["summary"]["cash_breakdown"]) == 2
    assert snap_with_cash["summary"]["cash_breakdown"][0]["channel"] == "长桥"

    monkeypatch.setattr(svc, "resolve_channel_cash_details", lambda _res: {})
    snap_no_cash = svc.build_after_hours_snapshot(
        trade_date=date(2026, 1, 5),
        person_key="ALL",
        person_id=None,
        person_name="全部人员",
        db_path=demo_path,
        quote_fetcher=lambda _syms: {},
        news_fetcher=lambda _syms: {},
    )
    assert snap_no_cash["summary"]["cash_balance"] is None
    assert snap_no_cash["summary"]["cash_pct"] is None
    assert snap_no_cash["summary"]["cash_breakdown"] == []


def test_ai_service_injects_system_proxies_when_env_missing(monkeypatch):
    """验证无代理环境变量时，_inject_system_proxies 自动从系统代理读取并注入。"""
    import urllib.request

    from portfolio.services.ai_service import CodexAIService

    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"},
    )
    env = {}
    CodexAIService._inject_system_proxies(env)
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7897"


def test_after_hours_default_model_timeout_and_news_dedup():
    """验证默认模型为 gemini-4-neon-high、CLI 超时 600 秒及新闻去重限流逻辑。"""
    import inspect

    from portfolio.services.after_hours_report import (
        DEFAULT_AFTER_HOURS_MODEL,
        _dedupe_and_trim_news_for_prompt,
    )
    from portfolio.services.ai_service import CodexAIService

    assert DEFAULT_AFTER_HOURS_MODEL == "gemini-4-neon-high"
    sig = inspect.signature(CodexAIService._run_cli_process)
    assert sig.parameters["max_timeout"].default == 600.0

    news = {
        "TQQQ": [
            {"title": "Nasdaq tumbles on AI chip selloff today", "publisher": "Reuters"},
            {"title": "Nasdaq tumbles on AI chip selloff today!", "publisher": "Yahoo"},
            {"title": "ProShares TQQQ sees heavy volume", "publisher": "MarketWatch"},
            {"title": "Third distinct story for TQQQ", "publisher": "CNBC"},
        ],
        "SOXL": [
            {"title": "Nasdaq tumbles on AI chip selloff today", "publisher": "Reuters"},
            {"title": "Semiconductor index drops 5%", "publisher": "Bloomberg"},
        ],
    }
    holdings = [{"symbol": "TQQQ"}, {"symbol": "SOXL"}]
    trimmed = _dedupe_and_trim_news_for_prompt(news, holdings, max_per_symbol=2, max_total=10)
    trimmed_dict = dict(trimmed)
    assert len(trimmed_dict["TQQQ"]) == 2
    assert len(trimmed_dict["SOXL"]) == 1
    assert trimmed_dict["SOXL"][0]["title"] == "Semiconductor index drops 5%"


def test_desktop_after_hours_sync_person_bidirectional(demo_path, tmp_path, qtbot):
    """验证桌面端盘后复盘页与全局人员筛选框双向联动（例如拉科/ALL/个人）。"""
    store = AfterHoursReportStore(tmp_path / "reports.json")
    store.save_report({
        "id": "2026-11-25::个人",
        "trade_date": "2026-11-25",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-25T21:10:00+08:00",
        "trigger": "manual",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {"summary": {"day_pnl": 100.0, "total_market_value": 10000, "trade_count": 0, "holding_count": 1}},
        "markdown": "## 一、今日总览\n\n个人",
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
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {"summary": {"day_pnl": -50.0, "total_market_value": 2000, "trade_count": 0, "holding_count": 1}},
        "markdown": "## 一、今日总览\n\n拉科",
    })

    page = AfterHoursReportPage(store=store, db_path=demo_path)
    qtbot.addWidget(page)
    persons = [{"id": 1, "name": "个人"}, {"id": 5, "name": "拉科"}]
    page.populate_persons(persons)

    emitted: list[object] = []
    page.personSelectionChanged.connect(emitted.append)

    # 1. 全局选中「拉科」(id=5) -> 盘后页自动切到「拉科」并定位拉科存档，不回发信号
    page.sync_person_from_main(5, persons)
    assert page.person_combo.currentData() == "拉科"
    assert emitted == []

    # 2. 全局选中「全部启用人员」(id=None) -> 盘后页自动切到「ALL」
    page.sync_person_from_main(None, persons)
    assert page.person_combo.currentData() == "ALL"
    assert emitted == []

    # 3. 在盘后页手动切回「拉科」-> 发射 personSelectionChanged(5)
    idx_lake = page.person_combo.findData("拉科")
    page.person_combo.setCurrentIndex(idx_lake)
    assert emitted == [5]
