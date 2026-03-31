from __future__ import annotations

from .market_data import fetch_candles
from .advanced_signal_engine import Candle, build_trade_idea


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _calc_market_regime(idea) -> dict:
    breakout = _safe_float(idea.breakout_probability_pct)
    breakdown = _safe_float(idea.breakdown_probability_pct)
    bounce = _safe_float(idea.bounce_probability_pct)
    trend = _safe_float(idea.trend_strength_pct)
    confidence = _safe_float(idea.confidence_pct)
    rr = _safe_float(idea.rr_ratio)
    volume_ratio = _safe_float(idea.volume_ratio, 1.0)
    rsi = _safe_float(idea.rsi_pct, 50.0)
    current = _safe_float(idea.current)
    support = _safe_float(idea.support_level)
    resistance = _safe_float(idea.resistance_level)

    direction_edge = abs(breakout - breakdown)
    dominant_prob = max(breakout, breakdown, bounce)
    range_width_pct = ((resistance - support) / current * 100.0) if current > 0 and resistance > support else 0.0

    choppy_points = 0
    if trend < 58:
        choppy_points += 1
    if direction_edge < 10:
        choppy_points += 1
    if volume_ratio < 1.05:
        choppy_points += 1
    if 45 <= rsi <= 55:
        choppy_points += 1
    if 0 < range_width_pct < 1.4:
        choppy_points += 1

    is_choppy = choppy_points >= 3

    setup_quality = 0.0
    setup_quality += min(confidence, 100.0) * 0.38
    setup_quality += min(trend, 100.0) * 0.22
    setup_quality += min(dominant_prob, 100.0) * 0.18
    setup_quality += min(max(rr, 0.0), 3.0) * 9.0
    setup_quality += min(max(volume_ratio, 0.0), 2.0) * 6.0
    setup_quality += min(direction_edge, 30.0) * 0.5
    if is_choppy:
        setup_quality -= 18.0

    regime = "trend" if trend >= 62 and direction_edge >= 12 and volume_ratio >= 1.05 else "range"
    if is_choppy:
        regime = "choppy"

    return {
        "is_choppy": is_choppy,
        "choppy_score": choppy_points,
        "direction_edge": direction_edge,
        "dominant_probability_pct": dominant_prob,
        "range_width_pct": range_width_pct,
        "regime": regime,
        "setup_quality": round(max(0.0, min(setup_quality, 100.0)), 2),
    }


