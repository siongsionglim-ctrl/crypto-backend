from __future__ import annotations

from typing import List, Dict, Any

from .market_data import fetch_candles
from .trading_engine import generate_signal


DEFAULT_SCAN_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "SUIUSDT",
    "TRXUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "APTUSDT",
]


def _safe_float(v, default=0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
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

    dominant_prob = max(breakout, breakdown, bounce)

    # Hard penalty for HOLD
    if action == "HOLD":
        return -999.0 + confidence * 0.01

    score = 0.0
    score += confidence * 0.45
    score += trend * 0.20
    score += dominant_prob * 0.20
    score += min(rr, 4.0) * 10.0
    score += min(volume_ratio, 3.0) * 3.0

    # Bonus if action direction matches dominant structure
    if action == "BUY" and breakout >= breakdown:
        score += 8.0
    if action == "SELL" and breakdown >= breakout:
        score += 8.0

    # Penalty if weak RR
    if rr < 1.2:
        score -= 12.0

    return round(score, 4)


def scan_symbols(
    symbols: List[str] | None = None,
    min_confidence_pct: float = 55.0,
    min_rr_ratio: float = 1.0,
    limit: int = 10,
) -> Dict[str, Any]:
    symbols = symbols or DEFAULT_SCAN_SYMBOLS

    results = []
    errors = []

    for symbol in symbols:
        try:
            candles = fetch_candles(symbol)
            if not candles:
                errors.append({"symbol": symbol, "reason": "No data"})
                continue

            signal = generate_signal(symbol)

            if signal.get("error"):
                errors.append({"symbol": symbol, "reason": signal["error"]})
                continue

            confidence = _safe_float(signal.get("confidence_pct"))
            rr = _safe_float(signal.get("rr_ratio"))
            action = str(signal.get("action", "HOLD")).upper()

            # Keep the full result, but mark whether it qualifies
            qualifies = (
                action in ("BUY", "SELL")
                and confidence >= min_confidence_pct
                and rr >= min_rr_ratio
            )

            scored = {
                **signal,
                "qualifies": qualifies,
                "scan_score": rank_score(signal),
            }
            results.append(scored)

        except Exception as e:
            errors.append({"symbol": symbol, "reason": str(e)})

    results.sort(key=lambda x: x.get("scan_score", -9999), reverse=True)

    qualified = [r for r in results if r.get("qualifies")]

    return {
        "ok": True,
        "scanned_count": len(symbols),
        "qualified_count": len(qualified),
        "top": qualified[:limit],
        "all": results[:limit],
        "errors": errors[:10],
    }