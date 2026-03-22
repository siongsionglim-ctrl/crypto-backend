from __future__ import annotations

from .market_data import fetch_candles
from .advanced_signal_engine import Candle, build_trade_idea



def decide_action_from_idea(idea):
    breakout = float(idea.breakout_probability_pct or 0.0)
    breakdown = float(idea.breakdown_probability_pct or 0.0)
    bounce = float(idea.bounce_probability_pct or 0.0)
    confidence = float(idea.confidence_pct or 0.0)
    rr = float(idea.rr_ratio or 0.0)
    trend = float(idea.trend_strength_pct or 0.0)
    bias = (idea.bias or "").lower()
    price = float(idea.current or 0.0)
    entry = float(idea.entry or price or 0.0)
    sl = float(idea.sl or 0.0)
    tp = float(idea.tp or 0.0)

    if confidence < 55:
        return "HOLD", f"Low confidence ({confidence:.1f}%)"
    if rr < 1.0:
        return "HOLD", f"Weak RR ({rr:.2f})"
    if price <= 0 or entry <= 0 or sl <= 0 or tp <= 0:
        return "HOLD", "Invalid levels"

    extension_pct = abs(price - entry) / price * 100.0 if price else 0.0
    if extension_pct > 3.5:
        return "HOLD", f"Price too extended from entry ({extension_pct:.2f}%)"

    if "bullish" in bias:
        if breakout >= 55 and breakout > breakdown + 8 and trend >= 45 and rr >= 1.25:
            return "BUY", (
                f"Bullish setup confirmed: breakout {breakout:.1f}%, confidence {confidence:.1f}%, RR {rr:.2f}"
            )
        if bounce >= 48 and breakout >= breakdown and rr >= 1.2 and confidence >= 60:
            return "BUY", f"Bullish bounce setup: bounce {bounce:.1f}%, confidence {confidence:.1f}%, RR {rr:.2f}"
        return "HOLD", f"Bullish bias but breakout not dominant ({breakout:.1f} vs {breakdown:.1f})"

    if "bearish" in bias:
        if breakdown >= 55 and breakdown > breakout + 8 and trend >= 45 and rr >= 1.25:
            return "SELL", (
                f"Bearish setup confirmed: breakdown {breakdown:.1f}%, confidence {confidence:.1f}%, RR {rr:.2f}"
            )
        return "HOLD", f"Bearish bias but breakdown not dominant ({breakdown:.1f} vs {breakout:.1f})"

    return "HOLD", "Neutral bias"



def generate_signal(
    symbol: str,
    exchange: str = "binance",
    timeframe: str = "1h",
    market_type: str = "future",
    testnet: bool = True,
):
    rows = fetch_candles(symbol, exchange=exchange, timeframe=timeframe, market_type=market_type, testnet=testnet)
    if not rows:
        return {"error": "No data"}

    candles = [
        Candle(
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
        )
        for r in rows
    ]

    idea = build_trade_idea(candles)
    final_action, decision_reason = decide_action_from_idea(idea)
    stop_distance_pct = ((idea.current - idea.sl) / idea.current * 100.0) if idea.current and idea.sl and final_action == "BUY" else ((idea.sl - idea.current) / idea.current * 100.0) if idea.current and idea.sl and final_action == "SELL" else None
    tp_distance_pct = ((idea.tp - idea.current) / idea.current * 100.0) if idea.current and idea.tp and final_action == "BUY" else ((idea.current - idea.tp) / idea.current * 100.0) if idea.current and idea.tp and final_action == "SELL" else None

    return {
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "market_type": market_type,
        "bias": idea.bias,
        "action": final_action,
        "raw_action": idea.action,
        "decision_reason": decision_reason,
        "price": idea.current,
        "entry": idea.entry,
        "entry_low": idea.entry_low,
        "entry_high": idea.entry_high,
        "sl": idea.sl,
        "tp": idea.tp,
        "rr_ratio": idea.rr_ratio,
        "trend_strength_pct": idea.trend_strength_pct,
        "breakout_probability_pct": idea.breakout_probability_pct,
        "breakdown_probability_pct": idea.breakdown_probability_pct,
        "bounce_probability_pct": idea.bounce_probability_pct,
        "support_level": idea.support_level,
        "resistance_level": idea.resistance_level,
        "rsi_pct": idea.rsi_pct,
        "volume_ratio": idea.volume_ratio,
        "confidence_pct": idea.confidence_pct,
        "grade": idea.grade,
        "confidence_reasons": idea.confidence_reasons,
        "reason": idea.reason,
        "stop_distance_pct": stop_distance_pct,
        "tp_distance_pct": tp_distance_pct,
        "should_execute_now": final_action in ("BUY", "SELL") and float(idea.confidence_pct or 0.0) >= 60 and float(idea.rr_ratio or 0.0) >= 1.2,
    }
