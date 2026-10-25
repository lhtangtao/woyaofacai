from dataclasses import dataclass
from pathlib import Path

from portfolio.db.connection import SourceError, readonly_connection
from portfolio.db.repositories.provenance import get_provenance
from portfolio.db.repositories.trades import query_trades
from portfolio.db.schema_validator import validate_schema
from portfolio.services.post_trade_positions import attach_post_trade_positions


@dataclass(frozen=True)
class SourceInfo:
    path: Path
    bytes: int
    modified_at_ns: int
    schema: dict
    persons: tuple[dict, ...]
    accounts: tuple[dict, ...]
    instruments: tuple[dict, ...]
    latest_ledger_date: str | None
    latest_price_date: str | None
    latest_quote_utc: str | None
    last_successful_import_utc: str | None
    review_count: int
    is_demo: bool


class TradesService:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()

    def source_info(self, *, cancel=None):
        with readonly_connection(self.path, cancel=cancel) as connection:
            schema = validate_schema(connection)
            if not schema.compatible:
                raise SourceError("结构检查未通过：\n" + "\n".join(schema.issues))
            persons = tuple(dict(row) for row in connection.execute("SELECT id,name,enabled FROM persons ORDER BY id"))
            accounts = tuple(dict(row) for row in connection.execute(
                "SELECT id,broker_code,display_name,enabled,base_currency FROM broker_accounts ORDER BY broker_code,id"))
            instruments = tuple(dict(row) for row in connection.execute(
                "SELECT id,symbol,market,category,currency FROM instruments ORDER BY symbol,market"))
            latest = connection.execute("SELECT max(day) FROM (SELECT max(business_date) AS day FROM broker_trades "
                                        "UNION ALL SELECT max(business_date) FROM person_operations)").fetchone()[0]
            dates = connection.execute("SELECT max(trade_date),max(quoted_at_utc) FROM market_prices").fetchone()
            sync = connection.execute("SELECT max(imported_at_utc) FROM import_batches WHERE status='SUCCESS'").fetchone()[0]
            review = connection.execute("SELECT (SELECT count(*) FROM raw_records WHERE parse_status IN ('PENDING','REVIEW_REQUIRED'))"
                                        "+(SELECT count(*) FROM person_operations WHERE link_status='REVIEW_REQUIRED')"
                                        "+(SELECT count(*) FROM cash_events WHERE accounting_role='REVIEW_REQUIRED')"
                                        "+(SELECT count(*) FROM asset_adjustments WHERE status='REVIEW_REQUIRED')").fetchone()[0]
            stat = self.path.stat()
            is_demo = bool(connection.execute("SELECT EXISTS(SELECT 1 FROM import_batches WHERE source_scope='SYNTHETIC_DEMO')").fetchone()[0])
            return SourceInfo(self.path, stat.st_size, stat.st_mtime_ns, schema.to_dict(), persons,
                              accounts, instruments, latest, dates[0], dates[1], sync, review, is_demo)

    def get_trades(self, scope, *, cancel=None, **filters):
        with readonly_connection(self.path, cancel=cancel) as connection:
            page = query_trades(connection, scope, **filters)
            return attach_post_trade_positions(connection, page, cancel=cancel)

    def get_trade_provenance(self, source_id, *, sensitive=False, cancel=None):
        with readonly_connection(self.path, cancel=cancel) as connection:
            return get_provenance(connection, source_id, sensitive=sensitive)
