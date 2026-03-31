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
    action = str(signal.get("action", "HOLD")).upper().strip()

    confidence = _safe_float(signal.get("confidence_pct"))
    rr = _safe_float(signal.get("rr_ratio"))
    trend = _safe_float(signal.get("trend_strength_pct"))

    breakout = _safe_float(signal.get("breakout_probability_pct"))
    breakdown = _safe_float(signal.get("breakdown_probability_pct"))
    bounce = _safe_float(signal.get("bounce_probability_pct"))

    volume_ratio = _safe_float(signal.get("volume_ratio"), 1.0)
    direction_edge = _safe_float(signal.get("direction_edge"))
    setup_quality = _safe_float(signal.get("setup_quality"))

    price = _safe_float(signal.get("price"))
    entry = _safe_float(signal.get("entry") or signal.get("price"))
    sl = _safe_float(signal.get("sl") or signal.get("stop_loss"))

    is_choppy = bool(signal.get("is_choppy"))
    should_execute_now = bool(signal.get("should_execute_now"))
    market_regime = str(signal.get("market_regime") or "").lower().strip()
    trend_label = str(signal.get("trend") or "").upper().strip()
    setup_type = str(signal.get("setup_type") or "").lower().strip()

    dominant_prob = max(breakout, breakdown, bounce)

    score = 0.0

    # HOLD should be weak, but not totally discarded
    if action == "HOLD":
        score -= 20.0

    # base quality
    score += confidence * 0.28
    score += trend * 0.18
    score += dominant_prob * 0.14
    score += min(rr, 4.0) * 11.0
    score += min(volume_ratio, 2.0) * 7.0
    score += min(direction_edge, 30.0) * 0.7
    score += setup_quality * 0.18

    # regime / structure bonus
    if market_regime == "trend":
        score += 8.0
    elif market_regime == "range":
        score -= 3.0

    # action alignment
    if action == "BUY":
        if breakout >= breakdown + 8:
            score += 6.0
        if trend_label in ("BULLISH", "UPTREND"):
            score += 7.0
    elif action == "SELL":
        if breakdown >= breakout + 8:
            score += 6.0
        if trend_label in ("BEARISH", "DOWNTREND"):
            score += 7.0

    # setup-type bonus
    if "breakout" in setup_type:
        score += 6.0
    elif "pullback" in setup_type:
        score += 4.0
    elif "reversal" in setup_type:
        score += 2.0

    # softer penalties
    if is_choppy:
        score -= 12.0

    if not should_execute_now:
        score -= 6.0

    if rr < 1.0:
        score -= 10.0
    elif rr < 1.15:
        score -= 4.0

    if trend < 40:
        score -= 8.0
    elif trend < 50:
        score -= 3.0

    if confidence < 50:
        score -= 8.0

    # stop-loss width penalty
    if entry > 0 and sl > 0:
        sl_pct = abs(entry - sl) / entry * 100.0
        if sl_pct > 4.0:
            score -= 12.0
        elif sl_pct > 2.5:
            score -= 5.0

    # late-entry penalty
    if price > 0 and entry > 0:
        distance_pct = abs(price - entry) / price * 100.0
        if distance_pct > 1.2:
            score -= 6.0
        elif distance_pct > 0.7:
            score -= 2.0

    return round(score, 4)