def decide_action_from_idea(idea):
    breakout = _safe_float(idea.breakout_probability_pct)
    breakdown = _safe_float(idea.breakdown_probability_pct)
    bounce = _safe_float(idea.bounce_probability_pct)
    confidence = _safe_float(idea.confidence_pct)
    rr = _safe_float(idea.rr_ratio)
    trend = _safe_float(idea.trend_strength_pct)
    volume_ratio = _safe_float(idea.volume_ratio, 1.0)
    rsi = _safe_float(idea.rsi_pct, 50.0)
    bias = (idea.bias or "").lower()
    price = _safe_float(idea.current)
    entry = _safe_float(idea.entry, price)
    sl = _safe_float(idea.sl)
    tp = _safe_float(idea.tp)
    support = _safe_float(idea.support_level)
    resistance = _safe_float(idea.resistance_level)

    regime = _calc_market_regime(idea)

    if confidence < 50:
        return "HOLD", f"V4 filter: very low confidence ({confidence:.1f}%)"

    if rr < 0.9:
        return "HOLD", f"V4 filter: very weak RR ({rr:.2f})"

    if price <= 0 or entry <= 0 or sl <= 0 or tp <= 0:
        return "HOLD", "V4 filter: invalid levels"

    if regime["is_choppy"]:
        return "HOLD", (
            f"V4 choppy caution: market messy "
            f"(score {regime['choppy_score']}/5, edge {regime['direction_edge']:.1f})"
        )

    extension_pct = abs(price - entry) / price * 100.0 if price else 0.0
    if extension_pct > 2.5:
        return "HOLD", f"V4 filter: price too extended from entry ({extension_pct:.2f}%)"

    stop_distance_pct = abs(price - sl) / price * 100.0 if price else 0.0
    if stop_distance_pct < 0.20:
        return "HOLD", f"V4 filter: stop too tight ({stop_distance_pct:.2f}%)"
    if stop_distance_pct > 3.2:
        return "HOLD", f"V4 filter: stop too wide ({stop_distance_pct:.2f}%)"

    breakout_edge = breakout - breakdown
    breakdown_edge = breakdown - breakout
    support_distance_pct = abs(price - support) / price * 100.0 if price and support > 0 else 999.0
    resistance_distance_pct = abs(resistance - price) / price * 100.0 if price and resistance > 0 else 999.0

    # momentum override for futures
    bullish_momentum = breakout >= 68 and volume_ratio >= 1.08
    bearish_momentum = breakdown >= 68 and volume_ratio >= 1.08

    if "bullish" in bias:
        if not (50 <= rsi <= 70):
            return "HOLD", f"V4 bullish filter: RSI not supportive ({rsi:.1f})"

        if bullish_momentum and trend >= 55 and rr >= 1.1:
            return "BUY", (
                f"V4 momentum long: breakout {breakout:.1f}%, edge {breakout_edge:.1f}, "
                f"trend {trend:.1f}%, RR {rr:.2f}, vol {volume_ratio:.2f}x"
            )

        if breakout >= 56 and breakout_edge >= 8 and trend >= 55 and rr >= 1.15 and volume_ratio >= 1.0:
            return "BUY", (
                f"V4 breakout long: breakout {breakout:.1f}%, edge {breakout_edge:.1f}, "
                f"trend {trend:.1f}%, RR {rr:.2f}, vol {volume_ratio:.2f}x"
            )

        if bounce >= 54 and breakout_edge >= 3 and trend >= 52 and rr >= 1.15 and support_distance_pct <= 1.3:
            return "BUY", (
                f"V4 pullback long: bounce {bounce:.1f}%, trend {trend:.1f}%, "
                f"support distance {support_distance_pct:.2f}%"
            )

        return "HOLD", (
            f"V4 bullish filter: no clean trigger (breakout {breakout:.1f}, edge {breakout_edge:.1f}, "
            f"trend {trend:.1f}, vol {volume_ratio:.2f})"
        )

    if "bearish" in bias:
        if not (30 <= rsi <= 50):
            return "HOLD", f"V4 bearish filter: RSI not supportive ({rsi:.1f})"

        if bearish_momentum and trend >= 55 and rr >= 1.1:
            return "SELL", (
                f"V4 momentum short: breakdown {breakdown:.1f}%, edge {breakdown_edge:.1f}, "
                f"trend {trend:.1f}%, RR {rr:.2f}, vol {volume_ratio:.2f}x"
            )

        if breakdown >= 56 and breakdown_edge >= 8 and trend >= 55 and rr >= 1.15 and volume_ratio >= 1.0:
            return "SELL", (
                f"V4 breakdown short: breakdown {breakdown:.1f}%, edge {breakdown_edge:.1f}, "
                f"trend {trend:.1f}%, RR {rr:.2f}, vol {volume_ratio:.2f}x"
            )

        if bounce <= 46 and breakdown_edge >= 3 and trend >= 52 and rr >= 1.15 and resistance_distance_pct <= 1.3:
            return "SELL", (
                f"V4 pullback short: bounce {bounce:.1f}%, trend {trend:.1f}, "
                f"resistance distance {resistance_distance_pct:.2f}%"
            )

        return "HOLD", (
            f"V4 bearish filter: no clean trigger (breakdown {breakdown:.1f}, edge {breakdown_edge:.1f}, "
            f"trend {trend:.1f}, vol {volume_ratio:.2f})"
        )

    return "HOLD", "V4 no-trade zone: neutral bias"

