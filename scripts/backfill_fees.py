#!/usr/bin/env python3
"""补齐Money3本地费用；已缓存的订单不再查询。默认读规则及已有费用证据。"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))


def main():
    from portfolio.paths import DATABASE_PATH
    from portfolio.sync.trade_fees import load_evidence, store_evidence, refresh_fees

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--fetch-api", action="store_true", help="仅为缺少证据的订单调用只读券商API")
    parser.add_argument("--import-collected", type=Path, help="导入此前本机已采集的API原始费用证据")
    args = parser.parse_args()
    database = args.db.expanduser().resolve()
    if not database.is_relative_to(PROJECT_DIR) or not database.is_file():
        parser.error("费用只能写入Money3内已有业务数据库")
    report_dir = PROJECT_DIR / "reports/fees"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = report_dir / ("before_fees_" + stamp + ".sqlite3")
    with (
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as original,
        sqlite3.connect(backup) as target,
    ):
        original.backup(target)

    def connection():
        c = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def persist(evidence):
        with connection() as c:
            store_evidence(c, evidence)

    if args.import_collected:
        from portfolio.sync.trade_fees import longbridge_evidence, schwab_evidence

        folder = args.import_collected.resolve()
        if not folder.is_relative_to(PROJECT_DIR):
            parser.error("采集结果必须来自Money3本机目录")
        with connection() as c:
            orders = json.loads((folder / "longbridge_orders.json").read_text())
            matches = json.loads((folder / "longbridge_fee_matches.json").read_text())
            store_evidence(c, longbridge_evidence(c, orders, matches))
            archive_file = folder / "schwab_api_archive.json"
            if archive_file.exists():
                store_evidence(c, schwab_evidence(c, json.loads(archive_file.read_text())))
    warnings = []
    if args.fetch_api:
        from portfolio.sync.fee_api import fetch_missing

        with connection() as c:
            _, warnings = fetch_missing(c, load_evidence(c), persist)
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        summary = refresh_fees(c)
        invalid = c.execute("""SELECT count(*) FROM (
            SELECT t.id FROM broker_trades t JOIN person_trade_fees f ON f.broker_trade_id=t.id
            WHERE t.record_state='ACTIVE' AND t.accounting_role='ACCOUNT_LEDGER' AND t.fees_e8 IS NOT NULL
            GROUP BY t.id HAVING count(*)<>count(f.fees_e8) OR sum(f.fees_e8)<>t.fees_e8)""").fetchone()[0]
        if invalid:
            raise ValueError("人员费用分摊未守恒，事务回滚")
        if c.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("费用外键检查失败")
        summary["channels"] = [
            dict(r)
            for r in c.execute("""SELECT a.broker_code,count(*) trades,
            sum(t.fees_e8 IS NOT NULL) known,sum(t.fees_e8) fees_e8 FROM broker_trades t JOIN broker_accounts a ON a.id=t.account_id
            WHERE t.record_state='ACTIVE' AND t.accounting_role='ACCOUNT_LEDGER' GROUP BY a.broker_code""")
        ]
        summary["unknown"] = [
            dict(r)
            for r in c.execute("""SELECT t.id,a.broker_code,i.symbol,t.business_date,f.status FROM broker_trades t
            JOIN broker_accounts a ON a.id=t.account_id JOIN instruments i ON i.id=t.instrument_id
            JOIN broker_fee_records f ON f.broker_trade_id=t.id WHERE t.record_state='ACTIVE' AND t.accounting_role='ACCOUNT_LEDGER' AND t.fees_e8 IS NULL""")
        ]
    summary.update(backup=str(backup), warnings=warnings)
    (report_dir / ("fee_summary_" + stamp + ".json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in {"unknown", "backup"}}, ensure_ascii=False, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
