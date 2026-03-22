from __future__ import annotations

from typing import List, Dict, Any

from .market_data import fetch_candles
from .trading_engine import generate_signal


DEFAULT_SCAN_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "SUIUSDT", "TRXUSDT", "LTCUSDT", "BCHUSDT", "APTUSDT",
]


def _safe_float(v, default=0.0) -> float:
    try:
        return default if v is None else float(v)
    except Exception:
        return default



def rank_score(signal: Dict[str, Any]) -> float:
    action = str(signal.get("action", "HOLD")).upper()
    confidence = _safe_float(signal.get("confidence_pct"))
    rr = _safe_float(signal.get("rr_ratio"))
    trend = _safe_float(signal.get("trend_strength_pct"))
    breakout = _safe_float(signal.get("breakout_probability_pct"))
    breakdown = _safe_float(signal.get("breakdown_probability_pct"))
    bounce = _safe_float(signal.get("bounce_probability_pct"))
    volume_ratio = _safe_float(signal.get("volume_ratio"), 1.0)
    stop_distance_pct = abs(_safe_float(signal.get("stop_distance_pct"), 1.0))
    extension_penalty = abs(_safe_float(signal.get("price")) - _safe_float(signal.get("entry"))) / max(_safe_float(signal.get("price"), 1.0), 1e-9) * 100.0

    dominant_prob = max(breakout, breakdown, bounce)
    if action == "HOLD":
        return -999.0 + confidence * 0.01

    score = 0.0
    score += confidence * 0.42
    score += trend * 0.18
    score += dominant_prob * 0.17
    score += min(rr, 4.0) * 10.0
    score += min(volume_ratio, 3.0) * 3.5

    if action == "BUY" and breakout >= breakdown:
        score += 7.0
    if action == "SELL" and breakdown >= breakout:
        score += 7.0
    if rr < 1.2:
        score -= 14.0
    if stop_distance_pct > 3.5:
        score -= 8.0
    if extension_penalty > 2.5:
        score -= 10.0

    return round(score, 4)



def scan_symbols(
    symbols: List[str] | None = None,
    min_confidence_pct: float = 55.0,
    min_rr_ratio: float = 1.0,
    limit: int = 10,
    exchange: str = "binance",
    timeframe: str = "1h",
    market_type: str = "future",
    testnet: bool = True,
) -> Dict[str, Any]:
    symbols = symbols or DEFAULT_SCAN_SYMBOLS
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for symbol in symbols:
        try:
            candles = fetch_candles(symbol, exchange=exchange, timeframe=timeframe, market_type=market_type, testnet=testnet)
            if not candles:
                errors.append({"symbol": symbol, "reason": "No data"})
                continue

            signal = generate_signal(symbol, exchange=exchange, timeframe=timeframe, market_type=market_type, testnet=testnet)
            if signal.get("error"):
                errors.append({"symbol": symbol, "reason": str(signal["error"])})
                continue

            confidence = _safe_float(signal.get("confidence_pct"))
            rr = _safe_float(signal.get("rr_ratio"))
            action = str(signal.get("action", "HOLD")).upper()
            qualifies = action in ("BUY", "SELL") and confidence >= min_confidence_pct and rr >= min_rr_ratio

            scored = {**signal, "qualifies": qualifies, "scan_score": rank_score(signal)}
            results.append(scored)
        except Exception as e:
            errors.append({"symbol": symbol, "reason": str(e)})

    results.sort(key=lambda x: x.get("scan_score", -9999), reverse=True)
    qualified = [r for r in results if r.get("qualifies")]
    return {
        "ok": True,
        "exchange": exchange,
        "timeframe": timeframe,
        "market_type": market_type,
        "scanned_count": len(symbols),
        "qualified_count": len(qualified),
        "top": qualified[:limit],
        "all": results[:limit],
        "errors": errors[:10],
    }
