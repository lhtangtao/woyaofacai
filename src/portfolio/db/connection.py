import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class SourceError(ValueError):
    pass


def _read_authorizer(action, arg1, arg2, database, trigger):
    allowed = {
        sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE,
    }
    if action in allowed:
        return sqlite3.SQLITE_OK
    read_pragmas = {
        "table_info", "table_xinfo", "foreign_key_list", "index_list", "index_info",
        "integrity_check", "quick_check", "foreign_key_check", "user_version",
        "database_list", "query_only", "journal_mode",
    }
    if action == sqlite3.SQLITE_PRAGMA and arg1 in read_pragmas:
        if arg1 in {"query_only", "journal_mode", "user_version"} and arg2 is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@contextmanager
def readonly_connection(path: str | Path, *, cancel: threading.Event | None = None, timeout=15):
    try:
        source = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceError("数据库不存在或不可访问，请重新选择文件。") from exc
    if not source.is_file():
        raise SourceError("请选择一个 SQLite 文件；程序不会创建空源库。")
    connection = None
    try:
        connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.set_authorizer(_read_authorizer)
        deadline = time.monotonic() + timeout
        connection.set_progress_handler(
            lambda: int(time.monotonic() > deadline or (cancel is not None and cancel.is_set())), 1000
        )
        connection.execute("BEGIN")
        yield connection
    except sqlite3.Error as exc:
        if cancel is not None and cancel.is_set():
            raise SourceError("查询已取消。") from exc
        raise SourceError("只读查询失败：文件无效、结构不兼容、查询超时或数据库暂时不可用。") from exc
    finally:
        if connection is not None:
            connection.close()
