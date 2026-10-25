"""Read models shared by the Decimal engines and the desktop UI."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from portfolio.domain.models import QueryScope

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class Issue:
    rule: str
    severity: str
    message: str
    day: str = ""
    person_id: int | None = None
    account_id: int | None = None
    instrument_id: int | None = None
    source_ids: tuple[int, ...] = ()
    action: str = "在 Money3 交易流水核对原始输入，再使用本地同步脚本入库。"


@dataclass(frozen=True)
class Event:
    id: int
    source_id: int
    person_id: int | None
    account_id: int
    instrument_id: int
    day: str
    time: str
    sequence: int
    side: str
    quantity: Decimal
    amount: Decimal | None
    price: Decimal | None
    kind: str = "MARKET_TRADE"
    metadata: dict = field(default_factory=dict, compare=False)

    @property
    def key(self):
        return self.person_id, self.account_id, self.instrument_id

    @property
    def incoming(self):
        return self.side in ("BUY", "TRANSFER_IN")


@dataclass(frozen=True)
class Price:
    instrument_id: int
    day: str
    value: Decimal
    kind: str
    status: str
    source_id: int
    quoted_at: str | None = None
    basis: str = "UNKNOWN"


@dataclass
class Position:
    key: tuple
    quantity: Decimal = ZERO
    buy_quantity: Decimal = ZERO
    sell_quantity: Decimal = ZERO
    buy: Decimal | None = ZERO
    sell: Decimal | None = ZERO
    count: int = 0
    first: str = ""
    last: str = ""
    last_price: Decimal | None = None
    source_ids: list[int] = field(default_factory=list)
    warnings: set[str] = field(default_factory=set)

    @property
    def net(self):
        return None if self.buy is None or self.sell is None else self.buy - self.sell

    @property
    def cost(self):
        return self.net / self.quantity if self.quantity > ZERO and self.net is not None else None


@dataclass(frozen=True)
class ReportRow:
    values: tuple[Any, ...]
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    name: str
    columns: tuple[str, ...]
    rows: tuple[ReportRow, ...]
    note: str = ""


@dataclass(frozen=True)
class Metric:
    name: str
    value: Decimal | None
    formula: str
    day: str
    ratio: bool = False
    status: str = "COMPLETE"


@dataclass
class Snapshot:
    scope: QueryScope
    events: list[Event]
    instruments: dict[int, dict]
    accounts: dict[int, dict]
    persons: dict[int, dict]
    actions: list[dict]
    baselines: list[dict]
    prices: list[Price]
    extras: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    source_identity: str = ""


@dataclass
class Analytics:
    scope: QueryScope
    reports: dict[str, Report]
    metrics: tuple[Metric, ...]
    issues: list[Issue]
    positions: dict[tuple, Position]
    traces: dict[int, tuple]
    latest_profit_day: str | None
    valuation_days: tuple[str, ...]
    charts: tuple = ()
    trading_days: tuple[str, ...] = ()
    calculated_at: str = ""
    source_identity: str = ""


def strict_sum(values):
    items = list(values)
    return None if any(value is None for value in items) else sum(items, ZERO)


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and denominator > ZERO else None


def plus(left, right):
    return None if left is None or right is None else left + right
