"""Money3本地同步组件；从既有账本适配器迁入并裁剪非同步入口。"""

from __future__ import annotations
import math


import re


def normalize_order_id(value: object) -> str:
    """返回订单 ID 的文本表示；空值返回空字符串。

    券商订单 ID 可超过 Excel/IEEE-754 浮点数的精确范围，必须按文本读写。
    已被历史 Excel 转为浮点数的数据只能还原其当前整数值，丢失的尾数无法恢复。
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        if value.is_integer():
            return format(value, ".0f")
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text
