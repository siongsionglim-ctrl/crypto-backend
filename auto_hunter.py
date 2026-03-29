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


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clamp((v - lo) / (hi - lo), 0.0, 1.0)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return _clamp((v - lo) / (hi - lo), 0.0, 1.0)


def _score_signal(signal: dict, config: dict | None = None) -> tuple[float, list[str], dict]:
    """
    Hunter V3 intelligent scoring.
    Returns:
      score, reasons, breakdown
    """

    config = config or {}
    reasons: list[str] = []
    breakdown: dict[str, float] = {}

    action = _safe_upper(signal.get("action"))
    trend = _safe_upper(signal.get("trend"))
    market_regime = str(signal.get("market_regime") or "").lower().strip()
    setup_type = str(signal.get("setup_type") or "").lower().strip()

    confidence = _safe_float(signal.get("confidence_pct"), 0.0)
    rr = _safe_float(signal.get("rr_ratio"), 0.0)
    trend_strength = _safe_float(signal.get("trend_strength_pct"), 0.0)
    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown_prob = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    bounce = _safe_float(signal.get("bounce_probability_pct"), 0.0)

    volume_ratio = _safe_float(signal.get("volume_ratio"), 1.0)
    direction_edge = _safe_float(signal.get("direction_edge"), 0.0)
    setup_quality = _safe_float(signal.get("setup_quality"), 0.0)
    should_execute_now = bool(signal.get("should_execute_now"))
    is_choppy = bool(signal.get("is_choppy"))
    range_mode = bool(signal.get("range_mode"))

    price = _safe_float(signal.get("price"), 0.0)
    entry = _safe_float(signal.get("entry") or signal.get("price"), 0.0)
    sl = _safe_float(signal.get("sl") or signal.get("stop_loss"), 0.0)
    stop_distance_pct = _safe_float(signal.get("stop_distance_pct"), 0.0)

    strong_threshold = _safe_float(config.get("hunter_strong_threshold"), 72.0)
    medium_threshold = _safe_float(config.get("hunter_medium_threshold"), 60.0)
    min_rr = _safe_float(config.get("hunter_min_rr"), 1.4)
    min_volume_ratio = _safe_float(config.get("hunter_min_volume_ratio"), 1.05)

    total = 0.0

    # 1) Base signal quality (0-22)
    base_score = (
        _norm(confidence, 45.0, 90.0) * 8.0
        + _norm(trend_strength, 40.0, 80.0) * 6.0
        + _norm(setup_quality, 35.0, 85.0) * 8.0
    )
    total += base_score
    breakdown["base_quality"] = round(base_score, 2)
    reasons.append(f"base_quality={base_score:.1f}")

    # 2) Directional edge (0-18)
    directional_score = 0.0
    if action == "BUY":
        directional_score += _norm(max(breakout, bounce), 50.0, 80.0) * 10.0
        directional_score += _norm(direction_edge, 4.0, 20.0) * 4.0
        if trend in ("BULLISH", "UPTREND", "BULLISH BIAS"):
            directional_score += 4.0
            reasons.append("trend_align=buy")
    elif action == "SELL":
        directional_score += _norm(breakdown_prob, 50.0, 80.0) * 10.0
        directional_score += _norm(direction_edge, 4.0, 20.0) * 4.0
        if trend in ("BEARISH", "DOWNTREND", "BEARISH BIAS"):
            directional_score += 4.0
            reasons.append("trend_align=sell")
    else:
        directional_score -= 12.0
        reasons.append("penalty=hold_action")

    total += directional_score
    breakdown["directional_edge"] = round(directional_score, 2)

    # 3) RR and stop quality (0-16)
    rr_stop_score = 0.0
    rr_stop_score += _norm(rr, max(0.8, min_rr - 0.3), 2.4) * 10.0

    if 0.25 <= stop_distance_pct <= 2.8:
        rr_stop_score += 6.0
    elif 0.15 <= stop_distance_pct <= 3.5:
        rr_stop_score += 2.0
    else:
        rr_stop_score -= 6.0
        reasons.append(f"penalty=bad_stop_distance_{stop_distance_pct:.2f}%")

    total += rr_stop_score
    breakdown["rr_stop_quality"] = round(rr_stop_score, 2)

    # 4) Volume + participation (0-12)
    volume_score = 0.0
    volume_score += _norm(volume_ratio, 0.95, 1.8) * 8.0

    breakout_like = (action == "BUY" and breakout >= 58) or (action == "SELL" and breakdown_prob >= 58)
    if breakout_like:
        if volume_ratio >= min_volume_ratio:
            volume_score += 4.0
            reasons.append("volume_confirms_break")
        else:
            volume_score -= 5.0
            reasons.append("penalty=weak_break_volume")

    total += volume_score
    breakdown["volume_quality"] = round(volume_score, 2)

    # 5) Regime + execution timing (0-14)
    regime_exec_score = 0.0

    if is_choppy or market_regime == "choppy":
        regime_exec_score -= 12.0
        reasons.append("penalty=choppy_market")
    elif market_regime == "trend":
        regime_exec_score += 6.0
        reasons.append("bonus=trend_regime")
    elif market_regime in ("range", "sideways", "neutral"):
        if range_mode:
            regime_exec_score += 3.0
            reasons.append("bonus=range_fit")
        else:
            regime_exec_score -= 4.0
            reasons.append("penalty=non_range_in_range_market")

    if should_execute_now:
        regime_exec_score += 8.0
        reasons.append("execute_now=yes")
    else:
        regime_exec_score -= 3.0
        reasons.append("execute_now=no")

    total += regime_exec_score
    breakdown["regime_execution"] = round(regime_exec_score, 2)

    # 6) Entry efficiency / late entry penalty (-10 to +8)
    entry_efficiency = 0.0
    if price > 0 and entry > 0:
        distance_pct = abs(price - entry) / price * 100.0
        if distance_pct <= 0.35:
            entry_efficiency += 8.0
            reasons.append("entry_timing=excellent")
        elif distance_pct <= 0.75:
            entry_efficiency += 4.0
            reasons.append("entry_timing=good")
        elif distance_pct <= 1.2:
            entry_efficiency -= 2.0
            reasons.append("penalty=slightly_late_entry")
        else:
            entry_efficiency -= 8.0
            reasons.append(f"penalty=late_entry_{distance_pct:.2f}%")

    total += entry_efficiency
    breakdown["entry_efficiency"] = round(entry_efficiency, 2)

    # 7) Setup-type nuance (-2 to +6)
    setup_bonus = 0.0
    if "breakout" in setup_type:
        setup_bonus += 5.0
    elif "pullback" in setup_type:
        setup_bonus += 4.0
    elif "reversal" in setup_type:
        setup_bonus += 2.0

    if range_mode:
        setup_bonus += 2.0

    total += setup_bonus
    breakdown["setup_bonus"] = round(setup_bonus, 2)

    # Final anti-garbage floor checks
    if confidence < 48:
        total -= 8.0
        reasons.append("penalty=very_low_confidence")
    if rr < 1.0 and not range_mode:
        total -= 10.0
        reasons.append("penalty=rr_below_1")
    if trend_strength < 35 and not range_mode:
        total -= 8.0
        reasons.append("penalty=very_weak_trend")

    total = _clamp(total, 0.0, 100.0)
    breakdown["total"] = round(total, 2)

    if total >= strong_threshold:
        reasons.append("quality=STRONG")
    elif total >= medium_threshold:
        reasons.append("quality=MEDIUM")
    else:
        reasons.append("quality=WEAK")

    return round(total, 2), reasons, breakdown


