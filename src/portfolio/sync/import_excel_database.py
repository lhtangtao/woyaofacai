"""Money3本地同步组件；从既有账本适配器迁入并裁剪非同步入口。"""

from __future__ import annotations
import math


from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN


SCALE = 100_000_000


QUANTUM = Decimal("0.00000001")


def text(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def number(value) -> Decimal | None:
    value = text(value)
    if value.lower() in ("", "-", "—", "nan", "none", "nat"):
        return None
    value = value.replace(",", "").replace("$", "").replace("¥", "").replace("￥", "")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"无法识别数值：{value[:80]}") from exc
    if not result.is_finite():
        raise ValueError("不允许把非有限数值写入账本")
    return result


def fixed_e8(value, *, allow_round: bool = False) -> int | None:
    amount = number(value)
    if amount is None:
        return None
    rounded = amount.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    if amount != rounded and not allow_round:
        raise ValueError("数值超过 8 位小数；必须保留原值并明确记录精度规范化")
    result = int(rounded * SCALE)
    if not -(2**63) <= result < 2**63:
        raise ValueError("数值超过 SQLite 64 位定点整数范围")
    return result
