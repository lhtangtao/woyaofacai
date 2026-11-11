"""Explicit write entry points for confirmed domestic imports."""
import sqlite3
from uuid import uuid4

from .calculate import VERSION, rebuild
from .tiantian_positions import rebuild_tiantian
from .storage import CALC_FILE, CALC_SCHEMA, RAW_FILE, backup, connect, import_documents, insert, now, operation


def publish(documents, **options):
    with operation(**options) as root:
        backup(root)
        imports = import_documents(root, documents)
        with connect(root / RAW_FILE, readonly=True) as db:
            has_opening = db.execute("SELECT 1 FROM raw_fund_opening_positions LIMIT 1").fetchone()
        result = run_calculation(root) if has_opening else {"status": "awaiting_opening"}
    return {"imports": imports, "calculation": result}


def run_calculation(root, *, full=False):
    started = now()
    try:
        result = rebuild(root, full=full)
        tiantian = rebuild_tiantian(root)
        if tiantian:
            result["tiantian"] = tiantian
        return result
    except (OSError, ValueError, ArithmeticError, sqlite3.Error) as exc:
        with connect(root / CALC_FILE) as db:
            db.executescript(CALC_SCHEMA)
            insert(db, "calc_runs", dict(
                id=uuid4().hex, calculation_type="全量" if full else "增量", is_full=int(full),
                raw_version="未知", calculation_version=VERSION, started_at=started, completed_at=now(),
                status="失败", error=type(exc).__name__,
            ))
        raise


def recalculate(**options):
    with operation(**options) as root:
        backup(root)
        return run_calculation(root, full=True)
