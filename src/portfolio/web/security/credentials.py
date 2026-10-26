"""User credentials, scrypt password hashing, and login rate limiting."""

from datetime import datetime
import hashlib
import hmac
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_CUSTOM_SECURITY_DB_PATH: Optional[Path] = None


def get_default_security_db_path() -> Path:
    """Return default persistent SQLite database path for security credentials."""
    base_dir = Path.home() / "Library" / "Application Support" / "MoneyPortfolio"
    if not base_dir.exists():
        base_dir = Path.home() / ".money_portfolio"
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base_dir, 0o700)
    except Exception:
        pass
    return base_dir / "security.sqlite3"


def set_security_db_path(path: Optional[Union[str, Path]]) -> None:
    """Set custom security SQLite database path (used for tests or isolated environments)."""
    global _CUSTOM_SECURITY_DB_PATH
    if path is None:
        _CUSTOM_SECURITY_DB_PATH = None
    else:
        _CUSTOM_SECURITY_DB_PATH = Path(path).resolve()


def reset_security_db_path() -> None:
    """Reset custom security SQLite database path back to default production path."""
    global _CUSTOM_SECURITY_DB_PATH
    _CUSTOM_SECURITY_DB_PATH = None


def get_security_db_path() -> Path:
    """Return active SQLite database path for security credentials."""
    if _CUSTOM_SECURITY_DB_PATH is not None:
        return _CUSTOM_SECURITY_DB_PATH
    env_path = os.environ.get("MONEY_SECURITY_DB_PATH")
    if env_path:
        return Path(env_path).resolve()
    return get_default_security_db_path()


@contextmanager
def isolated_security_db(custom_path: Optional[Union[str, Path]] = None):
    """Context manager for temporary isolated security database."""
    import tempfile
    old_custom = _CUSTOM_SECURITY_DB_PATH
    old_env = os.environ.get("MONEY_SECURITY_DB_PATH")
    temp_dir_obj = None
    try:
        if custom_path is None:
            temp_dir_obj = tempfile.TemporaryDirectory()
            target_path = Path(temp_dir_obj.name) / "test_security.sqlite3"
        else:
            target_path = Path(custom_path).resolve()
        set_security_db_path(target_path)
        os.environ["MONEY_SECURITY_DB_PATH"] = str(target_path)
        init_security_db()
        yield target_path
    finally:
        set_security_db_path(old_custom)
        if old_env is not None:
            os.environ["MONEY_SECURITY_DB_PATH"] = old_env
        elif "MONEY_SECURITY_DB_PATH" in os.environ:
            del os.environ["MONEY_SECURITY_DB_PATH"]
        if temp_dir_obj is not None:
            try:
                temp_dir_obj.cleanup()
            except Exception:
                pass


def get_db_connection() -> sqlite3.Connection:
    """Get SQLite connection with ROW factory enabled."""
    db_path = get_security_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        os.chmod(db_path, 0o600)
    except Exception:
        pass
    return conn


