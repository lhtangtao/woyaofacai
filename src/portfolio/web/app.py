"""Money Portfolio Mobile Web & PWA Server.

Provides a responsive, touch-optimized web interface for iPhone / iPad,
exposing full portfolio holdings, desktop analytical reports (28 reports),
27 ECharts financial charts, professional K-line candlestick charts with
technical indicators, AI investment advising, Bark push notifications,
and multi-user role-based authentication with account isolation (e.g. 拉科).

Security & Access Control (PRD v2.0):
- Same Physical LAN: Password-free read-only access (LAN_VIEWER).
- Remote / Tailscale / 5G: Strict password authentication required.
- Password hashing with scrypt, persistent random FLASK_SECRET_KEY,
  server-side session management, anti-brute-force rate limiting, and CSRF protection.
- Original financial database & Excel strictly read-only.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
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

from flask import Flask, g, jsonify, render_template, request, session

from portfolio.domain.models import Ledger, QueryScope
from portfolio.domain.technical_indicators import DailyBar, series, technicals
from portfolio.paths import API_CONFIG_PATH, DATABASE_PATH, PROJECT_DIR
from portfolio.services.ai_service import CodexAIService
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.charts_service import build_charts
from portfolio.services.monitor_store import MonitorStore
from portfolio.services.notification_service import notification_service
from portfolio.services.trades_service import TradesService

# Security modules
from portfolio.web.security.audit import record_audit_event
from portfolio.web.security.auth_context import (
    ADMIN_CAPABILITIES,
    CAP_AI_CHAT,
    CAP_AI_STOCK,
    CAP_AUDIT_LOGS,
    CAP_MANAGE_BARK,
    CAP_MANAGE_RULES,
    CAP_MANAGE_USERS,
    CAP_VIEW_ASSETS,
    CAP_VIEW_MARKET,
    AuthContext,
    build_auth_context,
)
from portfolio.web.security.credentials import (
    create_user,
    get_user_by_id,
    get_user_by_username,
    init_security_db,
    is_login_rate_limited,
    list_users,
    record_login_attempt,
    set_user_password,
    toggle_user_enabled,
    verify_password,
)
from portfolio.web.security.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    generate_csrf_token,
    validate_csrf,
)
from portfolio.web.security.network_access import classify_network_access
from portfolio.web.security.sessions import (
    create_session,
    get_flask_secret_key,
    revoke_all_user_sessions,
    revoke_session,
    validate_session,
)

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "money_session_id"

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# SEC-04 & AC-18: Persistent, high-entropy secret key without weak hardcoded fallback
app.secret_key = get_flask_secret_key()

# Ensure security database is initialized
init_security_db()


# ------------------------------------------------------------------------------
# Request Hooks for Authentication, Authorization & CSRF (PRD 5.1, 7.1, 6.2)
# ------------------------------------------------------------------------------


@app.before_request
def authenticate_and_authorize_request():
    """Evaluate network origin, session token, and compute AuthContext for each request."""
    # 1. Server-side network origin classification
    access_mode = classify_network_access(request.remote_addr, request.headers)

    # 2. Extract and validate server-side session from HTTP-only cookie
    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    session_user = validate_session(raw_session_token) if raw_session_token else None

    # 3. Construct immutable AuthContext
    g.auth_context = build_auth_context(
        access_mode=access_mode,
        session_user=session_user,
        session_id=raw_session_token,
    )

    # 4. Enforce CSRF protection for mutating requests (POST, PUT, DELETE, PATCH)
    is_csrf_valid, csrf_err = validate_csrf(request, allow_login_exemption=True)
    if not is_csrf_valid:
        record_audit_event(
            "CSRF_FAILURE",
            g.auth_context.username,
            access_mode,
            request.remote_addr,
            "403_CSRF",
            {"path": request.path, "err": csrf_err},
        )
        return jsonify({"status": "error", "message": csrf_err, "code": "CSRF_ERROR"}), 403

    # 5. Ensure a consistent CSRF token exists for template rendering and response cookie
    g.csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not g.csrf_token:
        g.csrf_token = generate_csrf_token()
        g.needs_csrf_cookie = True
    else:
        g.needs_csrf_cookie = False


@app.after_request
def inject_security_headers_and_csrf(response):
    """Set CSRF cookie and standard security response headers."""
    # Ensure client has fresh CSRF cookie matching g.csrf_token if not already in request cookies
    if getattr(g, "needs_csrf_cookie", False) or CSRF_COOKIE_NAME not in request.cookies:
        token = getattr(g, "csrf_token", None) or generate_csrf_token()
        is_secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        response.set_cookie(
            CSRF_COOKIE_NAME,
            token,
            httponly=False,  # Needs to be readable by JS for X-CSRF-Token header
            samesite="Lax",
            secure=is_secure,
            path="/",
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


# ------------------------------------------------------------------------------
# Authorization Decorators (SEC-06)
# ------------------------------------------------------------------------------


def require_capability(cap: str):
    """Decorator to enforce capability requirement on endpoint."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            ctx: AuthContext = getattr(g, "auth_context", None)
            if not ctx:
                return jsonify({"status": "error", "message": "未初始化认证上下文"}), 500

            # Remote unauthenticated -> 401
            if ctx.is_unauthenticated():
                record_audit_event(
                    "ACCESS_DENIED",
                    None,
                    ctx.access_mode,
                    request.remote_addr,
                    "401_UNAUTHENTICATED",
                    {"endpoint": request.path, "required_capability": cap},
                )
                return jsonify({
                    "status": "error",
                    "message": "当前处于远程访问环境，需要账号密码登录后查看",
                    "code": "UNAUTHENTICATED",
                }), 401

            # Authenticated or LAN_VIEWER, but lacks specific capability -> 403
            if not ctx.has_capability(cap):
                record_audit_event(
                    "ACCESS_DENIED",
                    ctx.username,
                    ctx.access_mode,
                    request.remote_addr,
                    "403_FORBIDDEN",
                    {"endpoint": request.path, "required_capability": cap},
                )
                return jsonify({
                    "status": "error",
                    "message": "当前身份为只读权限，不允许执行此操作。请使用有权限的账号登录。",
                    "code": "FORBIDDEN",
                }), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def local_only(f):
    """Decorator to restrict endpoint to Mac local loopback (127.0.0.1) only."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ctx: AuthContext = getattr(g, "auth_context", None)
        if not ctx or ctx.access_mode != "LOCAL_LOOPBACK":
            return jsonify({
                "status": "error",
                "message": "此安全操作仅限在运行 Money Portfolio 的 Mac 本机上执行",
                "code": "LOCAL_ONLY_FORBIDDEN",
            }), 403
        return f(*args, **kwargs)
    return decorated_function


# ------------------------------------------------------------------------------
# User & Scope Resolution Helpers
# ------------------------------------------------------------------------------


def get_current_user() -> Dict[str, Any]:
    """Return safe user profile dictionary for current request context."""
    ctx: AuthContext = getattr(g, "auth_context", None)
    if ctx:
        return ctx.to_dict()
    # Fallback to safe guest context if called outside request
    return {
        "username": "局域网访客",
        "role": "guest",
        "principal_type": "LAN_VIEWER",
        "person_id": None,
        "person_name": "局域网访客",
        "is_authenticated": False,
        "login_required": False,
    }


def get_current_user_scope() -> Tuple[Optional[int], Optional[str]]:
    """Return (person_id, person_name) tuple for current user context."""
    ctx: AuthContext = getattr(g, "auth_context", None)
    if ctx and ctx.allowed_person_ids and len(ctx.allowed_person_ids) == 1:
        return ctx.allowed_person_ids[0], ctx.person_name
    return None, None


def get_user_query_scope(as_of: Optional[date] = None, currency: str = "USD") -> QueryScope:
    """Build QueryScope strictly filtered by current user's authorized person_id.

    SEC-01 & SEC-02 & PRD 4.4:
    - USER (e.g. 拉科): strictly locked to person_id=5.
    - ADMIN & LAN_VIEWER: full portfolio scope.
    """
    ctx: AuthContext = getattr(g, "auth_context", None)
    person_ids = None

    if ctx and ctx.principal_type == "USER":
        # Force strict isolation for regular users
        person_ids = ctx.allowed_person_ids
    elif ctx and ctx.principal_type in ("ADMIN", "LAN_VIEWER"):
        # Full view by default
        person_ids = None

    return QueryScope(
        as_of=as_of or date.today(),
        reporting_currency=currency,
        ledger=Ledger.PERSON,
        person_ids=person_ids,
    )


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


# Shared service references
_analytics_svc: Optional[AnalyticsService] = None
_trades_svc: Optional[TradesService] = None
_monitor_store: Optional[MonitorStore] = None


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
    """Retrieve local network addresses including physical LAN and Tailscale."""
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


# ------------------------------------------------------------------------------
# Web Index Route
# ------------------------------------------------------------------------------


@app.route("/")
def index():
    net = get_network_info()
    ctx: AuthContext = getattr(g, "auth_context", None)
    user_data = ctx.to_dict() if ctx else get_current_user()

    csrf_token = getattr(g, "csrf_token", None) or request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()

    return render_template(
        "mobile.html",
        lan_ip=net["lan_ip"],
        tailscale_ip=net["tailscale_ip"],
        all_lan_ips=net["all_lan_ips"],
        port=net["port"],
        bark_url=notification_service.bark_url or "",
        current_user=user_data,
        is_remote_login=ctx.is_unauthenticated() if ctx else False,
        is_lan_viewer=ctx.is_lan_viewer() if ctx else False,
        csrf_token=csrf_token,
    )


# ------------------------------------------------------------------------------
# Authentication & User Management Routes (PRD 6.1, 6.2, 7.2)
# ------------------------------------------------------------------------------


@app.route("/api/auth/csrf", methods=["GET"])
def api_auth_csrf():
    """Return fresh CSRF token, supporting force refresh."""
    force_refresh = request.args.get("refresh") in ("1", "true")
    token = generate_csrf_token() if force_refresh else (request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token())
    is_secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    resp = jsonify({"status": "ok", "csrf_token": token})
    resp.set_cookie(CSRF_COOKIE_NAME, token, httponly=False, samesite="Lax", secure=is_secure, path="/")
    return resp


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Authenticate user with username and password, issuing secure server-side session."""
    client_ip = request.remote_addr or "127.0.0.1"
    try:
        data = request.get_json(silent=True) or {}
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify({"status": "error", "message": "账号或密码不能为空"}), 400

        # Rate limiting check (AC-22)
        if is_login_rate_limited(client_ip, username):
            record_audit_event("LOGIN_FAILED", username, g.auth_context.access_mode, client_ip, "429_RATE_LIMITED")
            return jsonify({"status": "error", "message": "登录失败次数过多，请 15 分钟后再试"}), 429

        # Scrypt password verification against security database
        user = get_user_by_username(username)
        if not user or not verify_password(password, user["password_hash"]):
            record_login_attempt(client_ip, username, success=False)
            record_audit_event("LOGIN_FAILED", username, g.auth_context.access_mode, client_ip, "INVALID_CREDENTIALS")
            return jsonify({"status": "error", "message": "账号或密码错误"}), 401

        if user.get("enabled", 1) != 1:
            record_login_attempt(client_ip, username, success=False)
            record_audit_event("LOGIN_FAILED", username, g.auth_context.access_mode, client_ip, "ACCOUNT_DISABLED")
            return jsonify({"status": "error", "message": "该账号已被停用，请联系管理员"}), 403

        # Record successful login
        record_login_attempt(client_ip, username, success=True)

        # Create server-side session
        raw_session_token = create_session(
            user_id=user["id"],
            username=user["username"],
            role=user["role"],
            person_id=user.get("person_id"),
            person_name=user.get("person_name"),
            ip_address=client_ip,
            user_agent=request.user_agent.string,
        )

        record_audit_event("LOGIN_SUCCESS", username, g.auth_context.access_mode, client_ip, "SUCCESS")

        is_secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        resp = jsonify({
            "status": "ok",
            "message": f"欢迎，{username}！",
            "user": {
                "id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "person_id": user.get("person_id"),
                "person_name": user.get("person_name"),
                "is_authenticated": True,
            },
        })

        # Set secure HTTP-only session cookie
        resp.set_cookie(
            SESSION_COOKIE_NAME,
            raw_session_token,
            httponly=True,
            samesite="Lax",
            secure=is_secure,
            path="/",
            max_age=7 * 86400,
        )
        return resp
    except Exception as e:
        logger.exception("Login error")
        return jsonify({"status": "error", "message": "服务器处理登录异常"}), 500


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    """Return current request authorization context and user profile."""
    ctx: AuthContext = getattr(g, "auth_context", None)
    return jsonify({"status": "ok", "user": ctx.to_dict() if ctx else get_current_user()})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Revoke session token and reset to LAN_VIEWER or UNAUTHENTICATED (PRD 6.3)."""
    raw_session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_session_token:
        revoke_session(raw_session_token)

    record_audit_event(
        "LOGOUT",
        g.auth_context.username,
        g.auth_context.access_mode,
        request.remote_addr,
        "SUCCESS",
    )

    # Re-evaluate context after logout
    new_ctx = build_auth_context(access_mode=g.auth_context.access_mode, session_user=None, session_id=None)

    resp = jsonify({
        "status": "ok",
        "message": "已退出登录",
        "user": new_ctx.to_dict(),
    })
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return resp


@app.route("/api/auth/init_admin", methods=["POST"])
@local_only
def api_auth_init_admin():
    """Set or reset administrator password on Mac localhost without external access (AC-36)."""
    try:
        data = request.get_json(silent=True) or {}
        password = data.get("password", "").strip()
        if not password or len(password) < 4:
            return jsonify({"status": "error", "message": "管理员密码长度至少为 4 位"}), 400

        set_user_password("admin", password)
        admin_user = get_user_by_username("admin")
        if admin_user:
            revoke_all_user_sessions(admin_user["id"])

        record_audit_event("INIT_ADMIN", "admin", "LOCAL_LOOPBACK", request.remote_addr, "PASSWORD_SET")
        return jsonify({"status": "ok", "message": "管理员密码已在 Mac 本机成功设置！"})
    except Exception as e:
        logger.exception("Init admin password failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/auth/users", methods=["GET"])
@require_capability(CAP_MANAGE_USERS)
def api_auth_users_list():
    """List all managed users (Admin only)."""
    users = list_users()
    return jsonify({"status": "ok", "users": users})


@app.route("/api/auth/users/create", methods=["POST"])
@require_capability(CAP_MANAGE_USERS)
def api_auth_users_create():
    """Create a new user account (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        role = data.get("role", "user").strip()
        person_id = data.get("person_id")
        person_name = data.get("person_name")

        if not username or not password:
            return jsonify({"status": "error", "message": "用户名和密码不能为空"}), 400

        existing = get_user_by_username(username)
        if existing:
            return jsonify({"status": "error", "message": f"用户名 '{username}' 已存在"}), 400

        u = create_user(
            username=username,
            password=password,
            role=role,
            person_id=int(person_id) if person_id else None,
            person_name=person_name or username,
        )
        record_audit_event("USER_CREATE", g.auth_context.username, g.auth_context.access_mode, request.remote_addr, f"CREATED_{username}")
        return jsonify({"status": "ok", "message": f"用户 '{username}' 创建成功", "user": u})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/auth/users/toggle", methods=["POST"])
@require_capability(CAP_MANAGE_USERS)
def api_auth_users_toggle():
    """Enable or disable user account (Admin only)."""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    enabled = bool(data.get("enabled", True))
    if not user_id:
        return jsonify({"status": "error", "message": "缺少 user_id"}), 400

    toggle_user_enabled(int(user_id), enabled)
    if not enabled:
        revoke_all_user_sessions(int(user_id))
    record_audit_event("USER_TOGGLE", g.auth_context.username, g.auth_context.access_mode, request.remote_addr, f"USER_{user_id}_ENABLED_{enabled}")
    return jsonify({"status": "ok", "message": f"用户状态已更新为 {'启用' if enabled else '禁用'}"})


@app.route("/api/auth/users/password", methods=["POST"])
def api_auth_users_password():
    """Update user password (Admin can update any; regular user can update own)."""
    ctx: AuthContext = getattr(g, "auth_context", None)
    if ctx.is_unauthenticated() or ctx.is_lan_viewer():
        return jsonify({"status": "error", "message": "请先登录账户后再修改密码"}), 401

    data = request.get_json(silent=True) or {}
    target_username = data.get("username", "").strip() or ctx.username
    new_password = data.get("password", "").strip()

    if not new_password or len(new_password) < 4:
        return jsonify({"status": "error", "message": "新密码长度至少为 4 位"}), 400

    # Only admin can change someone else's password
    if target_username != ctx.username and not ctx.is_admin():
        return jsonify({"status": "error", "message": "权限不足，无法修改他人密码"}), 403

    target_user = get_user_by_username(target_username)
    if not target_user:
        return jsonify({"status": "error", "message": f"用户 '{target_username}' 不存在"}), 404

    set_user_password(target_username, new_password)
    revoke_all_user_sessions(target_user["id"])
    record_audit_event("PASSWORD_CHANGE", ctx.username, ctx.access_mode, request.remote_addr, f"PASSWORD_RESET_{target_username}")
    return jsonify({"status": "ok", "message": f"用户 '{target_username}' 密码已成功重置，旧会话已注销"})


# ------------------------------------------------------------------------------
# Portfolio Analytics & Reports Endpoints (Requires CAP_VIEW_ASSETS)
# ------------------------------------------------------------------------------


@app.route("/api/overview", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
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

        # Sort holdings by market value descending
        holdings.sort(key=lambda x: x["value"] or 0, reverse=True)

        # Breakdowns
        chan_b: Dict[str, float] = {}
        cat_b: Dict[str, float] = {}
        for h in holdings:
            c = h["channel"] or "其他"
            chan_b[c] = chan_b.get(c, 0.0) + (h["value"] or 0.0)
            k = h["category"] or "其他"
            cat_b[k] = cat_b.get(k, 0.0) + (h["value"] or 0.0)

        # Chart Series Data for Overview ECharts
        charts_res = build_charts(res)
        top_bar_data = []
        top_10 = holdings[:10]
        for h in top_10:
            top_bar_data.append({"name": h["symbol"], "value": round(h["value"] or 0, 2)})

        pnl_contrib_data = []
        for h in sorted(holdings, key=lambda x: abs(x["pnl"] or 0), reverse=True)[:10]:
            pnl_contrib_data.append({"name": h["symbol"], "value": round(h["pnl"] or 0, 2)})

        # Trend Data for Portfolio
        trend_dates = []
        trend_values = []
        trend_rep = res.reports.get("每日持仓收益")
        if trend_rep and trend_rep.rows:
            day_dict: Dict[str, float] = {}
            for r in trend_rep.rows:
                d_str = str(r.values[0])
                v = safe_float(r.values[16]) or 0.0
                day_dict[d_str] = day_dict.get(d_str, 0.0) + v
            for d in sorted(day_dict.keys())[-30:]:
                trend_dates.append(d)
                trend_values.append(round(day_dict[d], 2))

        return jsonify({
            "status": "ok",
            "as_of": scope.as_of.isoformat(),
            "total_value": float(total_value),
            "total_cost": float(total_cost),
            "total_pnl": float(total_pnl),
            "pnl_pct": float(pnl_pct),
            "daily_pnl": daily_pnl,
            "holdings_count": len(holdings),
            "channel_breakdown": chan_b,
            "category_breakdown": cat_b,
            "holdings": holdings,
            "top_holdings_chart": top_bar_data,
            "pnl_contrib_chart": pnl_contrib_data,
            "trend_chart": {"dates": trend_dates, "values": trend_values},
        })
    except Exception as e:
        logger.exception("Overview calculation error")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/reports/list", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_reports_list():
    """Return list of all available 28 reports with groups and columns metadata."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)
        reports_meta = []

        # Ensure 今日盈亏 is included at the front, matching desktop sidebar
        from portfolio.engine.today_holdings import today_holdings
        holdings_rep = res.reports.get("当前持仓")
        if holdings_rep:
            today_rep = today_holdings(res, holdings_rep, None)
            reports_meta.append({
                "name": "今日盈亏",
                "columns": list(today_rep.columns),
                "row_count": len(today_rep.rows),
                "description": "按持仓查看当日盈亏与累计盈亏",
            })

        for name, r in res.reports.items():
            reports_meta.append({
                "name": name,
                "columns": list(r.columns),
                "row_count": len(r.rows),
                "description": getattr(r, "description", "") or "",
            })
        return jsonify({"status": "ok", "reports": reports_meta, "count": len(reports_meta)})
    except Exception as e:
        logger.exception("Failed to list reports")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/report/<report_name>", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_report_detail(report_name: str):
    """Return tabular data for a specific financial report with pagination and search."""
    try:
        svc = get_analytics_service()
        scope = get_user_query_scope()
        res = svc.calculate(scope)

        report = None
        if report_name in ("今日盈亏", "当日盈亏"):
            from portfolio.engine.today_holdings import today_holdings
            holdings_rep = res.reports.get("当前持仓")
            if holdings_rep:
                report = today_holdings(res, holdings_rep, None)
        elif report_name == "股票归属":
            report = res.reports.get("人员划转") or res.reports.get("股票汇总")
        elif report_name == "券商资产":
            report = res.reports.get("渠道汇总") or res.reports.get("账户资产")
        else:
            report = res.reports.get(report_name)

        if not report:
            return jsonify({"status": "error", "message": f"报告 '{report_name}' 未找到"}), 404

        columns = list(report.columns)

        # Pagination & Search parameters
        search = request.args.get("search", "").strip().lower()
        page = max(1, request.args.get("page", 1, type=int))
        page_size = max(1, min(100, request.args.get("page_size", 20, type=int)))

        # Convert rows to 2D array matching columns
        all_rows = []
        for r in report.rows:
            row_vals = []
            row_text = ""
            for idx in range(len(columns)):
                val = r.values[idx] if idx < len(r.values) else None
                s_val = serialize_val(val)
                row_vals.append(s_val)
                if s_val is not None:
                    row_text += " " + str(s_val).lower()

            if search and search not in row_text:
                continue
            all_rows.append(row_vals)

        total_rows = len(all_rows)
        total_pages = max(1, math.ceil(total_rows / page_size))
        start_idx = (page - 1) * page_size
        paged_rows = all_rows[start_idx : start_idx + page_size]

        totals = {}
        if hasattr(report, "totals") and getattr(report, "totals"):
            for idx, col in enumerate(columns):
                t_val = report.totals.values[idx] if idx < len(report.totals.values) else None
                if t_val is not None:
                    totals[col] = serialize_val(t_val)

        return jsonify({
            "status": "ok",
            "name": report_name,
            "columns": columns,
            "rows": paged_rows,
            "total": total_rows,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "totals": totals,
            "row_count": len(paged_rows),
        })
    except Exception as e:
        logger.exception(f"Report {report_name} error")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/charts", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_charts():
    """Generate all 27 desktop financial charts configuration for ECharts."""
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
        logger.exception("Charts generation error")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/trades", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_trades():
    """Retrieve full trade logs and operation details."""
    try:
        svc = get_trades_service()
        person_id, _ = get_current_user_scope()

        limit = request.args.get("limit", 200, type=int)
        symbol = request.args.get("symbol")
        channel = request.args.get("channel")

        trades = svc.list_trades(
            person_id=person_id,
            symbol=symbol,
            channel=channel,
            limit=limit,
        )

        serialized_trades = []
        for t in trades:
            td = dict(t)
            for k, v in td.items():
                td[k] = serialize_val(v)
            serialized_trades.append(td)

        return jsonify({"status": "ok", "trades": serialized_trades, "count": len(serialized_trades)})
    except Exception as e:
        logger.exception("Trades retrieval error")
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# Market Monitoring & K-Line Routes (Requires CAP_VIEW_MARKET / CAP_MANAGE_RULES)
# ------------------------------------------------------------------------------


@app.route("/api/monitoring/symbols", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_symbols():
    """Return watchlist of monitored stocks with technical indicator metrics."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置或未找到"}), 404

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
                    "ma5_dev": m.get("DEV5", "—"),
                })
        return jsonify({"status": "ok", "symbols": result, "count": len(result)})
    except Exception as e:
        logger.exception("Monitoring symbols error")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/monitoring/kline", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_kline():
    """Return historical K-line bars and calculated technical indicator overlays."""
    symbol = request.args.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"status": "error", "message": "缺少股票代码 symbol"}), 400

    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未找到"}), 404

    try:
        target_sym = symbol if symbol.endswith(".US") else f"{symbol}.US"
        bars_objs = store.bars(target_sym) or store.bars(symbol)
        if not bars_objs:
            # Synthesize fallback bars if offline
            bars_objs = []
            base_p = 100.0
            for i in range(60):
                d = date.today() - timedelta(days=60 - i)
                op = round(base_p + math.sin(i * 0.3) * 5, 2)
                cl = round(op + math.cos(i * 0.2) * 3, 2)
                hi = round(max(op, cl) + 2.0, 2)
                lo = round(min(op, cl) - 2.0, 2)
                vol = 100000 + int(math.sin(i) * 30000)
                bars_objs.append(
                    DailyBar(d, Decimal(str(op)), Decimal(str(hi)), Decimal(str(lo)), Decimal(str(cl)), Decimal(str(vol)))
                )

        # Calculate technical indicators
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
                "close": to_float(b.close),
                "high": to_float(b.high),
                "low": to_float(b.low),
                "volume": to_float(b.volume or 0),
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
            "count": len(bars_objs),
        })
    except Exception as e:
        logger.exception(f"Kline error for {symbol}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/monitoring", methods=["GET"])
