"""Independent observation cache, rule storage, and alert event persistence.
Never receives a financial source connection.
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from portfolio.domain.monitor_rules import AlertEvent, MonitorRule, RuntimeState, describe_rule
from portfolio.domain.technical_indicators import DailyBar

BASIS = "LONGPORT_FORWARD_V1"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class MonitorStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if (
            self.path.suffix.lower() not in (".sqlite3", ".sqlite", ".db")
            or self.path.name != "monitoring.sqlite3"
        ):
            raise ValueError("监控缓存必须使用独立的 monitoring.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self):
        with self.connect() as db:
            # 1. Base auxiliary tables
            db.executescript("""
                CREATE TABLE IF NOT EXISTS watch_sources(
                    scope_key TEXT, analysis_symbol TEXT, market TEXT, held_symbol TEXT,
                    source_account_id INTEGER, source_person_id INTEGER, payload TEXT);
                CREATE TABLE IF NOT EXISTS symbol_mappings(
                    namespace TEXT, held_symbol TEXT, payload TEXT, PRIMARY KEY(namespace, held_symbol));
                CREATE TABLE IF NOT EXISTS daily_bars(
                    analysis_symbol TEXT, basis TEXT, bar_date TEXT, payload TEXT,
                    PRIMARY KEY(analysis_symbol, basis, bar_date));
                CREATE TABLE IF NOT EXISTS market_state(analysis_symbol TEXT PRIMARY KEY, payload TEXT);
                CREATE TABLE IF NOT EXISTS indicator_cache(
                    analysis_symbol TEXT, basis TEXT, bar_date TEXT, payload TEXT,
                    PRIMARY KEY(analysis_symbol, basis));
                CREATE TABLE IF NOT EXISTS monitor_runtime_state(
                    rule_id TEXT PRIMARY KEY,
                    last_observed_value TEXT,
                    last_observed_at TEXT,
                    rearmed INTEGER NOT NULL DEFAULT 1,
                    cooldown_until TEXT,
                    last_eval_state TEXT,
                    updated_at TEXT);
            """)

            # 2. Check and migrate watch_targets if old CHECK constraint or missing source_type
            tbl_info = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='watch_targets'").fetchone()
            if tbl_info is None:
                db.execute("""
                    CREATE TABLE watch_targets(
                        scope_key TEXT, analysis_symbol TEXT, market TEXT, alerts_enabled INTEGER NOT NULL DEFAULT 0,
                        archive_state TEXT, source_type TEXT DEFAULT 'HOLDING', created_at TEXT, last_synced_at TEXT,
                        PRIMARY KEY(scope_key, analysis_symbol, market));
                """)
            else:
                create_sql = tbl_info[0] or ""
                if "CHECK(alerts_enabled=0)" in create_sql or "source_type" not in create_sql:
                    db.executescript("""
                        CREATE TABLE IF NOT EXISTS watch_targets_v2(
                            scope_key TEXT, analysis_symbol TEXT, market TEXT, alerts_enabled INTEGER NOT NULL DEFAULT 0,
                            archive_state TEXT, source_type TEXT DEFAULT 'HOLDING', created_at TEXT, last_synced_at TEXT,
                            PRIMARY KEY(scope_key, analysis_symbol, market));
                        INSERT OR IGNORE INTO watch_targets_v2 (scope_key, analysis_symbol, market, alerts_enabled, archive_state, source_type, created_at, last_synced_at)
                            SELECT scope_key, analysis_symbol, market, 0, archive_state, 'HOLDING', created_at, last_synced_at FROM watch_targets;
                        DROP TABLE watch_targets;
                        ALTER TABLE watch_targets_v2 RENAME TO watch_targets;
                    """)

            # 3. Check and migrate monitor_rules table
            rule_cols = [r["name"] for r in db.execute("PRAGMA table_info(monitor_rules)").fetchall()]
            if not rule_cols or "rule_id" not in rule_cols:
                db.execute("DROP TABLE IF EXISTS monitor_rules")
                db.execute("""
                    CREATE TABLE monitor_rules (
                        rule_id TEXT PRIMARY KEY,
                        scope_key TEXT,
                        analysis_symbol TEXT,
                        monitored_symbol TEXT,
                        market TEXT DEFAULT 'US',
                        rule_type TEXT,
                        expression_json TEXT,
                        check_mode TEXT,
                        session_scope TEXT,
                        repeat_mode TEXT,
                        cooldown_seconds INTEGER DEFAULT 1800,
                        notification_channels TEXT,
                        sound_name TEXT,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        completed INTEGER NOT NULL DEFAULT 0,
                        trigger_count INTEGER NOT NULL DEFAULT 0,
                        last_trigger_at TEXT,
                        name TEXT,
                        note TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    );
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_monitor_rules_scope_symbol ON monitor_rules(scope_key, analysis_symbol);")

            # 4. Check and migrate alert_events table
            evt_cols = [r["name"] for r in db.execute("PRAGMA table_info(alert_events)").fetchall()]
            if not evt_cols or "event_id" not in evt_cols:
                db.execute("DROP TABLE IF EXISTS alert_events")
                db.execute("""
                    CREATE TABLE alert_events (
                        event_id TEXT PRIMARY KEY,
                        rule_id TEXT,
                        rule_version INTEGER,
                        rule_name TEXT,
                        analysis_symbol TEXT,
                        monitored_symbol TEXT,
                        observed_price TEXT,
                        threshold_value TEXT,
                        trigger_reason TEXT,
                        quote_session TEXT,
                        observed_at TEXT,
                        detected_at TEXT,
                        input_snapshot_json TEXT,
                        indicator_snapshot_json TEXT,
                        notification_status TEXT,
                        read_at TEXT,
                        created_at TEXT
                    );
                """)
                db.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_detected_at ON alert_events(detected_at DESC);")
                db.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_rule_id ON alert_events(rule_id);")

    # --- Overrides / Mappings ---
    def overrides(self, namespace: str) -> Dict[str, Any]:
        with self.connect() as db:
            return {
                r["held_symbol"]: json.loads(r["payload"])
                for r in db.execute("SELECT * FROM symbol_mappings WHERE namespace=?", (namespace,))
            }

    def save_override(self, namespace: str, mapping: Any):
        if not mapping.user_override:
            raise ValueError("必须由用户明确确认覆盖映射")
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO symbol_mappings VALUES(?,?,?)",
                (namespace, mapping.held_symbol, dumps(mapping.record())),
            )

    # --- Watch Targets ---
    def sync(self, scope_key: str, sources: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
        """
        Authoritative *scoped* holding snapshot. Retain old target configuration.
        Preserve manual watch targets (source_type == 'MANUAL').
        """
        stamp = now.isoformat()
        with self.connect() as db:
            # Only mark non-manual holdings as NOT_HELD
            db.execute(
                "UPDATE watch_targets SET archive_state='NOT_HELD' WHERE scope_key=? AND (source_type != 'MANUAL' OR source_type IS NULL)",
                (scope_key,),
            )
            for symbol, market in {(s["analysis_symbol"], s["market"]) for s in sources}:
                db.execute(
                    "DELETE FROM watch_sources WHERE scope_key=? AND analysis_symbol=? AND market=?",
                    (scope_key, symbol, market),
                )
            for source in sources:
                symbol, market = source["analysis_symbol"], source["market"]
                db.execute(
                    """INSERT INTO watch_targets (scope_key, analysis_symbol, market, alerts_enabled, archive_state, source_type, created_at, last_synced_at)
                    VALUES(?,?,?,0,'HELD','HOLDING',?,?)
                    ON CONFLICT(scope_key,analysis_symbol,market) DO UPDATE SET archive_state='HELD', source_type='HOLDING', last_synced_at=excluded.last_synced_at""",
                    (scope_key, symbol, market, stamp, stamp),
                )
                db.execute(
                    "INSERT INTO watch_sources VALUES(?,?,?,?,?,?,?)",
                    (
                        scope_key,
                        symbol,
                        market,
                        source["held_symbol"],
                        source["account_id"],
                        source["person_id"],
                        dumps(source),
                    ),
                )
        return self.targets(scope_key)

    def add_manual_target(self, scope_key: str, symbol: str, market: str = "US") -> bool:
        """Manually add an unheld stock to watch targets."""
        stamp = datetime.now(timezone.utc).isoformat()
        clean_symbol = symbol.strip().upper()
        if not clean_symbol:
            return False
        with self.connect() as db:
            db.execute(
                """INSERT INTO watch_targets (scope_key, analysis_symbol, market, alerts_enabled, archive_state, source_type, created_at, last_synced_at)
                VALUES(?,?,?,0,'HELD','MANUAL',?,?)
                ON CONFLICT(scope_key,analysis_symbol,market) DO UPDATE SET archive_state='HELD', source_type='MANUAL', last_synced_at=excluded.last_synced_at""",
                (scope_key, clean_symbol, market.upper(), stamp, stamp),
            )
        return True

    def remove_manual_target(self, scope_key: str, symbol: str, market: str = "US") -> bool:
        """Remove a manual target or archive it."""
        with self.connect() as db:
            db.execute(
                "DELETE FROM watch_targets WHERE scope_key=? AND analysis_symbol=? AND market=? AND source_type='MANUAL'",
                (scope_key, symbol, market),
            )
            # Also deactivate rules on this symbol
            db.execute(
                "UPDATE monitor_rules SET enabled=0 WHERE scope_key=? AND analysis_symbol=?",
                (scope_key, symbol),
            )
        return True

    def targets(self, scope_key: str) -> List[Dict[str, Any]]:
        with self.connect() as db:
            items = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM watch_targets WHERE scope_key=? ORDER BY analysis_symbol", (scope_key,)
                )
            ]
            rule_counts = self.active_rule_count_by_symbol(scope_key)
            for item in items:
                item["sources"] = [
                    json.loads(r[0])
                    for r in db.execute(
                        "SELECT payload FROM watch_sources WHERE scope_key=? AND analysis_symbol=? AND market=?",
                        (scope_key, item["analysis_symbol"], item["market"]),
                    )
                ]
                item["active_rules_count"] = rule_counts.get(item["analysis_symbol"], 0)
        return items

    # --- Market Data & Daily Bars ---
    def bars(self, symbol: str) -> List[DailyBar]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM daily_bars WHERE analysis_symbol=? AND basis=? ORDER BY bar_date",
                (symbol, BASIS),
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row[0])
            result.append(
                DailyBar(
                    date.fromisoformat(payload["day"]),
                    *(Decimal(payload[k]) for k in ("open", "high", "low", "close", "volume")),
                )
            )
        return result

    def state(self, symbol: str) -> Dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM market_state WHERE analysis_symbol=?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_market(self, symbol: str, state: Dict[str, Any], bars: Optional[List[DailyBar]] = None, replace: bool = False, indicators: Any = None):
        with self.connect() as db:
            if replace:
                db.execute("DELETE FROM daily_bars WHERE analysis_symbol=? AND basis=?", (symbol, BASIS))
            for bar in bars or ():
                db.execute(
                    "INSERT OR REPLACE INTO daily_bars VALUES(?,?,?,?)",
                    (symbol, BASIS, bar.day.isoformat(), dumps(asdict(bar))),
                )
            db.execute("INSERT OR REPLACE INTO market_state VALUES(?,?)", (symbol, dumps(state)))
            if indicators is not None:
                db.execute(
                    "INSERT OR REPLACE INTO indicator_cache VALUES(?,?,?,?)",
                    (symbol, BASIS, state.get("bar_date", ""), dumps(indicators)),
                )

    # --- Monitor Rules CRUD ---
    def add_rule(self, rule: MonitorRule) -> MonitorRule:
        """Insert a new monitoring rule (defaults to disabled)."""
        rec = rule.to_record()
        with self.connect() as db:
            db.execute(
                """INSERT INTO monitor_rules (
                    rule_id, scope_key, analysis_symbol, monitored_symbol, market, rule_type,
                    expression_json, check_mode, session_scope, repeat_mode, cooldown_seconds,
                    notification_channels, sound_name, enabled, completed, trigger_count,
                    last_trigger_at, name, note, version, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec["rule_id"], rec["scope_key"], rec["analysis_symbol"], rec["monitored_symbol"],
                    rec["market"], rec["rule_type"], rec["expression_json"], rec["check_mode"],
                    rec["session_scope"], rec["repeat_mode"], rec["cooldown_seconds"],
                    rec["notification_channels"], rec["sound_name"], rec["enabled"], rec["completed"],
                    rec["trigger_count"], rec["last_trigger_at"], rec["name"], rec["note"],
                    rec["version"], rec["created_at"], rec["updated_at"]
                ),
            )
            # Initialize empty runtime state
            db.execute(
                "INSERT OR REPLACE INTO monitor_runtime_state (rule_id, rearmed, updated_at) VALUES (?, 1, ?)",
                (rec["rule_id"], rec["created_at"]),
            )
        return rule

    def update_rule(self, rule: MonitorRule) -> MonitorRule:
        rule.version += 1
        rule.updated_at = datetime.now(timezone.utc).isoformat()
        rec = rule.to_record()
        with self.connect() as db:
            db.execute(
                """UPDATE monitor_rules SET
                    scope_key=?, analysis_symbol=?, monitored_symbol=?, market=?, rule_type=?,
                    expression_json=?, check_mode=?, session_scope=?, repeat_mode=?, cooldown_seconds=?,
                    notification_channels=?, sound_name=?, enabled=?, completed=?, trigger_count=?,
                    last_trigger_at=?, name=?, note=?, version=?, updated_at=?
                WHERE rule_id=?""",
                (
                    rec["scope_key"], rec["analysis_symbol"], rec["monitored_symbol"], rec["market"],
                    rec["rule_type"], rec["expression_json"], rec["check_mode"], rec["session_scope"],
                    rec["repeat_mode"], rec["cooldown_seconds"], rec["notification_channels"],
                    rec["sound_name"], rec["enabled"], rec["completed"], rec["trigger_count"],
                    rec["last_trigger_at"], rec["name"], rec["note"], rec["version"], rec["updated_at"],
                    rec["rule_id"]
                ),
            )
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        with self.connect() as db:
            db.execute("DELETE FROM monitor_rules WHERE rule_id=?", (rule_id,))
            db.execute("DELETE FROM monitor_runtime_state WHERE rule_id=?", (rule_id,))
        return True

    def get_rule(self, rule_id: str) -> Optional[MonitorRule]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM monitor_rules WHERE rule_id=?", (rule_id,)).fetchone()
            if row:
                return MonitorRule.from_record(dict(row))
        return None

    def get_rules(
        self,
        scope_key: Optional[str] = None,
        analysis_symbol: Optional[str] = None,
        enabled_only: bool = False,
    ) -> List[MonitorRule]:
        query = "SELECT * FROM monitor_rules WHERE 1=1"
        params: List[Any] = []
        if scope_key:
            query += " AND scope_key=?"
            params.append(scope_key)
        if analysis_symbol:
            query += " AND analysis_symbol=?"
            params.append(analysis_symbol)
        if enabled_only:
            query += " AND enabled=1 AND completed=0"
        query += " ORDER BY created_at DESC"

        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
            return [MonitorRule.from_record(dict(r)) for r in rows]

    def duplicate_rule(self, rule_id: str) -> Optional[MonitorRule]:
        rule = self.get_rule(rule_id)
        if not rule:
            return None
        import uuid
        new_rule = MonitorRule(
            rule_id=f"rule_{uuid.uuid4().hex[:12]}",
            scope_key=rule.scope_key,
            analysis_symbol=rule.analysis_symbol,
            monitored_symbol=rule.monitored_symbol,
            market=rule.market,
            rule_type=rule.rule_type,
            expression=dict(rule.expression),
            check_mode=rule.check_mode,
            session_scope=rule.session_scope,
            repeat_mode=rule.repeat_mode,
            cooldown_seconds=rule.cooldown_seconds,
            notification_channels=rule.notification_channels,
            sound_name=rule.sound_name,
            enabled=False,  # Default paused per PRD
            completed=False,
            trigger_count=0,
            last_trigger_at="",
            name=f"{rule.name} (副本)",
            note=rule.note,
        )
        return self.add_rule(new_rule)

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> bool:
        with self.connect() as db:
            db.execute("UPDATE monitor_rules SET enabled=? WHERE rule_id=?", (1 if enabled else 0, rule_id))
            if enabled:
                # Reset rearmed state when user explicitly enables rule
                db.execute("UPDATE monitor_runtime_state SET rearmed=1, last_observed_value='' WHERE rule_id=?", (rule_id,))
        return True

    def batch_set_enabled(self, rule_ids: List[str], enabled: bool) -> int:
        if not rule_ids:
            return 0
        with self.connect() as db:
            q = f"UPDATE monitor_rules SET enabled=? WHERE rule_id IN ({','.join('?' * len(rule_ids))})"
            cursor = db.execute(q, [1 if enabled else 0] + rule_ids)
            if enabled:
                q_state = f"UPDATE monitor_runtime_state SET rearmed=1, last_observed_value='' WHERE rule_id IN ({','.join('?' * len(rule_ids))})"
                db.execute(q_state, rule_ids)
            return cursor.rowcount

    def batch_set_all_enabled(self, scope_key: str, enabled: bool) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE monitor_rules SET enabled=? WHERE scope_key=?",
                (1 if enabled else 0, scope_key),
            )
            if enabled:
                db.execute(
                    """UPDATE monitor_runtime_state SET rearmed=1, last_observed_value=''
                    WHERE rule_id IN (SELECT rule_id FROM monitor_rules WHERE scope_key=?)""",
                    (scope_key,),
                )
            return cursor.rowcount

    def active_rule_count_by_symbol(self, scope_key: str) -> Dict[str, int]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT analysis_symbol, count(*) as cnt FROM monitor_rules WHERE scope_key=? AND enabled=1 AND completed=0 GROUP BY analysis_symbol",
                (scope_key,),
            ).fetchall()
            return {r["analysis_symbol"]: r["cnt"] for r in rows}

    # --- Runtime State CRUD ---
    def get_runtime_state(self, rule_id: str) -> RuntimeState:
        with self.connect() as db:
            row = db.execute("SELECT * FROM monitor_runtime_state WHERE rule_id=?", (rule_id,)).fetchone()
            if row:
                return RuntimeState.from_record(dict(row))
        return RuntimeState(rule_id=rule_id)

    def save_runtime_state(self, state: RuntimeState):
        rec = state.to_record()
        with self.connect() as db:
            db.execute(
                """INSERT INTO monitor_runtime_state (
                    rule_id, last_observed_value, last_observed_at, rearmed, cooldown_until, last_eval_state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    last_observed_value=excluded.last_observed_value,
                    last_observed_at=excluded.last_observed_at,
                    rearmed=excluded.rearmed,
                    cooldown_until=excluded.cooldown_until,
                    last_eval_state=excluded.last_eval_state,
                    updated_at=excluded.updated_at""",
                (
                    rec["rule_id"], rec["last_observed_value"], rec["last_observed_at"],
                    rec["rearmed"], rec["cooldown_until"], rec["last_eval_state"], rec["updated_at"]
                ),
            )

    def reset_runtime_state(self, rule_id: str):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE monitor_runtime_state SET rearmed=1, last_observed_value='', cooldown_until='', updated_at=? WHERE rule_id=?",
                (now, rule_id),
            )

    # --- Alert Events CRUD ---
    def save_alert_event(self, event: AlertEvent) -> AlertEvent:
        rec = event.to_record()
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO alert_events (
                    event_id, rule_id, rule_version, rule_name, analysis_symbol, monitored_symbol,
                    observed_price, threshold_value, trigger_reason, quote_session, observed_at,
                    detected_at, input_snapshot_json, indicator_snapshot_json, notification_status,
                    read_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec["event_id"], rec["rule_id"], rec["rule_version"], rec["rule_name"],
                    rec["analysis_symbol"], rec["monitored_symbol"], rec["observed_price"],
                    rec["threshold_value"], rec["trigger_reason"], rec["quote_session"],
                    rec["observed_at"], rec["detected_at"], rec["input_snapshot_json"],
                    rec["indicator_snapshot_json"], rec["notification_status"],
                    rec["read_at"], rec["created_at"]
                ),
            )
        return event

    def get_alert_events(
        self,
        limit: int = 200,
        unread_only: bool = False,
        symbol: Optional[str] = None,
    ) -> List[AlertEvent]:
        query = "SELECT * FROM alert_events WHERE 1=1"
        params: List[Any] = []
        if unread_only:
            query += " AND (read_at IS NULL OR read_at = '')"
        if symbol:
            query += " AND (analysis_symbol=? OR monitored_symbol=?)"
            params.extend([symbol, symbol])
        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)

        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
            return [AlertEvent.from_record(dict(r)) for r in rows]

    def mark_events_read(self, event_ids: Optional[List[str]] = None) -> int:
        stamp = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            if event_ids:
                q = f"UPDATE alert_events SET read_at=? WHERE event_id IN ({','.join('?' * len(event_ids))})"
                cursor = db.execute(q, [stamp] + event_ids)
            else:
                cursor = db.execute("UPDATE alert_events SET read_at=? WHERE read_at IS NULL OR read_at=''", (stamp,))
            return cursor.rowcount

    def clear_alert_events(self) -> int:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM alert_events")
            return cursor.rowcount

    def today_alert_count(self) -> int:
        today_prefix = datetime.now().strftime("%Y-%m-%d")
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) FROM alert_events WHERE detected_at LIKE ?",
                (f"{today_prefix}%",),
            ).fetchone()
            return row[0] if row else 0

    def stats(self, scope_key: str) -> Dict[str, Any]:
        with self.connect() as db:
            targets_count = db.execute("SELECT count(*) FROM watch_targets WHERE scope_key=?", (scope_key,)).fetchone()[0]
            manual_count = db.execute("SELECT count(*) FROM watch_targets WHERE scope_key=? AND source_type='MANUAL'", (scope_key,)).fetchone()[0]
            enabled_rules = db.execute("SELECT count(*) FROM monitor_rules WHERE scope_key=? AND enabled=1 AND completed=0", (scope_key,)).fetchone()[0]
            total_rules = db.execute("SELECT count(*) FROM monitor_rules WHERE scope_key=?", (scope_key,)).fetchone()[0]
            today_alerts = self.today_alert_count()
            return {
                "targets_count": targets_count,
                "manual_count": manual_count,
                "enabled_rules": enabled_rules,
                "total_rules": total_rules,
                "today_alerts": today_alerts,
            }

    def counts(self) -> Tuple[int, int]:
        with self.connect() as db:
            return tuple(
                db.execute("SELECT count(*) FROM " + t).fetchone()[0]
                for t in ("monitor_rules", "alert_events")
            )
