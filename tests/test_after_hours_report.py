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
    # 默认全部展开持仓卡片且新闻标题带可点击外链
    from PySide6.QtWidgets import QLabel

    labels = page.movers_box.parentWidget().findChildren(QLabel) if page.movers_box.parentWidget() else []
    assert any("https://example.com/news/1" in lbl.text() and lbl.openExternalLinks() for lbl in labels)

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


def test_after_hours_market_calendar_holiday_and_early_close():
    """验证感恩节休市跳过与黑五半日市 17:10 ET 自动触发逻辑。"""
    from portfolio.services.after_hours_report import resolve_default_trade_date

    # 2026-11-26 感恩节全天休市：不触发，默认日期回退至 11-25
    ready_tg, _ = is_after_hours_ready(datetime(2026, 11, 26, 20, 30, tzinfo=NY_TZ))
    assert ready_tg is False
    assert resolve_default_trade_date(datetime(2026, 11, 26, 20, 30, tzinfo=NY_TZ)) == date(2026, 11, 25)

    # 2026-11-27 黑五半日市：13:00 ET 收盘后默认日期切到 11-27，17:10 ET 到达触发窗口
    assert resolve_default_trade_date(datetime(2026, 11, 27, 12, 59, tzinfo=NY_TZ)) == date(2026, 11, 25)
    assert resolve_default_trade_date(datetime(2026, 11, 27, 13, 0, tzinfo=NY_TZ)) == date(2026, 11, 27)
    ready_bf_before, _ = is_after_hours_ready(datetime(2026, 11, 27, 17, 9, tzinfo=NY_TZ))
    ready_bf_after, target_bf = is_after_hours_ready(datetime(2026, 11, 27, 17, 10, tzinfo=NY_TZ))
    assert ready_bf_before is False
    assert ready_bf_after is True
    assert target_bf == date(2026, 11, 27)


def test_after_hours_leverage_profile_cost_anomaly_and_total_assets(demo_path, monkeypatch):
    """验证杠杆倍数解析、异常成本熔断告警及总资产净值口径。"""
    from decimal import Decimal

    from portfolio.services.after_hours_report import (
        build_after_hours_snapshot,
        resolve_leverage_profile,
    )

    assert resolve_leverage_profile("QQQM") == (False, Decimal("1"))
    assert resolve_leverage_profile("TQQQ") == (True, Decimal("3"))
    assert resolve_leverage_profile("SQQQ") == (True, Decimal("-3"))

    # 构造带异常成本（现价与均价偏离 >50% 且比值 >=2x）的持仓，验证仅告警不篡改
    class _FakeRow:
        def __init__(self, values):
            self.values = values

    class _FakeTable:
        def __init__(self, columns, rows):
            self.columns = columns
            self.rows = rows

    class _FakeResult:
        def __init__(self, reports):
            self.reports = reports

    class _FakeService:
        def __init__(self, db_path):
            pass

        def calculate(self, scope):
            return _FakeResult({
                "当前持仓": _FakeTable(
                    ["渠道", "分类", "Ticker", "持仓数量", "累计净投入($)", "持仓成本价($)", "持仓市值($)", "持仓盈亏金额($)"],
                    [
                        _FakeRow(["IBKR", "股票", "AAPL", "10", "100.00", "10.00", "2000.00", "1900.00"]),
                        _FakeRow(["IBKR", "杠杆", "TQQQ", "10", "500.00", "50.00", "600.00", "100.00"]),
                        _FakeRow(["IBKR", "股票", "MRVL", "10", "2500.00", "250.00", "1000.00", "-1500.00"]),
                        _FakeRow(["IBKR", "杠杆", "SPAL", "10", "600.00", "60.00", "100.00", "-500.00"]),
                    ],
                ),
                "每日持仓收益": _FakeTable([], []),
                "交易回放": _FakeTable([], []),
            })

    monkeypatch.setattr("portfolio.services.after_hours_report.AnalyticsService", _FakeService)
    monkeypatch.setattr(
        "portfolio.services.after_hours_report.fetch_after_hours_quotes",
        lambda symbols: {
            "AAPL": {
                "regular_close": Decimal("200.00"),
                "regular_change_pct": Decimal("0.01"),
                "after_hours_price": None,
                "after_hours_change_pct": None,
                "source": "mock",
            },
            "TQQQ": {
                "regular_close": Decimal("60.00"),
                "regular_change_pct": Decimal("0.02"),
                "after_hours_price": None,
                "after_hours_change_pct": None,
                "source": "mock",
            },
            "MRVL": {
                "regular_close": Decimal("100.00"),
                "regular_change_pct": Decimal("-0.01"),
                "after_hours_price": None,
                "after_hours_change_pct": None,
                "source": "mock",
            },
            "SPAL": {
                "regular_close": Decimal("10.00"),
                "regular_change_pct": Decimal("-0.02"),
                "after_hours_price": None,
                "after_hours_change_pct": None,
                "source": "mock",
            },
            "QQQ": {"regular_close": Decimal("500.00"), "regular_change_pct": Decimal("0.01"), "source": "mock"},
            "SPY": {"regular_close": Decimal("600.00"), "regular_change_pct": Decimal("0.005"), "source": "mock"},
        },
    )
    monkeypatch.setattr(
        "portfolio.services.after_hours_report.resolve_channel_cash_details",
        lambda result: {"IBKR": {"channel": "IBKR", "amount": Decimal("400.00"), "source": "mock"}},
    )
    monkeypatch.setattr("portfolio.services.after_hours_report.fetch_ticker_news", lambda symbols: {})

    snap = build_after_hours_snapshot(demo_path, date(2026, 11, 25), person_key="个人", person_id=1, person_name="个人")
    summary = snap["summary"]
    # MRVL (2.5x < 5x) 与 SPAL (6x < 8x 杠杆阈值) 不误报，仅 AAPL (20x >= 5x) 触发异常提示
    assert summary["cost_anomaly_symbols"] == ["AAPL"]
    assert summary["unrealized_pnl_ex_anomaly"] == -1900.0
    assert summary["total_market_value"] == 3700.0
    assert summary["cash_balance"] == 400.0
    assert summary["total_assets"] == 4100.0
    # 组合等效敞口：(2000*1 + 600*3 + 1000*1 + 100*2) / 3700 = 5000 / 3700
    assert round(summary["effective_leverage"], 4) == round(5000 / 3700, 4)
    assert round(summary["effective_leverage_total_assets"], 4) == round(5000 / 4100, 4)


