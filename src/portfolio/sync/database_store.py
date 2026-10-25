"""Money3本地同步组件；从既有账本适配器迁入并裁剪非同步入口。"""

from __future__ import annotations
from contextlib import contextmanager


import os


from pathlib import Path


import sqlite3


from portfolio.paths import PROJECT_DIR as BASE_DIR

DEFAULT_DATABASE = BASE_DIR / "数据库" / "money.sqlite3"


SCHEMA_VERSION = 2


TABLES = frozenset(
    {
        "persons",
        "broker_accounts",
        "instruments",
        "instrument_aliases",
        "broker_trades",
        "person_operations",
        "cash_events",
        "corporate_actions",
        "historical_baselines",
        "asset_adjustments",
        "market_prices",
        "fx_rates",
        "import_batches",
        "raw_records",
    }
)


class DatabaseError(RuntimeError):
    """数据库尚未准备就绪或关键业务输入无效。"""


def database_path() -> Path:
    configured = os.environ.get("MONEY3_DATABASE", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATABASE


def validate_database(connection: sqlite3.Connection | None = None, path: Path | str | None = None) -> None:
    if connection is None:
        with _read_connection(database=path) as db:
            validate_database(db)
        return
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    found = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = TABLES - found
    if version != SCHEMA_VERSION or missing:
        detail = f"表结构版本 {version}（需要 {SCHEMA_VERSION}）"
        if missing:
            detail += "；缺少表：" + ", ".join(sorted(missing))
        raise DatabaseError(f"投资数据库结构不匹配：{detail}。请检查数据库路径及导入检查报告。")


def connect_database(path: Path | str | None = None, readonly: bool = False) -> sqlite3.Connection:
    selected = Path(path).expanduser().resolve() if path is not None else database_path()
    if not selected.is_file():
        raise DatabaseError(
            f"未找到投资数据库：{selected}。请先运行 scripts/migrate_local_data.py --source 指定旧项目完成一次性迁移；"
            "主流程不会自动重建数据库或回退读取旧 Excel。"
        )
    # mode=rw 防止路径拼写错误时悄悄创建空库。
    db = sqlite3.connect(selected.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        if readonly:
            db.execute("PRAGMA query_only=ON")
        validate_database(db)
        return db
    except Exception:
        db.close()
        raise


@contextmanager
def _read_connection(connection=None, database=None):
    own = connection is None
    db = connect_database(database, readonly=True) if own else connection
    factory = db.row_factory
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.row_factory = factory
        if own:
            db.close()
