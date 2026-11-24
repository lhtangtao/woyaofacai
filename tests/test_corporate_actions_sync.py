"""测试启动自动检查并同步美股拆/并股公司行动模块。"""

from datetime import date
from pathlib import Path
import sqlite3

from portfolio.sync.corporate_actions_sync import (
    SplitEvent,
    sync_missing_corporate_actions,
)


def _create_min_db(path: Path):
    schema = Path(__file__).resolve().parents[1] / "src" / "portfolio" / "resources" / "schema.sql"
    conn = sqlite3.connect(str(path))
    conn.executescript(schema.read_text(encoding="utf-8"))
    with conn:
        conn.execute(
            """
            INSERT INTO import_batches (
                id, batch_key, source_kind, source_scope, content_sha256,
                parser_version, status, imported_at_utc, record_count
            ) VALUES (1, 'seed', 'MANUAL', 'SEED', 'abc', 'v1', 'SUCCESS', '2026-01-01T00:00:00Z', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO raw_records (
                id, import_batch_id, source_locator, payload_json, payload_sha256,
                parse_status, source_sheet, source_row, record_kind
            ) VALUES (1, 1, 'seed:1', '{}', 'abc', 'ACCEPTED', 'SEED', 1, 'DATA')
            """
        )
        conn.execute("INSERT INTO persons (id, name, enabled) VALUES (1, '个人', 1)")
        conn.execute(
            """
            INSERT INTO broker_accounts (
                id, broker_code, account_key, display_name, base_currency,
                book_timezone, day_cutoff, enabled, source_record_id
            ) VALUES (1, 'LONGBRIDGE', 'LEGACY_DEFAULT', '长桥', 'USD', 'Asia/Shanghai', '08:00', 1, 1)
            """
        )
        conn.execute("INSERT INTO instruments (id, symbol, market, name, asset_type, currency, category) VALUES (10, 'SNDK', 'US', 'SanDisk', 'STOCK', 'USD', '正股')")
        conn.execute("INSERT INTO instruments (id, symbol, market, name, asset_type, currency, category) VALUES (20, 'KORU', 'US', 'KORU', 'ETF', 'USD', '杠杆')")
        # SNDK 2026-06-25 首次买入
        conn.execute(
            """
            INSERT INTO person_operations (
                id, operation_key, person_id, account_id, instrument_id,
                operation_kind, side, business_date, book_time, sequence_no,
                quantity_e8, price_e8, amount_e8, amount_basis, currency, link_status, source_record_id
            ) VALUES (1, 'op1', 1, 1, 10, 'MARKET_TRADE', 'BUY', '2026-06-25', '2026-06-25 14:00:00', 1,
                      100000000, 215800000000, 215800000000, 'GROSS', 'USD', 'LINKED', 1)
            """
        )
        # KORU 2025-01-01 首次买入，已有 2026-07-15 1拆20
        conn.execute(
            """
            INSERT INTO person_operations (
                id, operation_key, person_id, account_id, instrument_id,
                operation_kind, side, business_date, book_time, sequence_no,
                quantity_e8, price_e8, amount_e8, amount_basis, currency, link_status, source_record_id
            ) VALUES (2, 'op2', 1, 1, 20, 'MARKET_TRADE', 'BUY', '2025-01-01', '2025-01-01 14:00:00', 1,
                      100000000, 10000000000, 10000000000, 'GROSS', 'USD', 'LINKED', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO corporate_actions (
                action_key, instrument_id, effective_date, action_type,
                broker_scope, ratio_numerator, ratio_denominator, record_state, source_record_id
            ) VALUES ('20:2026-07-15:ALL', 20, '2026-07-15', 'SPLIT', 'ALL', 20, 1, 'ACTIVE', 1)
            """
        )
    conn.close()


def test_sync_missing_corporate_actions_inserts_new_and_is_idempotent(tmp_path):
    db_path = tmp_path / "test_splits.sqlite3"
    _create_min_db(db_path)

    def _fake_fetcher(symbol: str):
        if symbol == "SNDK":
            return [
                # 早于首次买入的旧拆股应跳过
                SplitEvent("SNDK", "2024-01-10", "SPLIT", 2, 1, "2:1", {}),
                # 晚于首次买入的新拆股应写入
                SplitEvent("SNDK", "2026-11-20", "SPLIT", 8, 1, "8:1", {"numerator": 8, "denominator": 1}),
            ]
        if symbol == "KORU":
            return [
                # 已有记录（差 1 天）应按 ±7 天防重规则跳过
                SplitEvent("KORU", "2026-07-16", "SPLIT", 20, 1, "20:1", {}),
            ]
        return []

    # 1. dry_run 不应修改数据库
    dry = sync_missing_corporate_actions(db_path, fetcher=_fake_fetcher, today=date(2026, 11, 25), dry_run=True)
    assert len(dry.inserted) == 1
    assert dry.inserted[0].symbol == "SNDK"
    conn = sqlite3.connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 1
    conn.close()

    # 2. 正式执行应写入 SNDK 1->8 并跳过 KORU
    res1 = sync_missing_corporate_actions(db_path, fetcher=_fake_fetcher, today=date(2026, 11, 25))
    assert len(res1.inserted) == 1
    assert res1.inserted[0].symbol == "SNDK"
    assert res1.inserted[0].ratio_numerator == 8
    assert res1.skipped_existing == 1
    assert "SNDK" in res1.summary_text

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT effective_date, action_type, ratio_numerator, ratio_denominator, record_state "
        "FROM corporate_actions WHERE instrument_id = 10"
    ).fetchone()
    conn.close()
    assert row == ("2026-11-20", "SPLIT", 8, 1, "ACTIVE")

    # 3. 再次运行必须幂等（0 新增）
    res2 = sync_missing_corporate_actions(db_path, fetcher=_fake_fetcher, today=date(2026, 11, 25))
    assert len(res2.inserted) == 0
    assert res2.skipped_existing == 2