def test_after_hours_delete_api_and_market_closed_guard(tmp_path, monkeypatch):
    """验证休市日生成拦截 (MARKET_CLOSED) 与 DELETE 删除历史日报权限隔离。"""
    store = AfterHoursReportStore(tmp_path / "reports.json")
    store.save_report({
        "id": "2026-11-26::个人",
        "trade_date": "2026-11-26",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-26T21:00:00+08:00",
        "trigger": "manual",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {"summary": {"day_pnl": 0.0, "total_market_value": 1000.0}},
        "markdown": "## 一、今日总览\n\n误生成休市日报",
    })
    monkeypatch.setattr(api_module, "get_after_hours_store", lambda: store)

    admin_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 1, "username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"},
    )
    admin_client = _make_client(admin_ctx)

    # 1. GET /reports 应返回 market_session
    res_list = admin_client.get("/api/v1/ai/after-hours/reports?person=个人")
    assert res_list.status_code == 200
    assert "market_session" in res_list.get_json()

    # 2. 休市日 (2026-11-26 感恩节) 触发生成返回 400 MARKET_CLOSED
    res_closed = admin_client.post(
        "/api/v1/ai/after-hours/generate",
        json={"trade_date": "2026-11-26", "person": "个人", "async": True},
    )
    assert res_closed.status_code == 400
    assert res_closed.get_json()["code"] == "MARKET_CLOSED"

    # 3. 隔离用户无权删除他人日报，管理员可删除
    lake_ctx = build_auth_context(
        "REMOTE_PUBLIC",
        session_user={"user_id": 2, "username": "拉科", "role": "user", "person_id": 5, "person_name": "拉科"},
    )
    lake_client = _make_client(lake_ctx)
    res_forbid = lake_client.delete("/api/v1/ai/after-hours/report/2026-11-26::个人")
    assert res_forbid.status_code == 403

    res_del = admin_client.delete("/api/v1/ai/after-hours/report/2026-11-26::个人")
    assert res_del.status_code == 200
    assert store.get_report("2026-11-26::个人") is None


