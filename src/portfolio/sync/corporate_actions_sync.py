"""启动自动检查并同步美股拆/并股公司行动（corporate_actions）。

设计原则：
1. 默认只读扫描数据库与外部拆股事件；未发现新拆股时绝不以写入模式打开源数据库。
2. 仅补充晚于该标的首次交易日期且 <= 当日的全新拆/并股事件；同一标的 ±7 天内已有
   同方向公司行动记录或已存在相同 action_key 时自动跳过，杜绝重复拆股。
3. 发现新拆股后在单事务中规范写入 import_batches -> raw_records -> corporate_actions，
   完整保留可追溯审计来源，不修改任何历史成交流水。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Callable
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PARSER_VERSION = "yahoo_splits_v1"


@dataclass(frozen=True)
class SplitEvent:
    symbol: str
    effective_date: str  # YYYY-MM-DD (美东除权交易日)
    action_type: str     # "SPLIT" or "REVERSE_SPLIT"
    ratio_numerator: int
    ratio_denominator: int
    split_ratio_text: str
    raw_payload: dict


@dataclass(frozen=True)
class SplitSyncResult:
    checked_symbols: int
    inserted: tuple[SplitEvent, ...]
    skipped_existing: int
    errors: tuple[str, ...]

    @property
    def summary_text(self) -> str:
        if not self.inserted:
            return ""
        items = []
        for ev in self.inserted:
            kind = "拆股" if ev.action_type == "SPLIT" else "合股"
            items.append(f"{ev.symbol} ({ev.effective_date} {ev.ratio_denominator}→{ev.ratio_numerator} {kind})")
        return "已自动同步公司行动：" + "、".join(items)


def _to_exact_ratio(numerator, denominator) -> tuple[int, int] | None:
    """将拆/并股比例转为最简正整数分数 (numerator, denominator)。"""
    try:
        frac = Fraction(str(numerator)) / Fraction(str(denominator))
    except Exception:
        return None
    if frac <= 0 or frac == 1:
        return None
    return int(frac.numerator), int(frac.denominator)


def fetch_yahoo_splits(symbol: str, *, range_str: str = "5y", timeout: float = 10.0) -> list[SplitEvent]:
    """从 Yahoo Finance Chart Events API 拉取单个美股标的的拆/并股事件。"""
    clean = (symbol or "").strip().upper().removesuffix(".US")
    if not clean:
        return []
    encoded = urllib.parse.quote(clean, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range={range_str}&interval=1d&events=splits"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))

    chart = data.get("chart") if isinstance(data, dict) else None
    results = chart.get("result") if isinstance(chart, dict) else None
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return []

    splits_map = (results[0].get("events") or {}).get("splits") or {}
    events: list[SplitEvent] = []
    for _, info in sorted(splits_map.items()):
        if not isinstance(info, dict):
            continue
        ts = info.get("date")
        if ts is None:
            continue
        ratio = _to_exact_ratio(info.get("numerator"), info.get("denominator"))
        if not ratio:
            continue
        num, den = ratio
        eff_date = datetime.fromtimestamp(int(ts), NY).date().isoformat()
        action_type = "SPLIT" if num > den else "REVERSE_SPLIT"
        events.append(
            SplitEvent(
                symbol=clean,
                effective_date=eff_date,
                action_type=action_type,
                ratio_numerator=num,
                ratio_denominator=den,
                split_ratio_text=str(info.get("splitRatio") or f"{num}:{den}"),
                raw_payload=dict(info),
            )
        )
    return events


def _load_candidates_readonly(db_path: Path) -> tuple[list[tuple[int, str, str]], dict[int, list[tuple[str, str, str]]]]:
    """只读读取有交易记录的美股标的（含最早交易日）及已有 corporate_actions。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # 收集每个标的在 person_operations 或 broker_trades 中的最早交易日期
        first_dates: dict[int, str] = {}
        for sql in (
            "SELECT instrument_id, MIN(business_date) AS d FROM person_operations WHERE business_date IS NOT NULL GROUP BY instrument_id",
            "SELECT instrument_id, MIN(business_date) AS d FROM broker_trades WHERE business_date IS NOT NULL GROUP BY instrument_id",
        ):
            for row in conn.execute(sql):
                iid = int(row["instrument_id"])
                d = str(row["d"] or "")
                if d and (iid not in first_dates or d < first_dates[iid]):
                    first_dates[iid] = d

        candidates: list[tuple[int, str, str]] = []
        for row in conn.execute("SELECT id, symbol, market FROM instruments"):
            iid = int(row["id"])
            sym = str(row["symbol"] or "").strip().upper()
            market = str(row["market"] or "US").strip().upper()
            if not sym or iid not in first_dates:
                continue
            # 仅检查美股代码（排除纯数字港/A股代码）
            if market not in ("", "US", "NASDAQ", "NYSE", "AMEX", "ARCA"):
                continue
            if sym.isdigit():
                continue
            candidates.append((iid, sym, first_dates[iid]))

        existing: dict[int, list[tuple[str, str, str]]] = {}
        for row in conn.execute(
            "SELECT instrument_id, effective_date, action_type, action_key FROM corporate_actions"
        ):
            iid = int(row["instrument_id"])
            existing.setdefault(iid, []).append(
                (str(row["effective_date"]), str(row["action_type"]), str(row["action_key"]))
            )
        return candidates, existing
    finally:
        conn.close()


