from __future__ import annotations

from engine.scanner_engine import scan_symbols
from exchange_executor import place_market_order
from risk_manager import evaluate_risk, record_trade, register_open_position


def normalize_side(action: str | None):
    if not action:
        return None
    a = action.upper().strip()
    if a == "BUY":
        return "buy"
    if a == "SELL":
        return "sell"
    return None


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_upper(value):
    return str(value or "").upper().strip()


def _score_signal(signal: dict) -> tuple[float, list[str]]:
    """
    Hunter v2 scoring:
    - confidence
    - RR ratio
    - trend strength
    - breakout / breakdown alignment
    - action quality
    - setup bonus
    - penalties for weak / late / wide-SL setups
    """

    market_regime = str(signal.get("market_regime") or "").lower().strip()
    range_mode = bool(signal.get("range_mode"))
    reasons = []

    confidence = _safe_float(signal.get("confidence_pct"), 0.0)
    rr = _safe_float(signal.get("rr_ratio"), 0.0)
    trend_strength = _safe_float(signal.get("trend_strength_pct"), 0.0)
    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    bounce = _safe_float(signal.get("bounce_probability_pct"), 0.0)

    price = _safe_float(signal.get("price"), 0.0)
    entry = _safe_float(signal.get("entry") or signal.get("price"), 0.0)
    sl = _safe_float(signal.get("sl") or signal.get("stop_loss"), 0.0)

    action = _safe_upper(signal.get("action"))
    trend = _safe_upper(signal.get("trend"))
    setup_type = str(signal.get("setup_type") or "").strip().lower()

    score = 0.0

    # confidence
    score += min(confidence, 100.0) * 0.30
    reasons.append(f"confidence={confidence:.1f}")

    # rr
    rr_component = min(rr, 3.0) / 3.0 * 20.0
    score += rr_component
    reasons.append(f"rr={rr:.2f}")

    # trend strength
    score += min(trend_strength, 100.0) * 0.20
    reasons.append(f"trend_strength={trend_strength:.1f}")

    # action and directional alignment
    if action == "BUY":
        directional_edge = max(breakout, bounce)
        score += directional_edge * 0.18
        reasons.append(f"buy_edge={directional_edge:.1f}")
        if trend in ("BULLISH", "UPTREND"):
            score += 8.0
            reasons.append("trend_aligned=buy")
    elif action == "SELL":
        directional_edge = breakdown
        score += directional_edge * 0.18
        reasons.append(f"sell_edge={directional_edge:.1f}")
        if trend in ("BEARISH", "DOWNTREND"):
            score += 8.0
            reasons.append("trend_aligned=sell")
    else:
        score -= 18.0
        reasons.append("penalty=hold_or_invalid_action")

    # setup bonuses
    if "breakout" in setup_type:
        score += 6.0
        reasons.append("bonus=breakout_setup")
    elif "pullback" in setup_type:
        score += 4.0
        reasons.append("bonus=pullback_setup")
    elif "reversal" in setup_type:
        score += 2.0
        reasons.append("bonus=reversal_setup")

    # penalties
    if rr < 1.0:
        if range_mode:
            score -= 4.0
            reasons.append("penalty=low_rr_range")
        else:
            score -= 10.0
            reasons.append("penalty=low_rr")

    if confidence < 50.0:
        score -= 10.0
        reasons.append("penalty=low_confidence")

    if trend_strength < 40.0:
        if not range_mode:
            score -= 8.0
            reasons.append("penalty=weak_trend")

        # Hunter v3 range bonuses
    if range_mode:
        score += 6.0
        reasons.append("bonus=range_mode")

        if market_regime in ("range", "sideways", "neutral"):
            score += 6.0
            reasons.append("bonus=range_regime")

        # range trades accept slightly smaller RR
        if rr >= 0.9:
            score += 3.0
            reasons.append("bonus=acceptable_rr_for_range")

    # stop-loss width penalty
    if entry > 0 and sl > 0:
        sl_pct = abs(entry - sl) / entry * 100.0
        if sl_pct > 4.0:
            score -= 12.0
            reasons.append(f"penalty=wide_sl_{sl_pct:.2f}%")
        elif sl_pct > 2.5:
            score -= 5.0
            reasons.append(f"penalty=mid_sl_{sl_pct:.2f}%")

    # late-entry penalty
    if price > 0 and entry > 0:
        distance_pct = abs(price - entry) / price * 100.0
        if distance_pct > 1.2:
            score -= 6.0
            reasons.append(f"penalty=late_entry_{distance_pct:.2f}%")

    return round(score, 2), reasons


