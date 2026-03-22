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

    decision_reason = []

    # Hard safety gates first
    if confidence < 55:
        return "HOLD", f"Low confidence ({confidence:.1f}%)"

    if rr < 1.0:
        return "HOLD", f"Weak RR ({rr:.2f})"

    if "bullish" in bias:
        if breakout >= 55 and breakout > breakdown + 8 and trend >= 45:
            if rr >= 1.3:
                return "BUY", (
                    f"Bullish setup confirmed: breakout {breakout:.1f}%, "
                    f"confidence {confidence:.1f}%, RR {rr:.2f}"
                )
            return "HOLD", f"Bullish setup but RR too weak ({rr:.2f})"

        if bounce >= 45 and breakout >= breakdown and rr >= 1.2 and confidence >= 60:
            return "BUY", (
                f"Bullish bounce setup: bounce {bounce:.1f}%, "
                f"confidence {confidence:.1f}%, RR {rr:.2f}"
            )

        decision_reason.append(
            f"Bullish bias but breakout not dominant "
            f"(breakout {breakout:.1f} vs breakdown {breakdown:.1f})"
        )

    elif "bearish" in bias:
        if breakdown >= 55 and breakdown > breakout + 8 and trend >= 45:
            if rr >= 1.3:
                return "SELL", (
                    f"Bearish setup confirmed: breakdown {breakdown:.1f}%, "
                    f"confidence {confidence:.1f}%, RR {rr:.2f}"
                )
            return "HOLD", f"Bearish setup but RR too weak ({rr:.2f})"

        decision_reason.append(
            f"Bearish bias but breakdown not dominant "
            f"(breakdown {breakdown:.1f} vs breakout {breakout:.1f})"
        )

    else:
        decision_reason.append("Neutral bias")

    return "HOLD", "; ".join(decision_reason) if decision_reason else "No valid trade setup"


def generate_signal(symbol: str):
    rows = fetch_candles(symbol)
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

    return {
        "symbol": symbol,
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
    }