def generate_signal(
    symbol: str,
    exchange: str = "binance",
    timeframe: str = "1h",
    market_type: str = "future",
    testnet: bool = True,
    websocket_enabled: bool = True,
    sl_mode: str = "hybrid",
    sl_atr_multiplier: float = 1.35,
    sl_buffer_atr: float = 0.15,
    sl_buffer_pct: float = 0.001,
    min_stop_pct: float = 0.0035,
    target_rr: float = 1.2,
):
    rows = fetch_candles(
        symbol,
        exchange=exchange,
        timeframe=timeframe,
        market_type=market_type,
        testnet=testnet,
        websocket_enabled=websocket_enabled,
    )
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

    idea = build_trade_idea(
        candles,
        sl_mode=sl_mode,
        sl_atr_multiplier=sl_atr_multiplier,
        sl_buffer_atr=sl_buffer_atr,
        sl_buffer_pct=sl_buffer_pct,
        min_stop_pct=min_stop_pct,
        target_rr=target_rr,
    )

    regime = _calc_market_regime(idea)
    final_action, decision_reason = decide_action_from_idea(idea)

    raw_action = str(idea.action or "HOLD").upper().strip()
    confidence = float(idea.confidence_pct or 0.0)
    rr = float(idea.rr_ratio or 0.0)
    trend = float(idea.trend_strength_pct or 0.0)
    volume_ratio = float(idea.volume_ratio or 0.0)
    breakout = float(idea.breakout_probability_pct or 0.0)
    breakdown = float(idea.breakdown_probability_pct or 0.0)

    price = float(idea.current or 0.0)
    entry = float(idea.entry or idea.current or 0.0)
    entry_distance_pct = (
        abs(price - entry) / price * 100.0
        if price > 0 and entry > 0
        else 0.0
    )

    bullish_structure = breakout >= max(55.0, breakdown + 8.0)
    bearish_structure = breakdown >= max(55.0, breakout + 8.0)

    structure_ok = (
        (raw_action == "BUY" and bullish_structure) or
        (raw_action == "SELL" and bearish_structure)
    )

    momentum_ok = volume_ratio >= 0.25
    trend_ok = trend >= 55.0
    confidence_ok = confidence >= 42.0
    rr_ok = rr >= 0.80
    not_extended = entry_distance_pct <= 2.5
    regime_ok = not regime["is_choppy"]

    v2_should_execute = (
        raw_action in ("BUY", "SELL")
        and structure_ok
        and trend_ok
        and confidence_ok
        and rr_ok
        and momentum_ok
        and not_extended
        and regime_ok
    )
    breakout_strong = (
        trend >= 75
        and confidence >= 70
        and volume_ratio >= 1.2
    )

    breakout_entry = (
        raw_action in ("BUY", "SELL")
        and breakout_strong
        and regime_ok
    )

    # FINAL EXECUTION (UPGRADE)
    v2_should_execute = v2_should_execute or breakout_entry

    if final_action == "HOLD" and v2_should_execute:
        final_action = raw_action
        decision_reason = (
            f"V2.2 execute | conf={confidence:.1f} rr={rr:.2f} "
            f"trend={trend:.1f} vol={volume_ratio:.2f}"
        )

    stop_distance_pct = (
        ((idea.current - idea.sl) / idea.current * 100.0)
        if idea.current and idea.sl and final_action == "BUY"
        else ((idea.sl - idea.current) / idea.current * 100.0)
        if idea.current and idea.sl and final_action == "SELL"
        else None
    )

    tp_distance_pct = (
        ((idea.tp - idea.current) / idea.current * 100.0)
        if idea.current and idea.tp and final_action == "BUY"
        else ((idea.current - idea.tp) / idea.current * 100.0)
        if idea.current and idea.tp and final_action == "SELL"
        else None
    )

    return {
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "market_type": market_type,
        "bias": idea.bias,
        "action": final_action,
        "raw_action": raw_action,
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
        "sl_mode": sl_mode,
        "sl_atr_multiplier": sl_atr_multiplier,
        "sl_buffer_atr": sl_buffer_atr,
        "sl_buffer_pct": sl_buffer_pct,
        "min_stop_pct": min_stop_pct,
        "target_rr": target_rr,
        "stop_distance_pct": stop_distance_pct,
        "tp_distance_pct": tp_distance_pct,
        "should_execute_now": v2_should_execute,
        "is_choppy": regime["is_choppy"],
        "choppy_score": regime["choppy_score"],
        "direction_edge": regime["direction_edge"],
        "dominant_probability_pct": regime["dominant_probability_pct"],
        "market_regime": regime["regime"],
        "range_width_pct": regime["range_width_pct"],
        "setup_quality": regime["setup_quality"],
        "strategy_version": "v5_structure_momentum",
        "data_source": "websocket" if exchange == "binance" and websocket_enabled else "rest",
    }