def _rank_candidates(scan_result: dict | None) -> list[dict]:
    top = (scan_result or {}).get("top", []) or []
    ranked = []

    for item in top:
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol")
        if not symbol or not isinstance(symbol, str):
            continue

        candidate = _range_trade_signal(item) or item
        score, score_reasons = _score_signal(candidate)
        enriched = dict(candidate)
        enriched["hunter_score"] = round(score, 2)
        enriched["hunter_score_reasons"] = score_reasons
        ranked.append(enriched)

    ranked.sort(key=lambda x: x.get("hunter_score", 0.0), reverse=True)
    return ranked


def _resolve_best_signal(scan_result: dict | None, min_hunter_score: float, range_min_hunter_score: float = 48.0) -> tuple[dict | None, list[dict]]:
    ranked = _rank_candidates(scan_result)
    if not ranked:
        return None, []

    best = ranked[0]
    threshold = range_min_hunter_score if best.get("range_mode") else min_hunter_score
    if _safe_float(best.get("hunter_score"), 0.0) < threshold:
        return None, ranked

    return best, ranked


def run_auto_hunter(config: dict, scan_result: dict | None = None):
    symbols = config.get("scan_symbols") or [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "SUIUSDT",
    ]

    # Hunter v2: broad scanner, strict final decision
    scanner_confidence = float(config.get("scanner_min_confidence_pct", 45.0))
    scanner_rr = float(config.get("scanner_min_rr_ratio", 0.8))
    final_confidence = float(config.get("min_confidence_pct", 52.0))
    final_rr = float(config.get("min_rr_ratio", 1.1))
    min_hunter_score = float(config.get("min_hunter_score", 58.0))
    best, ranked = _resolve_best_signal(
        scan_result,
        min_hunter_score=min_hunter_score,
        range_min_hunter_score=float(config.get("range_min_hunter_score", 48.0)),
    )

    if scan_result is None:
        scan_result = scan_symbols(
            symbols=symbols,
            min_confidence_pct=scanner_confidence,
            min_rr_ratio=scanner_rr,
            limit=int(config.get("scan_limit", 12)),
            exchange=config.get("scan_exchange") or config.get("exchange", "binance"),
            timeframe=config.get("scan_timeframe") or config.get("timeframe", "1h"),
            market_type=config.get("scan_market_type") or config.get("market_type", "future"),
            testnet=bool(config.get("testnet", True)),
        )

    best, ranked = _resolve_best_signal(scan_result, min_hunter_score=min_hunter_score)

    if not best:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "scan_result": scan_result,
            "ranked_candidates": ranked[:5],
            "reason": "No setup passed Hunter v2 score threshold",
        }

    symbol = best.get("symbol")
    if not symbol or not isinstance(symbol, str):
        return {
            "ok": False,
            "mode": "hunter_error",
            "scan_result": scan_result,
            "ranked_candidates": ranked[:5],
            "reason": f"Invalid symbol from scanner: {symbol}",
        }

    action = best.get("action")
    side = normalize_side(action)

    # optional directional fallback if action missing but trend is strong
    if not side:
        trend = _safe_upper(best.get("trend"))
        trend_strength = _safe_float(best.get("trend_strength_pct"), 0.0)
        if trend in ("BULLISH", "UPTREND") and trend_strength >= 65:
            side = "buy"
            best["action"] = "BUY"
        elif trend in ("BEARISH", "DOWNTREND") and trend_strength >= 65:
            side = "sell"
            best["action"] = "SELL"

    if not side:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "ranked_candidates": ranked[:5],
            "scan_result": scan_result,
            "reason": "Top setup is not executable (no valid BUY/SELL action)",
        }

    # final hard risk gate
    risk = evaluate_risk(
        signal=best,
        max_daily_trades=int(config.get("max_daily_trades", 5)),
        min_confidence_pct=final_confidence,
        min_rr_ratio=final_rr,
        cooldown_minutes=int(config.get("cooldown_minutes", 5)),
        allowed_sides=tuple(config.get("allowed_sides", ["BUY", "SELL"])),
        max_daily_loss_pct=float(config.get("max_daily_loss_pct", 5.0)),
        max_open_positions=int(config.get("max_open_positions", 2)),
        max_consecutive_losses=int(config.get("max_consecutive_losses", 3)),
        max_stop_loss_pct=float(config.get("max_stop_loss_pct", config.get("max_sl_pct", 5.0))),
    )

    if not risk.allowed:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "ranked_candidates": ranked[:5],
            "scan_result": scan_result,
            "reason": risk.reason or "Risk check failed",
        }

    if not config.get("auto_trade", False):
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "ranked_candidates": ranked[:5],
            "scan_result": scan_result,
            "reason": "Auto trade disabled in config",
        }

    entry_price = best.get("entry") or best.get("price")
    stop_loss = best.get("sl") or best.get("stop_loss")
    take_profit = best.get("tp") or best.get("take_profit")

    amount = float(config.get("amount", 0.001))
    risk_per_trade_pct = float(config.get("risk_per_trade_pct", 1.0))

    if best.get("range_mode"):
        amount *= float(config.get("range_amount_multiplier", 0.7))
        risk_per_trade_pct *= float(config.get("range_risk_multiplier", 0.7))

    try:
        order = place_market_order(
            exchange_name=config["exchange"],
            api_key=config["api_key"],
            secret=config["secret"],
            passphrase=config.get("passphrase"),
            symbol=symbol,
            side=side,
            amount=amount,
            testnet=bool(config.get("testnet", True)),
            market_type=config.get("market_type", "future"),
            leverage=int(config.get("leverage", 3)),
            auto_leverage=bool(config.get("auto_leverage", True)),
            risk_per_trade_pct=risk_per_trade_pct,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
    except Exception as e:
        return {
            "ok": False,
            "mode": "hunter_error",
            "best_signal": best,
            "ranked_candidates": ranked[:5],
            "scan_result": scan_result,
            "reason": f"Failed to place order: {e}",
        }

    register_open_position(
        symbol,
        best.get("action", side).upper(),
        float(order.get("amount") or config.get("amount", 0.001)),
        entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        order_meta={
            "market_symbol": order.get("market_symbol"),
            "requested_leverage": order.get("requested_leverage"),
            "applied_leverage": order.get("applied_leverage"),
            "notional_estimate": order.get("notional_estimate"),
            "exit_orders": order.get("exit_orders") or {},
            "exit_order_warnings": order.get("exit_order_warnings") or [],
            "hunter_score": best.get("hunter_score"),
            "hunter_score_reasons": best.get("hunter_score_reasons"),
        },
    )
    record_trade(best)

    return {
        "ok": True,
        "mode": "hunter_auto_trade",
        "best_signal": best,
        "ranked_candidates": ranked[:5],
        "scan_result": scan_result,
        "order": order,
        "reason": "Hunter v2 trade executed successfully with TP/SL",
    }

def _safe_bool(value) -> bool:
    return bool(value)


def _range_trade_signal(signal: dict) -> dict | None:
    """
    Hunter v3 range-fade logic.
    Returns a modified signal dict when a range setup is detected,
    otherwise returns None.
    """
    market_regime = str(signal.get("market_regime") or "").lower().strip()
    trend = _safe_upper(signal.get("trend"))
    action = _safe_upper(signal.get("action"))

    price = _safe_float(signal.get("price"), 0.0)
    entry = _safe_float(signal.get("entry") or signal.get("price"), 0.0)
    sl = _safe_float(signal.get("sl") or signal.get("stop_loss"), 0.0)

    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    bounce = _safe_float(signal.get("bounce_probability_pct"), 0.0)
    confidence = _safe_float(signal.get("confidence_pct"), 0.0)
    trend_strength = _safe_float(signal.get("trend_strength_pct"), 0.0)
    rr = _safe_float(signal.get("rr_ratio"), 0.0)

    is_choppy = _safe_bool(signal.get("is_choppy"))
    should_execute_now = _safe_bool(signal.get("should_execute_now"))

    # Need range/neutral environment
    is_range = market_regime in ("range", "sideways", "neutral")

    if not is_range:
        return None

    # Avoid fading a strong trend
    if trend in ("BULLISH", "UPTREND", "BEARISH", "DOWNTREND") and trend_strength >= 65:
        return None

    # Avoid random noisy mid-range trades
    if confidence < 40:
        return None

    # Optional: allow some chop, but not extreme chaos
    if is_choppy and trend_strength < 25:
        return None

    # RANGE SHORT:
    # near range high / resistance, breakout weak, breakdown stronger than breakout
    if breakout < 35 and breakdown >= breakout + 8 and action in ("HOLD", "SELL"):
        s = dict(signal)
        s["action"] = "SELL"
        s["setup_type"] = "range_short"
        s["range_mode"] = True
        s["rr_ratio"] = max(rr, 0.9)
        s["confidence_pct"] = max(confidence, 48.0)
        s["should_execute_now"] = True if should_execute_now else s.get("should_execute_now", True)
        return s

    # RANGE LONG:
    # near range low / support, breakdown weak, bounce stronger than breakdown
    if breakdown < 35 and bounce >= breakdown + 8 and action in ("HOLD", "BUY"):
        s = dict(signal)
        s["action"] = "BUY"
        s["setup_type"] = "range_long"
        s["range_mode"] = True
        s["rr_ratio"] = max(rr, 0.9)
        s["confidence_pct"] = max(confidence, 48.0)
        s["should_execute_now"] = True if should_execute_now else s.get("should_execute_now", True)
        return s

    return None