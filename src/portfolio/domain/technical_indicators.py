"""Completed daily OHLCV analytics. Decimal arithmetic; no portfolio accounting."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

D = Decimal
PERIODS = (5, 10, 20, 50, 60, 120, 200, 250)


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self):
        values = (self.open, self.high, self.low, self.close, self.volume)
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError("日K字段必须为有限 Decimal")
        if (
            min(values[:4]) <= 0
            or self.volume < 0
            or self.low > min(self.open, self.close)
            or self.high < max(self.open, self.close)
            or self.low > self.high
        ):
            raise ValueError("日K价格范围无效")


def sma(values, n):
    return [sum(values[i - n + 1 : i + 1], D(0)) / n if i + 1 >= n else None for i in range(len(values))]


def ema(values, n):
    """SMA seed after n real samples; no arbitrary zero seed."""
    out = [None] * len(values)
    if len(values) >= n:
        out[n - 1] = sum(values[:n], D(0)) / n
        alpha = D(2) / (n + 1)
        for i in range(n, len(values)):
            out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(values, n=14):
    out = [None] * len(values)
    if len(values) <= n:
        return out
    changes = [b - a for a, b in zip(values, values[1:])]
    gains = sum((max(x, D(0)) for x in changes[:n]), D(0)) / n
    losses = sum((max(-x, D(0)) for x in changes[:n]), D(0)) / n

    def value():
        return (
            D(50)
            if not gains and not losses
            else (D(100) if not losses else D(100) - D(100) / (1 + gains / losses))
        )

    out[n] = value()
    for i in range(n + 1, len(values)):
        gains = (gains * (n - 1) + max(changes[i - 1], D(0))) / n
        losses = (losses * (n - 1) + max(-changes[i - 1], D(0))) / n
        out[i] = value()
    return out


def boll(values, n=20, k=2):
    """Bollinger Bands (MB, UP, DN) with Decimal precision."""
    mid = sma(values, n)
    upper, lower = [None] * len(values), [None] * len(values)
    k_dec = D(k)
    for i in range(n - 1, len(values)):
        m = mid[i]
        if m is not None:
            variance = sum(((v - m) ** 2 for v in values[i - n + 1 : i + 1]), D(0)) / n
            std = variance.sqrt()
            upper[i] = m + k_dec * std
            lower[i] = m - k_dec * std
    return mid, upper, lower


def series(bars):
    closes = [b.close for b in bars]
    data = {f"MA{n}": sma(closes, n) for n in PERIODS}
    fast, slow = ema(closes, 12), ema(closes, 26)
    diff = [a - b if a is not None and b is not None else None for a, b in zip(fast, slow)]
    valid = [v for v in diff if v is not None]
    signal = [None] * (len(diff) - len(valid)) + ema(valid, 9)
    b_mid, b_up, b_low = boll(closes, 20, 2)
    data.update(
        RSI14=rsi(closes),
        DIF=diff,
        DEA=signal,
        MACD=[(a - b) * 2 if a is not None and b is not None else None for a, b in zip(diff, signal)],
        VMA5=sma([b.volume for b in bars], 5),
        VMA20=sma([b.volume for b in bars], 20),
        BOLL_MID=b_mid,
        BOLL_UP=b_up,
        BOLL_LOW=b_low,
    )
    return data


def deviation(price, base):
    return (price / base - 1) * 100 if price is not None and base is not None and base > 0 else None


def technicals(bars, price=None):
    data = series(bars)
    last = {k: v[-1] if v else None for k, v in data.items()}
    price = price if price is not None else (bars[-1].close if bars else None)
    for n in PERIODS:
        last[f"DEV{n}"] = deviation(price, last[f"MA{n}"])
    last["HIGH52"] = max((b.high for b in bars[-252:]), default=None) if len(bars) >= 252 else None
    last["LOW52"] = min((b.low for b in bars[-252:]), default=None) if len(bars) >= 252 else None
    last["AVAILABLE_HIGH"] = max((b.high for b in bars), default=None)
    last["DRAWDOWN"] = deviation(price, last["AVAILABLE_HIGH"])
    last["DEV_BOLL_UP"] = deviation(price, last.get("BOLL_UP"))
    last["DEV_BOLL_LOW"] = deviation(price, last.get("BOLL_LOW"))
    for n, label in ((20, "SHORT"), (50, "MEDIUM"), (200, "LONG")):
        delta = last[f"DEV{n}"]
        last[label] = (
            "数据不足"
            if delta is None
            else ("偏强" if delta > D(".5") else ("偏弱" if delta < D("-.5") else "震荡"))
        )
    # Last completed day only; P2 alert evaluation is deliberately absent.
    a, b = data["MA50"], data["MA200"]
    last["CROSS"] = "数据不足"
    if len(a) > 1 and all(x is not None for x in (a[-2], b[-2], a[-1], b[-1])):
        last["CROSS"] = (
            "金叉"
            if a[-2] <= b[-2] and a[-1] > b[-1]
            else ("死叉" if a[-2] >= b[-2] and a[-1] < b[-1] else "无交叉")
        )
    return last, data