def _rank_candidates(scan_result: dict | None, config: dict | None = None) -> list[dict]:
    top = (scan_result or {}).get("top", []) or []
    ranked = []

    strong_threshold = _safe_float((config or {}).get("hunter_strong_threshold"), 72.0)
    medium_threshold = _safe_float((config or {}).get("hunter_medium_threshold"), 60.0)

    for item in top:
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol")
        if not symbol or not isinstance(symbol, str):
            continue

        candidate = _range_trade_signal(item) or item
        score, score_reasons, breakdown = _score_signal(candidate, config=config)

        enriched = dict(candidate)
        enriched["hunter_score"] = round(score, 2)
        enriched["hunter_score_reasons"] = score_reasons
        enriched["hunter_score_breakdown"] = breakdown

        if score >= strong_threshold:
            enriched["quality"] = "STRONG"
            enriched["v3_action"] = "AUTO_TRADE"
        elif score >= medium_threshold:
            enriched["quality"] = "MEDIUM"
            enriched["v3_action"] = "WATCHLIST"
        else:
            enriched["quality"] = "WEAK"
            enriched["v3_action"] = "SKIP"

        ranked.append(enriched)

    ranked.sort(key=lambda x: x.get("hunter_score", 0.0), reverse=True)
    return ranked


def _resolve_best_signal(scan_result: dict | None, config: dict) -> tuple[dict | None, list[dict]]:
    ranked = _rank_candidates(scan_result, config=config)
    if not ranked:
        return None, []

    strong_th = float(config.get("hunter_strong_threshold", 72.0))
    medium_th = float(config.get("hunter_medium_threshold", 60.0))

    best = ranked[0]
    score = _safe_float(best.get("hunter_score"), 0.0)

    if score >= strong_th:
        best["v3_quality"] = "STRONG"
        best["v3_action"] = "AUTO_TRADE"
        return best, ranked

    if score >= medium_th:
        best["v3_quality"] = "MEDIUM"
        best["v3_action"] = "WATCHLIST"
        return best, ranked

    best["v3_quality"] = "WEAK"
    best["v3_action"] = "SKIP"
    return None, ranked


