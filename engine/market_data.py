from __future__ import annotations

from typing import Any
import requests


TIMEFRAME_MAP = {
    "1m": {"binance": "1m", "bybit": "1", "okx": "1m"},
    "3m": {"binance": "3m", "bybit": "3", "okx": "3m"},
    "5m": {"binance": "5m", "bybit": "5", "okx": "5m"},
    "15m": {"binance": "15m", "bybit": "15", "okx": "15m"},
    "30m": {"binance": "30m", "bybit": "30", "okx": "30m"},
    "1h": {"binance": "1h", "bybit": "60", "okx": "1H"},
    "4h": {"binance": "4h", "bybit": "240", "okx": "4H"},
    "1d": {"binance": "1d", "bybit": "D", "okx": "1D"},
}


class MarketDataError(RuntimeError):
    pass



def _requests_get(url: str, params: dict[str, Any] | None = None) -> Any:
    res = requests.get(url, params=params, timeout=20)
    res.raise_for_status()
    return res.json()



def _norm_timeframe(exchange: str, timeframe: str) -> str:
    tf = timeframe.lower()
    if tf not in TIMEFRAME_MAP:
        raise MarketDataError(f"Unsupported timeframe: {timeframe}")
    return TIMEFRAME_MAP[tf][exchange]



def fetch_candles(
    symbol: str,
    exchange: str = "binance",
    timeframe: str = "1h",
    limit: int = 250,
    market_type: str = "future",
    testnet: bool = True,
) -> list[dict[str, float]]:
    exchange = exchange.lower().strip()
    market_type = market_type.lower().strip()

    if exchange == "binance":
        return _fetch_binance(symbol, timeframe, limit, market_type, testnet)
    if exchange == "bybit":
        return _fetch_bybit(symbol, timeframe, limit, market_type)
    if exchange == "okx":
        return _fetch_okx(symbol, timeframe, limit, market_type)

    raise MarketDataError(f"Unsupported exchange: {exchange}")



def _fetch_binance(symbol: str, timeframe: str, limit: int, market_type: str, testnet: bool) -> list[dict[str, float]]:
    interval = _norm_timeframe("binance", timeframe)
    if market_type == "future":
        base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        url = f"{base}/fapi/v1/klines"
    else:
        base = "https://testnet.binance.vision" if testnet else "https://api.binance.com"
        url = f"{base}/api/v3/klines"

    data = _requests_get(url, params={"symbol": symbol, "interval": interval, "limit": limit})
    candles = []
    for row in data:
        candles.append({
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return candles



def _fetch_bybit(symbol: str, timeframe: str, limit: int, market_type: str) -> list[dict[str, float]]:
    interval = _norm_timeframe("bybit", timeframe)
    category = "linear" if market_type == "future" else "spot"
    url = "https://api.bybit.com/v5/market/kline"
    data = _requests_get(url, params={"category": category, "symbol": symbol, "interval": interval, "limit": limit})
    if data.get("retCode") != 0:
        raise MarketDataError(str(data))

    candles = []
    for row in reversed(data.get("result", {}).get("list", [])):
        candles.append({
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return candles



def _fetch_okx(symbol: str, timeframe: str, limit: int, market_type: str) -> list[dict[str, float]]:
    bar = _norm_timeframe("okx", timeframe)
    inst_id = _okx_symbol(symbol)
    url = "https://www.okx.com/api/v5/market/candles"
    data = _requests_get(url, params={"instId": inst_id, "bar": bar, "limit": limit})
    if data.get("code") not in (None, "0"):
        raise MarketDataError(str(data))

    candles = []
    for row in reversed(data.get("data", [])):
        candles.append({
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        })
    return candles



def _okx_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USDT-SWAP"
    if symbol.endswith("USDC"):
        return f"{symbol[:-4]}-USDC-SWAP"
    return symbol
