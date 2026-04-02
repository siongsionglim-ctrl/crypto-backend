from __future__ import annotations

from engine.scanner_engine import scan_symbols
from exchange_executor import place_market_order
from risk_manager import evaluate_risk, record_trade, register_open_position
from engine.trading_engine import generate_signal


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


def _score_signal(signal: dict, config: dict | None = None, htf_signal: dict | None = None) -> tuple[float, list[str], dict]:
    config = config or {}
    reasons: list[str] = []
    
    score_breakdown: dict[str, float] = {}
    action = str(signal.get("action") or "HOLD").upper().strip()
    regime = _classify_regime(signal)

    confidence = _safe_float(signal.get("confidence_pct"), 0.0)
    rr = _safe_float(signal.get("rr_ratio"), 0.0)
    trend_strength = _safe_float(signal.get("trend_strength_pct"), 0.0)
    volume_ratio = _safe_float(signal.get("volume_ratio"), 1.0)
    setup_quality = _safe_float(signal.get("setup_quality"), 0.0)
    direction_edge = _safe_float(signal.get("direction_edge"), 0.0)
    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown_prob = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    bounce = _safe_float(signal.get("bounce_probability_pct"), 0.0)

    total = 0.0

    # Base quality
    base_quality = (
        _norm(confidence, 45.0, 90.0) * 8.0
        + _norm(trend_strength, 35.0, 80.0) * 5.0
        + _norm(setup_quality, 30.0, 85.0) * 5.0
        + _norm(direction_edge, 3.0, 20.0) * 4.0
    )
    total += base_quality
    score_breakdown["base_quality"] = round(base_quality, 2)
    reasons.append(f"base={base_quality:.1f}")

    # RR quality
    rr_quality = _norm(rr, 1.0, 2.5) * 12.0
    total += rr_quality
    score_breakdown["rr_quality"] = round(rr_quality, 2)

    # Volume quality
    volume_quality = _norm(volume_ratio, 0.60, 1.5) * 10.0
    total += volume_quality
    score_breakdown["volume_quality"] = round(volume_quality, 2)

    # Regime fit
    regime_fit = 0.0
    if regime == "trend":
        if action == "BUY":
            regime_fit += _norm(breakout, 50.0, 80.0) * 8.0
        elif action == "SELL":
            regime_fit += _norm(breakdown_prob, 50.0, 80.0) * 8.0
        else:
            regime_fit -= 8.0
    elif regime == "range":
        regime_fit += _norm(bounce, 40.0, 75.0) * 8.0
        if action == "HOLD":
            regime_fit -= 4.0
    else:
        regime_fit -= 12.0
        reasons.append("penalty=choppy_regime")

    total += regime_fit
    score_breakdown["regime_fit"] = round(regime_fit, 2)

    # HTF alignment
    htf_score = 0.0
    if bool(config.get("hunter_enable_htf_confirm", True)):
        htf_score = _htf_alignment_score(signal, htf_signal)
        total += htf_score
    score_breakdown["htf_alignment"] = round(htf_score, 2)
    if htf_score < 0:
        reasons.append("penalty=htf_misaligned")
    elif htf_score > 0:
        reasons.append("bonus=htf_aligned")

    # Momentum boost
    momentum = _momentum_boost(signal, config)
    total += momentum
    score_breakdown["momentum_boost"] = round(momentum, 2)

    # Entry efficiency
    entry_eff = _entry_efficiency_penalty(signal, config)
    total += entry_eff
    score_breakdown["entry_efficiency"] = round(entry_eff, 2)

    # Hard penalties
    if action == "HOLD":
        total -= 6.0
        reasons.append("penalty=hold")
    if rr < _safe_float(config.get("hunter_min_rr"), 0.8):
        total -= 4.0
        reasons.append("penalty=low_rr")
    if volume_ratio < _safe_float(config.get("hunter_min_volume_ratio"), 0.8):
        total -= 2.0
        reasons.append("penalty=low_volume")

    total = _clamp(total, 0.0, 100.0)
    score_breakdown["total"] = round(total, 2)

    return round(total, 2), reasons, score_breakdown


