from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import json


STATE_FILE = Path("bot_state.json")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


def _default_state():
    return {
        "trade_count_today": 0,
        "last_trade_time": None,
        "last_trade_day": None,
    }


def load_state():
    if not STATE_FILE.exists():
        return _default_state()
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_daily_if_needed(state: dict):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("last_trade_day") != today:
        state["trade_count_today"] = 0
        state["last_trade_day"] = today
    return state


def evaluate_risk(
    signal: dict,
    max_daily_trades: int = 3,
    min_confidence_pct: float = 70.0,
    min_rr_ratio: float = 1.5,
    cooldown_minutes: int = 15,
    allowed_sides: tuple[str, ...] = ("BUY", "SELL"),
) -> RiskDecision:
    state = load_state()
    state = reset_daily_if_needed(state)

    action = str(signal.get("action", "HOLD")).upper()
    confidence = float(signal.get("confidence_pct") or 0.0)
    rr_ratio = float(signal.get("rr_ratio") or 0.0)

    if action not in allowed_sides:
        return RiskDecision(False, f"Action not tradable: {action}")

    if confidence < min_confidence_pct:
        return RiskDecision(False, f"Confidence too low: {confidence:.1f}% < {min_confidence_pct:.1f}%")

    if rr_ratio < min_rr_ratio:
        return RiskDecision(False, f"RR too low: {rr_ratio:.2f} < {min_rr_ratio:.2f}")

    trade_count_today = int(state.get("trade_count_today", 0))
    if trade_count_today >= max_daily_trades:
        return RiskDecision(False, f"Max daily trades reached: {trade_count_today}/{max_daily_trades}")

    last_trade_time = state.get("last_trade_time")
    if last_trade_time:
        try:
            last_dt = datetime.fromisoformat(last_trade_time)
            next_allowed = last_dt + timedelta(minutes=cooldown_minutes)
            if datetime.utcnow() < next_allowed:
                mins_left = int((next_allowed - datetime.utcnow()).total_seconds() // 60) + 1
                return RiskDecision(False, f"Cooldown active: wait about {mins_left} minute(s)")
        except Exception:
            pass

    return RiskDecision(True, "Risk checks passed")


def record_trade():
    state = load_state()
    state = reset_daily_if_needed(state)
    state["trade_count_today"] = int(state.get("trade_count_today", 0)) + 1
    state["last_trade_time"] = datetime.utcnow().isoformat()
    save_state(state)


def get_state():
    state = load_state()
    state = reset_daily_if_needed(state)
    save_state(state)
    return state