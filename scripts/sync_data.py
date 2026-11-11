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
    from portfolio.services.derived_maintenance import clean_stale_staging_files
    clean_stale_staging_files(database)
    if not args.no_api:
        from portfolio.sync.fee_api import fetch_missing
        from portfolio.sync.trade_fees import audit_recent_fees, load_evidence, store_evidence, refresh_fees
        from portfolio.sync.database_store import connect_database
        def persist(evidence):
            with connect_database(database) as writer:
                store_evidence(writer, evidence)
        with connect_database(database, readonly=True) as reader:
            _, warnings = fetch_missing(reader, load_evidence(reader), persist, recent_days=7)
        with connect_database(database) as writer:
            summary = refresh_fees(writer)
            recent = audit_recent_fees(writer, days=7)
        for warning in warnings:
            print('[FEES WARNING] '+warning)
        print(
            f"[FEES] 已确认 {summary['known_trades']} 笔费用，待补 {summary['unknown_trades']} 笔"
            f"（其中待次日结算 {summary.get('pending_settlement_trades', 0)} 笔）。"
        )
        print(
            f"[FEES AUDIT 7D] 近7天成交 {recent['total_trades']} 笔（含多人分摊 {recent['multi_person_orders']} 笔）："
            f"已入账 {recent['known_trades']} 笔，待结算 {recent['pending_settlement_trades']} 笔，"
            f"分摊异常 {recent['allocation_mismatches']} 笔。"
        )
        if recent["allocation_mismatches"]:
            print(f"[FEES WARNING] 近7天存在多人分摊不一致订单：{recent['mismatch_details']}")
    print("[DONE] Money3本地同步完成。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        import traceback
        exc_str = str(exc)
        print(
            f"[ERROR] 同步失败 [{type(exc).__name__}]: {exc}",
            file=sys.stderr,
        )
        print("--- 详细报错堆栈 ---", file=sys.stderr)
        traceback.print_exc()

        # 如果属于嘉信授权过期类错误，高亮打印直接复制可运行的解决方案
        if "authorize_schwab" in exc_str or "嘉信刷新授权失效" in exc_str:
            print("\n" + "=" * 68, file=sys.stderr)
            print("  🚨【检测到嘉信理财 (Schwab) 授权已过期 (约7天周期)】", file=sys.stderr)
            print("  👉 解决办法：在终端中直接复制并运行以下命令完成浏览器授权：", file=sys.stderr)
            print("     cd /Users/tangtao/money3 && .venv/bin/python scripts/authorize_schwab.py", file=sys.stderr)
            print("  👉 流程说明：登录嘉信 -> 复制浏览器跳转后的完整URL -> 粘回终端回车 -> 重新启动", file=sys.stderr)
            print("=" * 68 + "\n", file=sys.stderr)
        else:
            print("--- 请根据上方具体报错排查对应流水字段、行数据或数据库约束 ---", file=sys.stderr)
        raise SystemExit(1)