def test_desktop_after_hours_session_badges_closed_guard_and_delete(demo_path, tmp_path, qtbot):
    """验证桌面端半日市徽标、总资产显示、休市日生成拦截及删除存档交互。"""
    from PySide6.QtCore import QDate

    store = AfterHoursReportStore(tmp_path / "reports.json")
    store.save_report({
        "id": "2026-11-27::个人",
        "trade_date": "2026-11-27",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-28T06:15:00+08:00",
        "trigger": "auto",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {
            "market_session": {
                "trade_date": "2026-11-27",
                "is_trading_day": True,
                "is_early_close": True,
                "holiday_name": "感恩节次日半日市 (Black Friday)",
            },
            "summary": {
                "day_pnl": 320.0,
                "day_pnl_pct": 0.032,
                "day_pnl_pct_total_assets": 0.0267,
                "total_market_value": 10000.0,
                "cash_balance": 2000.0,
                "total_assets": 12000.0,
                "effective_leverage": 1.5,
                "effective_leverage_total_assets": 1.25,
                "trade_count": 1,
                "holding_count": 2,
            },
        },
        "markdown": "## 一、今日总览\n\n黑五半日市",
    })
    store.save_report({
        "id": "2026-11-25::个人",
        "trade_date": "2026-11-25",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-26T09:15:00+08:00",
        "trigger": "manual",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {"summary": {"day_pnl": -100.0, "total_market_value": 9500.0, "trade_count": 0, "holding_count": 2}},
        "markdown": "## 一、今日总览\n\n周三",
    })

    page = AfterHoursReportPage(store=store, db_path=demo_path)
    qtbot.addWidget(page)

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from portfolio.ui.date_picker import DatePicker

    assert isinstance(page.date_edit, DatePicker)
    assert page.date_edit.calendarPopup() is True
    assert page.date_edit.maximumDate() <= QDate.currentDate()
    before_wheel = page.date_edit.date()
    wheel_ev = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPointF(0, 0).toPoint(),
        QPointF(0, 120).toPoint(),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    page.date_edit.wheelEvent(wheel_ev)
    assert page.date_edit.date() == before_wheel

    assert not page.session_badge.isHidden()
    assert "半日市" in page.session_badge.text()
    assert "$12,000.00" in page.kpi_mv_val.text()
    assert page.delete_btn.isEnabled()

    # 感恩节休市日拦截生成
    page.date_edit.setDate(QDate(2026, 11, 26))
    page._start_generate()
    assert page._worker is None
    assert "休市" in page.status_lbl.text()

    # 删除当前选中存档
    page._delete_current_report(confirm=False)
    assert store.get_report("2026-11-27::个人") is None
    assert len(store.list_reports()) == 1


