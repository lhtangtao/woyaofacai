"""Money Portfolio Mobile Web & PWA Server.

Provides a responsive, touch-optimized web interface for iPhone / iPad,
exposing full portfolio holdings, desktop analytical reports (28 reports),
27 ECharts financial charts, professional K-line candlestick charts with
technical indicators, AI investment advising, Bark push notifications,
and multi-user role-based authentication with account isolation (e.g. 拉科).
"""

from datetime import date, datetime
from decimal import Decimal
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, session

from portfolio.domain.models import Ledger, QueryScope
from portfolio.domain.technical_indicators import series, technicals
from portfolio.paths import API_CONFIG_PATH, DATABASE_PATH, PROJECT_DIR
from portfolio.services.ai_service import CodexAIService
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.charts_service import build_charts
from portfolio.services.monitor_store import MonitorStore
from portfolio.services.notification_service import notification_service
from portfolio.services.trades_service import TradesService

logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "money_portfolio_lake_secret_key_2026")

# User account defaults and credentials
DEFAULT_USERS: Dict[str, Dict[str, Any]] = {
    "拉科": {
        "password": "lake",
        "role": "user",
        "person_id": 5,
        "person_name": "拉科",
    },
    "admin": {
        "password": "admin",
        "role": "admin",
        "person_id": None,
        "person_name": "管理员",
    },
}

# Local persistent AI session paths (100% shared with desktop AIAssistantPage & AIStockAnalysisPage)
def get_ai_data_dir() -> Path:
    """Return local disk storage directory matching desktop QStandardPaths.GenericDataLocation."""
    base_dir = Path.home() / "Library" / "Application Support" / "MoneyPortfolio"
    if not base_dir.exists():
        base_dir = Path.home() / ".money_portfolio"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def load_ai_chat_sessions() -> Dict[str, List[Dict[str, Any]]]:
    """Load persistent AI portfolio chat sessions (shared with desktop AIAssistantPage)."""
    path = get_ai_data_dir() / "ai_chat_sessions.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to load ai_chat_sessions.json: {e}")
        return {}


def save_ai_chat_sessions(sessions: Dict[str, List[Dict[str, Any]]]):
    """Save persistent AI portfolio chat sessions (shared with desktop AIAssistantPage)."""
    path = get_ai_data_dir() / "ai_chat_sessions.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save ai_chat_sessions.json: {e}")


def load_ai_stock_sessions() -> Dict[str, List[Dict[str, Any]]]:
    """Load persistent AI pure stock research sessions (shared with desktop AIStockAnalysisPage)."""
    path = get_ai_data_dir() / "ai_stock_analysis_sessions.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to load ai_stock_analysis_sessions.json: {e}")
        return {}


def save_ai_stock_sessions(sessions: Dict[str, List[Dict[str, Any]]]):
    """Save persistent AI pure stock research sessions (shared with desktop AIStockAnalysisPage)."""
    path = get_ai_data_dir() / "ai_stock_analysis_sessions.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save ai_stock_analysis_sessions.json: {e}")


# In-memory fallback
_ai_chat_histories: Dict[str, List[Dict[str, Any]]] = {}

# Shared service references
_analytics_svc: Optional[AnalyticsService] = None
_trades_svc: Optional[TradesService] = None
_monitor_store: Optional[MonitorStore] = None


def load_all_users() -> Dict[str, Dict[str, Any]]:
    """Load users merging DEFAULT_USERS with api_config.json if available."""
    users = dict(DEFAULT_USERS)
    if API_CONFIG_PATH.is_file():
        try:
            with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "users" in cfg and isinstance(cfg["users"], dict):
                    users.update(cfg["users"])
        except Exception as e:
            logger.warning(f"Failed to load users from api_config: {e}")
    return users


def get_current_user() -> Dict[str, Any]:
    """Resolve current user from session, headers, or query param, fallback to admin."""
    # 1. From session
    u = session.get("user")
    if u and isinstance(u, dict) and u.get("username"):
        return u

    # 2. From header or query param (for API clients/scripts)
    auth_user = request.headers.get("X-User") or request.args.get("auth_user")
    if auth_user:
        all_u = load_all_users()
        if auth_user in all_u:
            usr = dict(all_u[auth_user])
            usr.pop("password", None)
            usr["username"] = auth_user
            return usr

    # 3. Default session
    return {"username": "admin", "role": "admin", "person_id": None, "person_name": "管理员"}


def get_current_user_scope() -> Tuple[Optional[int], Optional[str]]:
    """Return (person_id, person_name) tuple for current user."""
    u = get_current_user()
    if u and u.get("person_id"):
        return u["person_id"], u.get("person_name")
    return None, None


def get_user_query_scope(as_of: Optional[date] = None, currency: str = "USD") -> QueryScope:
    """Build QueryScope strictly filtered by current user's person_id if restricted."""
    person_id, _ = get_current_user_scope()
    person_ids = (person_id,) if person_id else None
    return QueryScope(
        as_of=as_of or date.today(),
        reporting_currency=currency,
        ledger=Ledger.PERSON,
        person_ids=person_ids,
    )


def get_analytics_service() -> AnalyticsService:
    global _analytics_svc
    if _analytics_svc is None:
        _analytics_svc = AnalyticsService(DATABASE_PATH)
    return _analytics_svc