def _rank_candidates(scan_result: dict | None, config: dict | None = None) -> list[dict]:
    config = config or {}
    top = (scan_result or {}).get("top", []) or []
    ranked = []

    print(f"[HUNTER RANK] incoming top_count={len(top)}", flush=True)
    if top:
        print(f"[HUNTER RANK] first_top={top[:2]}", flush=True)

    strong_threshold = _safe_float(config.get("hunter_strong_threshold"), 60.0)
    medium_threshold = _safe_float(config.get("hunter_medium_threshold"), 48.0)
    htf_timeframe = str(config.get("hunter_htf_timeframe") or "1h")

    for item in top:
        if not isinstance(item, dict):
            continue

        candidate = _range_trade_signal(item) or item
        symbol = candidate.get("symbol")
        htf_signal = None

        if symbol and bool(config.get("hunter_enable_htf_confirm", True)):
            try:
                htf_signal = generate_signal(
                    symbol,
                    exchange=config.get("scan_exchange") or config.get("exchange", "binance"),
                    timeframe=htf_timeframe,
                    market_type=config.get("scan_market_type") or config.get("market_type", "future"),
                    testnet=bool(config.get("testnet", True)),
                )
            except Exception:
                htf_signal = None

        score, score_reasons, breakdown = _score_signal(candidate, config=config, htf_signal=htf_signal)
        decision = _timing_decision(candidate, score, config)
        regime = _classify_regime(candidate)

        enriched = dict(candidate)
        enriched["hunter_score"] = round(score, 2)
        enriched["hunter_score_reasons"] = score_reasons
        enriched["hunter_score_breakdown"] = breakdown
        enriched["regime"] = regime
        enriched["htf_signal"] = htf_signal or {}
        enriched["v4_decision"] = decision

        if score >= strong_threshold:
            enriched["quality"] = "STRONG"
        elif score >= medium_threshold:
            enriched["quality"] = "MEDIUM"
        else:
            enriched["quality"] = "WEAK"

        ranked.append(enriched)

    ranked.sort(key=lambda x: x.get("hunter_score", 0.0), reverse=True)

    print(f"[HUNTER RANK] ranked_count={len(ranked)}", flush=True)
    if ranked:
        print(f"[HUNTER RANK] best_ranked={ranked[0]}", flush=True)

    return ranked