def test_desktop_after_hours_ui_layout_sorting_folding_kpis_and_sidebar(demo_path, tmp_path, qtbot):
    """验证桌面端首屏信息层级、持仓分级折叠与排序、KPI 优化、追问直达与左侧存档折叠。"""
    from PySide6.QtCore import Qt

    store = AfterHoursReportStore(tmp_path / "reports.json")
    # 6 只不同权重持仓：前 4 只权重 >= 5%，后 2 只为小仓位 (< 5%)
    holdings = [
        {"symbol": "NVDA", "channels": ["IBKR"], "quantity": 10, "avg_cost": 100.0, "market_value": 4000, "weight_pct": 0.40, "regular_close": 140.0, "regular_change_pct": 0.03, "after_hours_price": 141.0, "after_hours_change_pct": 0.007, "day_pnl": 120.0, "unrealized_pnl": 400.0},
        {"symbol": "TQQQ", "channels": ["IBKR"], "quantity": 20, "avg_cost": 60.0, "market_value": 2500, "weight_pct": 0.25, "regular_close": 70.0, "regular_change_pct": -0.05, "after_hours_price": 69.0, "after_hours_change_pct": -0.014, "day_pnl": -350.0, "unrealized_pnl": -200.0, "is_leveraged": True, "leverage_factor": 3},
        {"symbol": "MSFT", "channels": ["IBKR"], "quantity": 5, "avg_cost": 400.0, "market_value": 2000, "weight_pct": 0.20, "regular_close": 410.0, "regular_change_pct": 0.01, "day_pnl": 20.0, "unrealized_pnl": 50.0},
        {"symbol": "AAPL", "channels": ["IBKR"], "quantity": 4, "avg_cost": 200.0, "market_value": 900, "weight_pct": 0.09, "regular_close": 225.0, "regular_change_pct": 0.02, "day_pnl": 18.0, "unrealized_pnl": 100.0},
        {"symbol": "AMD", "channels": ["IBKR"], "quantity": 2, "avg_cost": 180.0, "market_value": 400, "weight_pct": 0.04, "regular_close": 150.0, "regular_change_pct": -0.06, "day_pnl": -25.0, "unrealized_pnl": -60.0},
        {"symbol": "PLTR", "channels": ["IBKR"], "quantity": 3, "avg_cost": 50.0, "market_value": 200, "weight_pct": 0.02, "regular_close": 65.0, "regular_change_pct": 0.08, "day_pnl": 15.0, "unrealized_pnl": 45.0},
    ]
    store.save_report({
        "id": "2026-11-27::个人",
        "trade_date": "2026-11-27",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-27T21:00:00+08:00",
        "trigger": "manual",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {
            "holdings": holdings,
            "market_benchmarks": {
                "SPY": {"regular_change_pct": -0.004, "after_hours_change_pct": 0.002},
                "QQQ": {"regular_change_pct": 0.008, "after_hours_change_pct": -0.001},
            },
            "summary": {
                "day_pnl": -202.0,
                "day_pnl_pct": -0.02,
                "total_market_value": 10000.0,
                "cash_balance": 2000.0,
                "total_assets": 12000.0,
                "total_unrealized_pnl": -150.0,
                "unrealized_pct": -0.015,
                "leverage_pct": 0.25,
                "effective_leverage": 1.50,
                "effective_leverage_total_assets": 1.25,
                "trade_count": 0,
                "holding_count": 6,
            },
        },
        "structured": {
            "tldr": "核心仓位分化",
            "risks": [{"level": "提示", "title": "集中度", "detail": "观察"}],
            "checklist": [{"id": "c1", "ticker": "TQQQ", "level": "$68", "text": "关注支撑"}],
        },
        "markdown": "## 一、今日总览\n\n测试",
    })

    page = AfterHoursReportPage(store=store, db_path=demo_path)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    qtbot.wait(50)

    # 1. 首屏顺序：风控雷达+关注清单双栏位于持仓异动区域上方
    tab_ov = page.tabs.widget(0)
    ov_layout = tab_ov.layout()
    assert ov_layout.indexOf(page.rc_container) < ov_layout.indexOf(page.movers_section)

    # 2. 分级默认折叠：权重 >= 5% 的前 4 只展开，尾部小仓位 AMD/PLTR 默认折叠
    assert page._expanded_syms == {"NVDA", "TQQQ", "MSFT", "AAPL"}
    assert page.toggle_expand_all_btn.text() == "全部展开"

    # 单击切换单个标的展开，以及「全部展开 / 收起小仓位」切换
    page._toggle_symbol_expand("AMD")
    assert "AMD" in page._expanded_syms
    page._toggle_expand_all_movers()
    assert {"NVDA", "TQQQ", "MSFT", "AAPL", "AMD", "PLTR"}.issubset(page._expanded_syms)
    assert page.toggle_expand_all_btn.text() == "收起小仓位"
    page._toggle_expand_all_movers()
    assert page._expanded_syms == {"NVDA", "TQQQ", "MSFT", "AAPL"}

    # 3. 排序下拉（按当日盈亏贡献 / 按仓位权重 / 按涨跌幅）
    def _first_card_symbol() -> str:
        first_card = page.movers_box.itemAt(0).widget()
        for lbl in first_card.findChildren(type(page.count_lbl)):
            txt = lbl.text().strip()
            if txt in {"NVDA", "TQQQ", "MSFT", "AAPL", "AMD", "PLTR"}:
                return txt
        return ""

    assert _first_card_symbol() == "TQQQ"  # |day_pnl|=350 最大
    page.mover_sort_combo.setCurrentIndex(page.mover_sort_combo.findData("weight"))
    assert _first_card_symbol() == "NVDA"  # weight=40% 最大
    page.mover_sort_combo.setCurrentIndex(page.mover_sort_combo.findData("change"))
    assert _first_card_symbol() == "PLTR"  # |change|=8% 最大

    # 4. KPI 细节：浮亏动态文案、大盘基准双行着色、杠杆等效胶囊（无 0 笔）
    assert "浮亏" in page.kpi_un_sub.text()
    assert page.kpi_bench_val.isHidden()
    assert "SPY" in page.kpi_bench_sub.text() and "QQQ" in page.kpi_bench_sub.text()
    assert "#DC2626" in page.kpi_bench_sub.text() and "#16A34A" in page.kpi_bench_sub.text()
    assert page.kpi_eff_lev_badge.text() == "等效 1.50x"
    assert "笔" not in page.kpi_eff_lev_badge.text()

    # 5. 展开态显示盘后具体价格 & 筛选状态下「全部展开 / 收起小仓位」口径一致
    all_movers_text = " ".join(lbl.text() for lbl in page.movers_section.findChildren(type(page.count_lbl)))
    assert "盘后 $141.00" in all_movers_text
    assert "盘后 $69.00" in all_movers_text

    page._set_mover_filter("down")
    assert page.toggle_expand_all_btn.text() == "全部展开"
    page._toggle_expand_all_movers()
    assert "AMD" in page._expanded_syms
    assert page.toggle_expand_all_btn.text() == "收起小仓位"
    page._toggle_expand_all_movers()
    assert "AMD" not in page._expanded_syms
    assert "TQQQ" in page._expanded_syms
    assert page.toggle_expand_all_btn.text() == "全部展开"
    page._set_mover_filter("all")

    # 6. Hero 「追问 Gemini」直达聚焦（含重复点击不报错） & 左侧存档栏折叠/展开 & 零横向溢出
    page.jump_followup_btn.click()
    page.jump_followup_btn.click()
    qtbot.wait(240)
    assert page.followup_input.hasFocus() or page.isVisible()
    assert not page.left_card.isHidden()
    assert page.expand_sidebar_btn.isHidden()
    page.collapse_sidebar_btn.click()
    assert page.left_card.isHidden()
    assert not page.expand_sidebar_btn.isHidden()
    page.expand_sidebar_btn.click()
    assert not page.left_card.isHidden()
    assert page.right_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.right_container.width() <= page.right_scroll.viewport().width() + 2


