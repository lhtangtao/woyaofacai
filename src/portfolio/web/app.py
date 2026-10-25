"""Money Portfolio Mobile Web & PWA Server.

Provides a responsive, touch-optimized web interface for iPhone / iPad,
exposing portfolio holdings, technical monitoring, AI investment advising,
and Bark mobile push notification configuration.
"""

from datetime import date, datetime
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import re
import socket
import threading
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from portfolio.domain.models import Ledger, QueryScope
from portfolio.paths import API_CONFIG_PATH, DATABASE_PATH, PROJECT_DIR
from portfolio.services.ai_service import CodexAIService
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.notification_service import notification_service
from portfolio.services.trades_service import TradesService

logger = logging.getLogger(__name__)

app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# Shared service references
_analytics_svc: Optional[AnalyticsService] = None
_trades_svc: Optional[TradesService] = None


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


def get_lan_ips() -> List[str]:
    """Retrieve all local network IPv4 addresses (e.g. WiFi and Tailscale)."""
    ips = []
    try:
        import subprocess
        res = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet ") and not line.startswith("inet 127."):
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[1]
                    if not ip.startswith("198.18."):  # Exclude fake-ip
                        ips.append(ip)
    except Exception:
        pass
    if not ips:
        ips.append("127.0.0.1")
    return list(dict.fromkeys(ips))


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
    lan_ips = get_lan_ips()
    return render_template(
        "mobile.html",
        lan_ips=lan_ips,
        port=8888,
        bark_url=notification_service.bark_url or "",
    )


@app.route("/api/overview", methods=["GET"])
def api_overview():
    """Calculate portfolio summary, holdings, channels and category breakdown."""
    try:
        svc = get_analytics_service()
        scope = QueryScope(as_of=date.today(), reporting_currency="USD", ledger=Ledger.PERSON)
        res = svc.calculate(scope)

        holdings_report = res.reports.get("当前持仓")
        rows = holdings_report.rows if holdings_report else ()

        total_value = sum(Decimal(str(r.values[5])) for r in rows if r.values[5] is not None)
        total_cost = sum(Decimal(str(r.values[9])) for r in rows if r.values[9] is not None)
        total_pnl = sum(Decimal(str(r.values[10])) for r in rows if r.values[10] is not None)
        pnl_pct = (total_pnl / total_cost * 100) if total_cost else Decimal("0")

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
        holdings.sort(key=lambda x: (x["value"] or 0), reverse=True)

        # Channel Breakdown
        channel_data = {}
        for h in holdings:
            ch = h["channel"] or "其他"
            channel_data[ch] = channel_data.get(ch, 0.0) + (h["value"] or 0.0)

        # Category Breakdown
        cat_data = {}
        for h in holdings:
            cat = h["category"] or "其他"
            cat_data[cat] = cat_data.get(cat, 0.0) + (h["value"] or 0.0)

        return jsonify({
            "status": "ok",
            "as_of": date.today().isoformat(),
            "total_value": float(total_value),
            "total_cost": float(total_cost),
            "total_pnl": float(total_pnl),
            "pnl_pct": float(pnl_pct),
            "holdings_count": len(holdings),
            "holdings": holdings,
            "channel_breakdown": channel_data,
            "category_breakdown": cat_data,
        })
    except Exception as e:
        logger.exception("Failed to calculate overview")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/monitoring", methods=["GET"])
def api_monitoring():
    """Retrieve watch targets, technical rules, and recent alert events."""
    try:
        from PySide6.QtCore import QStandardPaths
        from portfolio.services.monitor_store import MonitorStore

        db_path = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
            / "monitoring.sqlite3"
        )
        if not db_path.exists():
            db_path = PROJECT_DIR / "monitoring.sqlite3"

        store = MonitorStore(db_path)
        # Load targets from store
        try:
            targets = store.targets("")
        except Exception:
            targets = []
        rules = store.get_rules()
        events = store.get_alert_events(limit=50)

        # Clean symbols
        clean_targets = []
        for t in targets:
            clean_targets.append({
                "analysis_symbol": t.get("analysis_symbol", "").removesuffix(".US"),
                "source_type": t.get("source_type", "HELD"),
                "archive_state": t.get("archive_state", "HELD"),
                "sources": [
                    {
                        "held_symbol": s.get("held_symbol", "").removesuffix(".US"),
                        "analysis_symbol": s.get("analysis_symbol", "").removesuffix(".US"),
                        "leverage_multiple": s.get("leverage_multiple", "1"),
                    }
                    for s in t.get("sources", [])
                ],
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
        from PySide6.QtCore import QStandardPaths
        from portfolio.services.monitor_store import MonitorStore

        db_path = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
            / "monitoring.sqlite3"
        )
        if not db_path.exists():
            db_path = PROJECT_DIR / "monitoring.sqlite3"

        store = MonitorStore(db_path)
        rule = store.get_rule(rule_id)
        if not rule:
            return jsonify({"status": "error", "message": "Rule not found"}), 404
        new_enabled = (rule.state != "ACTIVE")
        store.set_rule_enabled(rule_id, new_enabled)
        return jsonify({"status": "ok", "rule_id": rule_id, "state": "ACTIVE" if new_enabled else "PAUSED"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """Handle conversational questions with portfolio context on mobile."""
    try:
        data = request.get_json() or {}
        user_message = data.get("message", "").strip()
        symbol = data.get("symbol", "").strip().upper().removesuffix(".US") or None

        if not user_message:
            return jsonify({"status": "error", "message": "消息内容不能为空"}), 400

        # Build context
        svc = get_analytics_service()
        scope = QueryScope(as_of=date.today(), reporting_currency="USD", ledger=Ledger.PERSON)
        analytics = svc.calculate(scope)

        context_str = CodexAIService.build_portfolio_context(
            analytics,
            person_name="个人",
            symbol=symbol,
        )

        # Call Codex
        reply = CodexAIService.ask(user_message, context=context_str)
        return jsonify({
            "status": "ok",
            "reply": reply,
            "symbol": symbol,
            "time": datetime.now().strftime("%H:%M:%S"),
        })
    except Exception as e:
        logger.exception("AI chat failed")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/bark/test", methods=["POST"])
def api_bark_test():
    """Trigger a test push notification to user's iPhone."""
    try:
        data = request.get_json() or {}
        url = data.get("url", "").strip() or notification_service.bark_url

        if not url:
            return jsonify({"status": "error", "message": "请先填写 Bark 推送链接 (如 https://api.day.app/xxxx/)"}), 400

        # Temporarily set bark_url to test
        orig_url = notification_service.bark_url
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
    """Start the lightweight mobile server."""
    lan_ips = get_lan_ips()
    print("==================================================================")
    print("  🚀 Money Portfolio 移动端 / iPhone Web 服务已就绪！")
    print(f"  • 本机访问:  http://127.0.0.1:{port}")
    for ip in lan_ips:
        print(f"  • 手机访问 (同WiFi/Tailscale): http://{ip}:{port}")
    print("==================================================================")
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    run_mobile_server()
