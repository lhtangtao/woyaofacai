"""Presentation of the existing monthly net investment report."""
from collections import Counter
from decimal import Decimal

CASH_COLUMNS = (
    "月份", "券商渠道", "股票分类", "Ticker", "月初持仓数量", "本月买入数量",
    "本月卖出数量", "每月净变化", "月末持仓数量", "本月买入金额（USD）", "本月卖出金额（USD）",
    "本月净投入（USD）", "月末累计净投入（USD）",
)


NET_QUANTITY_COLUMN = 7

def _position_month(row):
    account = row.metadata.get("account_id")
    instrument = row.metadata.get("instrument_id")
    return (row.values[0], account, instrument) if account is not None and instrument is not None else None


def cash_flow_rows(net_rows, monthly_rows):
    counts = Counter(_position_month(row) for row in monthly_rows)
    quantities = {
        key: row.values[6:8]
        for row in monthly_rows
        if (key := _position_month(row)) is not None and counts[key] == 1
    }
    result = []
    for row in net_rows:
        buy, sell = quantities.get(_position_month(row), (None, None))
        change = Decimal(str(buy)) - Decimal(str(sell)) if buy is not None and sell is not None else None
        result.append((row.values[:5] + (buy, sell, change) + row.values[5:], row.metadata))
    return result


def sum_column(rows, column):
    if not rows or any(row[column] is None for row in rows):
        return None
    return sum((Decimal(str(row[column])) for row in rows), Decimal(0))
