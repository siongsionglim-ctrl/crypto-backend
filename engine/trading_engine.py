from .market_data import fetch_candles
from .advanced_signal_engine import Candle, build_trade_idea


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

    return {
        "symbol": symbol,
        "bias": idea.bias,
        "action": idea.action,
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