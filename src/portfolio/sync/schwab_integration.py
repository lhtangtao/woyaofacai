"""Money3本地同步组件；从既有账本适配器迁入并裁剪非同步入口。"""

from __future__ import annotations
from portfolio.sync.schwab_api import (
    ARCHIVE_FILE,
    BASE_DIR,
    NY,
    SchwabError,
    atomic_json,
    get_service,
    live_positions,
    normalize_transactions,
    number,
)


def check_quantities(expected: dict[str, float], actual: dict[str, float]) -> None:
    differences = [
        f"{symbol}: 流水 {expected.get(symbol, 0):g} / 实盘 {actual.get(symbol, 0):g}"
        for symbol in sorted(set(expected) | set(actual))
        if abs(expected.get(symbol, 0) - actual.get(symbol, 0)) > 1e-6
    ]
    if differences:
        raise SchwabError("嘉信历史持仓核对失败，需补历史成交/公司行动/期初基准：" + "；".join(differences))
