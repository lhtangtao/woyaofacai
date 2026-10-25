#!/usr/bin/env python3
"""Money3本地同步：券商→本目录交易流水→本目录数据库；不镜像其他项目。"""

import argparse
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))


def main():
    from portfolio.paths import DATABASE_PATH, PROJECT_DIR, WORKBOOK_PATH

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-api", action="store_true", help="仅将本地交易流水同步入库")
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--base", type=Path, default=PROJECT_DIR, help="包含交易流水.xlsx的Money3目录")
    parser.add_argument(
        "--check", action="store_true", help="只读检查数据库及五个核心sheet，不入库、不请求API"
    )
    args = parser.parse_args()
    from portfolio.sync.database_sync import TradeFlowWorkbook, TRADE_SHEETS, sync_database_orders
    from portfolio.sync.database_store import validate_database

    base, database = args.base.expanduser().resolve(), args.db.expanduser().resolve()
    if not base.is_relative_to(PROJECT_DIR) or not database.is_relative_to(PROJECT_DIR):
        parser.error("同步来源及目标必须位于Money3目录内，避免误写其他项目。")
    print(f"[Money3] 交易流水：{base / WORKBOOK_PATH.name}")
    print(f"[Money3] 数据库：{database}")
    validate_database(path=database)
    ledger = TradeFlowWorkbook(base)
    missing = (set(TRADE_SHEETS.values()) | {"内部划转"}) - ledger.sheet_names
    if missing:
        raise ValueError("交易流水缺少工作表：" + "、".join(sorted(missing)))
    if args.check:
        print("[OK] 本地数据库结构及五个核心sheet可读取；未执行同步。")
        return 0
    sync_database_orders(base_dir=base, database=database, refresh_sources=not args.no_api)
    if not args.no_api:
        from portfolio.sync.fee_api import fetch_missing
        from portfolio.sync.trade_fees import load_evidence, store_evidence, refresh_fees
        from portfolio.sync.database_store import connect_database
        def persist(evidence):
            with connect_database(database) as writer:
                store_evidence(writer, evidence)
        with connect_database(database, readonly=True) as reader:
            _, warnings = fetch_missing(reader, load_evidence(reader), persist)
        with connect_database(database) as writer:
            summary = refresh_fees(writer)
        for warning in warnings:
            print('[FEES WARNING] '+warning)
        print(f"[FEES] 已确认 {summary['known_trades']} 笔费用，待补 {summary['unknown_trades']} 笔。")
    print("[DONE] Money3本地同步完成。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # API异常可能包含敏感请求内容，不打印原始异常或完整traceback。
        print(
            f"[ERROR] 同步失败（{type(exc).__name__}）；请核对本地流水字段、凭证与授权，数据库事务已停止。",
            file=sys.stderr,
        )
        raise SystemExit(1)
