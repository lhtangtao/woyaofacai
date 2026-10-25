"""只在用户明确指定的全新目标文件生成合成示例，不复制用户资产。"""
import json
import sqlite3
from pathlib import Path

from portfolio.db.schema_validator import schema_sql


def build_demo(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 排他创建；即使误把源库路径传进来，也无法覆盖源库。
    with path.open("xb"):
        pass
    connection = sqlite3.connect(path)
    try:
        connection.executescript(schema_sql())
        connection.execute("INSERT INTO import_batches(id,batch_key,source_kind,source_scope,content_sha256,"
                           "parser_version,status,imported_at_utc,record_count,input_role) "
                           "VALUES(1,'SYNTHETIC_DEMO','MANUAL','SYNTHETIC_DEMO','demo','M1','SUCCESS',"
                           "'2026-01-10T16:00:00Z',6,'SYNTHETIC_DEMO')")
        connection.executemany("INSERT INTO persons(id,name,enabled) VALUES(?,?,?)",
                               [(1, "示例甲", 1), (2, "示例乙", 1), (3, "示例停用人员", 0)])
        for account, code in ((1, "SIM_A"), (2, "SIM_B")):
            connection.execute("INSERT INTO broker_accounts(id,broker_code,account_key,display_name,book_timezone,day_cutoff) "
                               "VALUES(?,?,?,?,?,?)", (account, code, f"DEMO_{account}", f"模拟账户 {account}", "America/New_York", "00:00"))
        connection.execute("INSERT INTO instruments(id,market,symbol,name,currency,asset_type,category) "
                           "VALUES(1,'US','DEMO','合成示例证券','USD','ETF','大盘')")
        for index in range(1, 7):
            payload = json.dumps({"fields": {"说明": "全部为合成测试数据，不是真实成交", "source_row": index,
                                              "app_secret": "demo-secret-not-a-real-key"}}, ensure_ascii=False)
            connection.execute("INSERT INTO raw_records(id,import_batch_id,source_locator,payload_json,payload_sha256,"
                               "parse_status,source_sheet,source_row) VALUES(?,1,?,?,?,'ACCEPTED','合成示例',?)",
                               (index, f"demo:{index}", payload, "synthetic", index + 1))
        trades = [(1, "BUY", 1_000_000_000, 10_000_000_000, 100_000_000_000, "ACTIVE", "ACCOUNT_LEDGER"),
                  (2, "SELL", 400_000_000, 12_000_000_000, 48_000_000_000, "ACTIVE", "ACCOUNT_LEDGER"),
                  (3, "BUY", 500_000_000, None, 50_000_000_000, "REVERSED", "ACCOUNT_LEDGER"),
                  (4, "BUY", 200_000_000, 10_000_000_000, 20_000_000_000, "ACTIVE", "REFERENCE")]
        for identifier, side, qty, price, amount, state, role in trades:
            day = f"2026-01-{identifier + 1:02d}"
            connection.execute("INSERT INTO broker_trades(id,trade_key,account_id,instrument_id,broker_order_id,record_granularity,"
                               "side,business_date,book_time,quantity_e8,price_e8,calculation_amount_e8,amount_basis,currency,"
                               "record_state,accounting_role,source_record_id) VALUES(?,?,1,1,?,'ORDER_TOTAL',?,?,?,?,?,?,'GROSS','USD',?,?,?)",
                               (identifier, f"demo:{identifier}", f"DEMO-12345678901234567890-{identifier}", side, day,
                                f"{day} 10:00:00", qty, price, amount, state, role, identifier))
        operations = [(1, 1, 1, "BUY", 600_000_000, 60_000_000_000),
                      (2, 2, 1, "BUY", 400_000_000, 40_000_000_000),
                      (3, 1, 2, "SELL", 400_000_000, 48_000_000_000),
                      (4, 1, 3, "BUY", 500_000_000, 50_000_000_000),
                      (5, 3, 1, "BUY", 1, 100)]
        for identifier, person, trade, side, qty, amount in operations:
            day = f"2026-01-{trade + 1:02d}"
            connection.execute("INSERT INTO person_operations(id,operation_key,person_id,account_id,instrument_id,broker_trade_id,"
                               "operation_kind,side,business_date,book_time,sequence_no,quantity_e8,price_e8,amount_e8,amount_basis,"
                               "currency,link_status,source_record_id,legacy_order_id) "
                               "VALUES(?,?,?,1,1,?,'MARKET_TRADE',?,?,?,?,?,?,?,'GROSS','USD','LINKED',?,?)",
                               (identifier, f"person:{identifier}", person, trade, side, day, f"{day} 10:00:00", identifier,
                                qty, 12_000_000_000 if side == "SELL" else 10_000_000_000,
                                amount, trade, f"DEMO-12345678901234567890-{trade}"))
        connection.execute("INSERT INTO market_prices(symbol,price_type,trade_date,period_key,quoted_at_utc,price_e8,"
                           "updated_at_utc,price_status,source_record_id) VALUES('DEMO','LATEST','2026-01-10','LATEST',"
                           "'2026-01-10T16:00:00Z',11000000000,'2026-01-10T16:00:00Z','SYNTHETIC',6)")
        # Deliberately includes the demo transaction dates; all values remain
        # explicitly synthetic. Real calendars always come from the source DB.
        for day, price in (("2026-01-02", 10000000000), ("2026-01-03", 12000000000),
                           ("2026-01-05", 11500000000), ("2026-01-06", 11200000000),
                           ("2026-01-07", 11300000000), ("2026-01-08", 11100000000), ("2026-01-09", 11000000000)):
            connection.execute("INSERT INTO market_prices(symbol,price_type,trade_date,period_key,price_e8,updated_at_utc,price_status,source_record_id) VALUES('DEMO','DAILY_CLOSE',?,?,?,'2026-01-10T16:00:00Z','SYNTHETIC',6)", (day, day, price))
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    except Exception:
        connection.close()
        path.unlink(missing_ok=True)  # 仅清理本函数排他创建的失败合成文件。
        raise
    else:
        connection.close()
    return path