def test_after_hours_five_new_dimensions_backend_and_prompt(demo_path, tmp_path, monkeypatch):
    """验证 5 项新汇总维度：趋势回撤、SMH/VIX/TNX 风向标、7天事件预警、昨日清单闭环与穿透敞口。"""
    from decimal import Decimal

    from portfolio.services.after_hours_report import (
        _normalize_tnx_yield,
        build_after_hours_snapshot,
        fetch_upcoming_earnings_events,
    )

    # 1. ^TNX 量纲归一化防 10 倍误差 + 真实 fetch_upcoming_earnings_events 路径验证（内置 verified_earnings.json）
    assert _normalize_tnx_yield(Decimal("42.5")) == Decimal("4.25")
    assert _normalize_tnx_yield(Decimal("4.922")) == Decimal("4.922")
    real_earn = fetch_upcoming_earnings_events(
        [{"symbol": "GGLL", "underlying": "GOOGL"}, {"symbol": "MSFT", "underlying": None}],
        date(2026, 10, 25),
        days_ahead=7,
    )
    earn_syms = {e["symbol"] for e in real_earn}
    assert {"GOOGL", "MSFT"}.issubset(earn_syms)
    assert all(1 <= e["days_away"] <= 7 for e in real_earn)

    # 构造多日「每日持仓收益」用于验证近 5 日、WTD、MTD、连跌 2 日与 30 日高点回撤，以及穿透敞口组合
    class _FakeRow:
        def __init__(self, values):
            self.values = values

    class _FakeTable:
        def __init__(self, columns, rows):
            self.columns = columns
            self.rows = rows

    class _FakeResult:
        def __init__(self, reports):
            self.reports = reports

    # 持仓：TQQQ(3x, $3000) + QQQM(1x, $2000) -> 纳指100 净敞口 $11000 (110%)
    #       GGLL(2x, $1000) + GOOGL(1x, $1000) -> GOOGL 净敞口 $3000 (30%)
    #       TSLL(2x, $1000) + TSLA(1x, $1000) -> TSLA 净敞口 $3000 (30%)
    #       SOXL(3x, $1000) -> 费城半导体 净敞口 $3000 (30%)
    # 总市值 = $10,000
    class _FakeService:
        def __init__(self, db_path):
            pass

        def calculate(self, scope):
            daily_cols = ["日期", "渠道", "分类", "Ticker", "日末状态"] + [f"c{i}" for i in range(5, 11)] + [
                "日末市值($)", "c12", "c13", "c14", "期初市值($)", "c16", "c17", "c18", "当日收益($)"
            ]

            def _drow(ds: str, end_mv: str, prev_mv: str, pnl: str):
                vals = [ds, "IBKR", "杠杆", "TQQQ", "持仓"] + [""] * 6 + [end_mv, "", "", "", prev_mv, "", "", "", pnl]
                return _FakeRow(vals)

            return _FakeResult({
                "当前持仓": _FakeTable(
                    ["渠道", "分类", "Ticker", "持仓数量", "累计净投入($)", "持仓成本价($)", "持仓市值($)", "持仓盈亏金额($)"],
                    [
                        _FakeRow(["IBKR", "杠杆", "TQQQ", "50", "2800", "56", "3000", "200"]),
                        _FakeRow(["IBKR", "大盘", "QQQM", "10", "1900", "190", "2000", "100"]),
                        _FakeRow(["IBKR", "杠杆", "GGLL", "20", "900", "45", "1000", "100"]),
                        _FakeRow(["IBKR", "正股", "GOOGL", "5", "950", "190", "1000", "50"]),
                        _FakeRow(["IBKR", "杠杆", "TSLL", "50", "950", "19", "1000", "50"]),
                        _FakeRow(["IBKR", "正股", "TSLA", "3", "950", "316.67", "1000", "50"]),
                        _FakeRow(["IBKR", "杠杆", "SOXL", "30", "1050", "35", "1000", "-50"]),
                    ],
                ),
                "每日持仓收益": _FakeTable(
                    daily_cols,
                    [
                        # 12 月第 1 周上涨至高点，随后连跌 2 日 (12-07, 12-08)
                        _drow("2026-12-01", "10000", "9800", "200"),
                        _drow("2026-12-02", "10500", "10000", "500"),
                        _drow("2026-12-03", "11000", "10500", "500"),
                        _drow("2026-12-04", "11000", "11000", "0"),
                        _drow("2026-12-07", "10450", "11000", "-550"),  # -5%
                        _drow("2026-12-08", "10000", "10450", "-450"),  # -4.306%
                    ],
                ),
                "交易回放": _FakeTable([], []),
            })

    monkeypatch.setattr("portfolio.services.after_hours_report.AnalyticsService", _FakeService)
    monkeypatch.setattr(
        "portfolio.services.after_hours_report.resolve_channel_cash_details",
        lambda result: {"IBKR": {"channel": "IBKR", "amount": Decimal("2000.00"), "source": "mock"}},
    )

    def _fake_quotes(symbols):
        assert {"SPY", "QQQ", "SMH", "^VIX", "^TNX"}.issubset(set(symbols))
        return {
            "SPY": {"regular_close": 600.0, "regular_change_pct": -0.005, "after_hours_change_pct": 0.001, "source": "mock"},
            "QQQ": {"regular_close": 500.0, "regular_change_pct": -0.01, "after_hours_change_pct": 0.002, "source": "mock"},
            "SMH": {"regular_close": 240.0, "regular_change_pct": -0.015, "source": "mock"},
            "^VIX": {"regular_close": 18.4, "regular_change_pct": 0.062, "source": "yahoo"},
            "^TNX": {"regular_close": 42.5, "prev_close": 42.0, "source": "yahoo"},  # 故意传入 10x 量纲
        }

    def _fake_earnings(holdings_list, td, days_ahead=7):
        return [
            {
                "date": "2026-12-11",
                "days_away": 3,
                "symbol": "GOOGL",
                "held_symbols": ["GGLL", "GOOGL"],
                "time_label": "盘后",
                "title": "GOOGL 财报 (盘后)（关联持仓 GGLL）",
            }
        ]

    # 测试：earnings_fetcher 抛异常时静默跳过，绝不阻塞
    snap_err = build_after_hours_snapshot(
        demo_path,
        date(2026, 12, 8),
        person_key="个人",
        person_id=1,
        person_name="个人",
        quote_fetcher=_fake_quotes,
        news_fetcher=lambda syms: {},
        earnings_fetcher=lambda hs, td, d=7: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    assert snap_err["upcoming_events"]["earnings"] == []
    assert any("FOMC" in e["title"] for e in snap_err["upcoming_events"]["macro"])

    # 昨日存档 + 今日生成完整闭环（使用 ALL 避免受 demo_path 演示人员名称影响）
    store = AfterHoursReportStore(tmp_path / "reports.json")
    store.save_report({
        "id": "2026-12-07::ALL",
        "trade_date": "2026-12-07",
        "person_key": "ALL",
        "person_id": None,
        "person_name": "全部人员",
        "generated_at": "2026-12-08T09:10:00+08:00",
        "trigger": "auto",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {"summary": {"day_pnl": -550.0, "total_market_value": 10450.0}},
        "structured": {
            "tldr": "昨日承压",
            "checklist": [
                {"id": "c1", "ticker": "TQQQ", "level": "$60", "text": "跌破 $60 减仓 20%"},
                {"id": "c2", "ticker": "GOOGL", "level": "$195", "text": "财报前控制杠杆"},
            ],
        },
        "checked_ids": ["c1"],
        "markdown": "## 一、今日总览\n\n昨日",
    })

    captured_prompts: list[str] = []

    def _fake_ai(prompt, prov, model):
        captured_prompts.append(prompt)
        return (
            "```json\n"
            + json.dumps(
                {
                    "regime": {"label": "避险降档", "tone": "error"},
                    "tldr": "连跌两日回撤扩大，临近 FOMC 与 GOOGL 财报需降杠杆。",
                    "risks": [{"level": "高危", "title": "FOMC 前杠杆偏高", "detail": "纳指穿透敞口超 110%"}],
                    "checklist": [{"id": "c1", "ticker": "TQQQ", "level": "$58", "text": "事件前降至 30% 以下"}],
                    "prev_checklist_review": {
                        "prev_date": "2026-12-07",
                        "summary": "昨日已勾选 TQQQ 止损线，GOOGL 财报降杠杆尚未执行。",
                        "items": [
                            {"ticker": "TQQQ", "status": "已执行", "note": "已按纪律核查"},
                            {"ticker": "GOOGL", "status": "未执行", "note": "仍持有 GGLL 2x 杠杆"},
                        ],
                    },
                },
                ensure_ascii=False,
            )
            + "\n```\n\n## 一、今日总览与大盘对比\n\n测试正文"
        )

    rep = generate_after_hours_report(
        trade_date=date(2026, 12, 8),
        person_key="ALL",
        run_sync=False,
        db_path=demo_path,
        store=store,
        quote_fetcher=_fake_quotes,
        news_fetcher=lambda syms: {},
        earnings_fetcher=_fake_earnings,
        ai_asker=_fake_ai,
    )

    snap = rep["snapshot"]
    # 断言 1：近期趋势与回撤
    trend = snap["summary"]["trend"]
    assert trend["pnl_5d"] == 0.0  # 500 + 500 + 0 - 550 - 450 = 0
    assert trend["pnl_wtd"] == -1000.0  # 12-07(-550) + 12-08(-450)
    assert trend["pnl_mtd"] == 200.0  # 200 + 500 + 500 + 0 - 550 - 450 = 200
    assert trend["streak_days"] == -2
    assert trend["drawdown_30d_pct"] < -0.08  # 从峰值 11000 回落约 -9.09%

    # 断言 2：SMH / VIX / TNX（TNX 归一化为 4.25）
    assert snap["market_benchmarks"]["SMH"]["regular_change_pct"] == -0.015
    assert snap["market_benchmarks"]["^VIX"]["regular_close"] == 18.4
    assert snap["market_benchmarks"]["^TNX"]["regular_close"] == 4.25

    # 断言 3：未来 7 天持仓财报与宏观大事件
    assert len(snap["upcoming_events"]["earnings"]) == 1
    assert snap["upcoming_events"]["earnings"][0]["symbol"] == "GOOGL"
    assert any("FOMC" in e["title"] for e in snap["upcoming_events"]["macro"])
    assert any("CPI" in e["title"] for e in snap["upcoming_events"]["macro"])

    # 断言 4：昨日清单上下文与 AI 复盘结构化输出
    assert snap["prev_checklist_context"]["prev_date"] == "2026-12-07"
    assert snap["prev_checklist_context"]["checked_ids"] == ["c1"]
    prev_rev = rep["structured"]["prev_checklist_review"]
    assert prev_rev["prev_date"] == "2026-12-07"
    assert len(prev_rev["items"]) == 2
    assert prev_rev["items"][0]["status"] == "已执行"

    # 断言 5：底层资产与板块穿透敞口
    under = {x["key"]: x for x in snap["exposure_breakdown"]["by_underlying"]}
    assert under["QQQ"]["label"] == "纳指100"
    assert under["QQQ"]["net_exposure"] == 11000.0
    assert round(under["QQQ"]["pct_of_mv"], 4) == 1.1
    assert under["GOOGL"]["net_exposure"] == 3000.0
    assert under["TSLA"]["net_exposure"] == 3000.0
    assert under["SOX"]["net_exposure"] == 3000.0
    sectors = {x["key"]: x for x in snap["exposure_breakdown"]["by_sector"]}
    assert sectors["纳指指数"]["net_exposure"] == 11000.0
    assert sectors["科技/AI"]["net_exposure"] == 6000.0
    assert sectors["半导体"]["net_exposure"] == 3000.0

    # Prompt 完整注入了 5 项新维度
    assert captured_prompts
    p_txt = captured_prompts[0]
    assert "近期趋势与回撤" in p_txt
    assert "US10Y" in p_txt and "VIX" in p_txt and "SMH" in p_txt
    assert "未来 7 天关键事件预警" in p_txt and "GOOGL 财报" in p_txt
    assert "上一交易日 (2026-12-07) 关注清单" in p_txt
    assert "底层资产与板块穿透敞口" in p_txt and "纳指100" in p_txt


def test_desktop_after_hours_new_dimensions_and_legacy_compat(demo_path, tmp_path, qtbot):
    """验证桌面端完整渲染 5 项新维度，且切换旧版无新字段存档时全部优雅隐藏、零报错与零横向溢出。"""
    from PySide6.QtCore import Qt

    store = AfterHoursReportStore(tmp_path / "reports.json")
    # 新版全字段存档
    store.save_report({
        "id": "2026-12-08::个人",
        "trade_date": "2026-12-08",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-12-08T21:10:00+08:00",
        "trigger": "auto",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {
            "summary": {
                "day_pnl": -450.0,
                "day_pnl_pct": -0.043,
                "total_market_value": 10000.0,
                "cash_balance": 2000.0,
                "total_assets": 12000.0,
                "leverage_pct": 0.60,
                "effective_leverage": 2.0,
                "trade_count": 0,
                "holding_count": 2,
                "trend": {
                    "pnl_5d": 180.0,
                    "pct_5d": 0.018,
                    "pnl_wtd": -1000.0,
                    "pct_wtd": -0.09,
                    "pnl_mtd": 200.0,
                    "pct_mtd": 0.02,
                    "streak_days": -2,
                    "drawdown_30d_pct": -0.091,
                },
            },
            "market_benchmarks": {
                "SPY": {"regular_change_pct": -0.005, "after_hours_change_pct": 0.001},
                "QQQ": {"regular_change_pct": -0.01, "after_hours_change_pct": 0.002},
                "SMH": {"regular_change_pct": -0.012},
                "^VIX": {"regular_close": 18.4, "regular_change_pct": 0.062},
                "^TNX": {"regular_close": 4.25},
            },
            "upcoming_events": {
                "earnings": [{"date": "2026-12-11", "days_away": 3, "symbol": "GOOGL", "title": "GOOGL 财报 (盘后)"}],
                "macro": [{"date": "2026-12-09", "days_away": 1, "title": "FOMC 利率决议与鲍威尔发布会"}],
            },
            "exposure_breakdown": {
                "by_underlying": [
                    {"key": "QQQ", "label": "纳指100", "net_exposure": 14500.0, "pct_of_mv": 1.45},
                    {"key": "SOX", "label": "费城半导体", "net_exposure": 1600.0, "pct_of_mv": 0.16},
                    {"key": "GOOGL", "label": "GOOGL", "net_exposure": 1200.0, "pct_of_mv": 0.12},
                ],
                "by_sector": [
                    {"key": "纳指指数", "label": "纳指指数", "net_exposure": 14500.0, "pct_of_mv": 1.45},
                ],
            },
            "holdings": [],
            "trades": [],
        },
        "structured": {
            "tldr": "连跌两日注意控制杠杆",
            "risks": [{"level": "高危", "title": "FOMC 风险", "detail": "关注决议"}],
            "checklist": [{"id": "c1", "ticker": "TQQQ", "level": "$58", "text": "关注支撑"}],
            "prev_checklist_review": {
                "prev_date": "2026-12-07",
                "summary": "昨日 TQQQ 止损线未触发，纪律执行良好。",
                "items": [{"ticker": "TQQQ", "status": "未触发", "note": "全天维持在 $60 上方"}],
            },
        },
        "markdown": "## 一、今日总览\n\n新版",
    })
    # 旧版缺失 5 项新字段存档
    store.save_report({
        "id": "2026-11-20::个人",
        "trade_date": "2026-11-20",
        "person_key": "个人",
        "person_id": 1,
        "person_name": "个人",
        "generated_at": "2026-11-20T21:10:00+08:00",
        "trigger": "manual",
        "provider": "gemini",
        "model": "gemini-4-neon-high",
        "sync_status": {"ran": True, "ok": True, "message": "ok"},
        "snapshot": {
            "summary": {"day_pnl": 100.0, "total_market_value": 8000.0, "trade_count": 0, "holding_count": 1},
            "market_benchmarks": {"SPY": {"regular_change_pct": 0.005}},
            "holdings": [],
            "trades": [],
        },
        "markdown": "## 一、今日总览\n\n老版旧存档",
    })

    page = AfterHoursReportPage(store=store, db_path=demo_path)
    qtbot.addWidget(page)
    page.resize(1280, 800)
    page.show()
    qtbot.wait(50)

    # 新版全部 5 项维度可见且内容准确
    assert not page.kpi_trend_sub.isHidden()
    assert "近5日" in page.kpi_trend_sub.text() and "连跌2日" in page.kpi_trend_sub.text()
    assert not page.kpi_macro_sub.isHidden()
    assert "SMH" in page.kpi_macro_sub.text() and "VIX" in page.kpi_macro_sub.text() and "US10Y" in page.kpi_macro_sub.text()
    assert not page.events_frame.isHidden()
    assert "GOOGL 财报" in page.events_lbl.text() and "FOMC" in page.events_lbl.text()
    assert not page.exposure_frame.isHidden()
    assert "纳指100 145%" in page.exposure_lbl.text()
    assert not page.prev_review_frame.isHidden()
    assert "2026-12-07" in page.prev_review_toggle_btn.text()
    assert not page.prev_review_lbl.isHidden()
    page.prev_review_toggle_btn.click()
    assert page.prev_review_lbl.isHidden()
    assert page.right_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert page.right_container.width() <= page.right_scroll.viewport().width() + 2

    # 切到旧版存档（row 1）：全部 5 项新区域优雅隐藏且零报错
    page.report_list.setCurrentRow(1)
    qtbot.wait(30)
    assert page.kpi_trend_sub.isHidden()
    assert page.kpi_macro_sub.isHidden()
    assert page.events_frame.isHidden()
    assert page.exposure_frame.isHidden()
    assert page.prev_review_frame.isHidden()


def test_should_auto_generate_report_overwrites_intraday_draft():
    """验证盘前/盘中提前手动生成的临时日报，在盘后结束窗口到来后仍会被自动调度覆盖重算。"""
    from portfolio.services.after_hours_report import should_auto_generate_report

    # 无存档 -> 应生成
    assert should_auto_generate_report(None, date(2026, 11, 27)) is True

    # 2026-11-27 黑五半日市（盘后触发时间 17:10 ET = 北京时间次日 06:10）
    # 昨晚北京时间 22:20（美东 09:20 开盘前）手动生成 -> 应允许盘后覆盖
    assert should_auto_generate_report({"generated_at": "2026-11-27T22:20:17+08:00"}, date(2026, 11, 27)) is True
    # 次日北京时间 06:15（美东 17:15，已过 17:10 窗口）生成 -> 不重复触发
    assert should_auto_generate_report({"generated_at": "2026-11-28T06:15:00+08:00"}, date(2026, 11, 27)) is False

    # 2026-11-25 常规交易日（触发时间 20:10 ET = 北京时间次日 09:10）
    # 盘中北京时间 23:00（美东 10:00）生成 -> 应允许盘后覆盖
    assert should_auto_generate_report({"generated_at": "2026-11-25T23:00:00+08:00"}, date(2026, 11, 25)) is True
    # 次日北京时间 09:12（美东 20:12）生成 -> 不重复触发
    assert should_auto_generate_report({"generated_at": "2026-11-26T09:12:00+08:00"}, date(2026, 11, 25)) is False