def init_security_db():
    """Initialize security schema and invalidate old default plain-text credentials."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                person_id INTEGER,
                person_name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                password_changed_at TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                person_id INTEGER,
                person_name TEXT,
                issued_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                ip_address TEXT,
                user_agent TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                user_id TEXT,
                source_class TEXT NOT NULL,
                ip_masked TEXT NOT NULL,
                result TEXT NOT NULL,
                metadata_json TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                username TEXT NOT NULL,
                attempt_time REAL NOT NULL,
                success INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_attempts_ip_time ON login_attempts(ip_address, attempt_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON auth_sessions(user_id)")

        # AC-17 Check: If users table is empty, seed initial user placeholders with cryptographically
        # random unguessable passwords, explicitly rendering default 'admin' and 'lake' invalid!
        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            now_iso = datetime.now().isoformat()
            # Random initial hash prevents any default password login
            dummy_hash_admin = hash_password(os.urandom(32).hex())
            dummy_hash_lake = hash_password(os.urandom(32).hex())

            cursor.execute("""
                INSERT INTO users (username, password_hash, role, person_id, person_name, enabled, created_at, updated_at, password_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("admin", dummy_hash_admin, "admin", None, "管理员", 1, now_iso, now_iso, None))

            cursor.execute("""
                INSERT INTO users (username, password_hash, role, person_id, person_name, enabled, created_at, updated_at, password_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("拉科", dummy_hash_lake, "user", 5, "拉科", 1, now_iso, now_iso, None))
            conn.commit()
            logger.info("Initialized security.sqlite3 with secure randomized user records (default passwords invalidated).")


def hash_password(plain_password: str) -> str:
    """Generate scrypt password hash with 16-byte random salt."""
    if not plain_password:
        raise ValueError("Password cannot be empty")
    salt = os.urandom(16)
    n = 16384
    r = 8
    p = 1
    h = hashlib.scrypt(plain_password.encode("utf-8"), salt=salt, n=n, r=r, p=p)
    return f"scrypt${n}${r}${p}${salt.hex()}${h.hex()}"


def verify_password(plain_password: str, stored_hash: str) -> bool:
    """Verify plain password against stored scrypt hash using constant-time comparison."""
    if not plain_password or not stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected_hash = bytes.fromhex(parts[5])

        calculated_hash = hashlib.scrypt(plain_password.encode("utf-8"), salt=salt, n=n, r=r, p=p)
        return hmac.compare_digest(calculated_hash, expected_hash)
    except Exception as e:
        logger.error(f"Error verifying password hash: {e}")
        return False


# Rate Limiting (PRD 6.1, AC-22: Prevent brute force, do not lock out across different IPs)
RATE_LIMIT_WINDOW_SECONDS = 900.0  # 15 minutes
MAX_FAILED_ATTEMPTS = 5


def is_login_rate_limited(ip_address: str, username: str) -> bool:
    """Check if incoming IP has exceeded failed login threshold."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM login_attempts
            WHERE ip_address = ? AND attempt_time > ? AND success = 0
        """, (ip_address, cutoff))
        failed_count = cursor.fetchone()[0]
        return failed_count >= MAX_FAILED_ATTEMPTS


def record_login_attempt(ip_address: str, username: str, success: bool):
    """Log a login attempt for rate limiting and clean up stale attempts."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO login_attempts (ip_address, username, attempt_time, success)
            VALUES (?, ?, ?, ?)
        """, (ip_address, username, now, 1 if success else 0))
        # Clear failures for this IP if login succeeded
        if success:
            cursor.execute("DELETE FROM login_attempts WHERE ip_address = ? AND success = 0", (ip_address,))
        # Cleanup records older than window
        cursor.execute("DELETE FROM login_attempts WHERE attempt_time < ?", (cutoff,))
        conn.commit()


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Retrieve user dictionary by username."""
    init_security_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve user dictionary by user ID."""
    init_security_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def list_users() -> List[Dict[str, Any]]:
    """List all managed users without password hashes."""
    init_security_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role, person_id, person_name, enabled, created_at, updated_at, password_changed_at FROM users")
        return [dict(r) for r in cursor.fetchall()]


def set_user_password(username: str, new_password: str) -> bool:
    """Set or update user's password using scrypt hash."""
    init_security_db()
    now_iso = datetime.now().isoformat()
    new_hash = hash_password(new_password)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET password_hash = ?, updated_at = ?, password_changed_at = ?
            WHERE username = ?
        """, (new_hash, now_iso, now_iso, username))
        conn.commit()
        return cursor.rowcount > 0


def create_user(
    username: str,
    password: str,
    role: str = "user",
    person_id: Optional[int] = None,
    person_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new user with scrypt hashed password."""
    init_security_db()
    now_iso = datetime.now().isoformat()
    p_hash = hash_password(password)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (username, password_hash, role, person_id, person_name, enabled, created_at, updated_at, password_changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (username, p_hash, role, person_id, person_name, 1, now_iso, now_iso, now_iso))
        new_id = cursor.lastrowid
        conn.commit()
        return {
            "id": new_id,
            "username": username,
            "role": role,
            "person_id": person_id,
            "person_name": person_name,
            "enabled": 1,
        }


def toggle_user_enabled(user_id: int, enabled: bool) -> bool:
    """Enable or disable a user account."""
    init_security_db()
    now_iso = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET enabled = ?, updated_at = ? WHERE id = ?", (1 if enabled else 0, now_iso, user_id))
        conn.commit()
        return cursor.rowcount > 0
