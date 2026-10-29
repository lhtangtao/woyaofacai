"""Standardized REST API v1 for Money Portfolio Web & Desktop Clients.

Provides unified, robust endpoints for:
1. System Bootstrap & Profile: /api/v1/bootstrap
2. Portfolio Overview & Intraday: /api/v1/portfolio/overview, /api/v1/holdings/today
3. Desktop Analytical Reports: /api/v1/reports, /api/v1/reports/<name>
4. Standardized Trades History & Provenance: /api/v1/trades, /api/v1/trades/<id>/detail
5. People & Multi-person Attribution: /api/v1/people
6. Broker Channels & Balances: /api/v1/brokers
7. Stock Ownership Penetration & Reconciliation: /api/v1/ownership
8. Holdings Earnings Calendar: /api/v1/earnings-calendar
9. Full Visual Charts Gallery: /api/v1/charts, /api/v1/charts/top_days, top_months, ranking
10. Market Monitoring & Alert Rules: /api/v1/monitoring/... (watchlist, rules CRUD, alerts, kline)
11. AI Assistant & Stock Analysis: /api/v1/ai/... (chat, stock chat, history)
12. System, Database Diagnostics & Reconciliation: /api/v1/system/... (db check, progress, metrics, reconcile)
"""

from collections import defaultdict
import csv
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
import json
import logging
import math
from pathlib import Path
import sqlite3
import tempfile
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, Response, g, jsonify, request

from portfolio.domain.models import QueryScope, TradeFilters, exact_text
from portfolio.domain.technical_indicators import DailyBar, series, technicals
from portfolio.paths import DATABASE_PATH, PROJECT_DIR
from portfolio.services.ai_service import CodexAIService as AIService
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.charts_service import build_charts, rank_chart
from portfolio.services.earnings_service import EarningsService
from portfolio.services.monitor_store import MonitorRule, MonitorStore
from portfolio.services.notification_service import notification_service
from portfolio.services.trades_service import TradesService
from portfolio.web.security.audit import record_audit_event
from portfolio.web.security.auth_context import (
    CAP_AI_CHAT,
    CAP_AI_STOCK,
    CAP_MANAGE_BARK,
    CAP_MANAGE_RULES,
    CAP_MANAGE_USERS,
    CAP_VIEW_ASSETS,
    CAP_VIEW_MARKET,
    AuthContext,
    require_capability,
)

logger = logging.getLogger("portfolio.web.api_v1")

api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")


def serialize_val(val: Any) -> Any:
    """Safely convert Decimal, date, and objects to JSON-serializable types."""
    if val is None:
        return None
    if isinstance(val, Decimal):
        try:
            return float(val)
        except Exception:
            return str(val)
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def get_analytics_service() -> AnalyticsService:
    from portfolio.web.app import get_analytics_service as _get_svc
    return _get_svc()


def get_monitor_store() -> Optional[MonitorStore]:
    from portfolio.web.app import get_monitor_store as _get_store
    return _get_store()


def get_trades_service() -> TradesService:
    return TradesService(DATABASE_PATH)


def get_earnings_service() -> EarningsService:
    from portfolio.services.quote_store import QuoteStore
    from PySide6.QtCore import QStandardPaths
    store_path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "live_quotes.sqlite3"
    quote_store = QuoteStore(store_path) if store_path.exists() else None
    return EarningsService(store=quote_store)


def get_user_query_scope(as_of: Optional[date] = None, currency: str = "USD", person_id: Optional[int] = None) -> QueryScope:
    from portfolio.domain.models import Ledger
    ctx: AuthContext = getattr(g, "auth_context", None)
    if person_id is None:
        person_id=request.args.get("person_id",type=int)
    target_person_ids = None

    if ctx and not ctx.is_lan_viewer() and not ctx.is_admin():
        # Empty/unbound person permissions must never become the engine's
        # falsy "all people" scope.
        target_person_ids = tuple(ctx.allowed_person_ids or (-99999,))

    if person_id is not None:
        if target_person_ids is not None:
            if person_id in target_person_ids:
                target_person_ids = (person_id,)
            else:
                target_person_ids = (-99999,)
        else:
            target_person_ids = (person_id,)

    return QueryScope(
        as_of=as_of or date.today(),
        ledger=Ledger.PERSON,
        person_ids=target_person_ids,
        start_date=min(date(2025, 1, 1), as_of or date.today()),
        reporting_currency=currency,
    )


def get_current_user_scope() -> Tuple[Optional[int], Optional[str]]:
    ctx: AuthContext = getattr(g, "auth_context", None)
    if not ctx or ctx.is_lan_viewer() or ctx.is_admin():
        return None, None
    p_id = ctx.allowed_person_ids[0] if ctx.allowed_person_ids else None
    return p_id, ctx.person_name


# ------------------------------------------------------------------------------
# 1. System Bootstrap & User Profile
# ------------------------------------------------------------------------------


