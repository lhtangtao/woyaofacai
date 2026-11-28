"""美股每个交易日盘后复盘日报引擎（自动同步流水 + 盘后行情 + 突发新闻 + Gemini 总结 + 定时调度）。

核心约束：
- 财务计算严格只读本地业务 SQLite 库，金额与涨跌幅使用 Decimal 精确计算，缺行情保持 None 不填 0；
- 券商流水同步仅通过 Money3 独立入口 `scripts/sync_data.py` 执行；
- 交易日与盘后结束时间严格按 `America/New_York` 时区判定（自动兼容夏/冬令时，盘后 20:00 结束，默认 20:10 触发）；
- Web 端与 Mac 桌面端共用同一份本地 JSON 存档文件，通过文件锁防重复触发。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from portfolio.domain.models import QueryScope
from portfolio.domain.us_market_calendar import (
    get_upcoming_us_macro_events,
    get_us_market_session,
    previous_us_trading_day,
)
from portfolio.paths import CREDENTIALS_PATH, DATABASE_PATH, PROJECT_DIR, quote_cache_path
from portfolio.services.ai_service import CodexAIService, normalize_ai_provider
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.channel_cash import resolve_channel_cash_details
from portfolio.services.earnings_links import DEFAULT_LINKS
from portfolio.services.monitor_mapping import resolve as resolve_monitor_mapping

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
BJ_TZ = ZoneInfo("Asia/Shanghai")

AFTER_HOURS_FILENAME = "after_hours_reports.json"
DEFAULT_AFTER_HOURS_MODEL = "gemini-4-neon-high"
# 明确为 1x 现货指数/商品/债券 ETF 的白名单，避免被 DEFAULT_LINKS 误判为杠杆
NON_LEVERAGED_ETF_SYMBOLS = frozenset({
    "QQQ", "QQQM", "SPY", "DIA", "IWM", "VOO", "VTI", "IVV",
    "SCHD", "SGOV", "TLT", "GLD", "SLV", "EWY", "DXJ",
})
# 常见杠杆/反向 ETF 精确倍数表（多头为正，空头/反向为负）
LEVERAGED_ETF_MULTIPLES: Dict[str, Decimal] = {
    "TQQQ": Decimal("3"),
    "SQQQ": Decimal("-3"),
    "SOXL": Decimal("3"),
    "SOXS": Decimal("-3"),
    "KORU": Decimal("3"),
    "UPRO": Decimal("3"),
    "SPXL": Decimal("3"),
    "TNA": Decimal("3"),
    "LABU": Decimal("3"),
    "QLD": Decimal("2"),
    "SSO": Decimal("2"),
    "NVDL": Decimal("2"),
    "NVDS": Decimal("-1.5"),
    "TSLL": Decimal("2"),
    "TSLS": Decimal("-1"),
    "CONL": Decimal("2"),
    "MSTU": Decimal("2"),
    "MSTX": Decimal("2"),
    "AAOX": Decimal("2"),
    "AVGX": Decimal("2"),
    "GGLL": Decimal("2"),
    "INTW": Decimal("2"),
    "LITX": Decimal("2"),
    "MUU": Decimal("2"),
    "ORCX": Decimal("2"),
    "SNXX": Decimal("2"),
    "SPAL": Decimal("2"),
    "STXL": Decimal("2"),
    "WDCX": Decimal("2"),
}
LEVERAGED_ETF_SYMBOLS = frozenset(LEVERAGED_ETF_MULTIPLES.keys())
BENCHMARK_SYMBOLS = ("SPY", "QQQ", "SMH", "^VIX", "^TNX")

# 底层资产穿透映射：补充常见宽基/行业/单股杠杆 ETF 的底层标的键
_EXTRA_UNDERLYING_MAP: Dict[str, str] = {
    "TQQQ": "QQQ",
    "SQQQ": "QQQ",
    "QLD": "QQQ",
    "QQQM": "QQQ",
    "QQQ": "QQQ",
    "UPRO": "SPY",
    "SPXL": "SPY",
    "SSO": "SPY",
    "VOO": "SPY",
    "IVV": "SPY",
    "SPY": "SPY",
    "SOXL": "SOX",
    "SOXS": "SOX",
    "SMH": "SOX",
    "SOXX": "SOX",
    "TNA": "IWM",
    "IWM": "IWM",
    "KORU": "EWY",
    "EWY": "EWY",
    "DXJ": "DXJ",
    "NVDL": "NVDA",
    "NVDS": "NVDA",
    "CONL": "COIN",
    "MSTU": "MSTR",
    "MSTX": "MSTR",
}

_UNDERLYING_LABEL_MAP: Dict[str, str] = {
    "QQQ": "纳指100",
    "SPY": "标普500",
    "SOX": "费城半导体",
    "IWM": "罗素2000",
    "EWY": "韩国指数",
    "DXJ": "日本指数",
}

_SECTOR_TICKER_MAP: Dict[str, str] = {
    # 纳指指数
    "QQQ": "纳指指数",
    "QQQM": "纳指指数",
    "TQQQ": "纳指指数",
    "SQQQ": "纳指指数",
    "QLD": "纳指指数",
    # 半导体
    "SOX": "半导体",
    "SOXL": "半导体",
    "SOXS": "半导体",
    "SMH": "半导体",
    "SOXX": "半导体",
    "NVDA": "半导体",
    "NVDL": "半导体",
    "NVDS": "半导体",
    "AMD": "半导体",
    "AVGO": "半导体",
    "AVGX": "半导体",
    "TSM": "半导体",
    "MU": "半导体",
    "MUU": "半导体",
    "INTC": "半导体",
    "INTW": "半导体",
    "MRVL": "半导体",
    "SNDK": "半导体",
    "SNXX": "半导体",
    "STX": "半导体",
    "STXL": "半导体",
    "WDC": "半导体",
    "WDCX": "半导体",
    "LITE": "半导体",
    "LITX": "半导体",
    "AAOI": "半导体",
    "AAOX": "半导体",
    "QCOM": "半导体",
    "ASML": "半导体",
    "AMAT": "半导体",
    "LRCX": "半导体",
    "KLAC": "半导体",
    "ARM": "半导体",
    # 科技/AI
    "AAPL": "科技/AI",
    "MSFT": "科技/AI",
    "GOOGL": "科技/AI",
    "GOOG": "科技/AI",
    "GGLL": "科技/AI",
    "AMZN": "科技/AI",
    "META": "科技/AI",
    "TSLA": "科技/AI",
    "TSLL": "科技/AI",
    "TSLS": "科技/AI",
    "ORCL": "科技/AI",
    "ORCX": "科技/AI",
    "PLTR": "科技/AI",
    "NFLX": "科技/AI",
    "CRM": "科技/AI",
    "NOW": "科技/AI",
    "CRWD": "科技/AI",
    "SNOW": "科技/AI",
    "APP": "科技/AI",
    # 宽基/海外指数
    "SPY": "宽基/海外指数",
    "VOO": "宽基/海外指数",
    "IVV": "宽基/海外指数",
    "UPRO": "宽基/海外指数",
    "SPXL": "宽基/海外指数",
    "SSO": "宽基/海外指数",
    "DIA": "宽基/海外指数",
    "IWM": "宽基/海外指数",
    "TNA": "宽基/海外指数",
    "KORU": "宽基/海外指数",
    "EWY": "宽基/海外指数",
    "DXJ": "宽基/海外指数",
    # 加密概念
    "COIN": "加密概念",
    "CONL": "加密概念",
    "MSTR": "加密概念",
    "MSTU": "加密概念",
    "MSTX": "加密概念",
    "IBIT": "加密概念",
    "MARA": "加密概念",
    # 防御/固收
    "SGOV": "防御/固收",
    "TLT": "防御/固收",
    "GLD": "防御/固收",
    "SLV": "防御/固收",
    "SCHD": "防御/固收",
}
_STORE_LOCK = threading.RLock()
_SCHEDULER_LOCK = threading.Lock()
_SCHEDULER_STARTED = False


def _normalize_tnx_yield(val: Optional[Decimal]) -> Optional[Decimal]:
    """归一化 10Y 美债收益率 (^TNX) 为真实百分点（例如 42.5 -> 4.25，4.922 保持 4.922）。"""
    if val is None:
        return None
    if val > Decimal("20"):
        return val / Decimal("10")
    return val


def resolve_leverage_profile(symbol: str, category: str = "") -> tuple[bool, Decimal]:
    """解析标的杠杆属性与精确倍数（如 1x / 2x / 3x / -1x / -3x）。

    优先级：
    1. 非杠杆现货 ETF 白名单（如 QQQM/QQQ/SPY）强制返回 (False, 1x)；
    2. `monitor_mapping.resolve()` 中已核验的精确杠杆倍数与方向；
    3. 内置 `LEVERAGED_ETF_MULTIPLES` 常见杠杆/反向 ETF 倍数表；
    4. 账本分类显式标注为「杠杆」时默认按 2x 计；
    5. 其余标的按 1x 普通仓位处理。
    """
    clean = str(symbol or "").strip().upper().removesuffix(".US")
    if not clean or clean in NON_LEVERAGED_ETF_SYMBOLS:
        return False, Decimal("1")

    try:
        mapping = resolve_monitor_mapping(f"{clean}.US", category=category or "")
        if mapping.status == "VERIFIED" and mapping.leverage_multiple:
            mult = _to_dec(mapping.leverage_multiple)
            if mult is not None and mult > 0:
                signed_mult = -mult if mapping.direction == "空" else mult
                is_lev = (abs(signed_mult) > Decimal("1")) or (signed_mult < Decimal("0"))
                return is_lev, signed_mult
    except Exception:
        pass

    if clean in LEVERAGED_ETF_MULTIPLES:
        factor = LEVERAGED_ETF_MULTIPLES[clean]
        return (abs(factor) > Decimal("1") or factor < Decimal("0")), factor

    if str(category or "").strip() == "杠杆":
        return True, Decimal("2")

    return False, Decimal("1")


def default_after_hours_path() -> Path:
    """返回盘后复盘日报持久化文件路径（支持环境变量覆盖）。"""
    env_path = os.environ.get("MONEY3_AFTER_HOURS_REPORTS", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    base_dir = Path.home() / "Library" / "Application Support" / "MoneyPortfolio"
    if not base_dir.exists():
        base_dir = Path.home() / ".money_portfolio"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / AFTER_HOURS_FILENAME


def _to_dec(val: Any) -> Optional[Decimal]:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except Exception:
        return None


def _fmt_dec(val: Optional[Decimal], signed: bool = False, pct: bool = False) -> str:
    if val is None:
        return "—"
    num = val * Decimal("100") if pct else val
    sign = "+" if signed else ""
    suffix = "%" if pct else ""
    return f"{num:{sign},.2f}{suffix}"


def _serialize_dec(val: Optional[Decimal]) -> Optional[float]:
    if val is None:
        return None
    return float(val)


def current_ny_now() -> datetime:
    return datetime.now(NY_TZ)


def resolve_default_trade_date(now_ny: Optional[datetime] = None) -> date:
    """返回默认应复盘的美股交易日（自动跳过周末与美股法定休市日，兼容半日市 13:00 ET 收盘）。"""
    now = now_ny or current_ny_now()
    d = now.date()
    session = get_us_market_session(d)
    if not session.is_trading_day:
        return previous_us_trading_day(d)
    close_et = session.regular_close_et or dt_time(16, 0)
    if now.time().replace(tzinfo=None) < close_et:
        return previous_us_trading_day(d)
    return d


def is_after_hours_ready(
    now_ny: Optional[datetime] = None,
    trigger_hour: Optional[int] = None,
    trigger_minute: Optional[int] = None,
) -> tuple[bool, date]:
    """判断当前美东时间是否已到达交易日盘后自动生成窗口。

    - 周末与美股法定休市日（如感恩节、圣诞节等）直接返回 False；
    - 常规交易日默认美东 20:10 触发，半日市（如黑五、平安夜）盘后 17:00 结束，默认美东 17:10 触发。
    """
    now = now_ny or current_ny_now()
    d = now.date()
    session = get_us_market_session(d)
    if not session.is_trading_day:
        return False, resolve_default_trade_date(now)

    default_trig = session.recommended_trigger_et or dt_time(20, 10)
    t_hour = default_trig.hour if trigger_hour is None else trigger_hour
    t_min = default_trig.minute if trigger_minute is None else trigger_minute
    ready = (now.hour > t_hour) or (now.hour == t_hour and now.minute >= t_min)
    return ready, d


def make_report_id(trade_date: str, person_key: str) -> str:
    return f"{trade_date}::{person_key}"


class AfterHoursReportStore:
    """盘后复盘日报本地持久化存储（Web 端与桌面端共用）。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else default_after_hours_path()

    def _load_raw(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "reports": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("reports"), dict):
                return raw
        except Exception as exc:
            logger.error("读取盘后复盘日报文件失败: %s", exc)
        return {"version": 1, "reports": {}}

    def _save_raw(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        with _STORE_LOCK:
            data = self._load_raw()
            item = data["reports"].get(report_id)
            return dict(item) if isinstance(item, dict) else None

    def has_report(self, trade_date: str, person_key: str) -> bool:
        return self.get_report(make_report_id(trade_date, person_key)) is not None

    def save_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        with _STORE_LOCK:
            data = self._load_raw()
            rid = str(report["id"])
            data["reports"][rid] = report
            self._save_raw(data)
            return report

    def get_previous_report(self, person_key: str, before_trade_date: str) -> Optional[Dict[str, Any]]:
        """查找同一人员范围严格早于 before_trade_date 的最近一份历史盘后日报。"""
        with _STORE_LOCK:
            data = self._load_raw()
            candidates: List[Dict[str, Any]] = []
            for rep in data["reports"].values():
                if not isinstance(rep, dict):
                    continue
                if rep.get("person_key") != person_key:
                    continue
                td = str(rep.get("trade_date") or "").strip()[:10]
                if td and td < before_trade_date:
                    candidates.append(dict(rep))
            if not candidates:
                return None
            candidates.sort(
                key=lambda x: (str(x.get("trade_date") or ""), str(x.get("generated_at") or "")),
                reverse=True,
            )
            return ensure_structured(candidates[0])

    def delete_report(self, report_id: str) -> bool:
        """删除指定 ID 的历史盘后日报存档（用于清理误生成的休市日日报等）。"""
        with _STORE_LOCK:
            data = self._load_raw()
            if report_id not in data["reports"]:
                return False
            del data["reports"][report_id]
            self._save_raw(data)
            return True

    def update_checklist_state(self, report_id: str, checked_ids: List[str]) -> Optional[Dict[str, Any]]:
        """持久化次日盘前关注清单的勾选状态。"""
        clean_ids = [str(cid).strip() for cid in (checked_ids or []) if str(cid).strip()]
        with _STORE_LOCK:
            data = self._load_raw()
            item = data["reports"].get(report_id)
            if not isinstance(item, dict):
                return None
            item["checked_ids"] = clean_ids
            self._save_raw(data)
            return dict(item)

    def append_followup(self, report_id: str, question: str, answer: str, model: str = "") -> Optional[Dict[str, Any]]:
        """将基于当前盘后日报的追问与 AI 回答追加保存到该日报记录中。"""
        with _STORE_LOCK:
            data = self._load_raw()
            item = data["reports"].get(report_id)
            if not isinstance(item, dict):
                return None
            history = list(item.get("followups_history") or [])
            now_bj = datetime.now(BJ_TZ)
            history.append({
                "question": question,
                "answer": answer,
                "model": model,
                "time_str": now_bj.strftime("%m-%d %H:%M"),
                "time": now_bj.isoformat(timespec="seconds"),
            })
            item["followups_history"] = history[-30:]
            self._save_raw(data)
            return dict(item)

    def list_reports(
        self,
        person_key: Optional[str] = None,
        limit: int = 90,
    ) -> List[Dict[str, Any]]:
        """按交易日倒序返回日报摘要列表（不含超大正文，详情按需读取）。"""
        with _STORE_LOCK:
            data = self._load_raw()
            items: List[Dict[str, Any]] = []
            for rep in data["reports"].values():
                if not isinstance(rep, dict):
                    continue
                if person_key and rep.get("person_key") != person_key:
                    continue
                rep_ready = ensure_structured(rep)
                summary = (rep_ready.get("snapshot") or {}).get("summary") or {}
                structured = rep_ready.get("structured") or {}
                tldr = str(structured.get("tldr") or "")
                items.append({
                    "id": rep_ready.get("id"),
                    "trade_date": rep_ready.get("trade_date"),
                    "person_key": rep_ready.get("person_key"),
                    "person_id": rep_ready.get("person_id"),
                    "person_name": rep_ready.get("person_name"),
                    "generated_at": rep_ready.get("generated_at"),
                    "trigger": rep_ready.get("trigger", "manual"),
                    "provider": rep_ready.get("provider", "gemini"),
                    "model": rep_ready.get("model", ""),
                    "sync_status": rep_ready.get("sync_status") or {},
                    "day_pnl": summary.get("day_pnl"),
                    "day_pnl_pct": summary.get("day_pnl_pct"),
                    "leverage_pct": summary.get("leverage_pct"),
                    "total_market_value": summary.get("total_market_value"),
                    "trade_count": summary.get("trade_count", 0),
                    "holding_count": summary.get("holding_count", 0),
                    "regime": structured.get("regime"),
                    "tldr": tldr[:80] + ("…" if len(tldr) > 80 else ""),
                })
            items.sort(
                key=lambda x: (x.get("trade_date") or "", x.get("generated_at") or ""),
                reverse=True,
            )
            return items[:limit]


def run_broker_sync(timeout_sec: int = 180) -> Dict[str, Any]:
    """调用 Money3 独立同步入口 `scripts/sync_data.py` 刷新券商流水。"""
    started = datetime.now(BJ_TZ).isoformat(timespec="seconds")
    sync_script = PROJECT_DIR / "scripts" / "sync_data.py"
    if not sync_script.is_file():
        return {
            "ran": False,
            "ok": False,
            "message": f"未找到同步脚本: {sync_script}",
            "finished_at": started,
        }
    py_bin = PROJECT_DIR / ".venv" / "bin" / "python"
    python_exec = str(py_bin) if py_bin.is_file() else sys.executable
    try:
        proc = subprocess.run(
            [python_exec, str(sync_script)],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        finished = datetime.now(BJ_TZ).isoformat(timespec="seconds")
        combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        tail_lines = [line.strip() for line in combined.splitlines() if line.strip()][-4:]
        brief = " | ".join(tail_lines) if tail_lines else ("同步成功" if proc.returncode == 0 else f"退出码 {proc.returncode}")
        if proc.returncode == 0:
            return {"ran": True, "ok": True, "message": brief[:400], "finished_at": finished}
        return {"ran": True, "ok": False, "message": f"券商同步失败: {brief[:400]}", "finished_at": finished}
    except subprocess.TimeoutExpired:
        return {
            "ran": True,
            "ok": False,
            "message": f"券商同步超时（>{timeout_sec}秒），已基于现有数据库继续生成日报",
            "finished_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        }
    except Exception as exc:
        return {
            "ran": True,
            "ok": False,
            "message": f"券商同步异常: {exc}",
            "finished_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        }


def fetch_after_hours_quotes(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量获取美股常规收盘价、昨收、盘后价与涨跌幅（优先长桥，缺项降级 Yahoo Finance）。"""
    clean_syms = sorted({s.strip().upper().removesuffix(".US") for s in symbols if s and s.strip()})
    if not clean_syms:
        return {}

    result: Dict[str, Dict[str, Any]] = {}

    # 1. 优先通过长桥 OpenAPI 获取最准确的常规收盘价 + 盘后价
    try:
        from longbridge.openapi import Config, QuoteContext

        from portfolio.sync.longbridge_credentials import load_longbridge_credentials

        creds = load_longbridge_credentials()
        app_key = app_secret = access_token = ""
        if isinstance(creds, dict):
            app_key = str(creds.get("app_key") or "").strip()
            app_secret = str(creds.get("app_secret") or "").strip()
            access_token = str(creds.get("access_token") or "").strip()
        elif isinstance(creds, (tuple, list)) and len(creds) >= 3:
            # load_longbridge_credentials() 返回 (access_token, app_key, app_secret)
            access_token = str(creds[0] or "").strip()
            app_key = str(creds[1] or "").strip()
            app_secret = str(creds[2] or "").strip()
        if app_key and app_secret and access_token:
            if hasattr(Config, "from_apikey"):
                cfg = Config.from_apikey(
                    app_key,
                    app_secret,
                    access_token,
                    enable_overnight=True,
                    enable_print_quote_packages=False,
                )
            else:
                cfg = Config(app_key, app_secret, access_token)
            ctx = QuoteContext(cfg)
            # 长桥不支持 ^VIX / ^TNX 等 Yahoo 指数前缀符号，跳过交由下方 Yahoo 降级拉取
            lb_syms = [f"{s}.US" for s in clean_syms if not s.startswith("^")]
            quotes = ctx.quote(lb_syms) if lb_syms else []
            for q in quotes:
                sym = str(getattr(q, "symbol", "")).upper().removesuffix(".US")
                last_done = _to_dec(getattr(q, "last_done", None))
                prev_close = _to_dec(getattr(q, "prev_close", None))
                post_part = getattr(q, "post_market_quote", None)
                post_price = _to_dec(getattr(post_part, "last_done", None)) if post_part is not None else None
                reg_pct = ((last_done - prev_close) / prev_close) if (last_done is not None and prev_close and prev_close > 0) else None
                post_pct = ((post_price - last_done) / last_done) if (post_price is not None and last_done and last_done > 0) else None
                total_pct = ((post_price - prev_close) / prev_close) if (post_price is not None and prev_close and prev_close > 0) else reg_pct
                result[sym] = {
                    "symbol": sym,
                    "regular_close": _serialize_dec(last_done),
                    "prev_close": _serialize_dec(prev_close),
                    "regular_change_pct": _serialize_dec(reg_pct),
                    "after_hours_price": _serialize_dec(post_price),
                    "after_hours_change_pct": _serialize_dec(post_pct),
                    "total_change_pct": _serialize_dec(total_pct),
                    "source": "longbridge",
                }
    except Exception as exc:
        logger.warning("长桥盘后行情获取失败，将尝试 Yahoo Finance 降级补齐: %s", exc)

    # 2. 对未获取到行情的标的（及 ^VIX / ^TNX 等指数）降级查询 Yahoo Finance Chart API
    missing = [s for s in clean_syms if s not in result]
    if missing:
        def _fetch_yahoo_quote(sym: str) -> Optional[Dict[str, Any]]:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d&includePrePost=true"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Money3/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                chart_res = ((payload.get("chart") or {}).get("result") or [{}])[0] or {}
                meta = chart_res.get("meta") or {}
                reg_price = _to_dec(meta.get("regularMarketPrice"))
                prev_close = _to_dec(meta.get("previousClose"))
                if prev_close is None:
                    closes_raw = ((((chart_res.get("indicators") or {}).get("quote") or [{}])[0]) or {}).get("close") or []
                    valid_closes = [_to_dec(c) for c in closes_raw if _to_dec(c) is not None]
                    if len(valid_closes) >= 2:
                        prev_close = valid_closes[-2]
                if prev_close is None:
                    prev_close = _to_dec(meta.get("chartPreviousClose"))
                post_price = _to_dec(meta.get("postMarketPrice"))
                if reg_price is None:
                    return None
                if sym == "^TNX":
                    reg_price = _normalize_tnx_yield(reg_price)
                    prev_close = _normalize_tnx_yield(prev_close)
                    post_price = _normalize_tnx_yield(post_price)
                reg_pct = ((reg_price - prev_close) / prev_close) if (prev_close and prev_close > 0) else None
                post_pct = ((post_price - reg_price) / reg_price) if (post_price is not None and reg_price > 0) else None
                total_pct = ((post_price - prev_close) / prev_close) if (post_price is not None and prev_close and prev_close > 0) else reg_pct
                return {
                    "symbol": sym,
                    "regular_close": _serialize_dec(reg_price),
                    "prev_close": _serialize_dec(prev_close),
                    "regular_change_pct": _serialize_dec(reg_pct),
                    "after_hours_price": _serialize_dec(post_price),
                    "after_hours_change_pct": _serialize_dec(post_pct),
                    "total_change_pct": _serialize_dec(total_pct),
                    "source": "yahoo",
                }
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(_fetch_yahoo_quote, s): s for s in missing}
            for fut in as_completed(futs):
                sym = futs[fut]
                item = fut.result()
                if item:
                    result[sym] = item

    return result


def fetch_ticker_news(symbols: List[str], hours_window: int = 36, max_per_symbol: int = 3) -> Dict[str, List[Dict[str, Any]]]:
    """按持仓及关联正股抓取近期突发新闻（Yahoo Finance Search API，免 Key，不编造）。"""
    query_map: Dict[str, str] = {}
    for raw in symbols:
        sym = raw.strip().upper().removesuffix(".US")
        if not sym:
            continue
        query_map[sym] = sym
        mapped = DEFAULT_LINKS.get(f"{sym}.US", "")
        if mapped:
            underlying = mapped.removesuffix(".US")
            query_map[underlying] = underlying

    if not query_map:
        return {}

    cutoff_ts = time.time() - hours_window * 3600
    news_by_sym: Dict[str, List[Dict[str, Any]]] = {}

    def _fetch_one(sym: str) -> tuple[str, List[Dict[str, Any]]]:
        url = (
            f"https://query1.finance.yahoo.com/v1/finance/search"
            f"?q={urllib.parse.quote(sym)}&quotesCount=0&newsCount=6"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Money3/1.0"})
        items: List[Dict[str, Any]] = []
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for n in data.get("news") or []:
                pub_ts = n.get("providerPublishTime") or 0
                if pub_ts and pub_ts < cutoff_ts:
                    continue
                title = str(n.get("title") or "").strip()
                if not title:
                    continue
                pub_str = (
                    datetime.fromtimestamp(pub_ts, tz=timezone.utc).astimezone(BJ_TZ).strftime("%m-%d %H:%M")
                    if pub_ts
                    else ""
                )
                items.append({
                    "title": title,
                    "publisher": str(n.get("publisher") or "").strip(),
                    "published_bj": pub_str,
                    "link": str(n.get("link") or "").strip(),
                })
                if len(items) >= max_per_symbol:
                    break
        except Exception:
            pass
        return sym, items

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_fetch_one, s) for s in sorted(query_map.keys())]
        for fut in as_completed(futs):
            sym, items = fut.result()
            if items:
                news_by_sym[sym] = items

    return news_by_sym


def resolve_person_scope(db_path: Path, person_key: Optional[str] = None, person_id: Optional[int] = None) -> tuple[str, Optional[int], str]:
    """解析人员筛选范围，返回 (person_key, person_id, person_display_name)。"""
    from portfolio.db.connection import readonly_connection

    persons: List[Dict[str, Any]] = []
    try:
        with readonly_connection(db_path) as conn:
            persons = [dict(r) for r in conn.execute("SELECT id, name FROM persons WHERE COALESCE(enabled, 1) = 1 ORDER BY id")]
    except Exception:
        persons = []

    if person_id is not None:
        matched = next((p for p in persons if p.get("id") == person_id), None)
        if matched:
            name = str(matched.get("name") or f"人员#{person_id}")
            return name, int(person_id), name
        return f"人员#{person_id}", int(person_id), f"人员#{person_id}"

    raw = (person_key or "个人").strip()
    if raw.upper() in ("ALL", "全部", "全部人员"):
        return "ALL", None, "全部人员"

    matched = next((p for p in persons if str(p.get("name") or "").strip() == raw), None)
    if matched:
        return str(matched["name"]), int(matched["id"]), str(matched["name"])

    if raw == "个人" and persons:
        first = persons[0]
        return str(first["name"]), int(first["id"]), str(first["name"])

    return "ALL", None, "全部人员"


def fetch_upcoming_earnings_events(
    holdings_list: List[Dict[str, Any]],
    trade_date: date,
    days_ahead: int = 7,
) -> List[Dict[str, Any]]:
    """扫描当前持仓及其关联正股未来 1~days_ahead 天内的财报日（优先复用 EarningsService 本地缓存与核实表，绝不阻塞）。"""
    if not holdings_list or days_ahead <= 0:
        return []

    target_to_held: Dict[str, List[str]] = {}
    for h in holdings_list:
        sym = str(h.get("symbol") or "").strip().upper().removesuffix(".US")
        if not sym:
            continue
        underlying = str(h.get("underlying") or "").strip().upper().removesuffix(".US")
        if not underlying:
            underlying = _EXTRA_UNDERLYING_MAP.get(sym, "")
        # 对单一正股杠杆 ETF，同时关注其关联正股的财报；宽基指数 ETF 不查单公司财报
        if underlying and underlying not in _UNDERLYING_LABEL_MAP:
            target_key = f"{underlying}.US"
            target_to_held.setdefault(target_key, [])
            if sym not in target_to_held[target_key]:
                target_to_held[target_key].append(sym)
        if sym not in _UNDERLYING_LABEL_MAP and sym not in _EXTRA_UNDERLYING_MAP:
            target_key = f"{sym}.US"
            target_to_held.setdefault(target_key, [])
            if sym not in target_to_held[target_key]:
                target_to_held[target_key].append(sym)

    if not target_to_held:
        return []

    try:
        from portfolio.services.earnings_service import EarningsService, reconciled_events
        from portfolio.services.quote_store import QuoteStore

        q_path = quote_cache_path()
        q_store = QuoteStore(q_path) if q_path.is_file() else None
        esvc = EarningsService(store=q_store)

        end_date = trade_date + timedelta(days=days_ahead)
        months_to_scan = sorted({(trade_date.year, trade_date.month), (end_date.year, end_date.month)})
        targets = set(target_to_held.keys())

        raw_events = []
        for y, m in months_to_scan:
            cached = esvc.get_cached(CREDENTIALS_PATH, y, m, ("US",), max_age_seconds=None)
            if cached is None:
                # 兼容其他 path key 的已持久化财报缓存：按核实表兜底
                month_events = reconciled_events([], year=y, month=m)
            else:
                month_events = reconciled_events(cached.events, year=y, month=m)
            for ev in month_events:
                if ev.symbol in targets:
                    raw_events.append(ev)

        upcoming: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for ev in raw_events:
            delta = (ev.day - trade_date).days
            if not (1 <= delta <= days_ahead):
                continue
            sym_clean = str(ev.symbol).upper().removesuffix(".US")
            dedup_key = (ev.day.isoformat(), sym_clean)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            t_lbl = str(ev.time_label or "").strip()
            held_syms = sorted(target_to_held.get(ev.symbol) or [sym_clean])
            held_note = (
                f"（关联持仓 {'/'.join(s for s in held_syms if s != sym_clean)}）"
                if any(s != sym_clean for s in held_syms)
                else ""
            )
            title = f"{sym_clean} 财报" + (f" ({t_lbl})" if t_lbl else "") + held_note
            upcoming.append({
                "date": ev.day.isoformat(),
                "days_away": delta,
                "symbol": sym_clean,
                "held_symbols": held_syms,
                "time_label": t_lbl,
                "title": title,
            })
        upcoming.sort(key=lambda x: (x["days_away"], x["symbol"]))
        return upcoming
    except Exception as exc:
        logger.debug("扫描未来 7 天持仓财报日程失败（已静默跳过）: %s", exc)
        return []


def _compute_recent_trend(
    daily_rep,
    trade_date: date,
    *,
    current_day_pnl: Optional[Decimal] = None,
    current_prev_mv: Optional[Decimal] = None,
    current_total_mv: Optional[Decimal] = None,
) -> Optional[Dict[str, Any]]:
    """基于本地「每日持仓收益」按 Decimal 精确计算近 5 日、WTD、MTD、连盈/连亏天数与近 30 日高点回撤。"""
    date_str = trade_date.isoformat()
    by_day: Dict[str, Dict[str, Decimal]] = {}

    if daily_rep and getattr(daily_rep, "rows", None):
        cols = list(daily_rep.columns)
        idx = {name: i for i, name in enumerate(cols)}

        def _pick(vals, names: tuple[str, ...], fallback_idx: Optional[int] = None):
            for name in names:
                if name in idx and idx[name] < len(vals):
                    return vals[idx[name]]
            if fallback_idx is not None and 0 <= fallback_idx < len(vals):
                return vals[fallback_idx]
            return None

        for row in daily_rep.rows:
            vals = row.values
            r_date = str(_pick(vals, ("日期", "交易日期"), 0) or "").strip()[:10]
            if not r_date or r_date > date_str:
                continue
            bucket = by_day.setdefault(
                r_date,
                {"pnl": Decimal("0"), "prev_mv": Decimal("0"), "end_mv": Decimal("0")},
            )
            dp = _to_dec(_pick(vals, ("当日收益($)", "当日盈亏($)", "当日盈亏", "今日盈亏($)", "今日盈亏"), 19))
            if dp is not None:
                bucket["pnl"] += dp
            pmv = _to_dec(_pick(vals, ("期初市值($)", "期初市值"), 15))
            if pmv is not None and pmv > 0:
                bucket["prev_mv"] += pmv
            emv = _to_dec(_pick(vals, ("日末市值($)", "期末市值($)", "日末市值"), 11))
            if emv is not None and emv > 0:
                bucket["end_mv"] += emv

    # 将目标日最终口径（含盘后变动，或账本尚无收盘行时的行情估算）并入序列末尾
    if current_day_pnl is not None:
        if date_str in by_day:
            by_day[date_str]["pnl"] = current_day_pnl
            if current_prev_mv is not None and current_prev_mv > 0 and by_day[date_str]["prev_mv"] <= 0:
                by_day[date_str]["prev_mv"] = current_prev_mv
            if current_total_mv is not None and current_total_mv > 0:
                by_day[date_str]["end_mv"] = current_total_mv
        else:
            by_day[date_str] = {
                "pnl": current_day_pnl,
                "prev_mv": current_prev_mv if (current_prev_mv is not None and current_prev_mv > 0) else Decimal("0"),
                "end_mv": current_total_mv if (current_total_mv is not None and current_total_mv > 0) else Decimal("0"),
            }

    if not by_day:
        return None

    ordered_days = sorted(by_day.keys())
    series: List[Dict[str, Any]] = []
    for ds in ordered_days:
        try:
            d_obj = date.fromisoformat(ds)
        except ValueError:
            continue
        item = by_day[ds]
        ret: Optional[Decimal] = None
        if item["prev_mv"] > 0:
            ret = item["pnl"] / item["prev_mv"]
        series.append({
            "date": d_obj,
            "date_str": ds,
            "pnl": item["pnl"],
            "prev_mv": item["prev_mv"],
            "end_mv": item["end_mv"],
            "ret": ret,
        })

    if not series:
        return None

    def _window_stats(items: List[Dict[str, Any]]) -> tuple[Optional[Decimal], Optional[Decimal]]:
        if not items:
            return None, None
        pnl_sum = sum((it["pnl"] for it in items), Decimal("0"))
        # 优先使用时间加权复利收益率，消除出入金扭曲；若窗口内存在缺失日收益率则回退期初市值口径
        if all(it["ret"] is not None for it in items):
            nav = Decimal("1")
            for it in items:
                nav *= Decimal("1") + it["ret"]
            return pnl_sum, nav - Decimal("1")
        base_mv = next((it["prev_mv"] for it in items if it["prev_mv"] > 0), None)
        pct = (pnl_sum / base_mv) if (base_mv is not None and base_mv > 0) else None
        return pnl_sum, pct

    # 1. 近 5 个交易日
    win_5d = series[-5:]
    pnl_5d, pct_5d = _window_stats(win_5d)

    # 2. 本周 (WTD)
    iso_week = trade_date.isocalendar()[:2]
    win_wtd = [it for it in series if it["date"].isocalendar()[:2] == iso_week]
    pnl_wtd, pct_wtd = _window_stats(win_wtd)

    # 3. 本月 (MTD)
    win_mtd = [it for it in series if it["date"].year == trade_date.year and it["date"].month == trade_date.month]
    pnl_mtd, pct_mtd = _window_stats(win_mtd)

    # 4. 截至目标日的连续盈利/亏损天数
    last_pnl = series[-1]["pnl"]
    if last_pnl > 0:
        streak_days = 0
        for it in reversed(series):
            if it["pnl"] > 0:
                streak_days += 1
            else:
                break
    elif last_pnl < 0:
        streak_days = 0
        for it in reversed(series):
            if it["pnl"] < 0:
                streak_days -= 1
            else:
                break
    else:
        streak_days = 0

    # 5. 近 30 个交易日高点回撤（优先按累计净值曲线计算，无日收益率时退化按日末市值计算）
    win_30d = series[-30:]
    drawdown_30d_pct: Optional[Decimal] = None
    if win_30d and all(it["ret"] is not None for it in win_30d):
        nav = Decimal("1")
        peak = Decimal("1")
        for it in win_30d:
            nav *= Decimal("1") + it["ret"]
            if nav > peak:
                peak = nav
        if peak > 0:
            drawdown_30d_pct = (nav - peak) / peak
    else:
        mvs = [it["end_mv"] for it in win_30d if it["end_mv"] > 0]
        if mvs:
            peak_mv = max(mvs)
            if peak_mv > 0:
                drawdown_30d_pct = (mvs[-1] - peak_mv) / peak_mv

    return {
        "pnl_5d": _serialize_dec(pnl_5d),
        "pct_5d": _serialize_dec(pct_5d),
        "pnl_wtd": _serialize_dec(pnl_wtd),
        "pct_wtd": _serialize_dec(pct_wtd),
        "pnl_mtd": _serialize_dec(pnl_mtd),
        "pct_mtd": _serialize_dec(pct_mtd),
        "streak_days": streak_days,
        "drawdown_30d_pct": _serialize_dec(drawdown_30d_pct),
    }


def _compute_exposure_breakdown(
    holdings_list: List[Dict[str, Any]],
    total_mv: Decimal,
    total_assets: Optional[Decimal],
) -> Dict[str, List[Dict[str, Any]]]:
    """按底层资产与主题板块穿透聚合等效净敞口（杠杆 ETF * 倍数 + 对应正股/指数，全程 Decimal）。"""
    by_under: Dict[str, Dict[str, Any]] = {}
    by_sec: Dict[str, Dict[str, Any]] = {}

    for h in holdings_list:
        sym = str(h.get("symbol") or "").strip().upper().removesuffix(".US")
        mv = _to_dec(h.get("market_value"))
        if not sym or mv is None or mv <= 0:
            continue
        lev_factor = _to_dec(h.get("leverage_factor"))
        if lev_factor is None:
            lev_factor = Decimal("1")
        net_exp = mv * lev_factor

        # 底层资产归属：优先单股杠杆映射 -> 扩展宽基/杠杆映射 -> 自身 Ticker
        raw_u = str(h.get("underlying") or "").strip().upper().removesuffix(".US")
        under_key = raw_u or _EXTRA_UNDERLYING_MAP.get(sym) or sym
        under_label = _UNDERLYING_LABEL_MAP.get(under_key, under_key)

        u_bucket = by_under.setdefault(
            under_key,
            {"key": under_key, "label": under_label, "net_exposure": Decimal("0"), "symbols": []},
        )
        u_bucket["net_exposure"] += net_exp
        if sym not in u_bucket["symbols"]:
            u_bucket["symbols"].append(sym)

        # 主题/板块归属：先按 Ticker/底层映射，再回退账本分类或「其他」
        sec_label = (
            _SECTOR_TICKER_MAP.get(sym)
            or _SECTOR_TICKER_MAP.get(under_key)
            or (str(h.get("category") or "").strip() if str(h.get("category") or "").strip() not in ("", "杠杆", "正股") else "")
            or "其他"
        )
        s_bucket = by_sec.setdefault(
            sec_label,
            {"key": sec_label, "label": sec_label, "net_exposure": Decimal("0"), "symbols": []},
        )
        s_bucket["net_exposure"] += net_exp
        if sym not in s_bucket["symbols"]:
            s_bucket["symbols"].append(sym)

    def _finalize(buckets: Dict[str, Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        ranked = sorted(buckets.values(), key=lambda x: abs(x["net_exposure"]), reverse=True)[:limit]
        out: List[Dict[str, Any]] = []
        for item in ranked:
            exp_val: Decimal = item["net_exposure"]
            pct_mv = (exp_val / total_mv) if total_mv > 0 else None
            pct_ta = (exp_val / total_assets) if (total_assets is not None and total_assets > 0) else None
            out.append({
                "key": item["key"],
                "label": item["label"],
                "net_exposure": _serialize_dec(exp_val),
                "pct_of_mv": _serialize_dec(pct_mv),
                "pct_of_total_assets": _serialize_dec(pct_ta),
                "symbols": item["symbols"],
            })
        return out

    return {
        "by_underlying": _finalize(by_under, 5),
        "by_sector": _finalize(by_sec, 4),
    }


def build_after_hours_snapshot(
    db_path: Path,
    trade_date: date,
    person_key: str,
    person_id: Optional[int],
    person_name: str,
    quote_fetcher: Callable[[List[str]], Dict[str, Dict[str, Any]]] = fetch_after_hours_quotes,
    news_fetcher: Callable[[List[str]], Dict[str, List[Dict[str, Any]]]] = fetch_ticker_news,
    earnings_fetcher: Callable[[List[Dict[str, Any]], date, int], List[Dict[str, Any]]] = fetch_upcoming_earnings_events,
    prev_checklist_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建目标交易日的完整盘后事实快照（严格只读业务库 + 盘后行情 + 突发新闻）。"""
    svc = AnalyticsService(db_path)
    scope = QueryScope(
        as_of=trade_date,
        person_ids=(person_id,) if person_id is not None else None,
    )
    res = svc.calculate(scope)
    date_str = trade_date.isoformat()
    warnings: List[str] = []

    def _pick(vals, idx_map: Dict[str, int], names: tuple[str, ...], fallback_idx: Optional[int] = None):
        for name in names:
            if name in idx_map and idx_map[name] < len(vals):
                return vals[idx_map[name]]
        if fallback_idx is not None and 0 <= fallback_idx < len(vals):
            return vals[fallback_idx]
        return None

    # 1. 当日逐笔交易记录（从「交易回放」报表精确提取 business_date == date_str）
    trades: List[Dict[str, Any]] = []
    replay_rep = res.reports.get("交易回放")
    if replay_rep and replay_rep.rows:
        cols = list(replay_rep.columns)
        idx = {name: i for i, name in enumerate(cols)}
        for row in replay_rep.rows:
            vals = row.values
            r_date = str(_pick(vals, idx, ("交易日期", "日期", "业务日期"), 0) or "").strip()[:10]
            if r_date != date_str:
                continue
            sym = str(_pick(vals, idx, ("Ticker", "股票", "代码"), 2) or "").strip().upper().removesuffix(".US")
            side = str(_pick(vals, idx, ("方向", "买卖", "操作类型"), 3) or "").strip()
            qty = _to_dec(_pick(vals, idx, ("股数", "数量", "成交数量")))
            price = _to_dec(_pick(vals, idx, ("价格", "成交价($)", "成交价", "单价($)")))
            amount = _to_dec(_pick(vals, idx, ("金额", "成交金额($)", "发生金额($)", "金额($)")))
            channel = str(_pick(vals, idx, ("渠道", "券商"), 1) or "").strip()
            p_name = str(_pick(vals, idx, ("人员", "归属人员")) or person_name).strip()
            trades.append({
                "date": r_date,
                "person": p_name,
                "channel": channel,
                "symbol": sym,
                "side": side,
                "quantity": _serialize_dec(qty),
                "price": _serialize_dec(price),
                "amount": _serialize_dec(amount),
            })

    # 2. 当前持仓（聚合按股票维度，兼容 HOLDINGS_COLUMNS 真实列名）
    holdings_map: Dict[str, Dict[str, Any]] = {}
    hold_rep = res.reports.get("当前持仓")
    total_mv = Decimal("0")
    total_cost = Decimal("0")
    total_unrealized = Decimal("0")
    if hold_rep and hold_rep.rows:
        cols = list(hold_rep.columns)
        idx = {name: i for i, name in enumerate(cols)}
        for row in hold_rep.rows:
            vals = row.values
            sym = str(_pick(vals, idx, ("Ticker", "股票", "代码"), 2) or "").strip().upper().removesuffix(".US")
            if not sym:
                continue
            qty = _to_dec(_pick(vals, idx, ("持仓数量", "当前持仓数量", "股数"), 3)) or Decimal("0")
            if abs(qty) < Decimal("0.5"):
                continue
            mv = _to_dec(_pick(vals, idx, ("持仓市值($)", "当前市值($)", "市值"), 5)) or Decimal("0")
            cost = _to_dec(_pick(vals, idx, ("累计净投入($)", "持仓成本", "净投入"), 9)) or Decimal("0")
            pnl = _to_dec(_pick(vals, idx, ("持仓盈亏金额($)", "浮盈", "累计盈亏"), 10)) or Decimal("0")
            avg_cost = _to_dec(_pick(vals, idx, ("持仓成本价($)", "持仓均价", "成本价"), 6))
            channel = str(_pick(vals, idx, ("渠道", "券商"), 0) or "").strip()
            category = str(_pick(vals, idx, ("分类", "资产分类"), 1) or "").strip()
            total_mv += mv
            total_cost += cost
            total_unrealized += pnl

            item = holdings_map.get(sym)
            if item is None:
                item = {
                    "symbol": sym,
                    "underlying": DEFAULT_LINKS.get(f"{sym}.US", "").removesuffix(".US") or None,
                    "category": category,
                    "channels": [],
                    "quantity": Decimal("0"),
                    "market_value": Decimal("0"),
                    "cost": Decimal("0"),
                    "unrealized_pnl": Decimal("0"),
                    "avg_cost": avg_cost,
                }
                holdings_map[sym] = item
            if channel and channel not in item["channels"]:
                item["channels"].append(channel)
            item["quantity"] += qty
            item["market_value"] += mv
            item["cost"] += cost
            item["unrealized_pnl"] += pnl

    # 3. 当日盈亏（来自「每日持仓收益」目标日汇总，真实列名「当日收益($)」第 19 列）
    day_pnl_by_sym: Dict[str, Decimal] = {}
    total_day_pnl: Optional[Decimal] = None
    ledger_prev_mv = Decimal("0")
    daily_rep = res.reports.get("每日持仓收益")
    if daily_rep and daily_rep.rows:
        cols = list(daily_rep.columns)
        idx = {name: i for i, name in enumerate(cols)}
        day_sum = Decimal("0")
        matched_rows = 0
        for row in daily_rep.rows:
            vals = row.values
            r_date = str(_pick(vals, idx, ("日期", "交易日期"), 0) or "").strip()[:10]
            if r_date != date_str:
                continue
            matched_rows += 1
            dp = _to_dec(_pick(vals, idx, ("当日收益($)", "当日盈亏($)", "当日盈亏", "今日盈亏($)", "今日盈亏"), 19))
            if dp is not None:
                day_sum += dp
                s = str(_pick(vals, idx, ("Ticker", "股票", "代码"), 3) or "").strip().upper().removesuffix(".US")
                if s:
                    day_pnl_by_sym[s] = day_pnl_by_sym.get(s, Decimal("0")) + dp
            pmv = _to_dec(_pick(vals, idx, ("期初市值($)", "期初市值"), 15))
            if pmv is not None and pmv > 0:
                ledger_prev_mv += pmv
        if matched_rows > 0:
            total_day_pnl = day_sum

    # 4. 采集盘后行情（持仓 + 当日交易标的 + SPY/QQQ/SMH/^VIX/^TNX 市场风向标）
    all_symbols = set(holdings_map.keys()) | {t["symbol"] for t in trades if t.get("symbol")}
    quote_symbols = sorted(all_symbols | set(BENCHMARK_SYMBOLS))
    quotes = quote_fetcher(quote_symbols) if quote_symbols else {}
    if all_symbols and not quotes:
        warnings.append("未能拉取到盘后实时/收盘行情，部分标的价格将保持为空。")

    benchmarks: Dict[str, Dict[str, Any]] = {}
    for sym in BENCHMARK_SYMBOLS:
        if sym in quotes and isinstance(quotes[sym], dict):
            q_item = dict(quotes[sym])
            if sym == "^TNX":
                reg_d = _normalize_tnx_yield(_to_dec(q_item.get("regular_close")))
                prev_d = _normalize_tnx_yield(_to_dec(q_item.get("prev_close")))
                q_item["regular_close"] = _serialize_dec(reg_d)
                if prev_d is not None:
                    q_item["prev_close"] = _serialize_dec(prev_d)
                    if reg_d is not None and prev_d > 0 and q_item.get("regular_change_pct") is None:
                        q_item["regular_change_pct"] = _serialize_dec((reg_d - prev_d) / prev_d)
            benchmarks[sym] = q_item

    # 为当日每笔成交补充收盘价与相对收盘价差口径
    for t in trades:
        q = quotes.get(t.get("symbol") or "") or {}
        close_p = _to_dec(q.get("regular_close"))
        trade_p = _to_dec(t.get("price"))
        t["close_price"] = _serialize_dec(close_p)
        vs_close = None
        if close_p is not None and trade_p is not None and trade_p > 0:
            side_up = str(t.get("side") or "").upper()
            if side_up in ("BUY", "买入"):
                vs_close = (close_p - trade_p) / trade_p
            elif side_up in ("SELL", "卖出"):
                vs_close = (trade_p - close_p) / trade_p
        t["vs_close_pct"] = _serialize_dec(vs_close)

    # 判断当日盈亏口径：
    # 1. 若账本「每日持仓收益」已有目标日收盘记录，优先采用账本精确常规盘收益（包含留存持仓与当日清仓/卖出损益，与资产总览 100% 对齐），
    #    并在此基础上叠加当前留存持仓的盘后实时变动（after_hours_price - regular_close）；
    # 2. 若目标日账本尚无收盘记录，则回退使用行情估算常规盘 + 盘后。
    ledger_day_pnl = total_day_pnl
    ledger_by_sym = dict(day_pnl_by_sym)
    has_ledger_day = ledger_day_pnl is not None
    day_pnl_basis = "ledger"
    reg_day_pnl_by_sym: Dict[str, Decimal] = dict(ledger_by_sym) if has_ledger_day else {}
    ah_day_pnl_by_sym: Dict[str, Decimal] = {}
    est_reg_total: Optional[Decimal] = ledger_day_pnl if has_ledger_day else None
    est_ah_total: Optional[Decimal] = None
    est_prev_mv = Decimal("0")

    if holdings_map and quotes:
        est_reg_sum = Decimal("0")
        ah_sum = Decimal("0")
        est_count = 0
        ah_count = 0
        est_reg_by_sym: Dict[str, Decimal] = {}
        for sym, item in holdings_map.items():
            q = quotes.get(sym) or {}
            qty_d = item["quantity"]
            reg_c = _to_dec(q.get("regular_close"))
            prev_c = _to_dec(q.get("prev_close"))
            if reg_c is not None and prev_c is not None:
                reg_dp = qty_d * (reg_c - prev_c)
                est_reg_by_sym[sym] = reg_dp
                est_reg_sum += reg_dp
                est_prev_mv += qty_d * prev_c
                est_count += 1

            ah_p = _to_dec(q.get("after_hours_price"))
            if reg_c is not None and ah_p is not None:
                ah_dp = qty_d * (ah_p - reg_c)
                ah_day_pnl_by_sym[sym] = ah_dp
                ah_sum += ah_dp
                ah_count += 1

        if has_ledger_day:
            if ah_count > 0:
                est_ah_total = ah_sum
                total_day_pnl = (ledger_day_pnl or Decimal("0")) + ah_sum
                day_pnl_by_sym = {
                    s: ledger_by_sym.get(s, Decimal("0")) + ah_day_pnl_by_sym.get(s, Decimal("0"))
                    for s in set(ledger_by_sym) | set(ah_day_pnl_by_sym)
                }
                day_pnl_basis = "ledger_plus_after_hours"
            else:
                day_pnl_basis = "ledger"
        elif est_count > 0:
            reg_day_pnl_by_sym = est_reg_by_sym
            est_reg_total = est_reg_sum
            est_ah_total = ah_sum if ah_count > 0 else None
            total_day_pnl = est_reg_sum + (ah_sum if ah_count > 0 else Decimal("0"))
            day_pnl_by_sym = {
                s: est_reg_by_sym.get(s, Decimal("0")) + ah_day_pnl_by_sym.get(s, Decimal("0"))
                for s in set(est_reg_by_sym) | set(ah_day_pnl_by_sym)
            }
            day_pnl_basis = "quote_after_hours" if ah_count > 0 else "quote_regular"

    market_session = get_us_market_session(trade_date)
    if market_session.is_early_close:
        warnings.append(
            f"今日为美股半日市（{market_session.holiday_name}，常规盘 13:00 ET 收盘、盘后 17:00 ET 结束）。"
        )

    leverage_mv = Decimal("0")
    notional_exposure = Decimal("0")
    gross_notional_exposure = Decimal("0")
    cost_anomaly_symbols: List[str] = []
    anomaly_unrealized_sum = Decimal("0")
    holdings_list: List[Dict[str, Any]] = []
    for sym, item in sorted(holdings_map.items(), key=lambda kv: -kv[1]["market_value"]):
        q = quotes.get(sym) or {}
        qty_d = item["quantity"]
        mv_d = item["market_value"]
        cost_d = item["cost"]
        avg_d = (cost_d / qty_d) if qty_d > 0 else item["avg_cost"]
        weight = (mv_d / total_mv) if total_mv > 0 else None
        is_lev, lev_factor = resolve_leverage_profile(sym, item.get("category") or "")
        if is_lev and mv_d > 0:
            leverage_mv += mv_d
        if mv_d > 0:
            notional_exposure += mv_d * lev_factor
            gross_notional_exposure += mv_d * abs(lev_factor)

        ref_price = _to_dec(q.get("regular_close"))
        if ref_price is None and qty_d > 0 and mv_d > 0:
            ref_price = mv_d / qty_d

        is_zero_cost = bool(avg_d is not None and avg_d <= 0)
        need_breakeven = None
        if avg_d is not None and avg_d > 0 and ref_price is not None and ref_price > 0 and ref_price < avg_d:
            need_breakeven = (avg_d - ref_price) / ref_price

        # 异常成本 / 疑似未复权熔断校验（仅标注告警，绝不篡改账本原值）
        cost_anomaly = False
        cost_anomaly_reason: Optional[str] = None
        if avg_d is not None and avg_d > 0 and ref_price is not None and ref_price > 0:
            ratio = max(avg_d / ref_price, ref_price / avg_d)
            reg_chg_abs = abs(_to_dec(q.get("regular_change_pct")) or Decimal("0"))
            min_ratio = Decimal("8.0") if is_lev else Decimal("5.0")
            if ratio >= min_ratio and reg_chg_abs < Decimal("0.35"):
                cost_anomaly = True
                cost_anomaly_reason = (
                    f"现价 (${ref_price:.2f}) 与摊薄均价 (${avg_d:.2f}) 偏离达 {ratio:.1f}x，"
                    f"请核对是否存在未同步拆并股或历史成交录入价异常"
                )
                cost_anomaly_symbols.append(sym)
                anomaly_unrealized_sum += item["unrealized_pnl"]
                warnings.append(f"⚠️ {sym} {cost_anomaly_reason}。")

        holdings_list.append({
            "symbol": sym,
            "underlying": item["underlying"],
            "category": item["category"],
            "is_leveraged": is_lev,
            "leverage_factor": _serialize_dec(lev_factor),
            "channels": item["channels"],
            "quantity": _serialize_dec(qty_d),
            "avg_cost": _serialize_dec(avg_d),
            "avg_cost_basis": "diluted",
            "is_zero_cost": is_zero_cost,
            "cost_anomaly": cost_anomaly,
            "cost_anomaly_reason": cost_anomaly_reason,
            "market_value": _serialize_dec(mv_d),
            "weight_pct": _serialize_dec(weight),
            "need_breakeven_pct": _serialize_dec(need_breakeven),
            "unrealized_pnl": _serialize_dec(item["unrealized_pnl"]),
            "day_pnl": _serialize_dec(day_pnl_by_sym.get(sym)),
            "regular_day_pnl": _serialize_dec(reg_day_pnl_by_sym.get(sym)),
            "after_hours_day_pnl": _serialize_dec(ah_day_pnl_by_sym.get(sym)),
            "regular_close": q.get("regular_close"),
            "regular_change_pct": q.get("regular_change_pct"),
            "after_hours_price": q.get("after_hours_price"),
            "after_hours_change_pct": q.get("after_hours_change_pct"),
            "total_change_pct": q.get("total_change_pct"),
            "quote_source": q.get("source"),
        })

    # 汇总衍生指标：当日收益率、分母透明化、累计浮盈率、杠杆暴露率、组合等效敞口、相对 QQQ Alpha
    day_pnl_pct: Optional[Decimal] = None
    day_pnl_denominator: Optional[Decimal] = None
    day_pnl_denominator_label = "前日持仓市值"
    if total_day_pnl is not None:
        if day_pnl_basis in ("ledger", "ledger_plus_after_hours") and ledger_prev_mv > 0:
            day_pnl_denominator = ledger_prev_mv
            day_pnl_pct = total_day_pnl / ledger_prev_mv
        elif day_pnl_basis in ("quote_after_hours", "quote_regular") and est_prev_mv > 0:
            day_pnl_denominator = est_prev_mv
            day_pnl_pct = total_day_pnl / est_prev_mv
        elif total_mv > 0:
            prev_mv = total_mv - total_day_pnl
            if prev_mv > 0:
                day_pnl_denominator = prev_mv
                day_pnl_pct = total_day_pnl / prev_mv

    unrealized_pct: Optional[Decimal] = None
    if total_cost > 0:
        unrealized_pct = total_unrealized / total_cost
    unrealized_pnl_ex_anomaly: Optional[Decimal] = (
        total_unrealized - anomaly_unrealized_sum if cost_anomaly_symbols else None
    )

    leverage_pct = (leverage_mv / total_mv) if total_mv > 0 else Decimal("0")
    effective_leverage = (notional_exposure / total_mv) if total_mv > 0 else Decimal("0")
    qqq_bench = benchmarks.get("QQQ") or {}
    qqq_cmp_pct = (
        _to_dec(qqq_bench.get("total_change_pct"))
        if day_pnl_basis in ("ledger_plus_after_hours", "quote_after_hours") and qqq_bench.get("total_change_pct") is not None
        else _to_dec(qqq_bench.get("regular_change_pct"))
    )
    alpha_vs_qqq = (day_pnl_pct - qqq_cmp_pct) if (day_pnl_pct is not None and qqq_cmp_pct is not None) else None

    # 4.5 采集可用现金余额与总资产（净值）（仅当当前范围具备真实分渠道现金账本时返回，无独立账本保持 None 绝不伪造 0）
    cash_total: Optional[Decimal] = None
    cash_pct: Optional[Decimal] = None
    total_assets: Optional[Decimal] = None
    day_pnl_pct_total_assets: Optional[Decimal] = None
    effective_leverage_total_assets: Optional[Decimal] = None
    cash_breakdown: List[Dict[str, Any]] = []
    try:
        cash_details = resolve_channel_cash_details(res)
    except Exception:
        cash_details = {}
    if isinstance(cash_details, dict) and cash_details:
        c_sum = Decimal("0")
        for ch_key, info in cash_details.items():
            amt = _to_dec((info or {}).get("amount"))
            if amt is not None:
                c_sum += amt
                cash_breakdown.append({
                    "channel": str((info or {}).get("channel") or ch_key),
                    "amount": _serialize_dec(amt),
                    "source": str((info or {}).get("source") or ""),
                })
        if cash_breakdown:
            cash_breakdown.sort(key=lambda x: -(x.get("amount") or 0))
            cash_total = c_sum
            total_assets = total_mv + cash_total
            if total_assets > 0:
                cash_pct = cash_total / total_assets
                effective_leverage_total_assets = notional_exposure / total_assets
            if total_day_pnl is not None and day_pnl_denominator is not None:
                prev_total_assets = day_pnl_denominator + cash_total
                if prev_total_assets > 0:
                    day_pnl_pct_total_assets = total_day_pnl / prev_total_assets

    # 4.6 计算近期趋势与回撤坐标（纯本地 Decimal 计算）
    trend_stats = _compute_recent_trend(
        daily_rep,
        trade_date,
        current_day_pnl=total_day_pnl,
        current_prev_mv=day_pnl_denominator,
        current_total_mv=total_mv,
    )

    # 4.7 底层资产与板块穿透敞口聚合
    exposure_breakdown = _compute_exposure_breakdown(holdings_list, total_mv, total_assets)

    # 4.8 未来 7 天关键事件预警（持仓财报倒计时 + 美股高影响宏观大事件）
    try:
        upcoming_earnings = earnings_fetcher(holdings_list, trade_date, 7) if earnings_fetcher else []
    except Exception:
        upcoming_earnings = []
    upcoming_macro = get_upcoming_us_macro_events(trade_date, days_ahead=7)
    upcoming_events = {
        "earnings": upcoming_earnings or [],
        "macro": upcoming_macro or [],
    }

    # 5. 采集近期突发新闻
    news_map = news_fetcher(sorted(all_symbols)) if all_symbols else {}

    return {
        "trade_date": date_str,
        "person_key": person_key,
        "person_id": person_id,
        "person_name": person_name,
        "market_session": market_session.to_dict(),
        "summary": {
            "holding_count": len(holdings_list),
            "trade_count": len(trades),
            "total_market_value": _serialize_dec(total_mv),
            "total_assets": _serialize_dec(total_assets),
            "total_cost": _serialize_dec(total_cost),
            "total_unrealized_pnl": _serialize_dec(total_unrealized),
            "unrealized_pct": _serialize_dec(unrealized_pct),
            "unrealized_pnl_ex_anomaly": _serialize_dec(unrealized_pnl_ex_anomaly),
            "cost_anomaly_symbols": cost_anomaly_symbols,
            "day_pnl": _serialize_dec(total_day_pnl),
            "regular_day_pnl": _serialize_dec(est_reg_total),
            "after_hours_day_pnl": _serialize_dec(est_ah_total),
            "ledger_day_pnl": _serialize_dec(ledger_day_pnl),
            "day_pnl_pct": _serialize_dec(day_pnl_pct),
            "day_pnl_denominator": _serialize_dec(day_pnl_denominator),
            "day_pnl_denominator_label": day_pnl_denominator_label,
            "day_pnl_pct_total_assets": _serialize_dec(day_pnl_pct_total_assets),
            "day_pnl_basis": day_pnl_basis,
            "leverage_pct": _serialize_dec(leverage_pct),
            "effective_leverage": _serialize_dec(effective_leverage),
            "effective_leverage_total_assets": _serialize_dec(effective_leverage_total_assets),
            "gross_notional_exposure": _serialize_dec(gross_notional_exposure),
            "alpha_vs_qqq": _serialize_dec(alpha_vs_qqq),
            "cash_balance": _serialize_dec(cash_total),
            "cash_pct": _serialize_dec(cash_pct),
            "cash_breakdown": cash_breakdown,
            "trend": trend_stats,
        },
        "market_benchmarks": benchmarks,
        "exposure_breakdown": exposure_breakdown,
        "upcoming_events": upcoming_events,
        "prev_checklist_context": prev_checklist_context,
        "trades": trades,
        "holdings": holdings_list,
        "news": news_map,
        "warnings": warnings,
    }


def _dedupe_and_trim_news_for_prompt(
    news: Dict[str, List[Dict[str, Any]]],
    holdings: List[Dict[str, Any]],
    *,
    max_per_symbol: int = 2,
    max_total: int = 16,
) -> List[tuple[str, List[Dict[str, Any]]]]:
    """按持仓权重优先去重并精简传给 AI 的新闻（每只标的最多 2~3 条不同新闻，防提示词过长超时）。"""
    if not news:
        return []
    priority_syms: List[str] = []
    for h in holdings:
        for s in (h.get("symbol"), h.get("underlying")):
            su = str(s or "").strip().upper()
            if su and su in news and su not in priority_syms:
                priority_syms.append(su)
    for s in sorted(news.keys()):
        if s not in priority_syms:
            priority_syms.append(s)

    seen_norm: set[str] = set()
    seen_prefix: set[str] = set()
    total_cnt = 0
    result: List[tuple[str, List[Dict[str, Any]]]] = []
    for sym in priority_syms:
        if total_cnt >= max_total:
            break
        picked: List[Dict[str, Any]] = []
        for it in news.get(sym) or []:
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            norm = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower())
            prefix = norm[:42]
            if not norm or norm in seen_norm or (len(prefix) >= 24 and prefix in seen_prefix):
                continue
            seen_norm.add(norm)
            if prefix:
                seen_prefix.add(prefix)
            picked.append(it)
            total_cnt += 1
            if len(picked) >= max_per_symbol or total_cnt >= max_total:
                break
        if picked:
            result.append((sym, picked))
    return result


def _build_after_hours_prompt(snapshot: Dict[str, Any], sync_status: Dict[str, Any]) -> str:
    """将结构化盘后事实快照拼装为发给 Gemini 的双轨（JSON + Markdown）复盘指令。"""
    summary = snapshot.get("summary") or {}
    benchmarks = snapshot.get("market_benchmarks") or {}
    trades = snapshot.get("trades") or []
    holdings = snapshot.get("holdings") or []
    news = snapshot.get("news") or {}
    warnings = snapshot.get("warnings") or []

    bench_lines = []
    for sym in ("SPY", "QQQ", "SMH"):
        b = benchmarks.get(sym)
        if b:
            bench_lines.append(
                f"- {sym}: 常规收盘 {_fmt_dec(_to_dec(b.get('regular_close')))} "
                f"({_fmt_dec(_to_dec(b.get('regular_change_pct')), signed=True, pct=True)}) | "
                f"盘后 {_fmt_dec(_to_dec(b.get('after_hours_price')))} "
                f"({_fmt_dec(_to_dec(b.get('after_hours_change_pct')), signed=True, pct=True)})"
            )
    vix_b = benchmarks.get("^VIX")
    if vix_b:
        bench_lines.append(
            f"- VIX 恐慌指数: {_fmt_dec(_to_dec(vix_b.get('regular_close')))} "
            f"({_fmt_dec(_to_dec(vix_b.get('regular_change_pct')), signed=True, pct=True)})"
        )
    tnx_b = benchmarks.get("^TNX")
    if tnx_b and tnx_b.get("regular_close") is not None:
        tnx_val = _to_dec(tnx_b.get("regular_close"))
        bench_lines.append(
            f"- 10Y 美债收益率 (US10Y): {tnx_val:.2f}% "
            f"({_fmt_dec(_to_dec(tnx_b.get('regular_change_pct')), signed=True, pct=True)})"
        )
    bench_text = "\n".join(bench_lines) if bench_lines else "- 暂无大盘基准行情"

    trade_lines = []
    for t in trades:
        trade_lines.append(
            f"- [{t.get('channel') or '未知渠道'}] {t.get('side')} {t.get('symbol')} "
            f"{_fmt_dec(_to_dec(t.get('quantity')))} 股 @ ${_fmt_dec(_to_dec(t.get('price')))} "
            f"(金额 ${_fmt_dec(_to_dec(t.get('amount')))} | 当日收盘 ${_fmt_dec(_to_dec(t.get('close_price')))})"
        )
    trade_text = "\n".join(trade_lines) if trade_lines else "- 当日无成交记录"

    holding_lines = []
    for h in holdings[:30]:
        u_tag = f"（关联正股 {h['underlying']}）" if h.get("underlying") else ""
        lev_factor = _to_dec(h.get("leverage_factor"))
        if h.get("is_leveraged"):
            f_txt = f"{lev_factor:g}x " if lev_factor is not None else ""
            lev_tag = f" [{f_txt}杠杆]"
        else:
            lev_tag = ""
        anom_tag = " [⚠ 成本大幅偏离/待核对]" if h.get("cost_anomaly") else ""
        avg_val = _to_dec(h.get("avg_cost"))
        avg_txt = f"${_fmt_dec(avg_val)}"
        if h.get("is_zero_cost") or (avg_val is not None and avg_val <= 0):
            avg_txt += "（已零成本/利润仓）"
        holding_lines.append(
            f"- {h['symbol']}{u_tag}{lev_tag}{anom_tag} [{','.join(h.get('channels') or [])}]: "
            f"持股 {_fmt_dec(_to_dec(h.get('quantity')))} | 摊薄均价 {avg_txt} | "
            f"市值 ${_fmt_dec(_to_dec(h.get('market_value')))} (占比 {_fmt_dec(_to_dec(h.get('weight_pct')), pct=True)}) | "
            f"常规收盘 ${_fmt_dec(_to_dec(h.get('regular_close')))} ({_fmt_dec(_to_dec(h.get('regular_change_pct')), signed=True, pct=True)}) | "
            f"盘后 ${_fmt_dec(_to_dec(h.get('after_hours_price')))} ({_fmt_dec(_to_dec(h.get('after_hours_change_pct')), signed=True, pct=True)}) | "
            f"当日盈亏 ${_fmt_dec(_to_dec(h.get('day_pnl')), signed=True)}"
        )
    holding_text = "\n".join(holding_lines) if holding_lines else "- 当前无持仓"

    trimmed_news = _dedupe_and_trim_news_for_prompt(news, holdings, max_per_symbol=2, max_total=16)
    news_lines = []
    for sym, items in trimmed_news:
        for it in items:
            news_lines.append(
                f"- [{sym}] ({it.get('published_bj') or '近期'} · {it.get('publisher') or '媒体'}) {it.get('title')}"
            )
    news_text = "\n".join(news_lines) if news_lines else "- 近 36 小时未抓取到持仓/交易标的的显著突发新闻"

    sync_note = sync_status.get("message") or ("同步成功" if sync_status.get("ok") else "未执行同步")
    warn_text = "\n".join(f"- {w}" for w in warnings) if warnings else "无"

    reg_dp = _to_dec(summary.get("regular_day_pnl"))
    ah_dp = _to_dec(summary.get("after_hours_day_pnl"))
    pnl_split = ""
    if reg_dp is not None and ah_dp is not None:
        pnl_split = f"（常规盘 ${_fmt_dec(reg_dp, signed=True)} / 盘后 ${_fmt_dec(ah_dp, signed=True)}）"

    cash_bal = _to_dec(summary.get("cash_balance"))
    cash_pct = _to_dec(summary.get("cash_pct"))
    total_assets_val = _to_dec(summary.get("total_assets"))
    cash_bd = summary.get("cash_breakdown") or []
    if cash_bal is not None:
        ch_txt = "、".join(
            f"{c.get('channel')} ${_fmt_dec(_to_dec(c.get('amount')))}"
            for c in cash_bd
            if c.get("amount") is not None
        )
        ta_part = f"总资产（净值）${_fmt_dec(total_assets_val)} | " if total_assets_val is not None else ""
        cash_line = (
            f"- {ta_part}可用现金余额：${_fmt_dec(cash_bal)}（占总资产 {_fmt_dec(cash_pct, pct=True)}）"
            + (f" [{ch_txt}]" if ch_txt else "")
            + "\n"
        )
    else:
        cash_line = "- 可用现金余额：当前人员范围无独立现金账本（以「—」展示，勿编造金额）\n"

    eff_lev = _to_dec(summary.get("effective_leverage"))
    eff_lev_txt = f" | 组合等效敞口：{eff_lev:.2f}x" if eff_lev is not None else ""
    denom_val = _to_dec(summary.get("day_pnl_denominator"))
    denom_label = summary.get("day_pnl_denominator_label") or "前日持仓市值"
    denom_txt = f"（分母：{denom_label} ${_fmt_dec(denom_val)}）" if denom_val is not None else ""
    ta_day_pct = _to_dec(summary.get("day_pnl_pct_total_assets"))
    ta_day_txt = f" / 总资产日收益率 {_fmt_dec(ta_day_pct, signed=True, pct=True)}" if ta_day_pct is not None else ""
    ms = snapshot.get("market_session") or {}
    session_line = (
        f"- 交易时段：半日市（{ms.get('holiday_name') or '提前收盘'}，13:00 ET 收盘 / 17:00 ET 盘后结束）\n"
        if ms.get("is_early_close")
        else ""
    )

    # 近期趋势与回撤坐标
    trend = summary.get("trend") or {}
    trend_parts = []
    if trend.get("pnl_5d") is not None:
        t5 = f"近5日 ${_fmt_dec(_to_dec(trend.get('pnl_5d')), signed=True)}"
        if trend.get("pct_5d") is not None:
            t5 += f" ({_fmt_dec(_to_dec(trend.get('pct_5d')), signed=True, pct=True)})"
        trend_parts.append(t5)
    if trend.get("pnl_wtd") is not None:
        tw = f"本周 ${_fmt_dec(_to_dec(trend.get('pnl_wtd')), signed=True)}"
        if trend.get("pct_wtd") is not None:
            tw += f" ({_fmt_dec(_to_dec(trend.get('pct_wtd')), signed=True, pct=True)})"
        trend_parts.append(tw)
    if trend.get("pnl_mtd") is not None:
        tm = f"本月 ${_fmt_dec(_to_dec(trend.get('pnl_mtd')), signed=True)}"
        if trend.get("pct_mtd") is not None:
            tm += f" ({_fmt_dec(_to_dec(trend.get('pct_mtd')), signed=True, pct=True)})"
        trend_parts.append(tm)
    if trend.get("drawdown_30d_pct") is not None:
        trend_parts.append(f"距30日高点回撤 {_fmt_dec(_to_dec(trend.get('drawdown_30d_pct')), signed=True, pct=True)}")
    if trend.get("streak_days"):
        sd = int(trend["streak_days"])
        trend_parts.append(f"连盈 {sd} 日" if sd > 0 else f"连亏 {abs(sd)} 日")
    trend_line = f"- 近期趋势与回撤：{' | '.join(trend_parts)}\n" if trend_parts else ""

    # 底层资产与板块穿透敞口
    exp_bd = snapshot.get("exposure_breakdown") or {}
    under_items = exp_bd.get("by_underlying") or []
    sec_items = exp_bd.get("by_sector") or []
    exp_lines = []
    if under_items:
        u_str = "、".join(
            f"{it['label']} {_fmt_dec(_to_dec(it.get('pct_of_mv')), pct=True)} (${_fmt_dec(_to_dec(it.get('net_exposure')))})"
            for it in under_items[:5]
        )
        exp_lines.append(f"- 按底层资产穿透 Top 5（占持仓市值）：{u_str}")
    if sec_items:
        s_str = "、".join(
            f"{it['label']} {_fmt_dec(_to_dec(it.get('pct_of_mv')), pct=True)}"
            for it in sec_items[:4]
        )
        exp_lines.append(f"- 按主题板块穿透 Top 4（占持仓市值）：{s_str}")
    exposure_block = ("\n【底层资产与板块穿透敞口】\n" + "\n".join(exp_lines) + "\n") if exp_lines else ""

    # 未来 7 天关键事件预警
    up_ev = snapshot.get("upcoming_events") or {}
    up_lines = []
    for ev in up_ev.get("earnings") or []:
        up_lines.append(f"- [财报 · T+{ev.get('days_away')}天 · {ev.get('date')}] {ev.get('title')}")
    for ev in up_ev.get("macro") or []:
        up_lines.append(f"- [宏观 · T+{ev.get('days_away')}天 · {ev.get('date')}] {ev.get('title')}")
    upcoming_block = (
        "\n【未来 7 天关键事件预警（持仓财报 + 宏观大事件）】\n" + "\n".join(up_lines) + "\n"
        if up_lines
        else ""
    )

    # 昨日关注清单执行闭环
    prev_ctx = snapshot.get("prev_checklist_context") or {}
    prev_cl = prev_ctx.get("checklist") or []
    prev_checked = set(prev_ctx.get("checked_ids") or [])
    prev_block = ""
    if prev_cl:
        p_lines = [
            f"- [{'已勾选' if c.get('id') in prev_checked else '未勾选'}] [{c.get('ticker')} · {c.get('level')}] {c.get('text')}"
            for c in prev_cl[:8]
        ]
        prev_block = (
            f"\n【上一交易日 ({prev_ctx.get('prev_date')}) 关注清单及执行勾选情况】\n"
            + "\n".join(p_lines)
            + "\n"
        )

    return (
        f"你是一位资深美股投资组合经理与盘后复盘分析师。请严格基于以下真实账本、盘后行情与抓取到的新闻事实，"
        f"为【{snapshot.get('person_name')}】撰写一份 {snapshot.get('trade_date')} 的《美股盘后复盘日报》。\n\n"
        f"【同步与数据状态】\n"
        f"- 券商同步：{sync_note}\n"
        f"{session_line}"
        f"- 当日盈亏口径：{summary.get('day_pnl_basis', 'ledger')}{denom_txt}\n"
        f"- 告警提示：{warn_text}\n\n"
        f"【组合总览】\n"
        f"- 持仓标的数：{summary.get('holding_count', 0)} | 当日成交笔数：{summary.get('trade_count', 0)} | 杠杆仓位占比：{_fmt_dec(_to_dec(summary.get('leverage_pct')), pct=True)}{eff_lev_txt}\n"
        f"- 当前总市值：${_fmt_dec(_to_dec(summary.get('total_market_value')))} | "
        f"总持仓成本：${_fmt_dec(_to_dec(summary.get('total_cost')))} | "
        f"累计浮盈：${_fmt_dec(_to_dec(summary.get('total_unrealized_pnl')), signed=True)} | "
        f"当日真实盈亏：${_fmt_dec(_to_dec(summary.get('day_pnl')), signed=True)} (持仓日收益率 {_fmt_dec(_to_dec(summary.get('day_pnl_pct')), signed=True, pct=True)}{ta_day_txt}){pnl_split}\n"
        f"{cash_line}{trend_line}"
        f"{exposure_block}"
        f"\n【大盘基准与市场风向标】\n{bench_text}\n"
        f"{upcoming_block}"
        f"{prev_block}\n"
        f"【当日真实交易记录】\n{trade_text}\n\n"
        f"【当前持仓与盘后最终行情】\n{holding_text}\n\n"
        f"【近 36 小时抓取到的相关新闻】\n{news_text}\n\n"
        f"【输出格式要求（务必严格遵守）】\n"
        f"第一步：先输出一个合法的 ```json 代码块（供客户端渲染仪表盘），格式如下：\n"
        "```json\n"
        "{\n"
        '  "regime": {"label": "四到八字情绪标签（如 震荡承压 · 防御优先）", "tone": "success|warning|error"},\n'
        '  "tldr": "60~120字的一句话核心定性，讲清今天盈亏、跑赢/跑输大盘核心原因及明日重点",\n'
        '  "mover_reasons": {"标的Ticker": "结合涨跌幅、盘后表现或新闻的一句话归因"},\n'
        '  "news_sentiments": {"新闻原标题": "利好|利空|中性"},\n'
        '  "news_digest": [{"symbol": "Ticker", "original_title": "上方新闻原标题", "zh_title": "精炼中文标题(15~32字)", "summary": "一句话中文核心影响摘要(25~55字)", "sentiment": "利好|利空|中性"}],\n'
        '  "risks": [{"level": "高危|中危|提示", "title": "风险标题", "detail": "具体说明"}],\n'
        '  "checklist": [{"id": "c1", "ticker": "标的或大盘", "level": "关键价位或触发条件", "text": "具体可执行的盘前关注动作（写明价格阈值与应对动作）"}],\n'
        '  "prev_checklist_review": {"prev_date": "YYYY-MM-DD", "summary": "2~3句简明复盘昨日关键价位是否触发及执行纪律（无昨日清单时输出 null）", "items": [{"ticker": "标的", "status": "已触发|未触发|已执行|未执行", "note": "一句话说明"}]},\n'
        '  "trades_summary": "今日操作总体点评（一句话）",\n'
        '  "trade_reviews": [{"symbol": "Ticker", "side": "BUY|SELL", "verdict": "四字短评", "tone": "success|warning|neutral", "comment": "逐笔点评"}],\n'
        '  "followups": ["适合继续追问的简短问题1", "问题2", "问题3"]\n'
        "}\n"
        "```\n\n"
        f"补充要求：\n"
        f"- `news_digest` 必须对上方抓取的英文新闻去噪，每只重点持仓筛选 1~2 条最相关的高价值新闻，全部翻译为流畅简体中文标题 `zh_title`，并写明一句话核心影响摘要 `summary`；若无新闻则输出空数组 `[]`。\n"
        f"- `checklist` 输出 3~5 条，每条必须包含具体标的 + 关键价位/触发条件 + 明确应对动作（如「若盘前跌破 $XX 则减仓 Y%」）。\n"
        f"- 若上方提供了未来 7 天持仓财报或宏观大事件（FOMC/CPI/PCE/NFP），务必在 `risks` 与 `checklist` 中明确提示杠杆降仓或事件前避险安排；严禁编造未列出的事件。\n"
        f"- 若上方提供了上一交易日关注清单，请结合今日实际行情与交易记录在 `prev_checklist_review` 中如实复盘执行闭环；若未提供昨日清单，输出 `null`。\n\n"
        f"第二步：在 JSON 代码块之后，继续输出完整的 5 章节 Markdown 正文：\n"
        f"1. 严禁编造任何未在上方提供的价格、涨跌幅、成交记录或新闻；若某标的盘后价为「—」或无新闻，如实说明即可。\n"
        f"2. 章节标题严格使用：\n"
        f"   - ## 一、今日总览与大盘对比\n"
        f"   - ## 二、今日交易操作复盘\n"
        f"   - ## 三、重点持仓异动与盘后/新闻归因\n"
        f"   - ## 四、仓位结构与风险提示\n"
        f"   - ## 五、次日盘前关注清单"
    )


def _extract_markdown_section_bullets(markdown: str, heading_prefix: str) -> List[str]:
    """从旧版 Markdown 正文中提取指定章节下的要点行（用于兼容降级解析）。"""
    lines = (markdown or "").splitlines()
    in_sec = False
    bullets: List[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            if in_sec:
                break
            if heading_prefix in s:
                in_sec = True
            continue
        if not in_sec or not s:
            continue
        cleaned = re.sub(r"^(\d+\.|[-*•])\s*", "", s).replace("**", "").strip()
        if cleaned and len(cleaned) >= 6:
            bullets.append(cleaned)
    return bullets


def _normalize_structured(parsed: Optional[Dict[str, Any]], markdown: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """补齐并校验结构化字段，确保新旧日报均具备完整的 2.0 渲染数据。"""
    src = parsed if isinstance(parsed, dict) else {}
    summary = snapshot.get("summary") or {}
    holdings = snapshot.get("holdings") or []
    trades = snapshot.get("trades") or []

    day_pnl = _to_dec(summary.get("day_pnl"))
    lev_pct = _to_dec(summary.get("leverage_pct")) or Decimal("0")

    # 1. regime
    raw_regime = src.get("regime") if isinstance(src.get("regime"), dict) else {}
    tone = str(raw_regime.get("tone") or "").strip().lower()
    if tone not in ("success", "warning", "error"):
        if lev_pct >= Decimal("0.5") or (day_pnl is not None and day_pnl < Decimal("-500")):
            tone = "error"
        elif day_pnl is not None and day_pnl > 0:
            tone = "success"
        else:
            tone = "warning"
    label = str(raw_regime.get("label") or "").strip()
    if not label:
        label = (
            "高杠杆承压 · 防控回撤" if tone == "error"
            else ("多头进攻 · 表现占优" if tone == "success" else "震荡整理 · 控制节奏")
        )

    # 2. tldr
    tldr = str(src.get("tldr") or "").strip()
    if not tldr:
        sec1 = _extract_markdown_section_bullets(markdown, "一、")
        non_table = [b for b in sec1 if not b.startswith("|") and "---" not in b]
        if non_table:
            raw_tldr = " ".join(non_table[:2])
            raw_tldr = re.sub(r"^(一句话定性|核心定性|今日定性)[:：]\s*", "", raw_tldr)
            raw_tldr = raw_tldr.replace("`", "").replace("|", " ").strip()
            tldr = re.sub(r"\s{2,}", " ", raw_tldr)[:180]
        else:
            tldr = (
                f"{snapshot.get('trade_date')} {snapshot.get('person_name')} 组合持仓 {summary.get('holding_count', 0)} 只、"
                f"成交 {summary.get('trade_count', 0)} 笔，当日盈亏 {_fmt_dec(day_pnl, signed=True)}。"
            )

    # 3. mover_reasons
    raw_reasons = src.get("mover_reasons") if isinstance(src.get("mover_reasons"), dict) else {}
    mover_reasons: Dict[str, str] = {}
    for k, v in raw_reasons.items():
        sym = str(k).strip().upper().removesuffix(".US")
        if sym and str(v or "").strip():
            mover_reasons[sym] = str(v).strip()

    # 4. news_sentiments & news_digest（中文精炼标题 + 一句话影响摘要）
    raw_sentiments = src.get("news_sentiments") if isinstance(src.get("news_sentiments"), dict) else {}
    news_sentiments: Dict[str, str] = {}
    for k, v in raw_sentiments.items():
        val = str(v or "").strip()
        if val in ("利好", "利空", "中性"):
            news_sentiments[str(k).strip()] = val

    # 建立 snapshot["news"] 索引，用于反查 publisher / published_bj / url
    snap_news = snapshot.get("news") or {}
    news_lookup: Dict[str, Dict[str, Any]] = {}
    for s_sym, s_items in snap_news.items():
        for it in s_items or []:
            raw_t = str(it.get("title") or "").strip()
            if raw_t and raw_t not in news_lookup:
                news_lookup[raw_t] = {
                    "symbol": str(s_sym).strip().upper().removesuffix(".US"),
                    "publisher": str(it.get("publisher") or "").strip(),
                    "published_bj": str(it.get("published_bj") or "").strip(),
                    "url": str(it.get("link") or it.get("url") or "").strip(),
                }

    raw_digest = src.get("news_digest") if isinstance(src.get("news_digest"), list) else []
    news_digest: List[Dict[str, str]] = []
    news_zh_map: Dict[str, Dict[str, str]] = {}
    seen_orig_titles = set()
    for item in raw_digest[:24]:
        if not isinstance(item, dict):
            continue
        orig_t = str(item.get("original_title") or "").strip()
        zh_t = str(item.get("zh_title") or "").strip()
        sum_t = str(item.get("summary") or "").strip()
        if not (zh_t or orig_t):
            continue
        sent_val = str(item.get("sentiment") or "").strip()
        if sent_val not in ("利好", "利空", "中性"):
            sent_val = news_sentiments.get(orig_t, "中性")
        meta = news_lookup.get(orig_t, {})
        sym_val = str(item.get("symbol") or meta.get("symbol") or "").strip().upper().removesuffix(".US")
        dedup_key = orig_t or f"{sym_val}::{zh_t}"
        if dedup_key in seen_orig_titles:
            continue
        seen_orig_titles.add(dedup_key)

        entry = {
            "symbol": sym_val,
            "original_title": orig_t,
            "zh_title": zh_t or orig_t,
            "summary": sum_t,
            "sentiment": sent_val,
            "publisher": meta.get("publisher") or str(item.get("publisher") or "").strip(),
            "published_bj": meta.get("published_bj") or str(item.get("published_bj") or "").strip(),
            "url": meta.get("url") or str(item.get("url") or "").strip(),
        }
        news_digest.append(entry)
        if orig_t:
            news_sentiments.setdefault(orig_t, sent_val)
            news_zh_map[orig_t] = {
                "zh_title": entry["zh_title"],
                "summary": entry["summary"],
                "sentiment": sent_val,
            }

    # 5. risks
    raw_risks = src.get("risks") if isinstance(src.get("risks"), list) else []
    risks: List[Dict[str, str]] = []
    for r in raw_risks[:6]:
        if isinstance(r, dict) and (r.get("title") or r.get("detail")):
            lv = str(r.get("level") or "提示").strip()
            if lv not in ("高危", "中危", "提示"):
                lv = "中危"
            risks.append({
                "level": lv,
                "title": str(r.get("title") or "风险提示").strip(),
                "detail": str(r.get("detail") or "").strip(),
            })
    if not risks:
        for b in _extract_markdown_section_bullets(markdown, "四、")[:4]:
            risks.append({
                "level": "高危" if ("高危" in b or "杠杆" in b or "单一" in b) else "提示",
                "title": b[:22] + ("…" if len(b) > 22 else ""),
                "detail": b,
            })
    if not risks and lev_pct >= Decimal("0.35"):
        risks.append({
            "level": "高危" if lev_pct >= Decimal("0.5") else "中危",
            "title": f"杠杆 ETF 仓位占比 {_fmt_dec(lev_pct, pct=True)}",
            "detail": "高杠杆标的在震荡市中存在复利波动率损耗，建议关注仓位控制与止盈保护。",
        })

    # 6. checklist
    raw_cl = src.get("checklist") if isinstance(src.get("checklist"), list) else []
    checklist: List[Dict[str, str]] = []
    for idx, c in enumerate(raw_cl[:8], 1):
        if isinstance(c, dict) and str(c.get("text") or "").strip():
            checklist.append({
                "id": str(c.get("id") or f"c{idx}").strip(),
                "ticker": str(c.get("ticker") or "组合").strip(),
                "level": str(c.get("level") or "重点观察").strip(),
                "text": str(c.get("text") or "").strip(),
            })
    if not checklist:
        syms = [h.get("symbol") for h in holdings if h.get("symbol")]
        for idx, b in enumerate(_extract_markdown_section_bullets(markdown, "五、")[:5], 1):
            matched_sym = next((s for s in syms if s in b.upper()), "关注")
            checklist.append({
                "id": f"c{idx}",
                "ticker": matched_sym,
                "level": "盘前核查",
                "text": b,
            })

    # 7. trades_summary & trade_reviews
    trades_summary = str(src.get("trades_summary") or "").strip()
    if not trades_summary:
        if not trades:
            trades_summary = "当日无成交记录，保持原有持仓观察市场节奏。"
        else:
            trades_summary = f"当日共成交 {len(trades)} 笔交易，可结合各笔成交价与收盘价对比评估进出场节奏。"

    raw_tr = src.get("trade_reviews") if isinstance(src.get("trade_reviews"), list) else []
    trade_reviews: List[Dict[str, str]] = []
    for r in raw_tr:
        if isinstance(r, dict) and r.get("symbol"):
            tr_tone = str(r.get("tone") or "neutral").strip().lower()
            if tr_tone not in ("success", "warning", "neutral"):
                tr_tone = "neutral"
            trade_reviews.append({
                "symbol": str(r.get("symbol")).strip().upper(),
                "side": str(r.get("side") or "").strip().upper(),
                "verdict": str(r.get("verdict") or "已复盘").strip(),
                "tone": tr_tone,
                "comment": str(r.get("comment") or "").strip(),
            })

    # 8. prev_checklist_review（可选昨日计划复盘，老日报无此字段时保持 None）
    raw_prev_rev = src.get("prev_checklist_review")
    prev_checklist_review: Optional[Dict[str, Any]] = None
    if isinstance(raw_prev_rev, dict):
        p_sum = str(raw_prev_rev.get("summary") or "").strip()
        p_date = str(
            raw_prev_rev.get("prev_date")
            or (snapshot.get("prev_checklist_context") or {}).get("prev_date")
            or ""
        ).strip()
        p_items: List[Dict[str, str]] = []
        raw_p_items = raw_prev_rev.get("items") if isinstance(raw_prev_rev.get("items"), list) else []
        for it in raw_p_items[:8]:
            if not isinstance(it, dict):
                continue
            tk = str(it.get("ticker") or "").strip()
            nt = str(it.get("note") or "").strip()
            st_val = str(it.get("status") or "未触发").strip()
            if st_val not in ("已触发", "未触发", "已执行", "未执行"):
                st_val = "未触发"
            if tk or nt:
                p_items.append({"ticker": tk or "组合", "status": st_val, "note": nt})
        if p_sum or p_items:
            prev_checklist_review = {
                "prev_date": p_date,
                "summary": p_sum,
                "items": p_items,
            }

    # 9. followups
    raw_fq = src.get("followups") if isinstance(src.get("followups"), list) else []
    followups = [str(q).strip() for q in raw_fq if str(q).strip()][:4]
    if not followups:
        top_sym = holdings[0]["symbol"] if holdings else "核心持仓"
        followups = [
            f"如果明天盘前 {top_sym} 回调 3%，应该减仓控制风险还是做 T？",
            "帮我测算把高杠杆仓位降到更稳健区间的调仓方案",
            "把这份盘后复盘提炼成 200 字精简笔记",
        ]

    return {
        "regime": {"label": label, "tone": tone},
        "tldr": tldr,
        "mover_reasons": mover_reasons,
        "news_sentiments": news_sentiments,
        "news_digest": news_digest,
        "news_zh_map": news_zh_map,
        "risks": risks,
        "checklist": checklist,
        "prev_checklist_review": prev_checklist_review,
        "trades_summary": trades_summary,
        "trade_reviews": trade_reviews,
        "followups": followups,
    }


def parse_structured_report(raw_text: str, snapshot: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    """解析 Gemini 双轨输出：分离前置 JSON 块与 Markdown 正文，失败时无损降级。"""
    text = (raw_text or "").strip()
    parsed_json: Optional[Dict[str, Any]] = None
    markdown = text

    match = re.match(r"^\s*```(?:json)?\s*\n(.*?)\n```\s*\n?(.*)$", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        json_str, rest_md = match.group(1).strip(), match.group(2).strip()
        try:
            candidate = json.loads(json_str)
            if isinstance(candidate, dict):
                parsed_json = candidate
                markdown = rest_md or text
        except json.JSONDecodeError:
            parsed_json = None

    structured = _normalize_structured(parsed_json, markdown, snapshot)
    return structured, markdown


def ensure_structured(report: Dict[str, Any]) -> Dict[str, Any]:
    """确保任意历史/新版日报对象均携带完整的 structured、checked_ids 与 followups_history。"""
    if not isinstance(report, dict):
        return report
    snapshot = report.get("snapshot") or {}
    markdown = str(report.get("markdown") or "")
    existing = report.get("structured")
    if isinstance(existing, dict) and existing.get("tldr") and isinstance(existing.get("checklist"), list):
        report["structured"] = _normalize_structured(existing, markdown, snapshot)
    else:
        structured, clean_md = parse_structured_report(markdown, snapshot)
        report["structured"] = structured
        report["markdown"] = clean_md
    if not isinstance(report.get("checked_ids"), list):
        report["checked_ids"] = []
    if not isinstance(report.get("followups_history"), list):
        report["followups_history"] = []
    return report


def ask_after_hours_followup(
    report: Dict[str, Any],
    question: str,
    provider: str = "gemini",
    model: Optional[str] = None,
    store: Optional[AfterHoursReportStore] = None,
    ai_asker: Optional[Callable[[str, str, Optional[str]], str]] = None,
) -> Dict[str, Any]:
    """基于指定盘后日报的完整快照与复盘总结向 AI 发起追问，并落盘追问记录。"""
    q = (question or "").strip()
    if not q:
        raise ValueError("请输入追问问题")

    rep = ensure_structured(dict(report))
    snap = rep.get("snapshot") or {}
    summary = snap.get("summary") or {}
    structured = rep.get("structured") or {}
    prov = normalize_ai_provider(provider)
    chosen_model = (model or "").strip() or None

    hist_lines = []
    for item in (rep.get("followups_history") or [])[-6:]:
        hist_lines.append(f"用户：{item.get('question')}\n助手：{str(item.get('answer') or '')[:1200]}")
    hist_text = "\n\n".join(hist_lines) if hist_lines else "无"

    prompt = (
        f"你是一位熟悉用户资产情况的资深美股投顾。以下是用户 {rep.get('trade_date')}（范围：{rep.get('person_name')}）的盘后复盘总结与事实快照：\n\n"
        f"- 核心定性：{structured.get('tldr')}\n"
        f"- 当日盈亏：${_fmt_dec(_to_dec(summary.get('day_pnl')), signed=True)}（{_fmt_dec(_to_dec(summary.get('day_pnl_pct')), signed=True, pct=True)}） | "
        f"总市值：${_fmt_dec(_to_dec(summary.get('total_market_value')))} | 杠杆占比：{_fmt_dec(_to_dec(summary.get('leverage_pct')), pct=True)}\n"
        f"- 完整复盘要点：\n{str(rep.get('markdown') or '')[:4500]}\n\n"
        f"【历史追问上下文】\n{hist_text}\n\n"
        f"【用户本次追问】\n{q}\n\n"
        f"请直接结合上述持仓数据、成本与盘后走势给出具体、可落地的建议（Markdown 排版，避免空泛套话）。"
    )

    chosen_model = (model or "").strip() or (DEFAULT_AFTER_HOURS_MODEL if prov == "gemini" else None)
    if ai_asker is not None:
        answer = ai_asker(prompt, prov, chosen_model)
    else:
        answer = CodexAIService.ask(prompt, context="", model=chosen_model, provider=prov)

    target_store = store or AfterHoursReportStore()
    used_model = chosen_model or (DEFAULT_AFTER_HOURS_MODEL if prov == "gemini" else "")
    updated = target_store.append_followup(str(rep["id"]), q, answer, model=used_model)
    return {
        "answer": answer,
        "model": used_model,
        "followups_history": (updated or {}).get("followups_history") or [],
    }


def generate_after_hours_report(
    trade_date: Optional[date] = None,
    person_key: Optional[str] = "个人",
    person_id: Optional[int] = None,
    run_sync: bool = True,
    provider: str = "gemini",
    model: Optional[str] = None,
    trigger: str = "manual",
    db_path: Path = DATABASE_PATH,
    store: Optional[AfterHoursReportStore] = None,
    sync_runner: Callable[[], Dict[str, Any]] = run_broker_sync,
    quote_fetcher: Callable[[List[str]], Dict[str, Dict[str, Any]]] = fetch_after_hours_quotes,
    news_fetcher: Callable[[List[str]], Dict[str, List[Dict[str, Any]]]] = fetch_ticker_news,
    earnings_fetcher: Callable[[List[Dict[str, Any]], date, int], List[Dict[str, Any]]] = fetch_upcoming_earnings_events,
    ai_asker: Optional[Callable[[str, str, Optional[str]], str]] = None,
    allow_non_trading_day: bool = False,
) -> Dict[str, Any]:
    """执行完整盘后复盘生成流程：交易日历校验 -> 可选同步券商流水 -> 采集行情与新闻 -> Gemini 双轨总结 -> 落盘保存。"""
    target_date = trade_date or resolve_default_trade_date()
    session = get_us_market_session(target_date)
    if not session.is_trading_day and not allow_non_trading_day:
        raise ValueError(
            f"{target_date.isoformat()} 美股休市（{session.holiday_name}），今日美股休市，已跳过生成盘后复盘日报。"
        )
    target_store = store or AfterHoursReportStore()
    prov = normalize_ai_provider(provider)

    # 1. 可选先同步券商流水
    if run_sync:
        sync_status = sync_runner()
    else:
        sync_status = {
            "ran": False,
            "ok": True,
            "message": "按请求跳过券商同步，直接使用当前本地数据库",
            "finished_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        }

    # 2. 解析人员范围、提取昨日关注清单上下文并构建结构化快照
    p_key, p_id, p_name = resolve_person_scope(db_path, person_key=person_key, person_id=person_id)
    prev_checklist_context: Optional[Dict[str, Any]] = None
    prev_rep = target_store.get_previous_report(p_key, target_date.isoformat())
    if prev_rep:
        prev_cl = (prev_rep.get("structured") or {}).get("checklist") or []
        if prev_cl:
            prev_checklist_context = {
                "prev_date": str(prev_rep.get("trade_date") or ""),
                "checklist": prev_cl,
                "checked_ids": list(prev_rep.get("checked_ids") or []),
            }

    snapshot = build_after_hours_snapshot(
        db_path=db_path,
        trade_date=target_date,
        person_key=p_key,
        person_id=p_id,
        person_name=p_name,
        quote_fetcher=quote_fetcher,
        news_fetcher=news_fetcher,
        earnings_fetcher=earnings_fetcher,
        prev_checklist_context=prev_checklist_context,
    )
    if sync_status.get("ran") and not sync_status.get("ok"):
        snapshot.setdefault("warnings", []).append(sync_status.get("message") or "券商同步失败")

    # 3. 调用 AI 生成双轨复盘报告并解析结构化字段
    prompt = _build_after_hours_prompt(snapshot, sync_status)
    chosen_model = (model or "").strip() or (DEFAULT_AFTER_HOURS_MODEL if prov == "gemini" else None)
    if ai_asker is not None:
        raw_reply = ai_asker(prompt, prov, chosen_model)
    else:
        raw_reply = CodexAIService.ask(prompt, context="", model=chosen_model, provider=prov)

    structured, markdown = parse_structured_report(raw_reply, snapshot)

    date_str = target_date.isoformat()
    report_id = make_report_id(date_str, p_key)
    existing = target_store.get_report(report_id) or {}
    report = {
        "id": report_id,
        "trade_date": date_str,
        "person_key": p_key,
        "person_id": p_id,
        "person_name": p_name,
        "generated_at": datetime.now(BJ_TZ).isoformat(timespec="seconds"),
        "trigger": trigger,
        "provider": prov,
        "model": chosen_model or (CodexAIService.get_agy_model() if prov == "gemini" else ""),
        "sync_status": sync_status,
        "snapshot": snapshot,
        "structured": structured,
        "markdown": markdown,
        "checked_ids": existing.get("checked_ids") if isinstance(existing.get("checked_ids"), list) else [],
        "followups_history": existing.get("followups_history") if isinstance(existing.get("followups_history"), list) else [],
    }
    return target_store.save_report(report)


def should_auto_generate_report(existing_report: Optional[Dict[str, Any]], ny_day: date) -> bool:
    """判断自动调度器是否应为指定美东交易日生成/覆盖盘后复盘日报。

    - 若当日尚无存档，返回 True；
    - 若当日已有存档，但生成时间早于当日盘后自动触发时间（常规日美东 20:10 / 半日市美东 17:10），
      说明是盘前或盘中提前手动生成的临时版本，盘后结束后应自动用最终收盘+盘后数据重算覆盖，返回 True；
    - 若已在盘后触发窗口之后生成过，返回 False 避免重复触发。
    """
    if not existing_report:
        return True
    gen_raw = str(existing_report.get("generated_at") or "").strip()
    if not gen_raw:
        return False
    try:
        gen_dt = datetime.fromisoformat(gen_raw.replace("Z", "+00:00"))
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=BJ_TZ)
        gen_ny = gen_dt.astimezone(NY_TZ)
    except Exception:
        return False

    session = get_us_market_session(ny_day)
    if not session.is_trading_day:
        return False
    trig_time = session.recommended_trigger_et or dt_time(20, 10)
    cutoff_ny = datetime.combine(ny_day, trig_time, tzinfo=NY_TZ)
    return gen_ny < cutoff_ny


def _scheduler_loop(store: AfterHoursReportStore, db_path: Path, poll_seconds: int = 60):
    """后台轻量化定时线程：美东每个交易日盘后结束窗口自动为「个人」生成或补全盘后复盘日报。"""
    lock_path = store.path.with_name("after_hours_scheduler.lock")
    while True:
        try:
            ready, ny_day = is_after_hours_ready()
            if ready and db_path.is_file():
                p_key, _, _ = resolve_person_scope(db_path, person_key="个人")
                rid = make_report_id(ny_day.isoformat(), p_key)
                if should_auto_generate_report(store.get_report(rid), ny_day):
                    lock_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(lock_path, "w", encoding="utf-8") as lock_file:
                        try:
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            # 另一进程（桌面端或 Web 端）正在生成，跳过本次轮询
                            time.sleep(poll_seconds)
                            continue
                        try:
                            # 拿到文件锁后二次确认，避免重复生成
                            if should_auto_generate_report(store.get_report(rid), ny_day):
                                logger.info("[盘后复盘调度] 检测到美股交易日 %s 盘后已结束，开始自动同步并生成日报", ny_day)
                                generate_after_hours_report(
                                    trade_date=ny_day,
                                    person_key="个人",
                                    run_sync=True,
                                    provider="gemini",
                                    trigger="auto",
                                    db_path=db_path,
                                    store=store,
                                )
                                logger.info("[盘后复盘调度] 交易日 %s 盘后复盘日报自动生成完成", ny_day)
                        finally:
                            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.warning("[盘后复盘调度] 后台检查或生成异常: %s", exc)
        time.sleep(poll_seconds)


def start_after_hours_scheduler(db_path: Path = DATABASE_PATH, store: Optional[AfterHoursReportStore] = None) -> bool:
    """幂等启动盘后复盘自动调度后台守护线程（每个进程仅启动一次）。"""
    global _SCHEDULER_STARTED
    if os.environ.get("MONEY3_DISABLE_AFTER_HOURS_SCHEDULER") == "1":
        return False
    with _SCHEDULER_LOCK:
        if _SCHEDULER_STARTED:
            return False
        _SCHEDULER_STARTED = True
        target_store = store or AfterHoursReportStore()
        t = threading.Thread(
            target=_scheduler_loop,
            args=(target_store, db_path),
            daemon=True,
            name="money3-after-hours-scheduler",
        )
        t.start()
        return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="手动或定时生成美股盘后复盘日报")
    parser.add_argument("--date", help="交易日期 YYYY-MM-DD（默认最近已收盘/当前交易日）")
    parser.add_argument("--person", default="个人", help="人员范围：个人 / ALL / 指定人员名")
    parser.add_argument("--no-sync", action="store_true", help="跳过券商流水同步，直接使用当前数据库")
    parser.add_argument("--provider", default="gemini", help="AI 引擎（gemini / google_api / codex）")
    parser.add_argument("--model", default=None, help="指定模型名称")
    args = parser.parse_args(argv)

    t_date = date.fromisoformat(args.date) if args.date else resolve_default_trade_date()
    rep = generate_after_hours_report(
        trade_date=t_date,
        person_key=args.person,
        run_sync=not args.no_sync,
        provider=args.provider,
        model=args.model,
        trigger="cli",
    )
    print(f"[OK] 已生成盘后复盘日报: {rep['id']} (同步: {rep['sync_status'].get('message')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