def get_trades_service() -> TradesService:
    global _trades_svc
    if _trades_svc is None:
        _trades_svc = TradesService(DATABASE_PATH)
    return _trades_svc


def get_monitor_store() -> Optional[MonitorStore]:
    global _monitor_store
    if _monitor_store is not None:
        return _monitor_store

    candidates = [
        Path.home() / "Library/Application Support/MoneyPortfolio/Money Portfolio/monitoring.sqlite3",
        PROJECT_DIR / "monitoring.sqlite3",
        PROJECT_DIR / "数据库" / "monitoring.sqlite3",
    ]
    for p in candidates:
        if p.is_file():
            try:
                st = MonitorStore(p)
                _monitor_store = st
                return st
            except Exception as e:
                logger.warning(f"Failed to open {p}: {e}")
    return None


def get_network_info() -> Dict[str, Any]:
    """Retrieve all local network IPv4 addresses including LAN and Tailscale."""
    lan_ips = []
    tailscale_ips = []
    try:
        res = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet ") and not line.startswith("inet 127."):
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    if ip.startswith("198.18."):
                        continue  # Exclude Clash fake-ip
                    if ip.startswith("100."):
                        tailscale_ips.append(ip)
                    else:
                        lan_ips.append(ip)
    except Exception:
        pass

    if not tailscale_ips:
        try:
            ts_res = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2)
            if ts_res.returncode == 0:
                for line in ts_res.stdout.splitlines():
                    ip = line.strip()
                    if ip.startswith("100."):
                        tailscale_ips.append(ip)
        except Exception:
            pass

    if not lan_ips:
        lan_ips.append("127.0.0.1")

    primary_lan = lan_ips[0] if lan_ips else "127.0.0.1"
    primary_tailscale = tailscale_ips[0] if tailscale_ips else None

    return {
        "lan_ip": primary_lan,
        "tailscale_ip": primary_tailscale,
        "all_lan_ips": list(dict.fromkeys(lan_ips)),
        "all_tailscale_ips": list(dict.fromkeys(tailscale_ips)),
        "port": 8888,
    }