def run_auto_hunter(config: dict, scan_result: dict | None = None):
    symbols = config.get("scan_symbols") or [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "SUIUSDT",
    ]
    print("[HUNTER DEBUG] scan_result keys =", list((scan_result or {}).keys()), flush=True)
    print("[HUNTER DEBUG] top count =", len((scan_result or {}).get("top", []) or []), flush=True)

    scanner_confidence = float(config.get("scanner_min_confidence_pct", 45.0))
    scanner_rr = float(config.get("scanner_min_rr_ratio", 0.8))
    final_confidence = float(config.get("min_confidence_pct", 52.0))
    final_rr = float(config.get("min_rr_ratio", 1.1))

    # ✅ STEP 1: Ensure scan_result exists FIRST
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

    # ✅ STEP 2: V3 scoring (correct call)
    best, ranked = _resolve_best_signal(scan_result, config=config)

    if not ranked:
        return {
            "ok": True,
            "mode": "hunter_v3",
            "status": "NO_DATA",
            "reason": "No candidates from scanner",
            "top_candidates": [],
            "scan_result": scan_result,
        }

    top_candidate = ranked[0]

    # ✅ STEP 3: No strong setup (but still show data!)
    if not best:
        return {
            "ok": True,
            "mode": "hunter_v3",
            "status": "NO_TRADE",
            "symbol": top_candidate.get("symbol"),
            "score": top_candidate.get("hunter_score"),
            "quality": top_candidate.get("quality"),
            "signal": top_candidate.get("action"),
            "reason": "Top candidate below threshold",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # ✅ STEP 4: MEDIUM = watch only
    if best.get("v3_action") == "WATCHLIST":
        return {
            "ok": True,
            "mode": "hunter_v3",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": "Medium setup, monitoring",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # ✅ STEP 5: validate trade
    symbol = best.get("symbol")
    action = best.get("action")
    side = normalize_side(action)

    if not symbol or not side:
        return {
            "ok": True,
            "mode": "hunter_v3",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": "Signal not executable",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # ✅ STEP 6: risk check
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
            "mode": "hunter_v3",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": risk.reason or "Risk rejected",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # ✅ STEP 7: auto trade OFF
    if not config.get("auto_trade", False):
        return {
            "ok": True,
            "mode": "hunter_v3",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": "Auto trade disabled",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # ✅ STEP 8: execute order
    try:
        order = place_market_order(
            exchange_name=config["exchange"],
            api_key=config["api_key"],
            secret=config["secret"],
            passphrase=config.get("passphrase"),
            symbol=symbol,
            side=side,
            amount=float(config.get("amount", 0.001)),
            testnet=bool(config.get("testnet", True)),
            market_type=config.get("market_type", "future"),
            leverage=int(config.get("leverage", 3)),
            auto_leverage=bool(config.get("auto_leverage", True)),
            risk_per_trade_pct=float(config.get("risk_per_trade_pct", 1.0)),
            entry_price=best.get("entry"),
            stop_loss=best.get("sl"),
            take_profit=best.get("tp"),
        )
    except Exception as e:
        return {
            "ok": False,
            "mode": "hunter_error",
            "reason": f"Order failed: {e}",
            "top_candidates": ranked[:5],
        }

    return {
        "ok": True,
        "mode": "hunter_v3",
        "status": "AUTO_TRADE",
        "symbol": best.get("symbol"),
        "score": best.get("hunter_score"),
        "quality": best.get("quality"),
        "signal": best.get("action"),
        "top_candidates": ranked[:5],
        "order": order,
        "reason": "Trade executed",
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