def scan_symbols(
    symbols: List[str] | None = None,
    min_confidence_pct: float = 45.0,
    min_rr_ratio: float = 0.8,
    limit: int = 12,
    exchange: str = "binance",
    timeframe: str = "1m",
    market_type: str = "future",
    testnet: bool = False,
    websocket_enabled: bool = True,
) -> Dict[str, Any]:
    symbols = symbols or DEFAULT_SCAN_SYMBOLS
    results = []
    errors = []

    print(f"[SCAN] processing {symbol}", flush=True)

    for symbol in symbols:
        try:
            candles = fetch_candles(
                symbol,
                exchange=exchange,
                timeframe=timeframe,
                market_type=market_type,
                testnet=testnet,
                websocket_enabled=websocket_enabled,
                sl_mode="hybrid",
                sl_atr_multiplier=1.35,
                sl_buffer_atr=0.15,
                sl_buffer_pct=0.001,
                min_stop_pct=0.0035,
                target_rr=max(1.2, float(min_rr_ratio or 1.2)),
            )
            print(f"[SCAN] candles len={len(candles)} symbol={symbol}", flush=True)

            if not candles:
                errors.append({"symbol": symbol, "reason": "No data"})
                candles = []

            signal = generate_signal(
                symbol,
                exchange=exchange,
                timeframe=timeframe,
                market_type=market_type,
                testnet=testnet,
                websocket_enabled=websocket_enabled,
            )
            print(f"[SCAN] signal={signal}", flush=True)

            if signal.get("error"):
                errors.append({"symbol": symbol, "reason": signal["error"]})
                signal = {
                    "symbol": symbol,
                    "action": "HOLD",
                    "confidence_pct": 0,
                    "rr_ratio": 0,
                    "trend_strength_pct": 0,
                    "volume_ratio": 1,
                    "should_execute_now": False,
                    "is_choppy": True,
                }

            confidence = _safe_float(signal.get("confidence_pct"))
            rr = _safe_float(signal.get("rr_ratio"))
            trend = _safe_float(signal.get("trend_strength_pct"))
            action = str(signal.get("action", "HOLD")).upper()

            # ✅ ADD DEBUG HERE
            print(
                f"[FILTER DEBUG] symbol={symbol} action={action} "
                f"confidence={confidence:.1f} rr={rr:.2f} trend={trend:.1f} "
                f"should_execute_now={signal.get('should_execute_now')} "
                f"is_choppy={signal.get('is_choppy')}",
                flush=True,
            )

            # broad filter only
            passes_minimums = (
                confidence >= min_confidence_pct
                and rr >= min_rr_ratio * 0.9
            )

            # ✅ optional reject-reason debug
            if not passes_minimums:
                reasons = []
                if confidence < min_confidence_pct:
                    reasons.append(
                        f"confidence {confidence:.1f} < min_confidence_pct {min_confidence_pct:.1f}"
                    )
                if rr < min_rr_ratio * 0.9:
                    reasons.append(
                        f"rr {rr:.2f} < min_rr_ratio*0.9 {(min_rr_ratio * 0.9):.2f}"
                    )
                print(
                    f"[FILTER REJECT] symbol={symbol} stage=passes_minimums reason={' | '.join(reasons)}",
                    flush=True,
                )

            # softer qualification for scanner output
            qualifies = (
                action in ("BUY", "SELL")
                and passes_minimums
                and trend >= 40.0
            )

            # ✅ optional qualifies debug
            if not qualifies:
                reasons = []
                if action not in ("BUY", "SELL"):
                    reasons.append(f"action={action}")
                if not passes_minimums:
                    reasons.append("passes_minimums=False")
                if trend < 40.0:
                    reasons.append(f"trend {trend:.1f} < 40.0")
                print(
                    f"[FILTER REJECT] symbol={symbol} stage=qualifies reason={' | '.join(reasons)}",
                    flush=True,
                )

            scan_score = rank_score(signal)
            scan_score_reasons = [
                f"action={signal.get('action', 'HOLD')}",
                f"confidence={_safe_float(signal.get('confidence_pct')):.1f}",
                f"rr={_safe_float(signal.get('rr_ratio')):.2f}",
            ]

            if bool(signal.get("is_choppy")):
                scan_score -= 8.0
                scan_score_reasons.append("penalty=choppy")
            if not bool(signal.get("should_execute_now")):
                scan_score -= 6.0
                scan_score_reasons.append("penalty=not_execute_now")

            scored = {
                **signal,
                "symbol": symbol,
                "qualifies": qualifies,
                "passes_minimums": passes_minimums,
                "scan_score": round(scan_score, 2),
                "scan_score_reasons": scan_score_reasons,
            }

            results.append(scored)

        except Exception as e:
            print(f"[SCAN ERROR] symbol={symbol} error={e}", flush=True)
            errors.append({"symbol": symbol, "reason": str(e)})

    results.sort(key=lambda x: x.get("scan_score", -9999), reverse=True)

    qualified = [r for r in results if r.get("passes_minimums")]
    source = "websocket" if exchange.lower() == "binance" and websocket_enabled else "rest"
    top_candidates = qualified if qualified else results
    print("[SCAN DEBUG] qualified_count =", len(qualified), "results_count =", len(results), "strategy_version=v3", flush=True)
    print("[SCAN DEBUG] first_top_symbols =", [x.get("symbol") for x in top_candidates[:5]], flush=True)

    return {
        "ok": True,
        "exchange": exchange,
        "timeframe": timeframe,
        "market_type": market_type,
        "data_source": source,
        "scanned_count": len(symbols),
        "qualified_count": len(qualified),
        "top": top_candidates[:limit],
        "all": results[:limit],
        "errors": errors[:10],
        "strategy_version": "v4",
    }