def serialize_val(val: Any) -> Any:
    """Recursively convert Decimal, dates, None to JSON serializable formats."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, Decimal):
        if not val.is_finite():
            return None
        f = float(val)
        return int(f) if f.is_integer() else f
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return str(val)


def safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# --- Web Routes ---


@app.route("/")
def index():
    net = get_network_info()
    user = get_current_user()
    return render_template(
        "mobile.html",
        lan_ip=net["lan_ip"],
        tailscale_ip=net["tailscale_ip"],
        all_lan_ips=net["all_lan_ips"],
        port=net["port"],
        bark_url=notification_service.bark_url or "",
        current_user=user,
    )


# --- Authentication & User Management Routes ---


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Authenticate user with username and password (e.g. 拉科 / lake)."""
    try:
        data = request.get_json() or {}
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify({"status": "error", "message": "账号或密码不能为空"}), 400

        all_u = load_all_users()
        if username not in all_u or all_u[username].get("password") != password:
            return jsonify({"status": "error", "message": "账号或密码错误"}), 401

        target = dict(all_u[username])
        target.pop("password", None)
        target["username"] = username

        session["user"] = target
        logger.info(f"User logged in: {username} (person_id: {target.get('person_id')})")
        return jsonify({"status": "ok", "message": f"欢迎，{username}！", "user": target})
    except Exception as e:
        logger.exception("Login error")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    """Return current logged-in user profile and permissions."""
    u = get_current_user()
    return jsonify({"status": "ok", "user": u})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Clear user session and reset to default."""
    session.pop("user", None)
    return jsonify({"status": "ok", "message": "已退出登录", "user": get_current_user()})


@app.route("/api/ai/status", methods=["GET"])
def api_ai_status():
    """Return Codex AI CLI login status and account type."""
    status = CodexAIService.check_login_status()
    return jsonify({"status": "ok", **status})


@app.route("/api/ai/filters", methods=["GET"])
def api_ai_filters():
    """Return person, channel, and symbol filters available for current user."""
    try:
        svc = get_analytics_service()
        person_id, person_name = get_current_user_scope()
        scope = get_user_query_scope()
        analytics = svc.calculate(scope)
        u = get_current_user()

        # 1. Persons list
        if person_id is not None:
            # Restricted user (e.g. 拉科): locked strictly to their own person
            persons = [{"id": person_id, "name": person_name, "locked": True}]
        else:
            # Admin user: can select all or specific person
            persons = [{"id": None, "name": "全部启用人员", "locked": False}]
            try:
                info = get_trades_service().source_info()
                for p in info.persons:
                    if p.get("enabled"):
                        persons.append({"id": p["id"], "name": p["name"], "locked": False})
            except Exception:
                pass

        # 2. Holdings & Channels
        holdings_rep = analytics.reports.get("当前持仓")
        symbols_set = set()
        channels_set = set()
        if holdings_rep and holdings_rep.rows:
            for r in holdings_rep.rows:
                if len(r.values) > 0 and r.values[0]:
                    channels_set.add(str(r.values[0]).strip())
                if len(r.values) > 2 and r.values[2]:
                    sym = str(r.values[2]).strip().upper().removesuffix(".US")
                    if sym:
                        symbols_set.add(sym)

        # Also get recent traded symbols from 交易回放 for completeness
        trades_rep = analytics.reports.get("交易回放")
        if trades_rep and trades_rep.rows:
            for r in trades_rep.rows[:100]:
                if len(r.values) > 1 and r.values[1]:
                    channels_set.add(str(r.values[1]).strip())
                if len(r.values) > 3 and r.values[3]:
                    sym = str(r.values[3]).strip().upper().removesuffix(".US")
                    if sym:
                        symbols_set.add(sym)

        channels = ["全部渠道"] + sorted(list(channels_set))
        symbols = ["全部股票"] + sorted(list(symbols_set))

        return jsonify({
            "status": "ok",
            "user": u,
            "persons": persons,
            "channels": channels,
            "symbols": symbols,
        })
    except Exception as e:
        logger.exception("Failed to get AI filters")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/history", methods=["GET"])
def api_ai_history():
    """Return AI portfolio chat history from disk matching desktop AIAssistantPage."""
    person_id, person_name = get_current_user_scope()
    req_person = request.args.get("person", "").strip()
    req_channel = request.args.get("channel", "").strip() or "全部渠道"
    req_symbol = request.args.get("symbol", "").strip().upper().removesuffix(".US") or "全部股票"

    if person_id is not None:
        target_person = person_name or "拉科"
    else:
        target_person = req_person or "全部人员"

    session_key = f"{target_person}___{req_channel}___{req_symbol}"
    sessions = load_ai_chat_sessions()
    history = sessions.get(session_key)

    # If empty, try fallback if person was 全部人员 or 个人
    if history is None:
        if target_person in ("全部人员", "全部启用人员"):
            history = sessions.get(f"个人___{req_channel}___{req_symbol}", [])
        else:
            history = []

    # Map message objects so they have content, role, time_str
    formatted = []
    for m in history:
        formatted.append({
            "role": m.get("role", "user"),
            "content": m.get("content", m.get("text", "")),
            "text": m.get("content", m.get("text", "")),
            "time_str": m.get("time_str", m.get("time", "")),
            "time": m.get("time_str", m.get("time", "")),
            "scope_tag": m.get("scope_tag", ""),
        })

    return jsonify({
        "status": "ok",
        "session_key": session_key,
        "history": formatted,
    })


@app.route("/api/ai/clear", methods=["POST"])
def api_ai_clear():
    """Clear AI conversation history for a specific combination on disk."""
    data = request.get_json() or {}
    person_id, person_name = get_current_user_scope()
    req_person = data.get("person", "").strip()
    req_channel = data.get("channel", "").strip() or "全部渠道"
    req_symbol = data.get("symbol", "").strip().upper().removesuffix(".US") or "全部股票"

    if person_id is not None:
        target_person = person_name or "拉科"
    else:
        target_person = req_person or "全部人员"

    session_key = f"{target_person}___{req_channel}___{req_symbol}"
    sessions = load_ai_chat_sessions()
    sessions[session_key] = []
    save_ai_chat_sessions(sessions)
    return jsonify({"status": "ok", "message": f"会话历史已清空: {session_key}", "session_key": session_key})


@app.route("/api/ai/stock/history", methods=["GET"])
def api_ai_stock_history():
    """Return pure stock fundamental research history from disk matching desktop AIStockAnalysisPage."""
    raw_symbol = request.args.get("symbol", "").strip().upper().removesuffix(".US")
    symbol = raw_symbol if raw_symbol and raw_symbol != "全部股票" else "全部股票"
    sessions = load_ai_stock_sessions()
    history = sessions.get(symbol, [])

    welcome_message = None
    if not history:
        if symbol != "全部股票":
            welcome_message = (
                f"您好！已为您切换至 **{symbol}** 的纯股票深度研究频道。\n\n"
                f"• **研究标的**：**{symbol}**\n"
                f"• **纯粹独立**：本页面**不依赖您的个人数据库账本**，专注于深入剖析 **{symbol}** 这家公司本身的商业模式、财报财务健康度、技术壁垒、竞争护城河与未来成长前景。\n\n"
                f"您可以直接针对 **{symbol}** 的近期财报、核心业务亮点或未来催化剂提出任何疑问。"
            )
        else:
            welcome_message = (
                f"您好！已为您进入 **全市场与行业宏观** 股票分析频道。\n\n"
                f"• **当前范围**：全部股票（宏观行业研究模式）\n"
                f"• **独立隔离**：在此处提出的问题聚焦于宏观市场、行业板块趋势或赛道对比。\n\n"
                f"如需针对某只具体标的（如 INTC、NVDA、AAPL）进行深入研报诊断，只需在上方「输入 Ticker」中选择即可。"
            )

    formatted = []
    for m in history:
        formatted.append({
            "role": m.get("role", "user"),
            "content": m.get("content", m.get("text", "")),
            "text": m.get("content", m.get("text", "")),
            "time_str": m.get("time_str", m.get("time", "")),
            "time": m.get("time_str", m.get("time", "")),
            "symbol_tag": m.get("symbol_tag", symbol),
        })

    return jsonify({
        "status": "ok",
        "symbol": symbol,
        "history": formatted,
        "welcome_message": welcome_message,
    })


@app.route("/api/ai/stock/chat", methods=["POST"])
def api_ai_stock_chat():
    """Execute pure stock fundamental research via Codex and persist to disk (shared with desktop)."""
    try:
        data = request.get_json() or {}
        user_message = data.get("message", "").strip()
        raw_symbol = data.get("symbol", "").strip().upper().removesuffix(".US")
        symbol = raw_symbol if raw_symbol and raw_symbol != "全部股票" else "全部股票"
        model = data.get("model") or None

        if not user_message:
            return jsonify({"status": "error", "message": "消息内容不能为空"}), 400

        reply = CodexAIService.ask_stock(
            user_message,
            symbol=symbol if symbol != "全部股票" else None,
            model=model,
        )

        time_str = datetime.now().strftime("%H:%M:%S")

        sessions = load_ai_stock_sessions()
        stock_msgs = sessions.setdefault(symbol, [])
        stock_msgs.append({
            "role": "user",
            "content": user_message,
            "time_str": time_str,
            "symbol_tag": symbol,
        })
        stock_msgs.append({
            "role": "assistant",
            "content": reply,
            "time_str": time_str,
            "symbol_tag": symbol,
        })
        save_ai_stock_sessions(sessions)

        return jsonify({
            "status": "ok",
            "symbol": symbol,
            "reply": reply,
            "time": time_str,
        })
    except Exception as e:
        logger.exception("AI stock analysis failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/stock/clear", methods=["POST"])
def api_ai_stock_clear():
    """Clear pure stock fundamental research history for a symbol on disk."""
    data = request.get_json() or {}
    raw_symbol = data.get("symbol", "").strip().upper().removesuffix(".US")
    symbol = raw_symbol if raw_symbol and raw_symbol != "全部股票" else "全部股票"
    sessions = load_ai_stock_sessions()
    sessions[symbol] = []
    save_ai_stock_sessions(sessions)
    return jsonify({"status": "ok", "message": f"已清空【{symbol}】研报分析历史", "symbol": symbol})


@app.route("/api/network/info", methods=["GET"])
def api_network_info():
    """Return local WiFi IP and Tailscale public IP for mobile setup."""
    return jsonify({"status": "ok", **get_network_info()})


@app.route("/api/overview", methods=["GET"])
def api_overview():
    """Calculate portfolio summary, holdings, channels and category breakdown for current user."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        holdings_report = res.reports.get("当前持仓")
        rows = holdings_report.rows if holdings_report else ()

        total_value = sum(Decimal(str(r.values[5])) for r in rows if r.values[5] is not None)
        total_cost = sum(Decimal(str(r.values[9])) for r in rows if r.values[9] is not None)
        total_pnl = sum(Decimal(str(r.values[10])) for r in rows if r.values[10] is not None)
        pnl_pct = (total_pnl / total_cost * 100) if total_cost else Decimal("0")

        # Today's PnL from 每日持仓收益 if available
        daily_pnl = 0.0
        daily_rep = res.reports.get("每日持仓收益")
        if daily_rep and daily_rep.rows:
            latest_day = daily_rep.rows[-1].values[0]
            daily_rows = [r for r in daily_rep.rows if r.values[0] == latest_day]
            try:
                day_sum = sum(Decimal(str(r.values[18])) for r in daily_rows if r.values[18] is not None)
                daily_pnl = float(day_sum)
            except Exception:
                pass

        holdings = []
        for r in rows:
            channel = str(r.values[0] or "")
            category = str(r.values[1] or "")
            sym = str(r.values[2] or "").removesuffix(".US")
            shares = safe_float(r.values[3])
            price = safe_float(r.values[4])
            val = safe_float(r.values[5])
            cost = safe_float(r.values[6])
            net = safe_float(r.values[9])
            pnl = safe_float(r.values[10])
            pnl_p = safe_float(r.values[11])
            pnl_pct_val = (pnl_p * 100.0) if pnl_p is not None else 0.0

            holdings.append({
                "channel": channel,
                "category": category,
                "symbol": sym,
                "shares": shares,
                "price": price,
                "value": val,
                "cost": cost,
                "net": net,
                "pnl": pnl,
                "pnl_pct": pnl_pct_val,
            })

        holdings.sort(key=lambda x: (x["value"] or 0), reverse=True)

        channel_data = {}
        for h in holdings:
            ch = h["channel"] or "其他"
            channel_data[ch] = channel_data.get(ch, 0.0) + (h["value"] or 0.0)

        cat_data = {}
        for h in holdings:
            cat = h["category"] or "其他"
            cat_data[cat] = cat_data.get(cat, 0.0) + (h["value"] or 0.0)

        # Snippet of monthly trend for overview sparkline
        monthly_trend = []
        trend_rep = res.reports.get("月度趋势")
        if trend_rep and trend_rep.rows:
            for r in trend_rep.rows[-6:]:
                monthly_trend.append({
                    "month": str(r.values[0]),
                    "monthly_pnl": safe_float(r.values[1]),
                    "cum_pnl": safe_float(r.values[2]),
                    "net_inflow": safe_float(r.values[3]),
                    "total_asset": safe_float(r.values[5]),
                })

        u = get_current_user()
        return jsonify({
            "status": "ok",
            "as_of": date.today().isoformat(),
            "user": u,
            "total_value": float(total_value),
            "total_cost": float(total_cost),
            "total_pnl": float(total_pnl),
            "pnl_pct": float(pnl_pct),
            "daily_pnl": daily_pnl,
            "holdings_count": len(holdings),
            "holdings": holdings[:10],
            "channel_breakdown": channel_data,
            "category_breakdown": cat_data,
            "monthly_trend": monthly_trend,
        })
    except Exception as e:
        logger.exception("Failed to calculate overview")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/reports/list", methods=["GET"])
