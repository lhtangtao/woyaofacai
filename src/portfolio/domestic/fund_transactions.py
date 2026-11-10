"""Raw fund transaction migration and Tiantian ingestion."""
from pathlib import Path
from uuid import uuid4

from .storage import RAW_FILE, RAW_SCHEMA, archive, backup, connect, insert, now, operation
from .tiantian import ACCOUNT, PLATFORM, SOURCE_TYPE, VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_schema_migrations (
    name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_fund_source_files (
    id TEXT PRIMARY KEY REFERENCES raw_import_batches(id),
    source_platform TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES raw_asset_accounts,
    period_start TEXT, period_end TEXT, statement_year INTEGER,
    actual_start TEXT, actual_end TEXT
);
CREATE TABLE IF NOT EXISTS raw_fund_transactions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES raw_asset_accounts,
    source_platform TEXT NOT NULL,
    source_file_id TEXT NOT NULL REFERENCES raw_fund_source_files,
    import_batch_id TEXT NOT NULL REFERENCES raw_import_batches,
    source_page INTEGER NOT NULL,
    source_record_no INTEGER NOT NULL,
    source_section TEXT,
    order_id TEXT, transaction_time TEXT, transaction_type_raw TEXT,
    fund_code TEXT, fund_name TEXT, fund_type TEXT, portfolio_fund_name TEXT,
    application_amount TEXT, application_shares TEXT, confirmed_amount TEXT,
    confirmed_shares TEXT, fee TEXT, confirmation_date TEXT, confirmed_nav TEXT,
    confirmation_status TEXT, linked_bank_card TEXT,
    raw_data TEXT NOT NULL, parse_status TEXT NOT NULL, parse_error TEXT
);
CREATE INDEX IF NOT EXISTS raw_fund_account_date ON raw_fund_transactions(account_id, confirmation_date);
CREATE INDEX IF NOT EXISTS raw_fund_account_code ON raw_fund_transactions(account_id, fund_code);
CREATE TABLE IF NOT EXISTS raw_fund_transaction_sources (
    transaction_id TEXT NOT NULL REFERENCES raw_fund_transactions,
    source_file_id TEXT NOT NULL REFERENCES raw_fund_source_files,
    source_page INTEGER NOT NULL, source_record_no INTEGER NOT NULL,
    raw_data TEXT NOT NULL,
    PRIMARY KEY(source_file_id, source_page, source_record_no)
);
"""

# Legacy rows keep their original identifiers so existing Alipay calculations and
# source links remain valid. New Alipay rows mirror in the same raw transaction.
ALIPAY_FIELDS = (
    ("id", "id"), ("account_id", "account_id"), ("source_platform", "'ALIPAY'"),
    ("source_file_id", "import_batch_id"), ("import_batch_id", "import_batch_id"),
    ("source_page", "source_page"), ("source_record_no", "source_row_no"),
    ("order_id", "order_id"), ("transaction_time", "transaction_time"),
    ("transaction_type_raw", "transaction_type"), ("fund_code", "fund_code"),
    ("fund_name", "fund_name"), ("portfolio_fund_name", "portfolio_fund_name"),
    ("application_amount", "application_amount"), ("application_shares", "application_shares"),
    ("confirmed_amount", "confirmed_amount"), ("confirmed_shares", "confirmed_shares"),
    ("fee", "fee"), ("confirmation_date", "confirmation_date"), ("raw_data", "raw_data"),
    ("parse_status", "parse_status"), ("parse_error", "parse_error"),
)
ALIPAY_COLUMNS = ", ".join(column for column, _ in ALIPAY_FIELDS)
LEGACY_COLUMNS = ", ".join(column for _, column in ALIPAY_FIELDS)
TRIGGER_VALUES = ", ".join(column if column.startswith("'") else "NEW." + column for _, column in ALIPAY_FIELDS)
TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS raw_mirror_alipay_trade
AFTER INSERT ON raw_alipay_fund_transactions
BEGIN
    INSERT OR IGNORE INTO raw_fund_source_files(id,source_platform,account_id)
    VALUES (NEW.import_batch_id,'ALIPAY',NEW.account_id);
    UPDATE raw_fund_source_files SET
        actual_start=CASE WHEN actual_start IS NULL OR NEW.confirmation_date<actual_start THEN NEW.confirmation_date ELSE actual_start END,
        actual_end=CASE WHEN actual_end IS NULL OR NEW.confirmation_date>actual_end THEN NEW.confirmation_date ELSE actual_end END
    WHERE id=NEW.import_batch_id;
    INSERT INTO raw_fund_transactions({ALIPAY_COLUMNS}) VALUES ({TRIGGER_VALUES});
    INSERT INTO raw_fund_transaction_sources VALUES
    (NEW.id,NEW.import_batch_id,NEW.source_page,NEW.source_row_no,NEW.raw_data);
END;
"""


