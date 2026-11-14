"""Local paths, safe source ingestion and separated SQLite schemas."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from portfolio.paths import DOMESTIC_ASSETS_DIR

from .alipay import PARSER_VERSION

ACCOUNT = "ALIPAY_FUND"
RAW_FILE = "domestic_assets_raw.db"
CALC_FILE = "domestic_assets_calc.db"

RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_import_batches (
    id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL, parser_version TEXT NOT NULL, total_pages INTEGER NOT NULL,
    parsed_rows INTEGER NOT NULL, error_rows INTEGER NOT NULL, duplicate_rows INTEGER NOT NULL,
    inserted_rows INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(source_type, file_sha256)
);
CREATE TABLE IF NOT EXISTS raw_asset_accounts (
    account_id TEXT PRIMARY KEY, account_name TEXT NOT NULL, platform TEXT NOT NULL,
    asset_type TEXT NOT NULL, currency TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_fund_opening_positions (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES raw_asset_accounts,
    import_batch_id TEXT NOT NULL REFERENCES raw_import_batches, snapshot_date TEXT NOT NULL,
    fund_code TEXT NOT NULL, fund_name TEXT NOT NULL, total_shares TEXT NOT NULL,
    unit_nav TEXT NOT NULL, unit_nav_date TEXT NOT NULL, market_value TEXT NOT NULL,
    source_page INTEGER NOT NULL, raw_data TEXT NOT NULL,
    UNIQUE(account_id, snapshot_date, fund_code)
);
CREATE TABLE IF NOT EXISTS raw_alipay_fund_transactions (
    id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES raw_asset_accounts,
    import_batch_id TEXT NOT NULL REFERENCES raw_import_batches, source_page INTEGER NOT NULL,
    source_row_no INTEGER NOT NULL, order_id TEXT NOT NULL, transaction_time TEXT,
    transaction_type TEXT NOT NULL, fund_name TEXT NOT NULL, portfolio_fund_name TEXT,
    fund_code TEXT NOT NULL, application_amount TEXT, application_shares TEXT,
    confirmed_amount TEXT, confirmed_shares TEXT, fee TEXT, confirmation_date TEXT,
    raw_data TEXT NOT NULL, parse_status TEXT NOT NULL, parse_error TEXT
);
CREATE INDEX IF NOT EXISTS raw_trade_date ON raw_alipay_fund_transactions(confirmation_date, fund_code);
CREATE INDEX IF NOT EXISTS raw_order ON raw_alipay_fund_transactions(account_id, order_id);
CREATE TABLE IF NOT EXISTS raw_fund_nav (
    id TEXT PRIMARY KEY, fund_code TEXT NOT NULL, nav_date TEXT NOT NULL,
    unit_nav TEXT NOT NULL, accumulated_nav TEXT, source TEXT NOT NULL,
    import_batch_id TEXT NOT NULL REFERENCES raw_import_batches
);
CREATE TABLE IF NOT EXISTS raw_parse_issues (
    id INTEGER PRIMARY KEY, import_batch_id TEXT NOT NULL REFERENCES raw_import_batches,
    source_page INTEGER NOT NULL, source_row_no INTEGER NOT NULL, reason TEXT NOT NULL, raw_data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_fund_opening_cost_baselines (
    account_id TEXT NOT NULL REFERENCES raw_asset_accounts,
    snapshot_date TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    opening_cost TEXT NOT NULL,
    verified_closed_pnl TEXT,
    basis_note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, snapshot_date, fund_code)
);
CREATE TABLE IF NOT EXISTS raw_account_historical_pnl_baselines (
    account_id TEXT NOT NULL REFERENCES raw_asset_accounts,
    as_of_date TEXT NOT NULL,
    scope TEXT NOT NULL,
    fund_code TEXT NOT NULL,
    fund_name TEXT NOT NULL,
    fund_type TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    basis_note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, as_of_date, scope)
);
"""