def api_reports_list():
    """Return categorized list of all 28 desktop reports filtered for current user."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        groups = [
            {
                "group": "资产管理",
                "reports": [
                    {"name": "当前持仓", "desc": "当前持仓明细、市值、浮动盈亏与占比"},
                    {"name": "股票汇总", "desc": "全股票历史与当前状态汇总"},
                    {"name": "渠道汇总", "desc": "各券商渠道资产、本金与收益分布"},
                    {"name": "分类汇总", "desc": "科技、大盘等资产分类分布"},
                    {"name": "人员资产", "desc": "按家庭/个人归属分类资产"},
                    {"name": "账户资产", "desc": "按券商具体独立账户汇总"},
                    {"name": "收益日历", "desc": "周一至周五每日盈亏矩阵日历"},
                    {"name": "季度矩阵", "desc": "各季度收益与年化收益矩阵"},
                ],
            },
            {
                "group": "交易与收益",
                "reports": [
                    {"name": "交易回放", "desc": "历史完整交易执行明细流水"},
                    {"name": "清仓收益", "desc": "已完成全部平仓标的累计历史实现收益"},
                    {"name": "每日持仓收益", "desc": "各标的每日数量变化与当日盈亏明细"},
                    {"name": "每月持仓收益", "desc": "各标的月度数量变化与月度盈亏"},
                    {"name": "每月微操收益", "desc": "做T日内微操统计与累计做T贡献"},
                    {"name": "微操配对证据", "desc": "做T日内微操逐笔买卖配对记录"},
                    {"name": "月度净投入明细", "desc": "每月各标的买入/卖出净资金流向"},
                    {"name": "完整交易净投入", "desc": "全周期交易净投入明细"},
                    {"name": "月度趋势", "desc": "月度收益、累计收益、总资产演进"},
                ],
            },
            {
                "group": "系统与记录",
                "reports": [
                    {"name": "导入批次", "desc": "历史数据源导入批次与状态"},
                    {"name": "资金事件", "desc": "出入金、分红等资金流水"},
                    {"name": "资产调整", "desc": "资产手工校准与调整记录"},
                    {"name": "指标说明", "desc": "财务与风控计算指标口径定义"},
                    {"name": "总览取值逻辑", "desc": "总览指标与数据源对应逻辑"},
                ],
            },
        ]

        for g in groups:
            for item in g["reports"]:
                rep = res.reports.get(item["name"])
                item["row_count"] = len(rep.rows) if rep else 0

        u = get_current_user()
        return jsonify({"status": "ok", "user": u, "groups": groups})
    except Exception as e:
        logger.exception("Failed to get reports list")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/report/<report_name>", methods=["GET"])
def api_get_report(report_name: str):
    """Retrieve tabular data for any specified report filtered by current user."""
    try:
        svc = get_analytics_service()
        currency = request.args.get("currency", "USD")
        scope = get_user_query_scope(currency=currency)
        res = svc.calculate(scope)

        rep = res.reports.get(report_name)
        if not rep:
            return jsonify({"status": "error", "message": f"未找到报表: {report_name}"}), 404

        columns = list(rep.columns)

        search = request.args.get("search", "").strip().lower()
        sort_col_idx = request.args.get("sort_col", None)
        sort_dir = request.args.get("sort_dir", "asc").lower()
        page = max(1, int(request.args.get("page", 1)))
        page_size = int(request.args.get("page_size", 50))

        all_rows = []
        for r in rep.rows:
            row_vals = [serialize_val(v) for v in r.values]
            if search:
                match = False
                for cell in row_vals:
                    if cell is not None and search in str(cell).lower():
                        match = True
                        break
                if not match:
                    continue
            all_rows.append(row_vals)

        if sort_col_idx is not None:
            try:
                col_idx = int(sort_col_idx)
                if 0 <= col_idx < len(columns):
                    reverse = (sort_dir == "desc")

                    def sort_key(row):
                        v = row[col_idx]
                        if v is None:
                            return (1, 0)
                        if isinstance(v, (int, float)):
                            return (0, v)
                        return (0, str(v))

                    all_rows.sort(key=sort_key, reverse=reverse)
            except Exception:
                pass

        total_count = len(all_rows)

        if page_size > 0:
            total_pages = max(1, math.ceil(total_count / page_size))
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_rows = all_rows[start_idx:end_idx]
        else:
            total_pages = 1
            page_rows = all_rows

        u = get_current_user()
        return jsonify({
            "status": "ok",
            "user": u,
            "name": report_name,
            "columns": columns,
            "rows": page_rows,
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        })
    except Exception as e:
        logger.exception(f"Failed to load report {report_name}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/charts", methods=["GET"])
def api_charts():
    """Serialize all 27 desktop analytical charts into ECharts-ready format for current user."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        selected_month = request.args.get("month", None)
        charts = build_charts(res, selected_month=selected_month)

        payload = []
        for c in charts:
            series_list = []
            for s in c.series:
                points = [[p[0], serialize_val(p[1])] for p in s.points]
                series_list.append({"name": s.name, "points": points})

            payload.append({
                "id": c.id,
                "title": c.title,
                "kind": c.kind,
                "note": c.note,
                "ratio": c.ratio,
                "link": c.link,
                "series": series_list,
                "x_ticks": list(c.x_ticks),
                "point_labels": c.point_labels,
                "vertical_lines": [list(vl) for vl in c.vertical_lines],
            })

        u = get_current_user()
        return jsonify({"status": "ok", "user": u, "charts": payload, "count": len(payload)})
    except Exception as e:
        logger.exception("Failed to build charts")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/trades", methods=["GET"])
