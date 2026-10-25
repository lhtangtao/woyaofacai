from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from enum import Enum
from typing import Any

E8 = Decimal(100_000_000)
TRADE_COLUMNS = (
    "交易时间", "渠道", "分类", "Ticker", "方向", "成交数量", "成交均价($)", "成交金额($)",
    "微操标记", "后持仓数量", "后累计净投入($)", "后累计净投入均价($)",
    "交易类型", "划转对象", "订单ID", "数据来源",
)


def from_e8(value: int | None) -> Decimal | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("e8 字段必须为整数，不能转换浮点数或文本来掩盖结构错误。")
    with localcontext() as context:
        context.prec = 50
        return Decimal(value) / E8


def exact_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value)


class Ledger(str, Enum):
    PERSON = "PERSON"
    BROKER = "BROKER"


@dataclass(frozen=True)
class QueryScope:
    as_of: date
    ledger: Ledger = Ledger.PERSON
    person_ids: tuple[int, ...] | None = None
    account_ids: tuple[int, ...] | None = None
    instrument_ids: tuple[int, ...] | None = None
    category: str | None = None
    start_date: date | None = None
    search: str = ""
    include_disabled: bool = False
    audit: bool = False
    reporting_currency: str = "USD"
    metric_mode: str = "EXCEL_COMPAT"

    def __post_init__(self):
        if self.start_date and self.start_date > self.as_of:
            raise ValueError("起始日期不能晚于基准日期。")
        if self.reporting_currency not in ("USD", "CNY") or self.metric_mode != "EXCEL_COMPAT":
            raise ValueError("仅支持 USD 原币或 USD 账本的 CNY 折算，EXCEL_COMPAT；标准会计口径尚未开放。")
        if self.ledger == Ledger.BROKER and self.person_ids:
            raise ValueError("券商真实账本不能按人员份额裁切；请切换到人员权益账本。")


@dataclass(frozen=True)
class TradeRow:
    record_id: int
    source_record_id: int
    values: tuple[Any, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TradeFilters:
    """List-only filters: never truncate the financial engine's cost history."""
    side: str | None = None
    link_status: str | None = None
    source_file: str = ""
    include_reversed: bool | None = None
    include_reference: bool | None = None

    def __post_init__(self):
        if self.side not in (None, "BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT"):
            raise ValueError("不支持的交易方向。")
        if self.link_status not in (None, "LINKED", "LEGACY_UNLINKED", "REVIEW_REQUIRED", "NOT_APPLICABLE"):
            raise ValueError("不支持的关联状态。")
        for flag in (self.include_reversed, self.include_reference):
            if flag is not None and type(flag) is not bool:
                raise ValueError("撤销/参考条件必须为布尔值。")


@dataclass(frozen=True)
class TradeStats:
    matched: int
    buys: int
    sells: int
    review: int


@dataclass(frozen=True)
class TradePage:
    rows: tuple[TradeRow, ...]
    total: int
    page: int
    size: int
    scope: QueryScope
    queried_at_utc: str
    completeness_status: str = "RAW_LEDGER_NOT_REPLAYED"
    stats: TradeStats | None = None
    filters: TradeFilters | None = None
