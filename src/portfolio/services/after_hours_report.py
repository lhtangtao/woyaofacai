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
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from portfolio.domain.models import QueryScope
from portfolio.paths import DATABASE_PATH, PROJECT_DIR
from portfolio.services.ai_service import CodexAIService, normalize_ai_provider
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.earnings_links import DEFAULT_LINKS

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
BJ_TZ = ZoneInfo("Asia/Shanghai")

AFTER_HOURS_FILENAME = "after_hours_reports.json"
_STORE_LOCK = threading.RLock()
_SCHEDULER_LOCK = threading.Lock()
_SCHEDULER_STARTED = False


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
    """返回默认应复盘的美股交易日（盘后 20:00 后为当日，否则回退至上一个工作日）。"""
    now = now_ny or current_ny_now()
    d = now.date()
    if d.weekday() >= 5:
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    if now.hour < 16:
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


def is_after_hours_ready(now_ny: Optional[datetime] = None, trigger_hour: int = 20, trigger_minute: int = 10) -> tuple[bool, date]:
    """判断当前美东时间是否已到达交易日盘后自动生成窗口（默认美东 20:10 后）。"""
    now = now_ny or current_ny_now()
    d = now.date()
    if d.weekday() >= 5:
        return False, resolve_default_trade_date(now)
    # 若本地交易日历明确标记今天休市则跳过
    try:
        from portfolio.services.quote_store import QuoteStore
        store = QuoteStore()
        cal = store.trading_days(d, d)
        if cal is not None and len(cal) == 0:
            # 确认日历已覆盖该日期（避免空白日历误判）
            covered = store.trading_days(d - timedelta(days=7), d + timedelta(days=7))
            if covered and d not in covered:
                return False, resolve_default_trade_date(now)
    except Exception:
        pass
    ready = (now.hour > trigger_hour) or (now.hour == trigger_hour and now.minute >= trigger_minute)
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
                summary = (rep.get("snapshot") or {}).get("summary") or {}
                items.append({
                    "id": rep.get("id"),
                    "trade_date": rep.get("trade_date"),
                    "person_key": rep.get("person_key"),
                    "person_id": rep.get("person_id"),
                    "person_name": rep.get("person_name"),
                    "generated_at": rep.get("generated_at"),
                    "trigger": rep.get("trigger", "manual"),
                    "provider": rep.get("provider", "gemini"),
                    "model": rep.get("model", ""),
                    "sync_status": rep.get("sync_status") or {},
                    "day_pnl": summary.get("day_pnl"),
                    "total_market_value": summary.get("total_market_value"),
                    "trade_count": summary.get("trade_count", 0),
                    "holding_count": summary.get("holding_count", 0),
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
        if creds:
            cfg = Config(creds["app_key"], creds["app_secret"], creds["access_token"])
            ctx = QuoteContext(cfg)
            lb_syms = [f"{s}.US" for s in clean_syms]
            quotes = ctx.quote(lb_syms)
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

    # 2. 对未获取到行情的标的降级查询 Yahoo Finance Chart API
    missing = [s for s in clean_syms if s not in result]
    if missing:
        def _fetch_yahoo_quote(sym: str) -> Optional[Dict[str, Any]]:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d&includePrePost=true"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Money3/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                meta = (((payload.get("chart") or {}).get("result") or [{}])[0]).get("meta") or {}
                reg_price = _to_dec(meta.get("regularMarketPrice"))
                prev_close = _to_dec(meta.get("chartPreviousClose") or meta.get("previousClose"))
                post_price = _to_dec(meta.get("postMarketPrice"))
                if reg_price is None:
                    return None
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


def build_after_hours_snapshot(
    db_path: Path,
    trade_date: date,
    person_key: str,
    person_id: Optional[int],
    person_name: str,
    quote_fetcher: Callable[[List[str]], Dict[str, Dict[str, Any]]] = fetch_after_hours_quotes,
    news_fetcher: Callable[[List[str]], Dict[str, List[Dict[str, Any]]]] = fetch_ticker_news,
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

    # 3. 当日盈亏（来自「每日持仓收益」目标日汇总）
    day_pnl_by_sym: Dict[str, Decimal] = {}
    total_day_pnl: Optional[Decimal] = None
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
            dp = _to_dec(_pick(vals, idx, ("当日盈亏($)", "当日盈亏", "今日盈亏($)", "今日盈亏"), 18))
            if dp is not None:
                day_sum += dp
                s = str(_pick(vals, idx, ("Ticker", "股票", "代码"), 2) or "").strip().upper().removesuffix(".US")
                if s:
                    day_pnl_by_sym[s] = day_pnl_by_sym.get(s, Decimal("0")) + dp
        if matched_rows > 0:
            total_day_pnl = day_sum

    # 4. 采集盘后行情（持仓 + 当日交易标的 + SPY/QQQ 大盘基准）
    all_symbols = set(holdings_map.keys()) | {t["symbol"] for t in trades if t.get("symbol")}
    quote_symbols = sorted(all_symbols | {"SPY", "QQQ"})
    quotes = quote_fetcher(quote_symbols) if quote_symbols else {}
    if all_symbols and not quotes:
        warnings.append("未能拉取到盘后实时/收盘行情，部分标的价格将保持为空。")

    benchmarks = {
        sym: quotes[sym]
        for sym in ("SPY", "QQQ")
        if sym in quotes
    }

    holdings_list: List[Dict[str, Any]] = []
    for sym, item in sorted(holdings_map.items(), key=lambda kv: -kv[1]["market_value"]):
        q = quotes.get(sym) or {}
        qty_d = item["quantity"]
        mv_d = item["market_value"]
        cost_d = item["cost"]
        avg_d = (cost_d / qty_d) if qty_d > 0 else item["avg_cost"]
        weight = (mv_d / total_mv) if total_mv > 0 else None
        holdings_list.append({
            "symbol": sym,
            "underlying": item["underlying"],
            "category": item["category"],
            "channels": item["channels"],
            "quantity": _serialize_dec(qty_d),
            "avg_cost": _serialize_dec(avg_d),
            "market_value": _serialize_dec(mv_d),
            "weight_pct": _serialize_dec(weight),
            "unrealized_pnl": _serialize_dec(item["unrealized_pnl"]),
            "day_pnl": _serialize_dec(day_pnl_by_sym.get(sym)),
            "regular_close": q.get("regular_close"),
            "regular_change_pct": q.get("regular_change_pct"),
            "after_hours_price": q.get("after_hours_price"),
            "after_hours_change_pct": q.get("after_hours_change_pct"),
            "total_change_pct": q.get("total_change_pct"),
            "quote_source": q.get("source"),
        })

    # 5. 采集近期突发新闻
    news_map = news_fetcher(sorted(all_symbols)) if all_symbols else {}

    return {
        "trade_date": date_str,
        "person_key": person_key,
        "person_id": person_id,
        "person_name": person_name,
        "summary": {
            "holding_count": len(holdings_list),
            "trade_count": len(trades),
            "total_market_value": _serialize_dec(total_mv),
            "total_cost": _serialize_dec(total_cost),
            "total_unrealized_pnl": _serialize_dec(total_unrealized),
            "day_pnl": _serialize_dec(total_day_pnl),
        },
        "market_benchmarks": benchmarks,
        "trades": trades,
        "holdings": holdings_list,
        "news": news_map,
        "warnings": warnings,
    }


def _build_after_hours_prompt(snapshot: Dict[str, Any], sync_status: Dict[str, Any]) -> str:
    """将结构化盘后事实快照拼装为发给 Gemini 的严谨中文复盘指令。"""
    summary = snapshot.get("summary") or {}
    benchmarks = snapshot.get("market_benchmarks") or {}
    trades = snapshot.get("trades") or []
    holdings = snapshot.get("holdings") or []
    news = snapshot.get("news") or {}
    warnings = snapshot.get("warnings") or []

    bench_lines = []
    for sym in ("SPY", "QQQ"):
        b = benchmarks.get(sym)
        if b:
            bench_lines.append(
                f"- {sym}: 常规收盘 {_fmt_dec(_to_dec(b.get('regular_close')))} "
                f"({_fmt_dec(_to_dec(b.get('regular_change_pct')), signed=True, pct=True)}) | "
                f"盘后 {_fmt_dec(_to_dec(b.get('after_hours_price')))} "
                f"({_fmt_dec(_to_dec(b.get('after_hours_change_pct')), signed=True, pct=True)})"
            )
    bench_text = "\n".join(bench_lines) if bench_lines else "- 暂无大盘基准行情"

    trade_lines = []
    for t in trades:
        trade_lines.append(
            f"- [{t.get('channel') or '未知渠道'}] {t.get('side')} {t.get('symbol')} "
            f"{_fmt_dec(_to_dec(t.get('quantity')))} 股 @ ${_fmt_dec(_to_dec(t.get('price')))} "
            f"(金额 ${_fmt_dec(_to_dec(t.get('amount')))})"
        )
    trade_text = "\n".join(trade_lines) if trade_lines else "- 当日无成交记录"

    holding_lines = []
    for h in holdings[:30]:
        u_tag = f"（关联正股 {h['underlying']}）" if h.get("underlying") else ""
        holding_lines.append(
            f"- {h['symbol']}{u_tag} [{','.join(h.get('channels') or [])}]: "
            f"持股 {_fmt_dec(_to_dec(h.get('quantity')))} | 均价 ${_fmt_dec(_to_dec(h.get('avg_cost')))} | "
            f"市值 ${_fmt_dec(_to_dec(h.get('market_value')))} (占比 {_fmt_dec(_to_dec(h.get('weight_pct')), pct=True)}) | "
            f"常规收盘 ${_fmt_dec(_to_dec(h.get('regular_close')))} ({_fmt_dec(_to_dec(h.get('regular_change_pct')), signed=True, pct=True)}) | "
            f"盘后 ${_fmt_dec(_to_dec(h.get('after_hours_price')))} ({_fmt_dec(_to_dec(h.get('after_hours_change_pct')), signed=True, pct=True)}) | "
            f"当日盈亏 ${_fmt_dec(_to_dec(h.get('day_pnl')), signed=True)}"
        )
    holding_text = "\n".join(holding_lines) if holding_lines else "- 当前无持仓"

    news_lines = []
    for sym, items in sorted(news.items()):
        for it in items:
            news_lines.append(
                f"- [{sym}] ({it.get('published_bj') or '近期'} · {it.get('publisher') or '媒体'}) {it.get('title')}"
            )
    news_text = "\n".join(news_lines) if news_lines else "- 近 36 小时未抓取到持仓/交易标的的显著突发新闻"

    sync_note = sync_status.get("message") or ("同步成功" if sync_status.get("ok") else "未执行同步")
    warn_text = "\n".join(f"- {w}" for w in warnings) if warnings else "无"

    return (
        f"你是一位资深美股投资组合经理与盘后复盘分析师。请严格基于以下真实账本、盘后行情与抓取到的新闻事实，"
        f"为【{snapshot.get('person_name')}】撰写一份 {snapshot.get('trade_date')} 的《美股盘后复盘日报》。\n\n"
        f"【同步与数据状态】\n"
        f"- 券商同步：{sync_note}\n"
        f"- 告警提示：{warn_text}\n\n"
        f"【组合总览】\n"
        f"- 持仓标的数：{summary.get('holding_count', 0)} | 当日成交笔数：{summary.get('trade_count', 0)}\n"
        f"- 当前总市值：${_fmt_dec(_to_dec(summary.get('total_market_value')))} | "
        f"总持仓成本：${_fmt_dec(_to_dec(summary.get('total_cost')))} | "
        f"累计浮盈：${_fmt_dec(_to_dec(summary.get('total_unrealized_pnl')), signed=True)} | "
        f"当日盈亏：${_fmt_dec(_to_dec(summary.get('day_pnl')), signed=True)}\n\n"
        f"【大盘基准 (盘中 + 盘后)】\n{bench_text}\n\n"
        f"【当日真实交易记录】\n{trade_text}\n\n"
        f"【当前持仓与盘后最终行情】\n{holding_text}\n\n"
        f"【近 36 小时抓取到的相关新闻】\n{news_text}\n\n"
        f"【写作与严谨性要求】\n"
        f"1. 严禁编造任何未在上方提供的价格、涨跌幅、成交记录或新闻；若某标的盘后价为「—」或无新闻，如实说明即可。\n"
        f"2. 请按以下 5 个清晰 Markdown 章节输出，观点犀利、数据准确、可直接阅读：\n"
        f"   - ## 一、今日总览与大盘对比（一句话定性今天表现 + 盈亏核心驱动）\n"
        f"   - ## 二、今日交易操作复盘（逐笔点评今日买卖/做 T 的时机、价位与成本影响；若当日无交易则点评空仓/持股不动是否合理）\n"
        f"   - ## 三、重点持仓异动与盘后/新闻归因（重点分析涨跌幅较大及盘后显著波动的标的，结合抓取到的真实新闻与杠杆 ETF 关联正股）\n"
        f"   - ## 四、仓位结构与风险提示（集中度、杠杆 ETF 波动风险、盘后跳空风险）\n"
        f"   - ## 五、次日盘前关注清单（给出 3~5 条具体、可盯盘的观察点）"
    )


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
    ai_asker: Optional[Callable[[str, str, Optional[str]], str]] = None,
) -> Dict[str, Any]:
    """执行完整盘后复盘生成流程：可选同步券商流水 -> 采集行情与新闻 -> Gemini 总结 -> 落盘保存。"""
    target_date = trade_date or resolve_default_trade_date()
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

    # 2. 解析人员范围并构建结构化快照
    p_key, p_id, p_name = resolve_person_scope(db_path, person_key=person_key, person_id=person_id)
    snapshot = build_after_hours_snapshot(
        db_path=db_path,
        trade_date=target_date,
        person_key=p_key,
        person_id=p_id,
        person_name=p_name,
        quote_fetcher=quote_fetcher,
        news_fetcher=news_fetcher,
    )
    if sync_status.get("ran") and not sync_status.get("ok"):
        snapshot.setdefault("warnings", []).append(sync_status.get("message") or "券商同步失败")

    # 3. 调用 AI 生成复盘报告
    prompt = _build_after_hours_prompt(snapshot, sync_status)
    chosen_model = (model or "").strip() or None
    if ai_asker is not None:
        markdown = ai_asker(prompt, prov, chosen_model)
    else:
        markdown = CodexAIService.ask(prompt, context="", model=chosen_model, provider=prov)

    date_str = target_date.isoformat()
    report = {
        "id": make_report_id(date_str, p_key),
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
        "markdown": markdown,
    }
    return target_store.save_report(report)


def _scheduler_loop(store: AfterHoursReportStore, db_path: Path, poll_seconds: int = 60):
    """后台轻量化定时线程：美东每个交易日 20:10 后自动为「个人」生成盘后复盘日报。"""
    lock_path = store.path.with_name("after_hours_scheduler.lock")
    while True:
        try:
            ready, ny_day = is_after_hours_ready()
            if ready and db_path.is_file():
                p_key, _, _ = resolve_person_scope(db_path, person_key="个人")
                if not store.has_report(ny_day.isoformat(), p_key):
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
                            if not store.has_report(ny_day.isoformat(), p_key):
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