def api_trades():
    """Retrieve paginated trade transactions strictly filtered for current user."""
    try:
        ts = get_trades_service()
        scope = get_user_query_scope()

        page = max(1, int(request.args.get("page", 1)))
        size = max(1, min(100, int(request.args.get("size", 30))))
        symbol = request.args.get("symbol", "").strip().upper() or None
        broker = request.args.get("broker", "").strip().upper() or None
        side = request.args.get("side", "").strip() or None

        filters = {}
        if symbol:
            filters["symbol"] = symbol
        if broker:
            filters["broker_code"] = broker
        if side:
            filters["legacy_direction"] = side

        trade_page = ts.get_trades(scope, page=page, size=size, filters=filters if filters else None)

        rows = []
        for r in trade_page.rows:
            rows.append({
                "record_id": r.record_id,
                "time": str(r.values[0]),
                "broker": str(r.values[1]),
                "category": str(r.values[2]),
                "symbol": str(r.values[3]),
                "direction": str(r.values[4]),
                "quantity": safe_float(r.values[5]),
                "price": safe_float(r.values[6]),
                "amount": safe_float(r.values[7]),
                "post_quantity": safe_float(r.values[9]),
                "post_net": safe_float(r.values[10]),
                "post_cost": safe_float(r.values[11]),
                "order_id": str(r.values[14] or ""),
            })

        u = get_current_user()
        return jsonify({
            "status": "ok",
            "user": u,
            "total": trade_page.total,
            "page": trade_page.page,
            "size": trade_page.size,
            "total_pages": math.ceil(trade_page.total / trade_page.size) if trade_page.size else 1,
            "rows": rows,
        })
    except Exception as e:
        logger.exception("Failed to get trades")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/monitoring/symbols", methods=["GET"])