def begin_raw_transaction(db):
    db.executescript("BEGIN IMMEDIATE;\n" + RAW_SCHEMA + SCHEMA + TRIGGER)
    db.execute("""INSERT OR IGNORE INTO raw_fund_source_files(id,source_platform,account_id,actual_start,actual_end)
        SELECT import_batch_id,'ALIPAY',account_id,MIN(confirmation_date),MAX(confirmation_date)
        FROM raw_alipay_fund_transactions GROUP BY import_batch_id,account_id""")
    db.execute(f"""INSERT INTO raw_fund_transactions({ALIPAY_COLUMNS})
        SELECT {LEGACY_COLUMNS} FROM raw_alipay_fund_transactions legacy
        WHERE NOT EXISTS (SELECT 1 FROM raw_fund_transactions unified WHERE unified.id=legacy.id)""")
    db.execute("""INSERT OR IGNORE INTO raw_fund_transaction_sources
        SELECT id,import_batch_id,source_page,source_row_no,raw_data FROM raw_alipay_fund_transactions""")
    count = verify_alipay_migration(db)
    db.execute("INSERT OR IGNORE INTO raw_schema_migrations VALUES ('unified_fund_transactions_v1',?)", (now(),))
    return count


def verify_alipay_migration(db):
    legacy = f"SELECT {LEGACY_COLUMNS} FROM raw_alipay_fund_transactions"
    unified = f"SELECT {ALIPAY_COLUMNS} FROM raw_fund_transactions WHERE source_platform='ALIPAY'"
    for left, right in ((legacy, unified), (unified, legacy)):
        if db.execute(f"SELECT 1 FROM ({left} EXCEPT {right}) LIMIT 1").fetchone():
            raise ValueError("支付宝迁移逐条核对失败，未提交原始库")
    return db.execute("SELECT COUNT(*) FROM raw_alipay_fund_transactions").fetchone()[0]


def preview_tiantian(root, documents):
    path = Path(root) / RAW_FILE
    files, records = set(), set()
    if path.is_file():
        with connect(path, readonly=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_fund_transactions'").fetchone():
                files.update(row[0] for row in db.execute(
                    "SELECT file_sha256 FROM raw_import_batches WHERE source_type=?", (SOURCE_TYPE,)))
                records.update(row[0] for row in db.execute(
                    "SELECT id FROM raw_fund_transactions WHERE account_id=?", (ACCOUNT,)))
    summaries = []
    for doc in documents:
        ids = {row["id"] for row in doc.rows}
        summaries.append(dict(doc.summary(), duplicate_rows=len(ids & records), already_imported=doc.digest in files))
        records.update(ids)
        files.add(doc.digest)
    return summaries


def import_tiantian(documents, **options):
    with operation(**options) as root:
        backup(root, names=(RAW_FILE,))
        with connect(root / RAW_FILE) as db:
            alipay_count = begin_raw_transaction(db)
            db.execute("INSERT OR IGNORE INTO raw_asset_accounts VALUES (?, '天天基金账户', '天天基金', '基金', 'CNY', ?)",
                       (ACCOUNT, now()))
            batches = [write_statement(db, root, doc) for doc in documents]
    return {"verified_alipay": alipay_count, "batches": batches}


def write_statement(db, root, doc):
    digest = doc.digest
    previous = db.execute("""SELECT inserted_rows, duplicate_rows FROM raw_import_batches
        WHERE source_type=? AND file_sha256=?""", (SOURCE_TYPE, digest)).fetchone()
    if previous is not None:
        return {"status": "already_imported", "inserted": 0, "previously_inserted": previous[0]}
    batch = uuid4().hex
    start, end = min(doc.dates, default=None), max(doc.dates, default=None)
    overlap = db.execute("""SELECT COUNT(*) FROM raw_fund_source_files WHERE account_id=?
        AND period_start<=? AND period_end>=?""", (ACCOUNT, doc.period_end, doc.period_start)).fetchone()[0]
    review = any(row["parse_status"] == "待核对" for row in doc.rows)
    insert(db, "raw_import_batches", dict(
        id=batch, source_type=SOURCE_TYPE, source_filename=doc.filename, file_sha256=digest,
        parser_version=VERSION, total_pages=doc.pages, parsed_rows=len(doc.rows), error_rows=len(doc.issues),
        duplicate_rows=0, inserted_rows=0, status="待核对" if doc.issues or review else "已导入", created_at=now(),
    ))
    insert(db, "raw_fund_source_files", dict(
        id=batch, source_platform=PLATFORM, account_id=ACCOUNT, period_start=doc.period_start,
        period_end=doc.period_end,
        statement_year=int(doc.period_start[:4]) if doc.period_start[:4] == doc.period_end[:4] else None,
        actual_start=start, actual_end=end,
    ))
    for problem in doc.issues:
        insert(db, "raw_parse_issues", dict(import_batch_id=batch, **problem))
    duplicates = 0
    for row in doc.rows:
        if db.execute("SELECT 1 FROM raw_fund_transactions WHERE id=? AND account_id=?", (row["id"], ACCOUNT)).fetchone():
            duplicates += 1
        else:
            insert(db, "raw_fund_transactions", dict(
                account_id=ACCOUNT, source_platform=PLATFORM, import_batch_id=batch, source_file_id=batch, **row,
            ))
        db.execute("INSERT INTO raw_fund_transaction_sources VALUES (?,?,?,?,?)",
                   (row["id"], batch, row["source_page"], row["source_record_no"], row["raw_data"]))
    inserted = len(doc.rows)-duplicates
    db.execute("UPDATE raw_import_batches SET inserted_rows=?,duplicate_rows=? WHERE id=?", (inserted, duplicates, batch))
    archive(root, doc, platform="tiantian")
    return {"status": "imported", "period": [doc.period_start, doc.period_end],
            "inserted": inserted, "duplicates": duplicates, "overlapping_batches": overlap}