def _is_already_recorded(
    existing_rows: list[tuple[str, str, str]],
    instrument_id: int,
    ev: SplitEvent,
) -> bool:
    """判断拆股事件是否已存在（完全同键，或 ±7 天内存在同方向公司行动）。"""
    action_key = f"{instrument_id}:{ev.effective_date}:ALL"
    ev_day = date.fromisoformat(ev.effective_date)
    for eff_str, act_type, a_key in existing_rows:
        if a_key == action_key:
            return True
        if act_type == ev.action_type:
            try:
                if abs((date.fromisoformat(eff_str) - ev_day).days) <= 7:
                    return True
            except ValueError:
                continue
    return False


def sync_missing_corporate_actions(
    db_path: Path | str,
    *,
    fetcher: Callable[[str], list[SplitEvent]] = fetch_yahoo_splits,
    today: date | None = None,
    dry_run: bool = False,
    max_workers: int = 8,
) -> SplitSyncResult:
    """检查并自动补录缺失的美股拆/并股记录到 corporate_actions。"""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return SplitSyncResult(0, (), 0, (f"数据库不存在: {path}",))

    cutoff = today or datetime.now(NY).date()
    candidates, existing_map = _load_candidates_readonly(path)
    if not candidates:
        return SplitSyncResult(0, (), 0, ())

    to_insert: list[tuple[int, SplitEvent]] = []
    skipped = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(candidates)))) as pool:
        future_map = {
            pool.submit(fetcher, sym): (iid, sym, first_date)
            for iid, sym, first_date in candidates
        }
        for fut in as_completed(future_map):
            iid, sym, first_date = future_map[fut]
            try:
                events = fut.result() or []
            except Exception as exc:
                errors.append(f"{sym}: {exc}")
                continue
            existing_rows = existing_map.get(iid, [])
            for ev in events:
                if ev.effective_date <= first_date:
                    continue
                try:
                    if date.fromisoformat(ev.effective_date) > cutoff:
                        continue
                except ValueError:
                    continue
                if _is_already_recorded(existing_rows, iid, ev):
                    skipped += 1
                    continue
                to_insert.append((iid, ev))

    if not to_insert or dry_run:
        return SplitSyncResult(
            checked_symbols=len(candidates),
            inserted=tuple(ev for _, ev in sorted(to_insert, key=lambda x: (x[1].effective_date, x[1].symbol))),
            skipped_existing=skipped,
            errors=tuple(errors),
        )

    # 发现全新拆/并股事件，规范写入 import_batches -> raw_records -> corporate_actions
    to_insert.sort(key=lambda x: (x[1].effective_date, x[1].symbol))
    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    batch_payload = json.dumps(
        [
            {
                "instrument_id": iid,
                "symbol": ev.symbol,
                "effective_date": ev.effective_date,
                "action_type": ev.action_type,
                "ratio_numerator": ev.ratio_numerator,
                "ratio_denominator": ev.ratio_denominator,
            }
            for iid, ev in to_insert
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    batch_sha = hashlib.sha256(batch_payload.encode("utf-8")).hexdigest()
    batch_key = f"yahoo_splits:{cutoff.isoformat()}:{batch_sha[:16]}"

    inserted_events: list[SplitEvent] = []
    conn = sqlite3.connect(str(path), timeout=30.0)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO import_batches (
                    batch_key, source_kind, source_scope, source_path,
                    content_sha256, parser_version, request_start, request_end,
                    status, imported_at_utc, record_count, input_role
                ) VALUES (?, 'API', 'YAHOO_SPLITS_AUTO', 'https://query1.finance.yahoo.com/v8/finance/chart',
                          ?, ?, ?, ?, 'SUCCESS', ?, ?, 'INCREMENTAL')
                """,
                (
                    batch_key,
                    batch_sha,
                    PARSER_VERSION,
                    to_insert[0][1].effective_date,
                    to_insert[-1][1].effective_date,
                    now_utc,
                    len(to_insert),
                ),
            )
            batch_id = conn.execute(
                "SELECT id FROM import_batches WHERE batch_key = ?", (batch_key,)
            ).fetchone()[0]

            for idx, (iid, ev) in enumerate(to_insert, start=1):
                action_key = f"{iid}:{ev.effective_date}:ALL"
                exists = conn.execute(
                    "SELECT 1 FROM corporate_actions WHERE action_key = ?", (action_key,)
                ).fetchone()
                if exists:
                    skipped += 1
                    continue

                payload_obj = {
                    "source": "Yahoo Finance Chart Splits API",
                    "symbol": ev.symbol,
                    "effective_date": ev.effective_date,
                    "action_type": ev.action_type,
                    "ratio_numerator": ev.ratio_numerator,
                    "ratio_denominator": ev.ratio_denominator,
                    "split_ratio": ev.split_ratio_text,
                    "raw": ev.raw_payload,
                }
                payload_text = json.dumps(payload_obj, ensure_ascii=False, sort_keys=True)
                payload_sha = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
                locator = f"yahoo_splits:{ev.symbol}:{ev.effective_date}"

                conn.execute(
                    """
                    INSERT OR IGNORE INTO raw_records (
                        import_batch_id, source_locator, source_entity_id,
                        original_time_text, payload_json, payload_sha256,
                        parse_status, parse_message, source_sheet, source_row, record_kind
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ACCEPTED', ?, 'YAHOO_SPLITS', ?, 'DATA')
                    """,
                    (
                        batch_id,
                        locator,
                        action_key,
                        ev.effective_date,
                        payload_text,
                        payload_sha,
                        f"自动检测到拆并股 {ev.symbol} {ev.ratio_denominator}→{ev.ratio_numerator}",
                        idx,
                    ),
                )
                raw_id = conn.execute(
                    "SELECT id FROM raw_records WHERE import_batch_id = ? AND source_locator = ?",
                    (batch_id, locator),
                ).fetchone()[0]

                conn.execute(
                    """
                    INSERT INTO corporate_actions (
                        action_key, instrument_id, effective_date, action_type,
                        broker_scope, ratio_numerator, ratio_denominator,
                        record_state, source_record_id
                    ) VALUES (?, ?, ?, ?, 'ALL', ?, ?, 'ACTIVE', ?)
                    """,
                    (
                        action_key,
                        iid,
                        ev.effective_date,
                        ev.action_type,
                        ev.ratio_numerator,
                        ev.ratio_denominator,
                        raw_id,
                    ),
                )
                inserted_events.append(ev)
    finally:
        conn.close()

    return SplitSyncResult(
        checked_symbols=len(candidates),
        inserted=tuple(inserted_events),
        skipped_existing=skipped,
        errors=tuple(errors),
    )