def api_monitoring_symbols():
    """Retrieve all available symbols in monitoring database with real-time indicators and trend status."""
    try:
        store = get_monitor_store()
        if not store:
            return jsonify({"status": "error", "message": "监控数据库未就绪"}), 404

        with store.connect() as db:
            cur = db.execute("SELECT DISTINCT analysis_symbol FROM daily_bars ORDER BY analysis_symbol")
            symbols = [r[0] for r in cur.fetchall()]

        items = []
        for s in symbols:
            clean_sym = s.removesuffix(".US")
            try:
                bars = store.bars(s)
                if not bars:
                    continue
                last_bar = bars[-1]
                last, _ = technicals(bars)

                rsi_val = float(last["RSI14"]) if last.get("RSI14") is not None else None
                ma5_dev = float(last.get("DEV5", 0.0)) if last.get("DEV5") is not None else 0.0

                items.append({
                    "symbol": clean_sym,
                    "market_symbol": s,
                    "price": float(last_bar.close),
                    "day": str(last_bar.day),
                    "bars_count": len(bars),
                    "trend_short": last.get("SHORT", "震荡"),
                    "trend_medium": last.get("MEDIUM", "震荡"),
                    "trend_long": last.get("LONG", "震荡"),
                    "cross": last.get("CROSS", "无交叉"),
                    "rsi": round(rsi_val, 1) if rsi_val is not None else None,
                    "ma5_dev": round(ma5_dev, 2),
                    "high52": float(last["HIGH52"]) if last.get("HIGH52") is not None else None,
                    "low52": float(last["LOW52"]) if last.get("LOW52") is not None else None,
                    "drawdown": float(last["DRAWDOWN"]) if last.get("DRAWDOWN") is not None else None,
                })
            except Exception as e:
                logger.warning(f"Error computing technicals for {s}: {e}")

        return jsonify({"status": "ok", "symbols": items, "count": len(items)})
    except Exception as e:
        logger.exception("Failed to get monitoring symbols")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/monitoring/kline", methods=["GET"])
