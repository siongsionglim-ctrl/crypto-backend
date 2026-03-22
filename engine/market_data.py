import requests


def fetch_candles(symbol):
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval=D&limit=200"
    r = requests.get(url).json()

    if r["retCode"] != 0:
        return []

    data = r["result"]["list"]

    candles = []
    for c in data:
        candles.append({
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5])
        })

    return candles[::-1]