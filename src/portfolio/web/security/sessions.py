"""Server-side session management with cryptographically secure random session tokens."""

from datetime import datetime, timedelta
import hashlib
import logging
import os
from pathlib import Path
import secrets
from typing import Any, Dict, Optional

from portfolio.web.security.credentials import get_db_connection, init_security_db

logger = logging.getLogger(__name__)

IDLE_TIMEOUT_HOURS = 12
MAX_LIFETIME_DAYS = 7


def get_secret_key_path() -> Path:
    """Return path to persistent secret key file."""
    base_dir = Path.home() / "Library" / "Application Support" / "MoneyPortfolio"
    if not base_dir.exists():
        base_dir = Path.home() / ".money_portfolio"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "secret.key"


def get_flask_secret_key() -> str:
    """Retrieve or generate cryptographically secure persistent secret key (PRD 6.2, AC-18)."""
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key and len(env_key) >= 32 and "lake" not in env_key.lower():
        return env_key

    key_file = get_secret_key_path()
    if key_file.is_file():
        try:
            content = key_file.read_text(encoding="utf-8").strip()
            if len(content) >= 32:
                return content
        except Exception as e:
            logger.warning(f"Error reading secret.key: {e}")

    # Generate new 256-bit secure key and persist with 0600 permissions
    new_key = secrets.token_hex(32)
    try:
        key_file.write_text(new_key, encoding="utf-8")
        os.chmod(key_file, 0o600)
        logger.info(f"Generated new secure FLASK_SECRET_KEY at {key_file}")
    except Exception as e:
        logger.warning(f"Failed to save secret.key to disk: {e}")
    return new_key


def create_session(
    user_id: int,
    username: str,
    role: str,
    person_id: Optional[int] = None,
    person_name: Optional[str] = None,
    ip_address: str = "",
    user_agent: str = "",
) -> str:
    """Create a new server-side session and return raw token for HTTP-only cookie."""
    init_security_db()
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    now = datetime.now()
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(days=MAX_LIFETIME_DAYS)).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO auth_sessions (
                session_id_hash, user_id, username, role, person_id,
                person_name, issued_at, last_seen_at, expires_at,
                revoked_at, ip_address, user_agent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """, (
            token_hash,
            user_id,
            username,
            role,
            person_id,
            person_name,
            now_iso,
            now_iso,
            expires_iso,
            ip_address,
            user_agent[:255] if user_agent else "",
        ))
        conn.commit()

    return raw_token


def validate_session(raw_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate raw session token from cookie, checking expiration, idle timeout and revocation."""
    if not raw_token or len(raw_token) < 20:
        return None

    init_security_db()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.session_id_hash, s.user_id, s.username, s.role, s.person_id,
                   s.person_name, s.issued_at, s.last_seen_at, s.expires_at,
                   s.revoked_at, u.enabled
            FROM auth_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.session_id_hash = ?
        """, (token_hash,))
        row = cursor.fetchone()
        if not row:
            return None

        # Check if revoked or user disabled (AC-19)
        if row["revoked_at"] is not None or row["enabled"] != 1:
            return None

        # Check max lifetime expiration
        expires_at = datetime.fromisoformat(row["expires_at"])
        if now > expires_at:
            return None

        # Check idle timeout (12 hours)
        last_seen = datetime.fromisoformat(row["last_seen_at"])
        if now - last_seen > timedelta(hours=IDLE_TIMEOUT_HOURS):
            cursor.execute("UPDATE auth_sessions SET revoked_at = ? WHERE session_id_hash = ?", (now.isoformat(), token_hash))
            conn.commit()
            return None

        # Update last_seen_at
        cursor.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE session_id_hash = ?", (now.isoformat(), token_hash))
        conn.commit()

        return {
            "session_id": raw_token[:10] + "...",
            "user_id": row["user_id"],
            "username": row["username"],
            "role": row["role"],
            "person_id": row["person_id"],
            "person_name": row["person_name"],
        }


def revoke_session(raw_token: Optional[str]):
    """Revoke specific session."""
    if not raw_token:
        return
    init_security_db()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now_iso = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE auth_sessions SET revoked_at = ? WHERE session_id_hash = ?", (now_iso, token_hash))
        conn.commit()


def revoke_all_user_sessions(user_id: int):
    """Revoke all sessions belonging to user (e.g. after password change or disable)."""
    init_security_db()
    now_iso = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now_iso, user_id))
        conn.commit()