def api_monitoring_kline():
    """Return full candlestick OHLCV and technical indicators (MA, BOLL, MACD, RSI) for a specific symbol."""
    try:
        store = get_monitor_store()
        if not store:
            return jsonify({"status": "error", "message": "监控数据库未就绪"}), 404

        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"status": "error", "message": "缺少标的代码参数"}), 400

        search_keys = [symbol, f"{symbol}.US", symbol.removesuffix(".US")]
        bars = None
        target_sym = symbol
        for key in search_keys:
            candidate = store.bars(key)
            if candidate:
                bars = candidate
                target_sym = key
                break

        if not bars:
            return jsonify({
                "status": "error",
                "message": f"标的 {symbol} 暂无离线日K历史数据。请先在桌面端行情监控中心添加并同步。",
            }), 404

        last, data = technicals(bars)

        dates = [b.day.isoformat() for b in bars]
        ohlcv = [[float(b.open), float(b.close), float(b.low), float(b.high), float(b.volume)] for b in bars]
        volumes = [float(b.volume) for b in bars]

        def clean_series(lst):
            return [float(x) if x is not None else None for x in lst]

        ma_data = {
            "ma5": clean_series(data.get("MA5", [])),
            "ma10": clean_series(data.get("MA10", [])),
            "ma20": clean_series(data.get("MA20", [])),
            "ma50": clean_series(data.get("MA50", [])),
            "ma200": clean_series(data.get("MA200", [])),
        }

        boll_data = {
            "mid": clean_series(data.get("BOLL_MID", [])),
            "up": clean_series(data.get("BOLL_UP", [])),
            "low": clean_series(data.get("BOLL_LOW", [])),
        }

        macd_data = {
            "dif": clean_series(data.get("DIF", [])),
            "dea": clean_series(data.get("DEA", [])),
            "bar": clean_series(data.get("MACD", [])),
        }

        rsi_data = clean_series(data.get("RSI14", []))

        vma_data = {
            "vma5": clean_series(data.get("VMA5", [])),
            "vma20": clean_series(data.get("VMA20", [])),
        }

        clean_technicals = {}
        for k, v in last.items():
            clean_technicals[k] = serialize_val(v)

        return jsonify({
            "status": "ok",
            "symbol": target_sym.removesuffix(".US"),
            "full_symbol": target_sym,
            "dates": dates,
            "ohlcv": ohlcv,
            "volumes": volumes,
            "ma": ma_data,
            "boll": boll_data,
            "macd": macd_data,
            "rsi": rsi_data,
            "vma": vma_data,
            "technicals": clean_technicals,
            "latest_close": float(bars[-1].close),
            "latest_date": str(bars[-1].day),
        })
    except Exception as e:
        logger.exception("Failed to get K-line data")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/monitoring", methods=["GET"])
def api_monitoring():
    """Retrieve watch targets, technical rules, and recent alert events."""
    try:
        store = get_monitor_store()
        if not store:
            return jsonify({"status": "ok", "targets": [], "rules": [], "alerts": []})

        with store.connect() as db:
            cur = db.execute("SELECT * FROM watch_targets ORDER BY analysis_symbol")
            raw_targets = [dict(r) for r in cur.fetchall()]

        rules = store.get_rules()
        events = store.get_alert_events(limit=50)

        seen = set()
        clean_targets = []
        for t in raw_targets:
            sym = t.get("analysis_symbol", "")
            if sym in seen:
                continue
            seen.add(sym)
            clean_targets.append({
                "analysis_symbol": sym.removesuffix(".US"),
                "full_symbol": sym,
                "source_type": t.get("source_type", "HELD"),
                "archive_state": t.get("archive_state", "HELD"),
            })

        clean_rules = []
        for r in rules:
            clean_rules.append({
                "rule_id": r.rule_id,
                "name": r.name,
                "analysis_symbol": r.analysis_symbol.removesuffix(".US"),
                "monitored_symbol": (r.monitored_symbol or "").removesuffix(".US"),
                "rule_type": r.rule_type,
                "state": r.state,
                "repeat_mode": r.repeat_mode,
                "threshold_value": r.threshold_value,
                "trigger_count": r.trigger_count,
                "last_triggered_at": r.last_triggered_at,
            })

        clean_events = []
        for e in events:
            clean_events.append({
                "event_id": e.event_id,
                "rule_name": e.rule_name,
                "analysis_symbol": e.analysis_symbol.removesuffix(".US"),
                "detected_at": e.detected_at,
                "observed_price": e.observed_price,
                "trigger_reason": e.trigger_reason,
                "read_at": e.read_at,
            })

        return jsonify({
            "status": "ok",
            "targets": clean_targets,
            "rules": clean_rules,
            "alerts": clean_events,
        })
    except Exception as e:
        logger.exception("Failed to get monitoring data")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/rules/<rule_id>/toggle", methods=["POST"])