CALC_SCHEMA = """
CREATE TABLE IF NOT EXISTS calc_fund_master (
    fund_code TEXT PRIMARY KEY, fund_name TEXT NOT NULL, fund_type TEXT, currency TEXT NOT NULL,
    first_trade_date TEXT, last_trade_date TEXT
);
CREATE TABLE IF NOT EXISTS calc_fund_transactions (
    id TEXT PRIMARY KEY, raw_transaction_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
    fund_code TEXT NOT NULL, transaction_date TEXT, effective_date TEXT, transaction_type TEXT NOT NULL,
    confirmed_amount TEXT, confirmed_shares TEXT, fee TEXT, cash_direction TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calc_fund_share_movements (
    id TEXT PRIMARY KEY, raw_transaction_id TEXT NOT NULL UNIQUE, fund_code TEXT NOT NULL,
    effective_date TEXT NOT NULL, movement_type TEXT NOT NULL, signed_shares TEXT NOT NULL,
    conversion_group_id TEXT
);
CREATE TABLE IF NOT EXISTS calc_fund_transfer_matches (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, outgoing_raw_id TEXT NOT NULL UNIQUE,
    incoming_raw_id TEXT NOT NULL UNIQUE, effective_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calc_fund_positions_daily (
    trade_date TEXT NOT NULL, account_id TEXT NOT NULL, fund_code TEXT NOT NULL, holding_shares TEXT,
    calculation_version TEXT NOT NULL, calculated_at TEXT NOT NULL,
    PRIMARY KEY(trade_date, account_id, fund_code)
);
CREATE TABLE IF NOT EXISTS calc_fund_positions_monthly (
    month TEXT NOT NULL, account_id TEXT NOT NULL, fund_code TEXT NOT NULL, holding_shares TEXT,
    unit_nav TEXT, nav_date TEXT, market_value TEXT, valuation_status TEXT NOT NULL,
    PRIMARY KEY(month, account_id, fund_code)
);
CREATE TABLE IF NOT EXISTS calc_fund_monthly_summary (
    month TEXT NOT NULL, fund_code TEXT NOT NULL, opening_shares TEXT, buy_amount TEXT,
    sell_amount TEXT, buy_shares TEXT, sell_shares TEXT, transfer_in_shares TEXT,
    transfer_out_shares TEXT, fee TEXT, closing_shares TEXT, closing_market_value TEXT,
    PRIMARY KEY(month, fund_code)
);
CREATE TABLE IF NOT EXISTS calc_runs (
    id TEXT PRIMARY KEY, calculation_type TEXT NOT NULL, is_full INTEGER NOT NULL,
    raw_version TEXT NOT NULL, calculation_version TEXT NOT NULL, started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL, status TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS calc_data_quality_checks (
    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES calc_runs,
    check_code TEXT NOT NULL, fund_code TEXT, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calc_raw_manifest (
    raw_id TEXT PRIMARY KEY, fund_code TEXT NOT NULL, fingerprint TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@contextmanager
def connect(path, *, readonly=False):
    path = Path(path).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if readonly else sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            yield connection
    finally:
        connection.close()


@contextmanager
def operation(root=DOMESTIC_ASSETS_DIR):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / ".operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if any((root / name).is_symlink() for name in (RAW_FILE, CALC_FILE)):
            raise ValueError("国内资产数据库文件不得是符号链接")
        yield root


def backup(root, *, names=(RAW_FILE, CALC_FILE)):
    sources = [root / name for name in names if (root / name).is_file()]
    if not sources:
        return
    destination = root / "backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
    destination.mkdir(mode=0o700, parents=True)
    for source in sources:
        with connect(source, readonly=True) as original, connect(destination / source.name) as copy:
            original.backup(copy)


def insert(connection, table, row):
    columns = ", ".join(row)
    params = ", ".join("?" for _ in row)
    connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({params})", tuple(row.values()))


def archive(root, doc, *, platform="alipay"):
    directory = root / "imports" / platform
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{doc.digest}.pdf"
    if path.exists():
        if path.read_bytes() != doc.content:
            raise ValueError("归档文件校验失败")
        return
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(doc.content)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def import_documents(root, documents):
    """Commit the raw import before touching any derived results."""
    if any(not doc.rows or (doc.source_type == "opening_asset" and doc.issues) for doc in documents):
        raise ValueError("导入未通过预检，请核对来源证明")
    from .fund_transactions import begin_raw_transaction

    with connect(root / RAW_FILE) as db:
        begin_raw_transaction(db)
        db.execute("INSERT OR IGNORE INTO raw_asset_accounts VALUES (?, '支付宝基金账户', '支付宝', '基金', 'CNY', ?)",
                   (ACCOUNT, now()))
        reports = []
        for doc in documents:
            if db.execute("SELECT 1 FROM raw_import_batches WHERE source_type=? AND file_sha256=?",
                          (doc.source_type, doc.digest)).fetchone():
                reports.append({"source_type": doc.source_type, "status": "already_imported", "inserted": 0})
                continue
            archive(root, doc)
            table = ("raw_fund_opening_positions" if doc.source_type == "opening_asset"
                     else "raw_alipay_fund_transactions")
            batch = uuid4().hex
            batch_row = dict(
                id=batch, source_type=doc.source_type, source_filename=doc.filename, file_sha256=doc.digest,
                parser_version=PARSER_VERSION, total_pages=doc.pages, parsed_rows=len(doc.rows),
                error_rows=len(doc.issues), duplicate_rows=0, inserted_rows=0,
                status="待核对" if doc.issues else "已导入", created_at=now(),
            )
            insert(db, "raw_import_batches", batch_row)
            for problem in doc.issues:
                insert(db, "raw_parse_issues", dict(import_batch_id=batch, **problem))
            duplicates = 0
            for row in doc.rows:
                previous = db.execute(f"SELECT * FROM {table} WHERE id=?", (row["id"],)).fetchone()
                if previous is not None:
                    ignored = {"source_page", "source_row_no", "raw_data"}
                    if any(previous[k] != v for k, v in row.items() if k not in ignored):
                        raise ValueError("同一原始记录的字段冲突，整批未写入")
                    duplicates += 1
                    insert(db, "raw_parse_issues", dict(
                        import_batch_id=batch, source_page=row["source_page"],
                        source_row_no=row.get("source_row_no", 0), reason="重复记录", raw_data=row["raw_data"],
                    ))
                    continue
                insert(db, table, dict(account_id=ACCOUNT, import_batch_id=batch, **row))
            inserted = len(doc.rows) - duplicates
            db.execute("UPDATE raw_import_batches SET duplicate_rows=?, inserted_rows=? WHERE id=?",
                       (duplicates, inserted, batch))
            reports.append({"source_type": doc.source_type, "status": "imported",
                            "inserted": inserted, "duplicates": duplicates})
    return reports
