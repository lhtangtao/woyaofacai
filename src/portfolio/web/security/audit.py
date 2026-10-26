"""Security audit event recording with privacy-preserving IP masking."""

from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from portfolio.web.security.credentials import get_db_connection, init_security_db

logger = logging.getLogger(__name__)


def mask_ip(ip: Optional[str]) -> str:
    """Mask the last octet of an IP address for privacy preservation (AC-39)."""
    if not ip:
        return "unknown"
    if ":" in ip:
        # IPv6: keep first 3 segments
        parts = ip.split(":")
        return ":".join(parts[:3]) + ":xxxx:xxxx"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
    return "xxx"


def record_audit_event(
    event_type: str,
    user_id: Optional[str],
    source_class: str,
    ip_address: Optional[str],
    result: str,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Record a security audit event in security.sqlite3 without logging sensitive secrets."""
    init_security_db()
    now_iso = datetime.now().isoformat()
    ip_m = mask_ip(ip_address)
    meta_json = json.dumps(metadata or {}, ensure_ascii=False) if metadata else None

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO auth_audit_events (occurred_at, event_type, user_id, source_class, ip_masked, result, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (now_iso, event_type, str(user_id) if user_id else None, source_class, ip_m, result, meta_json))
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to record audit event: {e}")


def get_recent_audit_events(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve recent audit events for admin inspection."""
    init_security_db()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, occurred_at, event_type, user_id, source_class, ip_masked, result, metadata_json
                FROM auth_audit_events ORDER BY id DESC LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "occurred_at": r[1],
                    "event_type": r[2],
                    "user_id": r[3],
                    "source_class": r[4],
                    "ip_address": r[5],
                    "result": r[6],
                    "metadata": json.loads(r[7]) if r[7] else {},
                }
                for r in rows
            ]
    except Exception as e:
        logger.error(f"Failed to read audit events: {e}")
        return []
