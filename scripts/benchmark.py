"""Explicit synthetic scale check. Never opens or mutates a real user database."""
import argparse
import json
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from portfolio.demo import build_demo
from portfolio.domain.models import QueryScope
from portfolio.services.analytics_service import AnalyticsService
from portfolio.services.trades_service import TradesService


def build(path):
    path = build_demo(path)  # Exclusive creation/refusal of existing targets.
    connection = sqlite3.connect(path)
    try:
        for table in ("person_operations", "broker_trades", "market_prices"):
            connection.execute(f"DELETE FROM {table}")  # This newly created synthetic fixture only.
        connection.executemany("INSERT INTO instruments(id,market,symbol,currency,asset_type,category) VALUES(?,'US',?,'USD','ETF','大盘')",
                               ((n + 10, f"S{n:04}") for n in range(2000)))
        days = []
        current = date(2020, 1, 1)
        while len(days) < 1000:
            if current.weekday() < 5:
                days.append(current.isoformat())
            current += timedelta(days=1)
        started = time.perf_counter()
        for index, day in enumerate(days):
            connection.executemany("INSERT INTO market_prices(symbol,price_type,trade_date,period_key,price_e8,updated_at_utc,price_status,source_record_id) VALUES(?,'DAILY_CLOSE',?,?,?,'2026-01-01T00:00:00Z','SYNTHETIC',1)",
                                   ((f"S{n:04}.US", day, day, 10000000000 + index * 1000000 + n, ) for n in range(2000)))
            trades, owners = [], []
            side = "BUY" if index % 2 == 0 else "SELL"
            for instrument in range(100):
                identifier = index * 100 + instrument + 1
                amount = 10000000000 + index * 1000000 + instrument
                trades.append((identifier, f"SYNTH:{identifier}", instrument + 10, f"ORDER:{identifier}", side, day, day + " 10:00:00", amount, amount))
                owners.append((identifier, f"OWNER:{identifier}", instrument + 10, identifier, side, day, day + " 10:00:00", identifier, amount, amount))
            connection.executemany("INSERT INTO broker_trades(id,trade_key,account_id,instrument_id,broker_order_id,record_granularity,side,business_date,book_time,quantity_e8,price_e8,calculation_amount_e8,amount_basis,currency,source_record_id) VALUES(?,?,1,?,?,'ORDER_TOTAL',?,?,?,100000000,?,?,'GROSS','USD',1)", trades)
            connection.executemany("INSERT INTO person_operations(id,operation_key,person_id,account_id,instrument_id,broker_trade_id,operation_kind,side,business_date,book_time,sequence_no,quantity_e8,price_e8,amount_e8,amount_basis,currency,link_status,source_record_id) VALUES(?,?,1,1,?,?,'MARKET_TRADE',?,?,?,?,100000000,?,?,'GROSS','USD','LINKED',1)", owners)
            if index % 100 == 0:
                connection.commit()
                print(f"synthetic price rows: {(index + 1) * 2000:,}", flush=True)
        connection.commit()
        print("fixture generated seconds", round(time.perf_counter() - started, 3), flush=True)
    finally:
        connection.close()
    return date.fromisoformat(days[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--existing", action="store_true", help="只读重测已生成的合成性能库")
    args = parser.parse_args()
    if args.existing:
        info = TradesService(args.output).source_info()
        if not info.is_demo:
            parser.error("只允许合成性能库，不允许把实际用户库当作压力生成目标。")
        asof = date.fromisoformat(info.latest_price_date)
    else:
        asof = build(args.output)
    result = {"kind": "SYNTHETIC_ONLY", "broker_trades": 100000, "person_operations": 100000, "market_prices": 2000000}
    started = time.perf_counter()
    service = TradesService(args.output)
    service.source_info()
    result["source_ready_seconds"] = time.perf_counter() - started
    started = time.perf_counter()
    page = service.get_trades(QueryScope(asof))
    result["first_page_seconds"], result["page_rows"] = time.perf_counter() - started, len(page.rows)
    started = time.perf_counter()
    analytics = AnalyticsService(args.output).calculate(QueryScope(asof))
    result["financial_pipeline_seconds"] = time.perf_counter() - started
    result["daily_rows"] = len(analytics.reports["每日持仓收益"].rows)
    result["notes"] = "财务计算在后台工作线程执行；此命令只量测计算与分页，不代表 UI 心跳测试。"
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