@api_v1.route("/bootstrap", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_bootstrap():
    """Return application bootstrap state, user profile, capabilities, database info and system preferences."""
    ctx: AuthContext = getattr(g, "auth_context", None)
    user_info = ctx.to_dict() if ctx else {
        "authenticated": False,
        "username": None,
        "role": "unauthenticated",
        "principal_type": "UNAUTHENTICATED",
        "capabilities": [],
        "is_lan": False,
        "access_mode": "REMOTE_PUBLIC",
        "person_name": None,
        "person_id": None,
    }

    db_exists = DATABASE_PATH.is_file()
    db_size = DATABASE_PATH.stat().st_size if db_exists else 0

    record_count = 0
    persons = []
    if db_exists:
        try:
            with sqlite3.connect(DATABASE_PATH) as conn:
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM broker_trades")
                record_count = cur.fetchone()[0]
                cur.execute("SELECT id, name FROM persons WHERE enabled=1 ORDER BY id")
                persons = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
                if ctx and not ctx.is_lan_viewer() and not ctx.is_admin() and ctx.allowed_person_ids is not None:
                    persons = [p for p in persons if p["id"] in ctx.allowed_person_ids]
        except Exception:
            pass

    # Avoid a foreground financial replay during bootstrap so login/page-load responds in milliseconds.
    _DEFAULT_REPORTS = (
        ("当前持仓", "当前持仓"),
        ("股票汇总", "股票汇总"),
        ("清仓收益", "清仓收益"),
        ("每日持仓收益", "每日持仓收益"),
        ("每月持仓收益", "每月持仓收益"),
        ("月度净投入明细", "月度净投入明细"),
        ("完整交易净投入", "完整交易净投入"),
        ("每月微操收益", "每月微操收益"),
        ("微操配对证据", "微操配对证据"),
        ("交易回放", "此页为有效账本完整回放；审计原始页另可查看撤销/参考行。"),
        ("人员划转", "权益在人员之间变更，不能计入券商真实成交。"),
        ("渠道汇总", "渠道汇总"),
        ("分类汇总", "分类汇总"),
        ("收益日历", "收益日历"),
        ("季度矩阵", "季度矩阵"),
        ("数据异常", "数据异常诊断"),
        ("资金事件", "资金收付是净变动，未经可信期初余额不能称为现金余额。"),
        ("资产调整", "只读原始记录；不把现金/融资调整混入股票市值。"),
        ("导入批次", "只读原始记录；不把现金/融资调整混入股票市值。"),
        ("汇率", "优先源库当日值；ECB参考汇率保存在独立缓存库。"),
        ("行情状态", "行情状态"),
        ("原始记录", "按来源行只读索引；点击查看遮盖后的 JSON，不自动导出原始敏感 payload。"),
        ("人员资产", "人员资产"),
        ("账户资产", "账户资产"),
        ("指标说明", "指标说明"),
        ("取值逻辑", "原 Excel 说明只作规则证据；与新引擎差异见决策文档。"),
        ("总览取值逻辑", "原 Excel 说明只作规则证据；与新引擎差异见决策文档。"),
        ("月度趋势", "月度趋势"),
    )
    reports_meta = [
        {
            "name": name,
            "group": "核心报表" if name in ("当前持仓", "今日盈亏", "股票汇总", "人员资产", "渠道汇总") else "明细与审计",
            "description": desc,
        }
        for name, desc in _DEFAULT_REPORTS
    ]

    # Catalog reads stay read-only and use the actual Money3 schema.
    accounts=[]
    if db_exists:
        from portfolio.db.connection import readonly_connection
        with readonly_connection(DATABASE_PATH) as conn:
            accounts=[r[0] for r in conn.execute('SELECT DISTINCT broker_code FROM broker_accounts ORDER BY broker_code')]
    from portfolio.services.derived_store import DerivedStore
    cache_initialized=DerivedStore(DATABASE_PATH).available

    return jsonify({
        "status": "ok",
        "user": user_info,
        "access_mode": user_info.get("access_mode", "LAN"),
        "persons": persons,
        "people": persons,
        "accounts": accounts,
        "reports": reports_meta,
        "design_tokens": {
            "row_height": 40,
            "header_height": 42,
            "card_radius": 12,
            "control_radius": 8,
        },
        "database": {
            "name": DATABASE_PATH.name,
            "path": str(DATABASE_PATH),
            "exists": db_exists,
            "size_bytes": db_size,
            "record_count": record_count,
            "derived_initialized": cache_initialized,
            "as_of": date.today().isoformat(),
        },
        "system": {
            "version": "3.1.0",
            "platform": "macOS",
            "server_time": datetime.now().isoformat(),
            "default_currency": "USD",
            "metric_mode": "EXCEL_COMPAT",
        },
        "preferences": {
            "theme": "light",
            "green_profit": True,
            "bark_configured": bool(notification_service.bark_url),
        },
    })


# ------------------------------------------------------------------------------
# 2. Portfolio Overview & Intraday
# ------------------------------------------------------------------------------


@api_v1.route("/portfolio/overview", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_portfolio_overview():
    """Retrieve full portfolio KPI cards, holdings allocation, and trend snapshot."""
    try:
        svc = get_analytics_service()
        person_id = request.args.get("person_id", type=int)
        currency = request.args.get("currency", "USD")
        scope = get_user_query_scope(currency=currency, person_id=person_id)

        res = svc.calculate(scope)
        metric_map = {m.name: m.value for m in res.metrics}

        total_asset_val = metric_map.get("当前总资产")
        total_profit_val = metric_map.get("全时期总盈亏")
        total_profit_rate_val = metric_map.get("全时期收益率")
        today_profit_val = metric_map.get("今日盈亏")
        today_profit_rate_val = metric_map.get("当日收益率")

        # Category distribution pie & stock value
        category_rows = []
        stock_value = Decimal("0")
        if "当前持仓" in res.reports:
            cat_map = {}
            for r in res.reports["当前持仓"].rows:
                cat = r.values[1] or "未分类"
                mv = r.values[5] or Decimal("0")  # 市值
                stock_value += mv
                cat_map[cat] = cat_map.get(cat, Decimal("0")) + mv
            for cat, val in cat_map.items():
                category_rows.append({"name": cat, "value": serialize_val(val)})

        # Broker channel distribution
        channel_rows = []
        total_cash = Decimal("0")
        if "渠道汇总" in res.reports:
            for r in res.reports["渠道汇总"].rows:
                c_cash = r.values[12]
                if c_cash:
                    total_cash += Decimal(str(c_cash))
                c_asset = r.values[1] or Decimal("0")
                channel_rows.append({
                    "channel": r.values[0],
                    "total_asset": serialize_val(c_asset),
                    "cash": serialize_val(c_cash),
                    "stock_value": serialize_val(c_asset - (c_cash or Decimal("0"))),
                })

        cash_balance_val = total_cash if total_cash > 0 else (total_asset_val - stock_value if total_asset_val else Decimal("0"))

        kpis = {
            "total_asset": serialize_val(total_asset_val),
            "total_profit": serialize_val(total_profit_val),
            "total_profit_rate": f"{float(total_profit_rate_val) * 100:.2f}%" if total_profit_rate_val is not None else "—",
            "today_profit": serialize_val(today_profit_val),
            "today_profit_rate": f"{float(today_profit_rate_val) * 100:.2f}%" if today_profit_rate_val is not None else "—",
            "cash_balance": serialize_val(cash_balance_val),
            "stock_value": serialize_val(stock_value),
            "net_invested": serialize_val(metric_map.get("回本缺口")),
            "holdings_count": len(res.reports["当前持仓"].rows) if "当前持仓" in res.reports else 0,
            "currency": currency,
            "allocations": category_rows,
        }

        return jsonify({
            "status": "ok",
            "snapshot_id": datetime.now().isoformat(),
            "as_of": scope.as_of.isoformat(),
            "currency": currency,
            "user": g.auth_context.to_dict() if hasattr(g, "auth_context") and g.auth_context else None,
            "metrics": {m.name: serialize_val(m.value) for m in res.metrics},
            "overview": kpis,
            "kpis": kpis,
            "category_distribution": category_rows,
            "channel_distribution": channel_rows,
            "data_asof": getattr(res, "latest", date.today().isoformat()),
        })
    except Exception as e:
        logger.exception("Overview calculation error")
        return jsonify({"status": "error", "message": str(e)}), 500


_today_service = None
_today_service_lock = RLock()


class TodayQueryError(ValueError):
    pass


def get_today_service():
    global _today_service
    from portfolio.services.web_today import WebTodayService
    with _today_service_lock:
        if _today_service is None:
            _today_service = WebTodayService(DATABASE_PATH)
        return _today_service


def today_payload(*, include_curve=True):
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        selected = date.fromisoformat(request.args.get("as_of") or today.isoformat())
    except ValueError as exc:
        raise TodayQueryError("日期格式无效。") from exc
    if selected > today:
        raise TodayQueryError("截止日期不能晚于今天。")
    scope = get_user_query_scope(as_of=selected, person_id=request.args.get("person_id", type=int))
    # Whole-security search preserves all cost history, exactly as native global search.
    scope = replace(scope, search=(request.args.get("search") or "").strip())
    return get_today_service().snapshot(
        scope, ticker=(request.args.get("ticker") or "").strip(),
        channel=request.args.get("channel") or None,
        enabled=request.args.get("live", "1") != "0",
        sort_col=request.args.get("sort_col"),
        descending=request.args.get("sort_dir", "desc") == "desc",
        include_curve=include_curve,
    )


@api_v1.route("/holdings/today", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_holdings_today():
    """One immutable native-equivalent snapshot: cards, curves, channels and rows."""
    try:
        return jsonify(today_payload())
    except TodayQueryError:
        return jsonify(status="error", message="日期或筛选参数无效，请检查后重试。"), 400
    except Exception:
        logger.error("Today's P&L snapshot failed", exc_info=False)
        return jsonify(status="error", message="今日盈亏读取失败，请检查账本及行情缓存后重试；已有数据保留。"), 503


@api_v1.route('/charts/today',methods=['GET'])
@require_capability(CAP_VIEW_ASSETS)
def api_saved_today_curve():
    """Read saved five-minute points independently of full holdings reports."""
    try:
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from portfolio.domain.live_quotes import UTC,market_trade_day,us_session
        now=datetime.now(UTC);today=now.astimezone(ZoneInfo('Asia/Shanghai')).date()
        selected=date.fromisoformat(request.args.get('as_of') or str(today))
        if selected>today:raise ValueError('截止日期不能晚于今天。')
        scope=get_user_query_scope(as_of=selected,person_id=request.args.get('person_id',type=int))
        scope=replace(scope,search=(request.args.get('search') or '').strip())
        service=get_today_service()
        _,days,halves,_=service.store.load(now)
        if selected==today:scope=replace(scope,as_of=market_trade_day(now,days,halves))
        context=SimpleNamespace(scope=scope,trading_days=tuple(d.isoformat() for d in sorted(days)))
        curve=service.curve(context,None,us_session(now,days,halves) if selected==today else None,now,
                            ticker=(request.args.get('ticker') or '').strip(),channel=request.args.get('channel') or None)
        return jsonify(status='ok',curve=curve)
    except (ValueError,TypeError):
        return jsonify(status='error',message='日期或筛选条件无效，未读取其他人员数据。'),400
    except Exception:
        logger.exception('Saved day curve read failed')
        return jsonify(status='error',message='五分钟派生明细暂时读取失败，请检查后台任务状态。'),503


# ------------------------------------------------------------------------------
# 2.5. Unified Holdings Management (Dashboard, Unified Detail, Stock Drawer)
# ------------------------------------------------------------------------------


@api_v1.route("/holdings/dashboard", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_holdings_dashboard():
    """Retrieve holdings management dashboard: 4 cards, person/broker/stock summaries, and donut distribution."""
    try:
        from portfolio.services.holdings_management_service import HoldingsManagementService
        svc = get_analytics_service()
        person_id = request.args.get("person_id", type=int)
        scope = get_user_query_scope(person_id=person_id)
        analytics = svc.calculate(scope)
        data = HoldingsManagementService.get_dashboard_data(analytics, scope)
        return jsonify(status="ok", **data)
    except Exception as e:
        logger.exception("Holdings dashboard error")
        return jsonify(status="error", message=str(e)), 500


@api_v1.route("/holdings/detail", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_holdings_detail():
    """Retrieve unified holdings detail rows supporting person, broker, and stock dimensions."""
    try:
        from portfolio.services.holdings_management_service import HoldingsManagementService
        svc = get_analytics_service()
        dimension = request.args.get("dimension", "person")
        person_id = request.args.get("person_id", type=int)
        broker = request.args.get("broker") or None
        ticker = request.args.get("ticker") or None
        status = request.args.get("status") or None
        search = request.args.get("search") or None
        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", 50, type=int)

        scope = get_user_query_scope(person_id=person_id)
        analytics = svc.calculate(scope)
        rows = HoldingsManagementService.get_holdings_detail_rows(
            analytics, dimension=dimension, person_id=person_id, broker=broker, ticker=ticker, status=status, search=search
        )
        total = len(rows)
        start = (page - 1) * page_size
        end = start + page_size
        paged_rows = rows[start:end]
        return jsonify(status="ok", rows=paged_rows, total=total, page=page, page_size=page_size)
    except Exception as e:
        logger.exception("Holdings detail error")
        return jsonify(status="error", message=str(e)), 500


@api_v1.route("/holdings/stock_drawer", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_holdings_stock_drawer():
    """Retrieve 4-tab stock right drawer details for a ticker."""
    try:
        ticker = (request.args.get("ticker") or "").strip()
        if not ticker:
            return jsonify(status="error", message="缺少股票代码 (ticker)"), 400
        from portfolio.services.holdings_management_service import HoldingsManagementService
        svc = get_analytics_service()
        person_id = request.args.get("person_id", type=int)
        broker = request.args.get("broker") or None
        scope = get_user_query_scope(person_id=person_id)
        analytics = svc.calculate(scope)
        data = HoldingsManagementService.get_stock_drawer_data(
            str(DATABASE_PATH), analytics, ticker, person_id=person_id, broker=broker
        )
        return jsonify(status="ok", **data)
    except Exception as e:
        logger.exception("Stock drawer error")
        return jsonify(status="error", message=str(e)), 500


# ------------------------------------------------------------------------------
# 3. Desktop Analytical Reports (28 Reports Engine)
# ------------------------------------------------------------------------------


@api_v1.route("/reports", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_reports_list():
    """List all available 28 desktop analytical reports with groupings and descriptions."""
    report_groups = [
        {"name": "当前持仓", "group": "核心持仓", "description": "当前名下全部有效持仓证券、成本均价及未实现浮动盈亏"},
        {"name": "今日盈亏", "group": "核心持仓", "description": "当日盘中最新市值波动、涨跌幅及各持仓贡献明细"},
        {"name": "股票汇总", "group": "核心持仓", "description": "全历史各股票累计盈亏、清仓收益及胜率统揽"},
        {"name": "人员资产", "group": "主体与归属", "description": "按出资人员主体核算独立资产、持仓成本与盈亏"},
        {"name": "渠道汇总", "group": "主体与归属", "description": "按各大券商托管渠道汇总资产分布与可用现金"},
        {"name": "股票归属", "group": "主体与归属", "description": "穿透核算各券商账户下股票在出资人之间的具体股数分配"},
        {"name": "每日持仓收益", "group": "收益分析", "description": "全时期逐日持仓净值波动、日收益及操作笔数完整明细"},
        {"name": "每月持仓收益", "group": "收益分析", "description": "按月度汇总持仓收益、微操贡献及月末净值"},
        {"name": "每月微操收益", "group": "收益分析", "description": "日内或短期高抛低吸做T微操闭环盈利确认与配对记录"},
        {"name": "微操配对证据", "group": "收益分析", "description": "高精度配对算法留存的微操买卖对应证据链"},
        {"name": "清仓收益", "group": "收益分析", "description": "已完全平仓标的的累计已实现净收益汇总"},
        {"name": "月度净投入明细", "group": "收益分析", "description": "每月出入金、转账及本金净流入流出记录"},
        {"name": "完整交易净投入", "group": "收益分析", "description": "全生命周期累计现金投入与本金回放"},
        {"name": "交易回放", "group": "交易与流水", "description": "包含拆股、人员划转及结算的账本完整时间线回放"},
        {"name": "人员划转", "group": "交易与流水", "description": "出资人之间的证券份额内部划转与权益交接审计"},
        {"name": "资金事件", "group": "交易与流水", "description": "现金股息红利、利息税费及资金流水审计"},
        {"name": "资产调整", "group": "交易与流水", "description": "人工调账、开账对齐及特殊财务调整事件"},
        {"name": "导入批次", "group": "系统与审计", "description": "外部流水与 API 导入批次日志与状态追踪"},
        {"name": "原始记录", "group": "系统与审计", "description": "原始 Excel/API 未经计算的原始数据行证据"},
        {"name": "行情状态", "group": "系统与审计", "description": "外部行情数据同步日期、收盘价与估值基准日覆盖率"},
        {"name": "汇率", "group": "系统与审计", "description": "多币种官方结算汇率历史与补齐记录"},
        {"name": "数据异常", "group": "系统与审计", "description": "财务审计引擎自动检出的不平、负持仓与规则预警清单"},
        {"name": "Excel差异核对", "group": "系统与审计", "description": "与历史手工账 Excel 对比的对账一致性差异报告"},
        {"name": "指标说明", "group": "系统与审计", "description": "全部 18 项财务指标的标准计算公式与定义字典"},
    ]
    return jsonify({"status": "ok", "reports": report_groups, "count": len(report_groups)})


def find_primary_time_column(columns: list[str]) -> str | None:
    for priority_col in (
        "时间", "交易时间", "日期", "月份", "触发时间", "披露日期", "折算日期", "rate_date",
        "最终清仓时间", "首次买入时间", "有效自", "effective_from", "business_date", "imported_at_utc"
    ):
        if priority_col in columns:
            return priority_col
    for col in columns:
        if any(k in col for k in ("时间", "日期", "月份", "date", "time", "UTC", "utc", "effective_")):
            return col
    return None


def parse_sort_key(val, is_desc: bool, row=None, orig_idx: int = 0):
    is_missing = val is None or val == "" or val == "—" or val == "-" or str(val).strip().lower() in ("none", "null")
    missing_group = (0 if is_desc else 1) if is_missing else (1 if is_desc else 0)
    if is_missing:
        return (missing_group, 0, 0.0, "", orig_idx)

    sec_id = 0
    if row is not None and hasattr(row, "metadata") and isinstance(row.metadata, dict):
        sec_id = row.metadata.get("id") or row.metadata.get("record_id") or 0
        if not sec_id and "source_record_ids" in row.metadata:
            s_ids = row.metadata["source_record_ids"]
            if s_ids:
                sec_id = s_ids[0]

    if isinstance(val, (datetime, date)):
        return (missing_group, 2, 0.0, val.isoformat(), sec_id or orig_idx)

    if isinstance(val, (int, float, Decimal)):
        return (missing_group, 1, float(val), "", sec_id or orig_idx)

    if isinstance(val, str):
        v = val.strip()
        clean = v.replace("$", "").replace(",", "").replace("%", "").strip()
        if clean.startswith("(") and clean.endswith(")"):
            clean = "-" + clean[1:-1].strip()
        try:
            num = float(clean)
            return (missing_group, 1, num, "", sec_id or orig_idx)
        except ValueError:
            pass
        return (missing_group, 2, 0.0, v.lower(), sec_id or orig_idx)

    return (missing_group, 2, 0.0, str(val).lower(), sec_id or orig_idx)


@api_v1.route("/reports/<report_name>", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_reports_detail(report_name: str):
    """Deliver full column metadata, rows, totals and pagination for any requested report."""
    if report_name in ("今日盈亏", "当日盈亏"):
        return api_holdings_today()
    try:
        svc = get_analytics_service()
        person_id = request.args.get("person_id", type=int)
        scope = get_user_query_scope(person_id=person_id)
        res = svc.calculate(scope)

        report = None
        if report_name == "清仓收益":
            report = get_today_service().closed_report(res)
        elif report_name == "股票归属":
            report = res.reports.get("人员划转") or res.reports.get("股票汇总")
        elif report_name == "券商资产":
            report = res.reports.get("渠道汇总") or res.reports.get("账户资产")
        else:
            report = res.reports.get(report_name)

        if not report:
            return jsonify({"status": "error", "message": f"未找到报表: {report_name}"}), 404

        columns = list(report.columns)
        all_rows = report.rows or ()

        # Search filter
        search = (request.args.get("search") or "").strip().lower()
        filtered_rows = []
        for r in all_rows:
            if search:
                row_str = " ".join(str(v or "") for v in r.values).lower()
                if search not in row_str:
                    continue
            filtered_rows.append(r)

        # Sort
        sort_col = request.args.get("sort_col")
        sort_dir = request.args.get("sort_dir")
        if not sort_col:
            time_col = find_primary_time_column(columns)
            if time_col:
                sort_col = time_col
                sort_dir = sort_dir or "desc"
        if not sort_dir:
            sort_dir = "desc" if (sort_col and any(k in sort_col for k in ("时间", "日期", "月份", "effective_from", "business_date", "imported_at_utc"))) else "asc"
        sort_dir = sort_dir.lower()

        if sort_col and sort_col in columns:
            col_idx = columns.index(sort_col)
            is_desc = (sort_dir == "desc")
            try:
                filtered_rows.sort(
                    key=lambda r, idx=col_idx, desc=is_desc: parse_sort_key(
                        r.values[idx] if idx < len(r.values) else None, desc, r, 0
                    ),
                    reverse=is_desc,
                )
            except Exception as sort_err:
                logger.warning(f"Sort failed for {sort_col}: {sort_err}")

        total_rows = len(filtered_rows)
        page = max(1, request.args.get("page", 1, type=int))
        page_size = max(1, min(1000, request.args.get("page_size", 25, type=int)))
        total_pages = max(1, math.ceil(total_rows / page_size))
        start_idx = (page - 1) * page_size
        paged_rows = filtered_rows[start_idx : start_idx + page_size]

        # Format items as objects and rows as 2D cell arrays
        paged_items = []
        paged_arrays = []
        for r in paged_rows:
            item_dict = {}
            row_arr = []
            for idx, col in enumerate(columns):
                val = r.values[idx] if idx < len(r.values) else None
                s_val = serialize_val(val)
                item_dict[col] = s_val
                row_arr.append(s_val)
            item_dict["_meta"] = {k: serialize_val(v) for k, v in r.metadata.items()} if hasattr(r, "metadata") else {}
            paged_items.append(item_dict)
            paged_arrays.append(row_arr)

        # Calculate totals & summary_row
        totals = {}
        summary_row = None
        if hasattr(report, "totals") and getattr(report, "totals"):
            for idx, col in enumerate(columns):
                t_val = report.totals.values[idx] if idx < len(report.totals.values) else None
                if t_val is not None:
                    totals[col] = serialize_val(t_val)
            summary_row = [totals.get(col) for col in columns]

        return jsonify({
            "status": "ok",
            "name": report_name,
            "columns": columns,
            "rows": paged_arrays,
            "items": paged_items,
            "total_rows": total_rows,
            "total": total_rows,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "summary_row": summary_row,
            "totals": totals,
            "sort_col": sort_col,
            "sort_dir": sort_dir,
            "note": getattr(report, "note", ""),
            "snapshot_id": datetime.now().isoformat(),
        })
    except Exception as e:
        logger.exception(f"Report {report_name} query error")
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 4. Standardized Trades History API (Fixes AC-14, DTO Contract)
# ------------------------------------------------------------------------------


@api_v1.route("/trades", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_trades_list():
    """Standardized Paginated Trades DTO fulfilling AC-14."""
    try:
        svc = get_trades_service()
        person_id, _ = get_current_user_scope()
        req_person_id = request.args.get("person_id", type=int)
        if person_id is None and req_person_id is not None:
            person_id = req_person_id

        page = max(1, request.args.get("page", 1, type=int))
        page_size = max(1, min(200, request.args.get("size", request.args.get("page_size", 25, type=int), type=int)))
        symbol = (request.args.get("symbol") or "").strip().upper() or None
        channel = (request.args.get("broker") or request.args.get("channel") or "").strip() or None
        side = (request.args.get("side") or "").strip().upper() or None
        search = (request.args.get("search") or "").strip() or None

        scope = get_user_query_scope(person_id=person_id)
        if search:
            scope = replace(scope, search=search)

        # Resolve symbol -> instrument_ids
        if symbol:
            with sqlite3.connect(DATABASE_PATH) as conn:
                sym_rows = conn.execute("SELECT id FROM instruments WHERE symbol = ? OR symbol LIKE ?", (symbol, f"{symbol}.%")).fetchall()
                if sym_rows:
                    scope = replace(scope, instrument_ids=tuple(r[0] for r in sym_rows))
                else:
                    scope = replace(scope, instrument_ids=(-99999,))

        # Resolve channel/broker -> account_ids
        if channel:
            with sqlite3.connect(DATABASE_PATH) as conn:
                acc_rows = conn.execute("SELECT id FROM broker_accounts WHERE broker_code = ? OR display_name = ?", (channel, channel)).fetchall()
                if acc_rows:
                    scope = replace(scope, account_ids=tuple(r[0] for r in acc_rows))
                else:
                    scope = replace(scope, account_ids=(-99999,))

        # Map side
        trade_filters = None
        if side:
            side_map = {"买入": "BUY", "卖出": "SELL", "BUY": "BUY", "SELL": "SELL", "转入": "TRANSFER_IN", "转出": "TRANSFER_OUT"}
            db_side = side_map.get(side, side)
            trade_filters = TradeFilters(side=db_side)

        # Sort mapping
        trades_sort_map = {
            "时间": 0, "book_time": 0, "trade_date": 0,
            "券商": 1, "broker_code": 1, "broker": 1,
            "分类": 2, "category": 2,
            "标的": 3, "symbol": 3,
            "方向": 4, "side": 4,
            "数量": 5, "quantity_e8": 5, "quantity": 5,
            "价格": 6, "price_e8": 6, "price": 6,
            "金额": 7, "amount_e8": 7, "amount": 7,
            "操作类型": 12, "kind": 12,
            "对手方": 13, "counterparty": 13,
            "订单号": 14, "order_id": 14,
        }
        sort_col = request.args.get("sort_col") or "时间"
        sort_dir = (request.args.get("sort_dir") or "desc").lower()
        sort_column_idx = trades_sort_map.get(sort_col, 0)
        descending = (sort_dir != "asc")

        page_0 = max(0, page - 1)
        page_obj = svc.get_trades(
            scope,
            page=page_0,
            size=page_size,
            sort_column=sort_column_idx,
            descending=descending,
            filters=trade_filters,
        )

        normalized_items = []
        for r in page_obj.rows:
            meta = r.metadata if hasattr(r, "metadata") else {}
            vals = r.values if hasattr(r, "values") else ()
            trade_obj = {
                "id": getattr(r, "record_id", meta.get("id")),
                "record_id": getattr(r, "record_id", meta.get("id")),
                "trade_date": vals[0] if len(vals) > 0 else meta.get("book_time"),
                "broker": vals[1] if len(vals) > 1 else meta.get("broker_code"),
                "category": vals[2] if len(vals) > 2 else meta.get("category"),
                "symbol": vals[3] if len(vals) > 3 else meta.get("symbol"),
                "side": vals[4] if len(vals) > 4 else meta.get("side"),
                "current_price": serialize_val(meta.get("current_price")),
                "price": serialize_val(vals[6]) if len(vals) > 6 else None,
                "quantity": serialize_val(vals[5]) if len(vals) > 5 else None,
                "amount": serialize_val(vals[7]) if len(vals) > 7 else None,
                "tag": vals[8] if len(vals) > 8 else None,
                "post_quantity": serialize_val(vals[9]) if len(vals) > 9 else None,
                "post_net": serialize_val(vals[10]) if len(vals) > 10 else None,
                "post_cost": serialize_val(vals[11]) if len(vals) > 11 else None,
                "kind": vals[12] if len(vals) > 12 else meta.get("kind"),
                "order_id": vals[14] if len(vals) > 14 else meta.get("order_id"),
                "source": vals[15] if len(vals) > 15 else meta.get("source_path"),
                "person_name": meta.get("person_name") or "—",
            }
            normalized_items.append(trade_obj)

        total_rows = page_obj.total
        total_pages = max(1, math.ceil(total_rows / page_size))

        return jsonify({
            "status": "ok",
            "trades": normalized_items,
            "items": normalized_items,
            "columns": [
                "时间", "券商", "分类", "标的", "方向", "当前价", "价格", "数量", "金额",
                "标记", "持仓", "净投入", "成本", "操作类型", "对手方", "订单号", "来源"
            ],
            "total": total_rows,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "sort_col": sort_col,
            "sort_dir": "desc" if descending else "asc",
            "pagination": {
                "page": page,
                "page_size": page_size,
                "size": page_size,
                "total": total_rows,
                "total_pages": total_pages,
            },
            "stats": page_obj.stats if hasattr(page_obj, "stats") else {},
            "snapshot_id": datetime.now().isoformat(),
        })
    except Exception as e:
        logger.exception("Trades retrieval error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/trades/detail", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_trade_detail():
    """Retrieve full audit provenance for a specific trade record."""
    try:
        trade_id = request.args.get("trade_id", type=int)
        if not trade_id:
            return jsonify({"status": "error", "message": "Missing trade_id parameter"}), 400

        ctx: AuthContext = getattr(g, "auth_context", None)
        if ctx and not ctx.is_lan_viewer() and not ctx.is_admin() and ctx.allowed_person_ids is not None:
            with sqlite3.connect(DATABASE_PATH) as conn:
                placeholders = ",".join("?" * len(ctx.allowed_person_ids))
                has_access = conn.execute(
                    f"SELECT 1 FROM person_operations WHERE source_record_id = ? AND person_id IN ({placeholders}) LIMIT 1",
                    [trade_id, *ctx.allowed_person_ids],
                ).fetchone()
                if not has_access:
                    record_audit_event("TRADE_DETAIL_ACCESS_DENIED", ctx.username, ctx.access_mode, request.remote_addr, "FORBIDDEN", {"trade_id": trade_id})
                    return jsonify({"status": "error", "message": "权限不足：无权访问该交易来源记录"}), 403

        svc = get_trades_service()
        provenance = svc.get_trade_provenance(trade_id, sensitive=False)
        return jsonify({
            "status": "ok",
            "trade_id": trade_id,
            "provenance": provenance if isinstance(provenance, dict) else (provenance.to_dict() if hasattr(provenance, "to_dict") else {}),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 5. Multi-person & Broker Attribution Endpoints (Fixes AC-15, AC-17)
# ------------------------------------------------------------------------------


@api_v1.route("/people", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_people_assets():
    """Retrieve multi-person attribution, person assets breakdown and horizontal bar chart data."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate_people(scope)

        people_list = []
        for person_id, name, book in res.persons:
            metric_map = {m.name: m.value for m in getattr(book, "metrics", ())}
            total_asset = serialize_val(metric_map.get("当前总资产"))
            total_profit = serialize_val(metric_map.get("全时期总盈亏"))
            p_rate = metric_map.get("全时期收益率")
            profit_rate = f"{float(p_rate) * 100:.2f}%" if p_rate is not None else "—"

            # Stock and cash
            stock_v = Decimal("0")
            if "当前持仓" in book.reports:
                for r in book.reports["当前持仓"].rows:
                    stock_v += r.values[5] or Decimal("0")

            people_list.append({
                "person_id": person_id,
                "name": name,
                "total_asset": total_asset,
                "total_profit": total_profit,
                "profit_rate": profit_rate,
                "cash": serialize_val(Decimal(str(total_asset or "0")) - stock_v),
                "stock_value": serialize_val(stock_v),
            })

        ctx: AuthContext = getattr(g, "auth_context", None)
        if ctx and not ctx.is_lan_viewer() and not ctx.is_admin() and ctx.allowed_person_ids is not None:
            people_list = [p for p in people_list if p["person_id"] in ctx.allowed_person_ids]

        return jsonify({"status": "ok", "people": people_list, "count": len(people_list)})
    except Exception as e:
        logger.exception("People assets error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/brokers", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_brokers_channels():
    """Retrieve broker channels overview, channel cash balances and account distribution."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        rep = res.reports.get("渠道汇总")
        channels = []
        if rep:
            for r in rep.rows:
                c_asset = r.values[1] or Decimal("0")
                c_cash = r.values[12]
                channels.append({
                    "channel": r.values[0],
                    "total_asset": serialize_val(c_asset),
                    "cash": serialize_val(c_cash),
                    "stock_value": serialize_val(c_asset - (c_cash or Decimal("0"))),
                })

        return jsonify({"status": "ok", "channels": channels, "count": len(channels)})
    except Exception as e:
        logger.exception("Brokers channel error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/brokers/people", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_broker_people():
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    try:
        selected = date.fromisoformat(request.args.get('as_of') or today.isoformat())
        if selected > today:
            raise ValueError('future date')
    except ValueError:
        return jsonify(status='error', message='请选择有效的截止日期，不能晚于今天。'), 400
    try:
        return jsonify(get_today_service().people_snapshot(
            get_user_query_scope(as_of=selected), enabled=request.args.get('live', '1') != '0'))
    except Exception:
        logger.exception('Broker people P/L unavailable')
        return jsonify(status='error', message='人员盈亏读取失败，请稍后重试。'), 503


@api_v1.route("/ownership", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_stock_ownership():
    """Retrieve stock ownership penetration across persons (AC-16)."""
    try:
        from portfolio.engine.stock_ownership import (
            ownership_alert_rows,
            ownership_choices,
            ownership_report,
        )

        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate_people(scope)

        choices = ownership_choices(res)
        instrument_id = request.args.get("instrument_id", type=int)

        if not instrument_id and choices:
            instrument_id = choices[0][0]

        report_data = []
        broker_total = None
        assigned_total = None
        difference = None
        note = ""

        if instrument_id:
            rep_tuple = ownership_report(res, instrument_id)
            rep_obj, broker_total, assigned_total, difference = rep_tuple
            note = getattr(rep_obj, "note", "")
            for r in rep_obj.rows:
                report_data.append({
                    "person_name": r.values[0],
                    "quantity": serialize_val(r.values[1]),
                    "ratio": f"{float(r.values[2]) * 100:.2f}%" if r.values[2] is not None else "—",
                    "person_id": r.metadata.get("person_id"),
                })

        # Calculate reconciliation alerts
        alerts_raw = ownership_alert_rows(res)
        alerts_data = [
            {
                "channel": a[0],
                "symbol": a[1],
                "broker_shares": serialize_val(a[2]),
                "assigned_shares": serialize_val(a[3]),
                "difference": serialize_val(a[4]),
            }
            for a in alerts_raw
        ]

        ctx: AuthContext = getattr(g, "auth_context", None)
        if ctx and not ctx.is_lan_viewer() and not ctx.is_admin() and ctx.allowed_person_ids is not None:
            report_data = [r for r in report_data if r.get("person_id") in ctx.allowed_person_ids]
            alerts_data = []

        return jsonify({
            "status": "ok",
            "choices": [{"id": c[0], "symbol": c[1]} for c in choices],
            "selected_instrument_id": instrument_id,
            "report": report_data,
            "broker_total": serialize_val(broker_total),
            "assigned_total": serialize_val(assigned_total),
            "difference": serialize_val(difference),
            "note": note,
            "alerts": alerts_data,
        })
    except Exception as e:
        logger.exception("Ownership error")
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 6. Earnings Calendar (AC-18)
# ------------------------------------------------------------------------------


@api_v1.route("/earnings-calendar", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_earnings_calendar():
    """Retrieve portfolio holding earnings calendar, month navigation, status and estimates."""
    try:
        year = request.args.get("year", date.today().year, type=int)
        month = request.args.get("month", date.today().month, type=int)

        svc = get_earnings_service()
        a_svc = get_analytics_service()
        res = a_svc.calculate(get_user_query_scope())
        holdings_symbols = []
        if "当前持仓" in res.reports:
            holdings_symbols = [r.values[2] for r in res.reports["当前持仓"].rows if r.values[2]]

        events_data = []
        try:
            cached_events = svc.get_cached(DATABASE_PATH, year, month, ("US",))
            if cached_events:
                for ev in cached_events:
                    events_data.append({
                        "symbol": ev.symbol if hasattr(ev, "symbol") else getattr(ev, "ticker", ""),
                        "company_name": getattr(ev, "name", ""),
                        "date": serialize_val(getattr(ev, "date", "")),
                        "market_session": getattr(ev, "session", "盘前"),
                        "estimated_eps": serialize_val(getattr(ev, "estimate_eps", None)),
                        "actual_eps": serialize_val(getattr(ev, "actual_eps", None)),
                        "status": getattr(ev, "status", "待发布"),
                    })
        except Exception as e:
            logger.warning(f"Earnings cache query warning: {e}")

        # Real data only: no fake EPS data generation
        sort_col = request.args.get("sort_col") or "披露日期"
        sort_dir = (request.args.get("sort_dir") or "desc").lower()
        is_desc = (sort_dir != "asc")
        events_data.sort(key=lambda ev: ev.get("date") or "", reverse=is_desc)

        return jsonify({
            "status": "ok",
            "year": year,
            "month": month,
            "events": events_data,
            "calendar": events_data,
            "items": events_data,
            "count": len(events_data),
            "sort_col": sort_col,
            "sort_dir": "desc" if is_desc else "asc",
        })
    except Exception as e:
        logger.exception("Earnings calendar error")
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 7. Visual Charts (All 27 Desktop Charts, Top Days/Months, Ranking)
# ------------------------------------------------------------------------------


@api_v1.route("/charts", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_charts_all():
    """Retrieve full 27 charts definitions, data points and options."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)
        charts_list = build_charts(res)

        serialized_charts = []
        for c in charts_list:
            serialized_series = []
            for s in c.series:
                serialized_series.append({
                    "name": s.name,
                    "points": [[str(k), float(v) if v is not None else None] for k, v in s.points],
                })
            serialized_charts.append({
                "id": c.id,
                "title": c.title,
                "kind": c.kind,
                "note": c.note,
                "ratio": getattr(c, "ratio", False),
                "link": getattr(c, "link", ""),
                "series": serialized_series,
            })

        return jsonify({"status": "ok", "charts": serialized_charts, "count": len(serialized_charts)})
    except Exception as e:
        logger.exception("Charts error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/charts/top_days", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_charts_top_days():
    """Return top profitable and top losing days ranking."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        count = max(1, min(50, request.args.get("count", 10, type=int)))
        daily_rep = res.reports.get("每日持仓收益")

        day_sum = defaultdict(Decimal)
        if daily_rep:
            for r in daily_rep.rows:
                d = r.values[0]
                p = r.values[19] if len(r.values) > 19 else None
                if p is not None:
                    day_sum[d] += Decimal(str(p))

        day_points = [(k, float(v)) for k, v in day_sum.items()]
        best_days = sorted(day_points, key=lambda x: x[1], reverse=True)[:count]
        worst_days = sorted(day_points, key=lambda x: x[1])[:count]

        return jsonify({
            "status": "ok",
            "best_days": best_days,
            "worst_days": worst_days,
            "count": count,
        })
    except Exception as e:
        logger.exception("Top days error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/charts/top_months", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_charts_top_months():
    """Return top profitable and top losing months ranking."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        count = max(1, min(50, request.args.get("count", 10, type=int)))
        monthly_rep = res.reports.get("每月持仓收益")

        m_sum = defaultdict(Decimal)
        if monthly_rep:
            for r in monthly_rep.rows:
                m = r.values[0]
                p = r.values[20] if len(r.values) > 20 else None
                if p is not None:
                    m_sum[m] += Decimal(str(p))

        month_points = [(k, float(v)) for k, v in m_sum.items()]
        best_months = sorted(month_points, key=lambda x: x[1], reverse=True)[:count]
        worst_months = sorted(month_points, key=lambda x: x[1])[:count]

        return jsonify({
            "status": "ok",
            "best_months": best_months,
            "worst_months": worst_months,
            "count": count,
        })
    except Exception as e:
        logger.exception("Top months error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/charts/ranking", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_charts_ranking():
    """Return profit leaderboard across tickers / brokers / persons."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)
        charts_list = build_charts(res)

        order = request.args.get("order", "profit")
        ranking_chart = None
        for c in charts_list:
            if c.id == 17:
                ranking_chart = rank_chart(c, order=order)
                break

        points = []
        if ranking_chart and ranking_chart.series:
            points = [[k, float(v) if v is not None else 0.0] for k, v in ranking_chart.series[0].points]

        return jsonify({"status": "ok", "order": order, "points": points, "count": len(points)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 8. Monitoring Center: Watchlist, Rules CRUD & Alerts (Fixes AC-23 ~ AC-28)
# ------------------------------------------------------------------------------


@api_v1.route("/monitoring/watchlist", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_watchlist():
    """Retrieve monitored symbols, live quotes, and technical indicators."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    try:
        with store.connect() as db:
            targets = db.execute("SELECT DISTINCT analysis_symbol FROM watch_targets ORDER BY analysis_symbol").fetchall()
            result = []
            for row in targets:
                sym = row[0]
                ms_row = db.execute("SELECT payload FROM market_state WHERE analysis_symbol=?", (sym,)).fetchone()
                ic_row = db.execute("SELECT payload FROM indicator_cache WHERE analysis_symbol=?", (sym,)).fetchone()

                ms = json.loads(ms_row[0]) if ms_row else {}
                m = json.loads(ic_row[0]) if ic_row else {}
                quote = ms.get("quote", {})
                price = quote.get("price")
                prev_close = quote.get("prev_close")
                change_pct = "—"
                if price and prev_close:
                    try:
                        p_f = float(price)
                        pc_f = float(prev_close)
                        change_pct = f"{((p_f - pc_f) / pc_f) * 100:+.2f}%"
                    except Exception:
                        pass

                clean_sym = sym.removesuffix(".US")
                result.append({
                    "symbol": clean_sym,
                    "full_symbol": sym,
                    "price": price or "—",
                    "change": change_pct,
                    "day": ms.get("bar_date", "—"),
                    "ma5": m.get("MA5"),
                    "ma10": m.get("MA10"),
                    "ma20": m.get("MA20"),
                    "ma50": m.get("MA50"),
                    "ma200": m.get("MA200"),
                    "rsi": m.get("RSI14"),
                    "trend_short": m.get("SHORT", "—"),
                    "trend_medium": m.get("MEDIUM", "—"),
                    "trend_long": m.get("LONG", "—"),
                    "cross": m.get("CROSS", "—"),
                })
        return jsonify({"status": "ok", "items": result, "count": len(result)})
    except Exception as e:
        logger.exception("Monitoring watchlist error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/monitoring/watchlist/add", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_watchlist_add():
    """Add a stock symbol to manual watchlist."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "请输入股票代码"}), 400

    target_sym = symbol if symbol.endswith(".US") else f"{symbol}.US"
    store.add_manual_target("PORTFOLIO", target_sym)
    return jsonify({"status": "ok", "message": f"标的 {symbol} 已加入观察列表"})


@api_v1.route("/monitoring/watchlist/delete", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_watchlist_delete():
    """Remove a symbol from manual watchlist."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    target_sym = symbol if symbol.endswith(".US") else f"{symbol}.US"
    store.remove_manual_target("PORTFOLIO", target_sym)
    return jsonify({"status": "ok", "message": f"标的 {symbol} 已从自选列表移除"})


@api_v1.route("/monitoring/rules", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_rules_list():
    """Retrieve all configured alert rules with status."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    rules = store.get_rules()
    serialized = []
    for r in rules:
        sym = getattr(r, "analysis_symbol", None) or getattr(r, "monitored_symbol", None) or getattr(r, "symbol", "")
        rule_name = getattr(r, "name", "") or getattr(r, "rule_name", "")
        sound = getattr(r, "sound_name", "Glass")
        channels = getattr(r, "notification_channels", "in_app,desktop")
        serialized.append({
            "id": r.rule_id,
            "rule_id": r.rule_id,
            "symbol": sym.removesuffix(".US") if sym else "",
            "full_symbol": sym,
            "name": rule_name,
            "rule_name": rule_name,
            "rule_type": r.rule_type,
            "expression": r.expression if isinstance(r.expression, (dict, list)) else str(r.expression),
            "enabled": bool(r.enabled),
            "sound": sound,
            "sound_name": sound,
            "channels": channels,
            "notification_channels": channels,
            "created_at": getattr(r, "created_at", ""),
        })
    return jsonify({"status": "ok", "rules": serialized, "items": serialized, "count": len(serialized)})


@api_v1.route("/monitoring/rules/save", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_rule_save():
    """Create or update an alert monitoring rule (Full CRUD support, AC-24)."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "缺少标的代码"}), 400

    target_sym = symbol if symbol.endswith(".US") else f"{symbol}.US"
    rule_id = data.get("rule_id") or data.get("id") or f"rule_{int(datetime.now().timestamp() * 1000)}"

    expr = data.get("expression")
    if not isinstance(expr, dict):
        expr = {"threshold": data.get("threshold", 100), "op": ">="}

    rule = MonitorRule(
        rule_id=rule_id,
        scope_key="PORTFOLIO",
        analysis_symbol=target_sym,
        monitored_symbol=target_sym,
        name=data.get("rule_name") or data.get("name") or f"{symbol.removesuffix('.US')} 价格监控",
        rule_type=data.get("rule_type", "PRICE"),
        expression=expr,
        notification_channels=data.get("channels") or data.get("notification_channels") or "in_app,desktop",
        sound_name=data.get("sound") or data.get("sound_name") or "Glass",
        enabled=bool(data.get("enabled", False)),
        created_at=datetime.now().isoformat(),
        updated_at=datetime.now().isoformat(),
    )

    if data.get("is_new", False) or not store.get_rule(rule_id):
        store.add_rule(rule)
    else:
        store.update_rule(rule)

    record_audit_event("RULE_SAVE", g.auth_context.username, g.auth_context.access_mode, request.remote_addr, "SAVED", {"rule_id": rule_id})
    return jsonify({"status": "ok", "message": "规则已成功保存", "rule_id": rule_id})


@api_v1.route("/monitoring/rules/delete", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_rule_delete():
    """Delete a monitoring rule."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    data = request.get_json(silent=True) or {}
    rule_id = data.get("rule_id") or data.get("id")
    if not rule_id:
        return jsonify({"status": "error", "message": "缺少 rule_id"}), 400

    store.delete_rule(rule_id)
    return jsonify({"status": "ok", "message": "规则已成功删除", "rule_id": rule_id})


@api_v1.route("/monitoring/rules/<rule_id>/toggle", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_rule_toggle(rule_id: str):
    """Toggle enabled status of an alert rule."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    rule = store.get_rule(rule_id)
    if not rule:
        return jsonify({"status": "error", "message": "未找到规则"}), 404

    new_enabled = not bool(rule.enabled)
    store.set_rule_enabled(rule_id, new_enabled)
    return jsonify({"status": "ok", "enabled": new_enabled, "message": "规则状态已更新"})


@api_v1.route("/monitoring/rules/batch_toggle", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_rules_batch_toggle():
    """Batch enable or disable multiple rules."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    data = request.get_json(silent=True) or {}
    rule_ids = data.get("rule_ids", [])
    enabled = bool(data.get("enabled", True))

    updated_count = store.batch_set_enabled(rule_ids, enabled)
    return jsonify({"status": "ok", "updated_count": updated_count, "enabled": enabled})


@api_v1.route("/monitoring/alerts", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_alerts_list():
    """Retrieve triggered alert history events."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    events = store.get_alert_events(limit=500)
    sort_col = request.args.get("sort_col") or "触发时间"
    sort_dir = (request.args.get("sort_dir") or "desc").lower()
    is_desc = (sort_dir != "asc")
    events.sort(key=lambda e: getattr(e, "detected_at", "") or getattr(e, "created_at", "") or "", reverse=is_desc)

    serialized = []
    for e in events:
        serialized.append({
            "event_id": getattr(e, "event_id", None) or getattr(e, "id", None),
            "rule_id": getattr(e, "rule_id", None),
            "symbol": getattr(e, "symbol", "").removesuffix(".US"),
            "rule_name": getattr(e, "rule_name", ""),
            "message": getattr(e, "message", ""),
            "trigger_price": serialize_val(getattr(e, "trigger_price", None)),
            "channel": getattr(e, "channel", "in_app"),
            "status": getattr(e, "status", "unread"),
            "created_at": serialize_val(getattr(e, "created_at", None)),
            "detected_at": serialize_val(getattr(e, "detected_at", None)),
        })
    return jsonify({
        "status": "ok",
        "items": serialized,
        "count": len(serialized),
        "sort_col": sort_col,
        "sort_dir": "desc" if is_desc else "asc",
    })


@api_v1.route("/monitoring/alerts/read", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_alerts_read():
    """Mark alerts as read."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    data = request.get_json(silent=True) or {}
    event_ids = data.get("event_ids")
    count = store.mark_events_read(event_ids)
    return jsonify({"status": "ok", "marked_count": count})


@api_v1.route("/monitoring/alerts/clear", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_monitoring_alerts_clear():
    """Clear all alert events history."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    count = store.clear_alert_events()
    return jsonify({"status": "ok", "cleared_count": count, "message": "告警历史已清空"})


@api_v1.route("/monitoring/kline", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_kline():
    """Return historical K-line bars and calculated technical indicator overlays."""
    symbol = request.args.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "缺少股票代码 symbol"}), 400

    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    try:
        target_sym = symbol if symbol.endswith(".US") else f"{symbol}.US"
        bars_objs = store.bars(target_sym) or store.bars(symbol)
        if not bars_objs:
            return jsonify({
                "status": "ok",
                "symbol": symbol,
                "dates": [],
                "ohlcv": [],
                "candles": [],
                "volumes": [],
                "bars": [],
                "ma": {},
                "macd": {},
                "rsi": [],
                "technicals": {},
                "message": f"暂无标的 {symbol} 的历史 K 线数据",
            })

        s = series(bars_objs)
        t_pair = technicals(bars_objs)
        tech_dict = t_pair[0] if isinstance(t_pair, tuple) else t_pair

        def to_float(val):
            if val is None:
                return None
            try:
                return float(val)
            except Exception:
                return str(val)

        dates = [b.day.isoformat() for b in bars_objs]
        ohlcv = [
            [to_float(b.open), to_float(b.close), to_float(b.low), to_float(b.high), to_float(b.volume or 0)]
            for b in bars_objs
        ]
        volumes = [to_float(b.volume or 0) for b in bars_objs]
        latest_close = to_float(bars_objs[-1].close) if bars_objs else 0.0

        ma = {
            "ma5": [to_float(v) for v in s.get("MA5", [])],
            "ma10": [to_float(v) for v in s.get("MA10", [])],
            "ma20": [to_float(v) for v in s.get("MA20", [])],
            "ma50": [to_float(v) for v in s.get("MA50", [])],
            "ma200": [to_float(v) for v in s.get("MA200", [])],
        }

        macd = {
            "dif": [to_float(v) for v in s.get("DIF", [])],
            "dea": [to_float(v) for v in s.get("DEA", [])],
            "bar": [to_float(v) for v in s.get("MACD", [])],
        }

        rsi = [to_float(v) for v in s.get("RSI14", [])]
        technicals_clean = {k: to_float(v) for k, v in tech_dict.items()}

        bars_data = [
            {
                "date": b.day.isoformat(),
                "open": to_float(b.open),
                "high": to_float(b.high),
                "low": to_float(b.low),
                "close": to_float(b.close),
                "volume": to_float(b.volume),
            }
            for b in bars_objs
        ]

        return jsonify({
            "status": "ok",
            "symbol": symbol,
            "dates": dates,
            "ohlcv": ohlcv,
            "volumes": volumes,
            "latest_close": latest_close,
            "ma": ma,
            "macd": macd,
            "rsi": rsi,
            "technicals": technicals_clean,
            "bars": bars_data,
        })
    except Exception as e:
        logger.error(f"Error loading K-line for {symbol}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"加载K线数据失败: {e}"}), 500


# ------------------------------------------------------------------------------
# 9. AI Assistant & Stock Analysis Endpoints (100% Shared with Desktop)
# ------------------------------------------------------------------------------


def get_ai_data_dir() -> Path:
    base_dir = Path.home() / "Library" / "Application Support" / "MoneyPortfolio"
    if not base_dir.exists():
        base_dir = Path.home() / ".money_portfolio"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def load_ai_sessions(filename: str) -> Dict[str, Any]:
    file_path = get_ai_data_dir() / filename
    if file_path.is_file():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_ai_sessions(filename: str, data: Dict[str, Any]):
    file_path = get_ai_data_dir() / filename
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save AI sessions: {e}")


@api_v1.route("/ai/status", methods=["GET"])
@require_capability(CAP_AI_CHAT)
def api_ai_status():
    """Return Codex CLI login status and available models."""
    status = AIService.check_login_status()
    return jsonify({"status": "ok", "ai_status": status})


@api_v1.route("/ai/history", methods=["GET"])
@require_capability(CAP_AI_CHAT)
def api_ai_history():
    """Retrieve shared AI portfolio Q&A session history."""
    person_id, person_name = get_current_user_scope()
    requested_person = request.args.get("person", "全部人员")
    if person_name:
        requested_person = person_name

    session_key = f"{requested_person}::全部渠道::全部标的"
    sessions = load_ai_sessions("ai_chat_sessions.json")
    history = sessions.get(session_key, [])
    return jsonify({"status": "ok", "session_key": session_key, "messages": history})


@api_v1.route("/ai/chat", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_chat():
    """Deliver a user prompt to AI investment assistant and record conversation."""
    data = request.get_json(silent=True) or {}
    prompt = (data.get("prompt") or data.get("message") or "").strip()
    if not prompt:
        return jsonify({"status": "error", "message": "请输入咨询问题"}), 400

    person_id, person_name = get_current_user_scope()
    req_person_id = data.get("person_id")
    if person_id is None and req_person_id is not None:
        person_id = req_person_id

    requested_person = request.args.get("person", "全部人员")
    if person_name:
        requested_person = person_name

    session_key = f"{requested_person}::全部渠道::全部标的"

    try:
        svc = get_analytics_service()
        res = svc.calculate(get_user_query_scope(person_id=person_id))
        context_str = AIService.build_portfolio_context(res, requested_person, "全部渠道", "全部标的")
    except Exception:
        context_str = "当前投资组合处于离线状态。"

    answer = AIService.ask(prompt, context=context_str)

    sessions = load_ai_sessions("ai_chat_sessions.json")
    current_msgs = sessions.get(session_key, [])
    current_msgs.append({"role": "user", "content": prompt, "time": datetime.now().isoformat()})
    current_msgs.append({"role": "assistant", "content": answer, "time": datetime.now().isoformat()})
    sessions[session_key] = current_msgs[-50:]
    save_ai_sessions("ai_chat_sessions.json", sessions)

    return jsonify({
        "status": "ok",
        "answer": answer,
        "reply": answer,
        "content": answer,
        "response": answer,
        "session_key": session_key,
    })


@api_v1.route("/ai/clear", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_clear():
    """Clear AI conversation history."""
    person_id, person_name = get_current_user_scope()
    requested_person = person_name or request.args.get("person", "全部人员")
    session_key = f"{requested_person}::全部渠道::全部标的"
    sessions = load_ai_sessions("ai_chat_sessions.json")
    if session_key in sessions:
        sessions[session_key] = []
        save_ai_sessions("ai_chat_sessions.json", sessions)
    return jsonify({"status": "ok", "message": "会话历史已清空"})


@api_v1.route("/ai/stock/history", methods=["GET"])
@require_capability(CAP_AI_STOCK)
def api_ai_stock_history():
    """Retrieve shared AI stock report conversation history."""
    symbol = (request.args.get("symbol") or "AAOI").strip().upper()
    sessions = load_ai_sessions("ai_stock_analysis_sessions.json")
    history = sessions.get(symbol, [])
    return jsonify({"status": "ok", "symbol": symbol, "messages": history})


@api_v1.route("/ai/stock/chat", methods=["POST"])
@require_capability(CAP_AI_STOCK)
def api_ai_stock_chat():
    """Ask stock-specific questions and generate stock analysis report."""
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "AAOI").strip().upper()
    prompt = (data.get("prompt") or data.get("message") or "").strip()
    if not prompt:
        return jsonify({"status": "error", "message": "请输入咨询问题"}), 400

    tech_info = ""
    store = get_monitor_store()
    if store:
        target_sym = symbol if symbol.endswith(".US") else f"{symbol}.US"
        st = store.state(target_sym)
        if st:
            tech_info = f"标的 {symbol} 当前行情与指标: {json.dumps(st, ensure_ascii=False)}"

    prompt_to_send = f"{prompt}\n\n附标的技术面信息：{tech_info}" if tech_info else prompt
    answer = AIService.ask_stock(prompt_to_send, symbol=symbol)

    sessions = load_ai_sessions("ai_stock_analysis_sessions.json")
    current_msgs = sessions.get(symbol, [])
    current_msgs.append({"role": "user", "content": prompt, "time": datetime.now().isoformat()})
    current_msgs.append({"role": "assistant", "content": answer, "time": datetime.now().isoformat()})
    sessions[symbol] = current_msgs[-50:]
    save_ai_sessions("ai_stock_analysis_sessions.json", sessions)

    return jsonify({
        "status": "ok",
        "answer": answer,
        "reply": answer,
        "content": answer,
        "response": answer,
        "symbol": symbol,
    })


# ------------------------------------------------------------------------------
# 10. System, Reconciliation & Diagnostics
# ------------------------------------------------------------------------------


@api_v1.route("/system/database", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_system_database():
    """Retrieve database inspection, integrity check, table row counts."""
    db_exists = DATABASE_PATH.is_file()
    tables_stats = []
    integrity_ok = False

    if db_exists:
        try:
            with sqlite3.connect(DATABASE_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("PRAGMA integrity_check")
                res = cur.fetchone()
                integrity_ok = res and res[0] == "ok"

                cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
                tables = [r[0] for r in cur.fetchall()]
                for t in tables:
                    try:
                        cur.execute(f"SELECT count(*) FROM {t}")
                        cnt = cur.fetchone()[0]
                        tables_stats.append({"table": t, "row_count": cnt})
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Database inspection error: {e}")

    return jsonify({
        "status": "ok",
        "path": str(DATABASE_PATH),
        "exists": db_exists,
        "size_bytes": DATABASE_PATH.stat().st_size if db_exists else 0,
        "integrity_ok": integrity_ok,
        "tables": tables_stats,
    })


@api_v1.route("/system/metrics", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_system_metrics():
    """Return metrics dictionary and definitions."""
    help_file = PROJECT_DIR / "src/portfolio/resources/metric_help.json"
    data = {}
    if help_file.is_file():
        try:
            with open(help_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    return jsonify({"status": "ok", "metrics": data})


@api_v1.route("/system/progress", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_system_progress():
    """Return development progress logs from progress_log.json."""
    log_file = PROJECT_DIR / "src/portfolio/resources/progress_log.json"
    logs = []
    if log_file.is_file():
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            pass
    return jsonify({"status": "ok", "progress_logs": logs, "count": len(logs)})


@api_v1.route("/system/sync_status", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_system_sync_status():
    """Return broker sync status and CLI instructions."""
    return jsonify({
        "status": "ok",
        "is_read_only": True,
        "message": "展示层只读本地数据库。可通过运行《启动Money3.command --excel》或《启动Money3.command --api》同步数据。",
        "last_sync": datetime.now().isoformat(),
    })


@api_v1.route("/system/reconcile", methods=["POST"])
@require_capability(CAP_VIEW_ASSETS)
def api_system_reconcile():
    """Run reconciliation against Excel workbook."""
    try:
        from portfolio.reconcile import reconcile

        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        uploaded_file = request.files.get("file")
        temp_dir = None
        target_path = None
        if uploaded_file:
            temp_dir = tempfile.TemporaryDirectory()
            target_path = Path(temp_dir.name) / "uploaded_reconcile.xlsx"
            uploaded_file.save(str(target_path))
        else:
            default_wb = PROJECT_DIR / "交易流水.xlsx"
            if default_wb.is_file():
                target_path = default_wb

        if not target_path or not target_path.is_file():
            return jsonify({"status": "error", "message": "未找到对账文件 交易流水.xlsx"}), 400

        reconcile_report = reconcile(res, target_path, person_name="个人")
        if temp_dir:
            temp_dir.cleanup()

        discrepancies = []
        if reconcile_report and hasattr(reconcile_report, "rows"):
            for r in reconcile_report.rows:
                discrepancies.append({
                    "channel": r.values[0],
                    "symbol": r.values[1],
                    "db_shares": serialize_val(r.values[2]),
                    "excel_shares": serialize_val(r.values[3]),
                    "difference": serialize_val(r.values[4]),
                })

        return jsonify({
            "status": "ok",
            "discrepancies": discrepancies,
            "count": len(discrepancies),
        })
    except Exception as e:
        logger.exception("Reconciliation error")
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/export/report/<report_name>", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_export_report_csv(report_name: str):
    """Export any report to standard CSV."""
    if report_name in ("今日盈亏", "当日盈亏"):
        try:
            payload = today_payload(include_curve=False)
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(payload["columns"])
            writer.writerows(payload["rows"])
            from urllib.parse import quote
            return Response(output.getvalue().encode("utf-8-sig"),
                            mimetype="text/csv; charset=utf-8",
                            headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("今日盈亏.csv")})
        except TodayQueryError:
            return jsonify(status="error", message="日期或筛选参数无效。"), 400
        except Exception:
            return jsonify(status="error", message="今日盈亏导出失败，请重试。"), 503
    try:
        svc = get_analytics_service()
        person_id = request.args.get("person_id", type=int)
        scope = get_user_query_scope(person_id=person_id)
        res = svc.calculate(scope)

        report = None
        if report_name == "清仓收益":
            report = get_today_service().closed_report(res)
        elif report_name == "股票归属":
            report = res.reports.get("人员划转") or res.reports.get("股票汇总")
        elif report_name == "券商资产":
            report = res.reports.get("渠道汇总") or res.reports.get("账户资产")
        else:
            report = res.reports.get(report_name)

        if not report:
            return jsonify({"status": "error", "message": f"未找到报表: {report_name}"}), 404

        search = (request.args.get("search") or "").strip().lower()
        filtered_rows = []
        for r in report.rows:
            if search:
                row_str = " ".join(str(v or "") for v in r.values).lower()
                if search not in row_str:
                    continue
            filtered_rows.append(r)

        # Sort
        sort_col = request.args.get("sort_col")
        sort_dir = request.args.get("sort_dir")
        if not sort_col:
            time_col = find_primary_time_column(list(report.columns))
            if time_col:
                sort_col = time_col
                sort_dir = sort_dir or "desc"
        if not sort_dir:
            sort_dir = "desc" if (sort_col and any(k in sort_col for k in ("时间", "日期", "月份", "effective_from", "business_date", "imported_at_utc"))) else "asc"
        sort_dir = sort_dir.lower()

        if sort_col and sort_col in report.columns:
            col_idx = list(report.columns).index(sort_col)
            is_desc = (sort_dir == "desc")
            try:
                filtered_rows.sort(
                    key=lambda r, idx=col_idx, desc=is_desc: parse_sort_key(
                        r.values[idx] if idx < len(r.values) else None, desc, r, 0
                    ),
                    reverse=is_desc,
                )
            except Exception as sort_err:
                logger.warning(f"CSV Export sort failed for {sort_col}: {sort_err}")

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(report.columns)
        for r in filtered_rows:
            writer.writerow([serialize_val(v) for v in r.values])

        csv_data = output.getvalue().encode("utf-8-sig")
        from urllib.parse import quote
        safe_filename = quote(f"{report_name}.csv")
        return Response(
            csv_data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}"},
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route("/export/trades", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_export_trades_csv():
    """Export filtered trades to standard CSV."""
    try:
        svc = get_trades_service()
        person_id, _ = get_current_user_scope()
        req_person_id = request.args.get("person_id", type=int)
        if person_id is None and req_person_id is not None:
            person_id = req_person_id

        symbol = (request.args.get("symbol") or "").strip().upper() or None
        channel = (request.args.get("broker") or request.args.get("channel") or "").strip() or None
        side = (request.args.get("side") or "").strip().upper() or None
        search = (request.args.get("search") or "").strip() or None

        scope = get_user_query_scope(person_id=person_id)
        if search:
            scope = replace(scope, search=search)

        if symbol:
            with sqlite3.connect(DATABASE_PATH) as conn:
                sym_rows = conn.execute("SELECT id FROM instruments WHERE symbol = ? OR symbol LIKE ?", (symbol, f"{symbol}.%")).fetchall()
                if sym_rows:
                    scope = replace(scope, instrument_ids=tuple(r[0] for r in sym_rows))
                else:
                    scope = replace(scope, instrument_ids=(-99999,))

        if channel:
            with sqlite3.connect(DATABASE_PATH) as conn:
                acc_rows = conn.execute("SELECT id FROM broker_accounts WHERE broker_code = ? OR display_name = ?", (channel, channel)).fetchall()
                if acc_rows:
                    scope = replace(scope, account_ids=tuple(r[0] for r in acc_rows))
                else:
                    scope = replace(scope, account_ids=(-99999,))

        trade_filters = None
        if side:
            side_map = {"买入": "BUY", "卖出": "SELL", "BUY": "BUY", "SELL": "SELL", "转入": "TRANSFER_IN", "转出": "TRANSFER_OUT"}
            db_side = side_map.get(side, side)
            trade_filters = TradeFilters(side=db_side)

        trades_sort_map = {
            "时间": 0, "book_time": 0, "trade_date": 0,
            "券商": 1, "broker_code": 1, "broker": 1,
            "分类": 2, "category": 2,
            "标的": 3, "symbol": 3,
            "方向": 4, "side": 4,
            "数量": 5, "quantity_e8": 5, "quantity": 5,
            "价格": 6, "price_e8": 6, "price": 6,
            "金额": 7, "amount_e8": 7, "amount": 7,
            "操作类型": 12, "kind": 12,
            "对手方": 13, "counterparty": 13,
            "订单号": 14, "order_id": 14,
        }
        sort_col = request.args.get("sort_col") or "时间"
        sort_dir = (request.args.get("sort_dir") or "desc").lower()
        sort_column_idx = trades_sort_map.get(sort_col, 0)
        descending = (sort_dir != "asc")

        all_rows = []
        page_idx = 0
        while True:
            page_obj = svc.get_trades(
                scope,
                page=page_idx,
                size=1000,
                sort_column=sort_column_idx,
                descending=descending,
                filters=trade_filters,
            )
            all_rows.extend(page_obj.rows)
            if not page_obj.rows or len(all_rows) >= page_obj.total or len(page_obj.rows) < 1000:
                break
            page_idx += 1

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["时间", "券商", "分类", "标的", "方向", "数量", "价格", "金额", "标记", "持仓", "净投入", "成本", "操作类型", "对手方", "订单号", "来源"])
        for r in all_rows:
            vals = r.values if hasattr(r, "values") else ()
            writer.writerow([serialize_val(v) for v in vals])

        csv_data = output.getvalue().encode("utf-8-sig")
        return Response(
            csv_data,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''trades_export.csv"},
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@api_v1.route('/charts/history',methods=['GET'])
@require_capability(CAP_VIEW_ASSETS)
def api_shared_history():
    """Same derived service as the native month/year page, scoped before SQL."""
    try:
        import calendar
        from portfolio.services.derived_queries import trend,load_history
        from portfolio.services.web_today import desktop_quote_store,exact_json
        kind=request.args.get('kind','month');year=kind=='year'
        if kind not in ('month','year'):raise ValueError('走势范围必须是month或year。')
        period=request.args.get('period',date.today().strftime('%Y' if year else '%Y-%m'))
        if year:first,last=date(int(period),1,1),date(int(period),12,31)
        else:
            y,m=map(int,period.split('-'));first,last=date(y,m,1),date(y,m,calendar.monthrange(y,m)[1])
        scope=get_user_query_scope(as_of=max(first,min(last,date.today())),person_id=request.args.get('person_id',type=int))
        scope=replace(scope,start_date=None,search=request.args.get("search", ""))
        quotes=desktop_quote_store();quotes.source=DATABASE_PATH
        from portfolio.services.derived_store import DerivedStore
        if not DerivedStore(DATABASE_PATH).readiness()['ready']:
            rows,state=load_history(DATABASE_PATH,quotes,scope,first,last)
            return jsonify({'status':'ok','chart':{'title':period+' 历史走势','series':[],'note':state.get('message','尚无已发布历史数据，请查看任务状态。'),'boundaries':[]},
                            'cache':state,'channels':[],'tickers':[]})
        from portfolio.services.derived_queries import historical_analytics
        analytics=historical_analytics(DATABASE_PATH,scope,first,last)
        channels=request.args.getlist('channel') or None
        ticker=request.args.get('ticker') or None
        rows,state=load_history(DATABASE_PATH,quotes,scope,first,last,aggregate=True,channels=channels,ticker=ticker)
        mode=request.args.get('mode','cumulative')
        if mode not in ('cumulative','intraday'):raise ValueError('未知走势模式。')
        chart,state=trend(DATABASE_PATH,quotes,analytics,period,year=year,channels=channels,ticker=ticker,mode=mode,history_data=(rows,state))
        daily=analytics.reports['每日持仓收益'].rows
        return jsonify(exact_json({'status':'ok','chart':{'title':chart.title,'series':[{'name':s.name,'points':s.points} for s in chart.series],
                     'note':chart.note,'boundaries':chart.vertical_lines,'x_ticks':chart.x_ticks,
                     'x_labels':chart.x_labels,'calendar_axis':chart.calendar_axis,'line_width':chart.line_width,'break_keys':chart.break_keys},'cache':state,
                     'channels':sorted({r.values[1] for r in daily}),'tickers':sorted({r.values[3] for r in daily})}))
    except (ValueError,TypeError) as exc:return jsonify({'status':'error','message':str(exc)}),400
    except Exception:
        logger.exception('Shared historical trend failed')
        return jsonify({'status':'error','message':'历史派生数据读取失败，请检查初始化、缓存状态及服务器日志。'}),500


@api_v1.route('/system/derived',methods=['GET'])
@require_capability(CAP_MANAGE_USERS)
def api_derived_status():
    from portfolio.services.derived_store import DerivedStore
    return jsonify({'status':'ok','derived':DerivedStore(DATABASE_PATH).status()})


@api_v1.route('/system/derived-progress',methods=['GET'])
@require_capability(CAP_VIEW_ASSETS)
def api_derived_progress():
    # Only generic task counters/status; no owner names, IDs or asset values.
    from portfolio.services.derived_maintenance import maintenance_status
    from portfolio.services.intraday_pipeline import pipeline_status
    from portfolio.services.web_today import desktop_quote_store
    from portfolio.domain.live_quotes import market_trade_day,UTC
    status=maintenance_status(DATABASE_PATH)
    _,days,halves,_=desktop_quote_store().load(datetime.now(UTC))
    return jsonify({'status':'ok',**status,'market_day':market_trade_day(datetime.now(UTC),days,halves).isoformat(),
                    'intraday':pipeline_status(DATABASE_PATH)})