def api_toggle_rule(rule_id: str):
    """Toggle a monitoring rule between ACTIVE and PAUSED."""
    try:
        store = get_monitor_store()
        if not store:
            return jsonify({"status": "error", "message": "监控数据库未就绪"}), 404

        rule = store.get_rule(rule_id)
        if not rule:
            return jsonify({"status": "error", "message": "未找到指定规则"}), 404
        new_enabled = (rule.state != "ACTIVE")
        store.set_rule_enabled(rule_id, new_enabled)
        return jsonify({"status": "ok", "rule_id": rule_id, "state": "ACTIVE" if new_enabled else "PAUSED"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """Handle conversational questions with portfolio context strictly isolated to current user."""
    try:
        data = request.get_json() or {}
        user_message = data.get("message", "").strip()
        req_symbol = data.get("symbol", "").strip().upper().removesuffix(".US")
        req_channel = data.get("channel", "").strip()
        req_person = data.get("person", "").strip()
        include_context = bool(data.get("include_context", True))
        model = data.get("model") or None

        if not user_message:
            return jsonify({"status": "error", "message": "消息内容不能为空"}), 400

        person_id, person_name = get_current_user_scope()
        if person_id is not None:
            # Strictly locked to current user (e.g. 拉科)
            target_person = person_name
        else:
            # Admin user
            target_person = req_person if req_person and req_person not in ("全部启用人员", "全部人员") else None

        target_channels = (req_channel,) if req_channel and req_channel != "全部渠道" else None
        target_symbol = req_symbol if req_symbol and req_symbol != "全部股票" else None

        context_str = ""
        if include_context:
            svc = get_analytics_service()
            scope = get_user_query_scope()
            analytics = svc.calculate(scope)
            context_str = CodexAIService.build_portfolio_context(
                analytics,
                person_name=target_person,
                channel_names=target_channels,
                symbol=target_symbol,
            )

        reply = CodexAIService.ask(user_message, context=context_str, model=model)

        time_str = datetime.now().strftime("%H:%M:%S")

        scope_desc = [
            f"{target_person or '全部人员'}",
            f"{req_channel or '全部渠道'}",
            f"{target_symbol or '全部股票'}",
        ]
        scope_tag = " · ".join(scope_desc)
        session_key = f"{target_person or '全部人员'}___{req_channel or '全部渠道'}___{target_symbol or '全部股票'}"

        # Persist to disk matching desktop AIAssistantPage
        sessions = load_ai_chat_sessions()
        session_msgs = sessions.setdefault(session_key, [])
        session_msgs.append({
            "role": "user",
            "content": user_message,
            "time_str": time_str,
            "scope_tag": scope_tag,
        })
        session_msgs.append({
            "role": "assistant",
            "content": reply,
            "time_str": time_str,
            "scope_tag": scope_tag,
        })
        save_ai_chat_sessions(sessions)

        return jsonify({
            "status": "ok",
            "reply": reply,
            "symbol": target_symbol,
            "scope_tag": scope_tag,
            "session_key": session_key,
            "time": time_str,
        })
    except Exception as e:
        logger.exception("AI chat failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/analyze_stock", methods=["POST"])
def api_ai_analyze_stock():
    """Generate in-depth AI investment research report for a specific stock with user portfolio scope."""
    try:
        data = request.get_json() or {}
        symbol = data.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"status": "error", "message": "请指定股票代码"}), 400

        svc = get_analytics_service()
        person_id, person_name = get_current_user_scope()
        scope = get_user_query_scope()
        analytics = svc.calculate(scope)

        context_str = CodexAIService.build_portfolio_context(
            analytics,
            person_name=person_name or "个人",
            symbol=symbol,
        )

        prompt = f"请针对股票 {symbol} 进行深度技术面与基本面投资分析，结合我的当前持仓及近期技术指标给出操作建议。"
        reply = CodexAIService.ask(prompt, context=context_str)

        return jsonify({
            "status": "ok",
            "symbol": symbol,
            "report": reply,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        logger.exception("AI stock analysis failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bark/test", methods=["POST"])
def api_bark_test():
    """Trigger a test push notification to user's iPhone."""
    try:
        data = request.get_json() or {}
        url = data.get("url", "").strip() or notification_service.bark_url

        if not url:
            return jsonify({"status": "error", "message": "请先填写 Bark 推送链接 (如 https://api.day.app/xxxx/)"}), 400

        notification_service.set_bark_url(url)
        notification_service.send_bark_notification(
            title="Money3 · 移动端联动成功 🎉",
            message="恭喜！您的 iPhone 已成功连接 Money3 行情预警系统。盘中股票突破或异动将在此第一时间通知！",
            sound="alarm.caf",
            group="Money3连接测试",
        )
        return jsonify({"status": "ok", "message": "测试推送已发出，请查看 iPhone 锁屏通知！"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bark/config", methods=["POST"])
def api_bark_config():
    """Persist user's Bark URL to api_config.json."""
    try:
        data = request.get_json() or {}
        url = data.get("url", "").strip()

        cfg = {}
        if API_CONFIG_PATH.is_file():
            try:
                with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        cfg["bark"] = {
            "enabled": bool(url),
            "url": url,
        }

        with open(API_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

        notification_service.set_bark_url(url)
        return jsonify({"status": "ok", "message": "Bark 配置已成功保存！", "url": url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def run_mobile_server(host: str = "0.0.0.0", port: int = 8888, debug: bool = False):
    """Start the mobile web server."""
    net = get_network_info()
    print("==================================================================")
    print("  🚀 Money Portfolio 移动端全量工作站已就绪！")
    print(f"  • 本机访问:  http://127.0.0.1:{port}")
    print(f"  • 家中同WiFi: http://{net['lan_ip']}:{port}")
    if net["tailscale_ip"]:
        print(f"  • 外出公网 (Tailscale): http://{net['tailscale_ip']}:{port}")
    else:
        print("  • 外出公网: 未检测到 Tailscale，已支持同局域网访问，安装 Tailscale 后自动生效公网")
    print("  • 支持用户: 拉科 (lake) / admin (admin)")
    print("==================================================================")
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_mobile_server()