def _resolve_best_signal(scan_result: dict | None, config: dict) -> tuple[dict | None, list[dict]]:
    ranked = _rank_candidates(scan_result, config=config)

    print(f"[HUNTER PICK] ranked_count={len(ranked)}", flush=True)
    if ranked:
        print(f"[HUNTER PICK] ranked_top3={ranked[:3]}", flush=True)

    if not ranked:
        print("[HUNTER BLOCK] ranked empty", flush=True)
        return None, []

    best = ranked[0]
    decision = str(best.get("v4_decision") or "SKIP").upper()

    print(
        f"[HUNTER PICK] best_symbol={best.get('symbol')} "
        f"action={best.get('action')} raw_action={best.get('raw_action')} "
        f"decision={decision} score={best.get('hunter_score')}",
        flush=True,
    )

    if decision == "AUTO_TRADE":
        best["v4_quality"] = best.get("quality", "STRONG")
        best["v4_action"] = "AUTO_TRADE"
        return best, ranked

    if decision == "WAIT_PULLBACK":
        best["v4_quality"] = best.get("quality", "MEDIUM")
        best["v4_action"] = "WAIT_PULLBACK"
        return best, ranked

    if decision == "WATCHLIST":
        best["v4_quality"] = best.get("quality", "MEDIUM")
        best["v4_action"] = "WATCHLIST"
        return best, ranked

    # 🔥 DEBUG FORCE MODE
    raw_action = str(best.get("raw_action") or "").upper().strip()
    action = str(best.get("action") or "").upper().strip()

    if action == "HOLD" and raw_action in ("BUY", "SELL"):
        print(f"[HUNTER FIX] forcing HOLD -> {raw_action}", flush=True)
        best["action"] = raw_action

    print(f"[HUNTER FIX] forcing best candidate anyway: {best.get('symbol')}", flush=True)
    best["v4_quality"] = best.get("quality", "WEAK")
    best["v4_action"] = "AUTO_TRADE"
    return best, ranked


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

    # STEP 1: ensure scan_result
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

    # STEP 2: V4 resolve
    best, ranked = _resolve_best_signal(scan_result, config=config)

    if not ranked:
        return {
            "ok": True,
            "mode": "hunter_v4",
            "status": "NO_DATA",
            "reason": "No candidates from scanner",
            "top_candidates": [],
            "scan_result": scan_result,
        }

    top_candidate = ranked[0]

    # STEP 3: no valid trade
    if not best:
        return {
            "ok": True,
            "mode": "hunter_v4",
            "status": "NO_TRADE",
            "symbol": top_candidate.get("symbol"),
            "score": top_candidate.get("hunter_score"),
            "quality": top_candidate.get("quality"),
            "signal": top_candidate.get("action"),
            "regime": top_candidate.get("regime"),
            "decision": top_candidate.get("v4_decision"),
            "reason": "Top candidate below threshold",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }
    print("[FORCE EXECUTION MODE]", flush=True)

    decision = str(best.get("v4_decision") or "SKIP").upper()

    # STEP 4: WAIT_PULLBACK
    if decision == "WAIT_PULLBACK":
        return {
            "ok": True,
            "mode": "hunter_v4",
            "status": "WAIT_PULLBACK",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "regime": best.get("regime"),
            "decision": decision,
            "reason": "Waiting for better entry",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
            "best_signal": best,
        }

    # STEP 5: WATCHLIST
    if decision == "WATCHLIST":
        return {
            "ok": True,
            "mode": "hunter_v4",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "regime": best.get("regime"),
            "decision": decision,
            "reason": "Monitoring setup",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
            "best_signal": best,
        }

    # STEP 6: validate executable
    symbol = best.get("symbol")
    action = best.get("action")

    raw_action = best.get("raw_action")

    # 🔥 FIX: override HOLD if raw_action is valid
    if action == "HOLD" and raw_action in ("BUY", "SELL"):
        print("[HUNTER FIX] overriding HOLD →", raw_action, flush=True)
        action = raw_action

    side = normalize_side(action)
    print(
        f"[FINAL SIGNAL] symbol={symbol} action={action} raw={raw_action} side={side}",
        flush=True
    )

    if not symbol or not side:
        return {
            "ok": True,
            "mode": "hunter_v4",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": "Signal not executable",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # STEP 7: risk check
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
            "mode": "hunter_v4",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": risk.reason or "Risk rejected",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }

    # STEP 8: auto trade OFF
    if not config.get("auto_trade", False):
        return {
            "ok": True,
            "mode": "hunter_v4",
            "status": "WATCHLIST",
            "symbol": best.get("symbol"),
            "score": best.get("hunter_score"),
            "quality": best.get("quality"),
            "signal": best.get("action"),
            "reason": "Auto trade disabled",
            "top_candidates": ranked[:5],
            "scan_result": scan_result,
        }
    
    print(f"[HUNTER DEBUG] best candidate = {best}", flush=True)
    print(f"[HUNTER DEBUG] action={best.get('action')} should_execute_now={best.get('should_execute_now')}", flush=True)

    # STEP 9: EXECUTE
    try:
        print(f"[HUNTER EXEC] symbol={symbol} side={side} auto_trade={config.get('auto_trade')} testnet={config.get('testnet')}", flush=True)
        print(f"[HUNTER EXEC] entry={best.get('entry')} sl={best.get('sl')} tp={best.get('tp')}", flush=True)
        print(f"[HUNTER EXEC] api_key_present={bool((config.get('api_key') or '').strip())} secret_present={bool((config.get('secret') or '').strip())}", flush=True)

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

        print(f"[HUNTER EXEC OK] order={order}", flush=True)

        print(f"[HUNTER EXEC ERROR] {type(e).__name__}: {e}", flush=True)
        
    except Exception as e:
        return {
            "ok": False,
            "mode": "hunter_error",
            "reason": f"Order failed: {e}",
            "top_candidates": ranked[:5],
        }
    print(f"[HUNTER EXEC ERROR] {type(e).__name__}: {e}", flush=True)

    return {
        "ok": True,
        "mode": "hunter_v4",
        "status": "AUTO_TRADE",
        "symbol": best.get("symbol"),
        "score": best.get("hunter_score"),
        "quality": best.get("quality"),
        "signal": best.get("action"),
        "regime": best.get("regime"),
        "decision": "AUTO_TRADE",
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

def _classify_regime(signal: dict) -> str:
    if bool(signal.get("is_choppy")):
        return "choppy"

    regime = str(signal.get("market_regime") or "").lower().strip()
    if regime in {"trend", "range"}:
        return regime

    trend_strength = _safe_float(signal.get("trend_strength_pct"), 0.0)
    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    bounce = _safe_float(signal.get("bounce_probability_pct"), 0.0)

    if trend_strength >= 55 and max(breakout, breakdown) >= 55:
        return "trend"
    if bounce >= 45 and trend_strength < 55:
        return "range"
    return "choppy"

def _htf_alignment_score(signal: dict, htf_signal: dict | None) -> float:
    if not isinstance(htf_signal, dict):
        return 0.0

    action = str(signal.get("action") or "").upper().strip()
    htf_action = str(htf_signal.get("action") or "").upper().strip()
    htf_trend = str(htf_signal.get("trend") or htf_signal.get("bias") or "").upper().strip()

    if action == "BUY":
        if htf_action == "BUY" or "BULLISH" in htf_trend:
            return 12.0
        if htf_action == "SELL" or "BEARISH" in htf_trend:
            return -12.0
        return 2.0

    if action == "SELL":
        if htf_action == "SELL" or "BEARISH" in htf_trend:
            return 12.0
        if htf_action == "BUY" or "BULLISH" in htf_trend:
            return -12.0
        return 2.0

    return -8.0

def _momentum_boost(signal: dict, config: dict) -> float:
    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    volume = _safe_float(signal.get("volume_ratio"), 1.0)
    action = str(signal.get("action") or "").upper().strip()

    trigger_pct = _safe_float(config.get("hunter_momentum_trigger_pct"), 65.0)
    min_volume = _safe_float(config.get("hunter_momentum_volume_ratio"), 1.1)

    if action == "BUY" and breakout >= trigger_pct and volume >= min_volume:
        return 12.0
    if action == "SELL" and breakdown >= trigger_pct and volume >= min_volume:
        return 12.0
    return 0.0

def _entry_efficiency_penalty(signal: dict, config: dict) -> float:
    price = _safe_float(signal.get("price"), 0.0)
    entry = _safe_float(signal.get("entry") or signal.get("price"), 0.0)
    if price <= 0 or entry <= 0:
        return 0.0

    distance_pct = abs(price - entry) / price * 100.0
    penalty = _safe_float(config.get("hunter_overextension_penalty"), 15.0)

    if distance_pct > 4.0:
        return -penalty
    if distance_pct > 2.0:
        return -4.0
    if distance_pct > 1.0:
        return -1.5
    return 3.0

def _timing_decision(signal: dict, score: float, config: dict) -> str:
    action = str(signal.get("action") or "").upper().strip()
    should_execute_now = bool(signal.get("should_execute_now"))
    breakout = _safe_float(signal.get("breakout_probability_pct"), 0.0)
    breakdown = _safe_float(signal.get("breakdown_probability_pct"), 0.0)
    momentum_trigger = _safe_float(config.get("hunter_momentum_trigger_pct"), 65.0)

    strong_th = _safe_float(config.get("hunter_strong_threshold"), 60.0)
    medium_th = _safe_float(config.get("hunter_medium_threshold"), 48.0)

    regime = str(signal.get("regime") or signal.get("market_regime") or "").lower().strip()

    price = _safe_float(signal.get("price"), 0.0)
    entry = _safe_float(signal.get("entry") or signal.get("price"), 0.0)
    distance_pct = abs(price - entry) / price * 100.0 if price > 0 and entry > 0 else 0.0

    if action not in {"BUY", "SELL"}:
        return "SKIP"

    if regime == "choppy":
        return "WATCHLIST" if score >= medium_th else "SKIP"

    if distance_pct > 2.5:
        return "WAIT_PULLBACK" if score >= medium_th else "SKIP"

    if score >= strong_th and should_execute_now:
        return "AUTO_TRADE"

    if score >= strong_th:
        if action == "BUY" and breakout >= momentum_trigger:
            return "AUTO_TRADE"
        if action == "SELL" and breakdown >= momentum_trigger:
            return "AUTO_TRADE"
        return "WAIT_PULLBACK"
    
    # allow medium-quality signals to trade if engine already says execute now
    if score >= medium_th and should_execute_now:
        return "AUTO_TRADE"

    if score >= medium_th:
        return "WATCHLIST"

    return "SKIP"

