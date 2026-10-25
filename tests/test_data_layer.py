import csv
import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest

from portfolio.cli.health import inspect_database
from portfolio.db.connection import SourceError, readonly_connection
from portfolio.db.repositories.provenance import redact
from portfolio.db.schema_validator import validate_schema
from portfolio.demo import build_demo
from portfolio.domain.models import TRADE_COLUMNS, Ledger, QueryScope
from portfolio.services.export_service import export_page
from portfolio.services.trades_service import TradesService


def scope(**kwargs):
    return QueryScope(date(2026, 1, 10), **kwargs)


def test_health_and_actual_demo_schema(demo_path):
    report = inspect_database(demo_path, integrity=True)
    assert report["compatible"] and len(report["tables"]) == 14 and len(report["views"]) == 7
    assert report["integrity"] == ["ok"] and report["foreign_key_issues"] == 0
    assert TradesService(demo_path).source_info().is_demo


@pytest.mark.parametrize("statement", ["DELETE FROM persons", "UPDATE persons SET name='bad'",
                                      "INSERT INTO persons(name) VALUES('bad')", "DROP TABLE persons",
                                      "PRAGMA user_version=99", "PRAGMA journal_mode=WAL", "PRAGMA query_only=OFF",
                                      "CREATE TABLE bad(x)", "ATTACH DATABASE ':memory:' AS other"])
def test_all_source_writes_denied_and_bytes_unchanged(demo_path, statement):
    before = hashlib.sha256(demo_path.read_bytes()).hexdigest()
    with readonly_connection(demo_path) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement)
    assert hashlib.sha256(demo_path.read_bytes()).hexdigest() == before


def test_missing_path_never_creates_empty_database(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(SourceError):
        TradesService(missing).source_info()
    assert not missing.exists()


def test_incompatible_schema_reports_exact_missing_objects(tmp_path):
    path = tmp_path / "bad.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE persons(id INTEGER PRIMARY KEY)")
    with readonly_connection(path) as connection:
        report = validate_schema(connection)
        assert not report.compatible
        assert "缺少字段：persons.name" in report.issues
        assert "缺少table：broker_trades" in report.issues


def test_broker_and_person_are_separate_no_union(demo_path):
    service = TradesService(demo_path)
    broker = service.get_trades(scope(ledger=Ledger.BROKER))
    person = service.get_trades(scope(person_ids=(2,)))
    assert broker.total == 2 and person.total == 1
    assert broker.rows[1].values[5] == Decimal(10)
    assert person.rows[0].values[5] == Decimal(4)
    assert len(person.rows[0].values) == 16
    assert person.rows[0].values[9] == Decimal(4)
    assert person.rows[0].values[8] is None
    assert person.rows[0].values[10:12] == (Decimal(400), Decimal(100))
    assert person.rows[0].values[14] == "DEMO-12345678901234567890-1"
    with pytest.raises(ValueError):
        service.get_trades(scope(ledger=Ledger.BROKER, person_ids=(2,)))


def test_disabled_reversed_and_reference_visible_only_in_requested_scope(demo_path):
    service = TradesService(demo_path)
    assert service.get_trades(scope()).total == 3
    assert service.get_trades(scope(include_disabled=True)).total == 4
    assert service.get_trades(scope(ledger=Ledger.BROKER, audit=True)).total == 4
    assert service.get_trades(scope(person_ids=(3,))).total == 0
    assert service.get_trades(scope(person_ids=(3,), include_disabled=True)).rows[0].values[5] == Decimal("0.00000001")


def test_every_filter_parameter_and_stable_server_sort(demo_path):
    service = TradesService(demo_path)
    assert service.get_trades(scope(account_ids=(2,))).total == 0
    assert service.get_trades(scope(account_ids=(1, 2))).total == 3
    assert service.get_trades(scope(instrument_ids=())).total == 0
    assert service.get_trades(scope(category="杠杆")).total == 0
    assert service.get_trades(scope(start_date=date(2026, 1, 3))).total == 1
    assert service.get_trades(QueryScope(date(2026, 1, 2))).total == 2
    assert service.get_trades(scope(search="' OR 1=1 --")).total == 0
    assert service.get_trades(scope(search="%" )).total == 0
    ascending = service.get_trades(scope(), sort_column=5, descending=False)
    assert [row.values[5] for row in ascending.rows] == [Decimal(4), Decimal(4), Decimal(6)]
    pages = [service.get_trades(scope(), size=1, page=i).rows[0].record_id for i in range(3)]
    assert len(set(pages)) == 3
    with pytest.raises(ValueError):
        service.get_trades(scope(), sort_column="id; DROP TABLE persons")
    with pytest.raises(ValueError):
        service.get_trades(scope(), size=1001)


def test_provenance_follows_source_row_and_masks_tokens(demo_path):
    service = TradesService(demo_path)
    row = service.get_trades(scope()).rows[0]
    data = service.get_trade_provenance(row.source_record_id)
    assert data["source_sheet"] == "合成示例" and data["source_row"] == row.source_record_id + 1
    assert "已遮盖" in data["payload"]["fields"]["app_secret"]
    full = service.get_trade_provenance(row.source_record_id, sensitive=True)
    assert full["payload"]["fields"]["app_secret"] == "demo-secret-not-a-real-key"
    assert "private" not in str(redact({"a": [{"refresh_token": "private"}], "url": "https://invalid/?secret=private"}))


def test_raw_json_decimal_values_are_not_rounded_to_binary_float(demo_path):
    with sqlite3.connect(demo_path) as connection:
        connection.execute('UPDATE raw_records SET payload_json=? WHERE id=1',
                           ('{"price":0.123456789123456789,"app_secret":"private"}',))
    result = TradesService(demo_path).get_trade_provenance(1)
    assert result["payload"]["price"] == Decimal("0.123456789123456789")
    assert "private" not in result["payload"]["app_secret"]


def test_readonly_permissions_unicode_uri_and_worker_connection_ownership(demo_path):
    demo_path.chmod(0o444)
    service = TradesService(demo_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.get_trades, scope(person_ids=(person,))) for person in (1, 2)]
        assert [future.result().total for future in futures] == [2, 1]
    assert service.source_info().path == demo_path.resolve()


def test_cancellation_interrupts_expensive_query(demo_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SourceError, match="取消"):
        with readonly_connection(demo_path, cancel=cancel) as connection:
            connection.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000) SELECT sum(x) FROM n").fetchone()


def test_export_precision_columns_and_exclusive_create(demo_path, tmp_path):
    page = TradesService(demo_path).get_trades(scope(include_disabled=True))
    path = tmp_path / "交易.csv"
    export_page(page, path)
    with path.open(encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == list(TRADE_COLUMNS)
    assert any(row[5] == "0.00000001" for row in rows[1:])
    assert rows[1][8:12] == ["", "2", "120", "60"]
    with pytest.raises(FileExistsError):
        export_page(page, path)


def test_demo_builder_never_overwrites_source(demo_path):
    before = demo_path.read_bytes()
    with pytest.raises(FileExistsError):
        build_demo(demo_path)
    assert demo_path.read_bytes() == before


def test_core_types_reject_unsupported_conversion_and_bad_dates():
    with pytest.raises(ValueError):
        scope(reporting_currency="EUR")
    with pytest.raises(ValueError):
        scope(metric_mode="ACCOUNTING")
    with pytest.raises(ValueError):
        scope(start_date=date(2027, 1, 1))