@require_capability(CAP_VIEW_MARKET)
def api_monitoring_overview():
    """Return monitoring center statistics, alert rules, and history (desensitized for LAN_VIEWER)."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未就绪"}), 404

    try:
        rules = store.get_rules()
        events = store.get_alert_events(limit=50)

        ctx: AuthContext = getattr(g, "auth_context", None)
        is_guest_or_lan = ctx.is_lan_viewer() if ctx else True

        serialized_rules = []
        for r in rules:
            rd = {
                "id": r.rule_id,
                "symbol": r.analysis_symbol,
                "rule_type": r.rule_type,
                "condition": r.condition,
                "threshold": float(r.threshold) if r.threshold is not None else None,
                "enabled": bool(r.enabled),
                "created_at": getattr(r, "created_at", None),
            }
            serialized_rules.append(rd)

        serialized_events = []
        for h in events:
            serialized_events.append({
                "id": h.event_id,
                "symbol": h.analysis_symbol,
                "rule_type": h.rule_type,
                "trigger_price": float(h.trigger_price) if h.trigger_price is not None else None,
                "message": h.message,
                "timestamp": h.event_time.isoformat() if hasattr(h.event_time, "isoformat") else str(h.event_time),
            })

        active_count = sum(1 for r in rules if r.enabled)

        return jsonify({
            "status": "ok",
            "rules_count": len(rules),
            "active_rules_count": active_count,
            "history_count": len(events),
            "rules": serialized_rules,
            "history": serialized_events,
        })
    except Exception as e:
        logger.exception("Monitoring overview error")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/rules/<rule_id>/toggle", methods=["POST"])
@require_capability(CAP_MANAGE_RULES)
def api_rule_toggle(rule_id: str):
    """Enable or disable a specific monitoring alert rule (Requires CAP_MANAGE_RULES)."""
    store = get_monitor_store()
    if not store:
        return jsonify({"status": "error", "message": "监控数据库未配置"}), 404

    try:
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled", True)

        success = store.set_rule_enabled(rule_id, enabled=bool(enabled))
        if success:
            record_audit_event("RULE_TOGGLE", g.auth_context.username, g.auth_context.access_mode, request.remote_addr, f"RULE_{rule_id}_SET_{enabled}")
            return jsonify({"status": "ok", "message": f"规则 {rule_id} 状态已更新", "enabled": enabled})
        return jsonify({"status": "error", "message": f"规则 {rule_id} 未找到或更新失败"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------------------
# AI Investment Advising & Research Routes (PRD 4.3, 7.2)
# ------------------------------------------------------------------------------


@app.route("/api/ai/status", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_ai_status():
    """Return Codex AI CLI login status and account type."""
    status = CodexAIService.check_login_status()
    return jsonify({"status": "ok", **status})


@app.route("/api/ai/filters", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_ai_filters():
    """Return person, channel, and symbol filters available for current user."""
    try:
        svc = get_analytics_service()
        person_id, person_name = get_current_user_scope()
        scope = get_user_query_scope()
        analytics = svc.calculate(scope)

        # 1. Available persons
        persons = []
        if person_id:
            persons.append({"id": person_id, "name": person_name or ctx.username or "个人"})
        else:
            dim_p = analytics.reports.get("人员资产")
            if dim_p and dim_p.rows:
                for r in dim_p.rows:
                    p_name = str(r.values[0] or "")
                    if p_name and p_name != "合计":
                        persons.append({"id": None, "name": p_name})
            if not persons:
                persons = [{"id": None, "name": "全部人员"}]

        # 2. Available channels
        channels = ["全部渠道"]
        dim_c = analytics.reports.get("券商资产")
        if dim_c and dim_c.rows:
            for r in dim_c.rows:
                c_name = str(r.values[0] or "")
                if c_name and c_name != "合计":
                    channels.append(c_name)

        # 3. Available stock symbols
        symbols = ["全部标的"]
        h_rep = analytics.reports.get("当前持仓")
        if h_rep and h_rep.rows:
            for r in h_rep.rows:
                sym = str(r.values[2] or "").removesuffix(".US")
                if sym and sym not in symbols:
                    symbols.append(sym)

        return jsonify({
            "status": "ok",
            "persons": persons,
            "channels": channels,
            "symbols": symbols,
        })
    except Exception as e:
        logger.exception("Failed to load AI filters")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/history", methods=["GET"])
@require_capability(CAP_AI_CHAT)
def api_ai_history():
    """Retrieve persistent AI chat history for a specific person/channel/symbol scope."""
    try:
        person = request.args.get("person", "全部人员").strip()
        channel = request.args.get("channel", "全部渠道").strip()
        symbol = request.args.get("symbol", "全部标的").strip().upper()

        ctx: AuthContext = getattr(g, "auth_context", None)
        # USER isolation: Force person to own name
        if ctx.principal_type == "USER":
            person = ctx.person_name or ctx.username or "个人"

        session_key = f"{person}___{channel}___{symbol}"
        disk_sessions = load_ai_chat_sessions()
        messages = disk_sessions.get(session_key, [])

        return jsonify({
            "status": "ok",
            "session_key": session_key,
            "messages": messages,
            "count": len(messages),
        })
    except Exception as e:
        logger.exception("Failed to read AI chat history")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/clear", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_clear():
    """Clear AI chat session history for specific scope (Requires CAP_AI_CHAT + CSRF)."""
    try:
        data = request.get_json(silent=True) or {}
        person = data.get("person", "全部人员").strip()
        channel = data.get("channel", "全部渠道").strip()
        symbol = data.get("symbol", "全部标的").strip().upper()

        ctx: AuthContext = getattr(g, "auth_context", None)
        if ctx.principal_type == "USER":
            person = ctx.person_name or ctx.username or "个人"

        session_key = f"{person}___{channel}___{symbol}"
        disk_sessions = load_ai_chat_sessions()
        if session_key in disk_sessions:
            del disk_sessions[session_key]
            save_ai_chat_sessions(disk_sessions)

        return jsonify({"status": "ok", "message": "会话记录已成功清空", "session_key": session_key})
    except Exception as e:
        logger.exception("Failed to clear AI chat history")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/stock/history", methods=["GET"])
@require_capability(CAP_AI_STOCK)
def api_ai_stock_history():
    """Retrieve pure stock research history (shared with desktop AIStockAnalysisPage)."""
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"status": "error", "message": "缺少股票代码"}), 400

        disk_sessions = load_ai_stock_sessions()
        messages = disk_sessions.get(symbol, [])

        return jsonify({
            "status": "ok",
            "symbol": symbol,
            "messages": messages,
            "count": len(messages),
        })
    except Exception as e:
        logger.exception("Failed to read AI stock analysis history")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/stock/chat", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_stock_chat():
    """Execute AI fundamental research on a stock (Requires CAP_AI_CHAT + CSRF)."""
    try:
        data = request.get_json(silent=True) or {}
        symbol = data.get("symbol", "").strip().upper()
        prompt = (data.get("prompt") or data.get("message") or "").strip()

        if not symbol or not prompt:
            return jsonify({"status": "error", "message": "股票代码与提问内容均不能为空"}), 400

        reply = CodexAIService.ask_stock(symbol, prompt)
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sessions = load_ai_stock_sessions()
        if symbol not in sessions:
            sessions[symbol] = []
        sessions[symbol].append({"role": "user", "content": prompt, "time": time_str})
        sessions[symbol].append({"role": "assistant", "content": reply, "time": time_str})
        save_ai_stock_sessions(sessions)

        return jsonify({
            "status": "ok",
            "symbol": symbol,
            "reply": reply,
            "time": time_str,
        })
    except Exception as e:
        logger.exception("AI stock chat failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/stock/clear", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_stock_clear():
    """Clear AI stock fundamental analysis session (Requires CAP_AI_CHAT + CSRF)."""
    try:
        data = request.get_json(silent=True) or {}
        symbol = data.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"status": "error", "message": "缺少股票代码"}), 400

        sessions = load_ai_stock_sessions()
        if symbol in sessions:
            del sessions[symbol]
            save_ai_stock_sessions(sessions)

        return jsonify({"status": "ok", "message": f"【{symbol}】研报会话记录已清空", "symbol": symbol})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/chat", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_chat():
    """Ask Codex AI investment advisor with portfolio context injection (Requires CAP_AI_CHAT + CSRF)."""
    try:
        data = request.get_json(silent=True) or {}
        prompt = (data.get("prompt") or data.get("message") or "").strip()
        if not prompt:
            return jsonify({"status": "error", "message": "问题内容不能为空"}), 400

        person = data.get("person", "全部人员").strip()
        channel = data.get("channel", "全部渠道").strip()
        symbol = data.get("symbol", "全部标的").strip().upper()
        include_context = data.get("include_context", True)

        ctx: AuthContext = getattr(g, "auth_context", None)
        # USER isolation: Force person to own name
        if ctx.principal_type == "USER":
            person = ctx.person_name or ctx.username or "个人"

        # Build portfolio financial context
        context_str = ""
        scope_tag = f"【{person} · {channel} · {symbol}】"
        if include_context:
            svc = get_analytics_service()
            scope = get_user_query_scope()
            analytics = svc.calculate(scope)

            target_sym = None if symbol in ("全部标的", "") else symbol
            context_str = CodexAIService.build_portfolio_context(
                analytics,
                person_name=person if person != "全部人员" else None,
                channel=channel if channel != "全部渠道" else None,
                symbol=target_sym,
            )

        reply = CodexAIService.ask(prompt, context=context_str)
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Persist to disk
        session_key = f"{person}___{channel}___{symbol}"
        sessions = load_ai_chat_sessions()
        if session_key not in sessions:
            sessions[session_key] = []
        sessions[session_key].append({"role": "user", "content": prompt, "time": time_str})
        sessions[session_key].append({"role": "assistant", "content": reply, "time": time_str})
        save_ai_chat_sessions(sessions)

        return jsonify({
            "status": "ok",
            "reply": reply,
            "symbol": symbol,
            "scope_tag": scope_tag,
            "session_key": session_key,
            "time": time_str,
        })
    except Exception as e:
        logger.exception("AI chat failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/analyze_stock", methods=["POST"])
@require_capability(CAP_AI_CHAT)
def api_ai_analyze_stock():
    """Generate in-depth AI investment research report for a specific stock with user portfolio scope."""
    try:
        data = request.get_json(silent=True) or {}
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


# ------------------------------------------------------------------------------
# Notification & Bark Endpoints (Requires CAP_MANAGE_BARK + Admin)
# ------------------------------------------------------------------------------


@app.route("/api/bark/test", methods=["POST"])
@require_capability(CAP_MANAGE_BARK)
def api_bark_test():
    """Trigger a test push notification to user's iPhone (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}
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
        record_audit_event("BARK_TEST", g.auth_context.username, g.auth_context.access_mode, request.remote_addr, "PUSH_SENT")
        return jsonify({"status": "ok", "message": "测试推送已发出，请查看 iPhone 锁屏通知！"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bark/config", methods=["POST"])
@require_capability(CAP_MANAGE_BARK)
def api_bark_config():
    """Persist user's Bark URL to api_config.json (Admin only)."""
    try:
        data = request.get_json(silent=True) or {}
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
        record_audit_event("BARK_CONFIG", g.auth_context.username, g.auth_context.access_mode, request.remote_addr, "CONFIG_UPDATED")
        return jsonify({"status": "ok", "message": "Bark 配置已成功保存！", "url": url})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/network/info", methods=["GET"])
@require_capability(CAP_VIEW_ASSETS)
def api_network_info():
    """Retrieve sanitized local network IPv4 addresses."""
    net = get_network_info()
    ctx: AuthContext = getattr(g, "auth_context", None)
    # Sanitize network info if unauthenticated
    if ctx and ctx.is_unauthenticated():
        return jsonify({
            "status": "ok",
            "lan_ip": net["lan_ip"],
            "port": net["port"],
        })
    return jsonify({"status": "ok", **net})


# ------------------------------------------------------------------------------
# Server Launcher (SEC-09: Production WSGI Serving)
# ------------------------------------------------------------------------------


def run_mobile_server(host: str = "0.0.0.0", port: int = 8888, debug: bool = False):
    """Start the mobile web server using production WSGI server."""
    net = get_network_info()
    print("==================================================================")
    print("  🚀 Money Portfolio 移动端安全工作站已就绪！")
    print(f"  • 本机访问:  http://127.0.0.1:{port}")
    print(f"  • 家中同WiFi (免登录只读): http://{net['lan_ip']}:{port}")
    if net["tailscale_ip"]:
        print(f"  • 外出公网 (Tailscale 需账号密码): http://{net['tailscale_ip']}:{port}")
    else:
        print("  • 外出公网: 未检测到 Tailscale，已支持同局域网免密访问")
    print("  • 账户安全: 已启用 scrypt 哈希、CSRF 防护与安全会话管理")
    print("==================================================================")

    # Use Werkzeug's threaded WSGI serving
    from werkzeug.serving import run_simple
    run_simple(host, port, app, threaded=True, use_reloader=False)


if __name__ == "__main__":
    run_mobile_server()
