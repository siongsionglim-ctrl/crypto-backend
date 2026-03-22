import numpy as np


def ema(data, period):
    k = 2 / (period + 1)
    ema_vals = [data[0]]

    for price in data[1:]:
        ema_vals.append(price * k + ema_vals[-1] * (1 - k))

    return ema_vals


def build_signal(candles):
    closes = [c["close"] for c in candles]

    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)

    current = closes[-1]

    if ema50[-1] > ema200[-1]:
        bias = "Bullish"
    elif ema50[-1] < ema200[-1]:
        bias = "Bearish"
    else:
        bias = "Neutral"

    if bias == "Bullish":
        action = "BUY"
    elif bias == "Bearish":
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "symbol": "LIVE",
        "bias": bias,
        "action": action,
        "price": current
    }