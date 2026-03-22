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
        "daily_realized_pnl_pct": 0.0,
        "consecutive_losses": 0,
        "open_positions": {},
        "last_signal_symbol": None,
        "last_signal_action": None,
        "last_run_time": None,
    }



def load_state():
    if not STATE_FILE.exists():
        return _default_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    out = _default_state()
    out.update(data or {})
    return out



def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")



def reset_daily_if_needed(state: dict):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if state.get("last_trade_day") != today:
        state["trade_count_today"] = 0
        state["daily_realized_pnl_pct"] = 0.0
        state["consecutive_losses"] = 0
        state["last_trade_day"] = today
    return state



def get_state():
    state = reset_daily_if_needed(load_state())
    save_state(state)
    return state



def has_open_position(symbol: str | None = None) -> bool:
    state = get_state()
    positions = state.get("open_positions", {}) or {}
    if symbol:
        return symbol in positions
    return bool(positions)



def register_open_position(symbol: str, side: str, amount: float, entry: float | None = None):
    state = get_state()
    state.setdefault("open_positions", {})[symbol] = {
        "side": side,
        "amount": amount,
        "entry": entry,
        "opened_at": datetime.utcnow().isoformat(),
    }
    save_state(state)



def remove_open_position(symbol: str):
    state = get_state()
    state.setdefault("open_positions", {}).pop(symbol, None)
    save_state(state)



def evaluate_risk(
    signal: dict,
    max_daily_trades: int = 3,
    min_confidence_pct: float = 70.0,
    min_rr_ratio: float = 1.5,
    cooldown_minutes: int = 15,
    allowed_sides: tuple[str, ...] = ("BUY", "SELL"),
    max_daily_loss_pct: float = 5.0,
    max_open_positions: int = 1,
    max_consecutive_losses: int = 3,
) -> RiskDecision:
    state = reset_daily_if_needed(load_state())

    action = str(signal.get("action", "HOLD")).upper()
    confidence = float(signal.get("confidence_pct") or 0.0)
    rr_ratio = float(signal.get("rr_ratio") or 0.0)
    symbol = str(signal.get("symbol") or "").upper().strip()
    stop_distance_pct = abs(float(signal.get("stop_distance_pct") or 0.0))

    if action not in allowed_sides:
        return RiskDecision(False, f"Action not tradable: {action}")
    if confidence < min_confidence_pct:
        return RiskDecision(False, f"Confidence too low: {confidence:.1f}% < {min_confidence_pct:.1f}%")
    if rr_ratio < min_rr_ratio:
        return RiskDecision(False, f"RR too low: {rr_ratio:.2f} < {min_rr_ratio:.2f}")
    if stop_distance_pct and stop_distance_pct > 5.0:
        return RiskDecision(False, f"Stop distance too wide: {stop_distance_pct:.2f}%")

    if float(state.get("daily_realized_pnl_pct", 0.0)) <= -abs(max_daily_loss_pct):
        return RiskDecision(False, f"Daily loss limit reached: {state.get('daily_realized_pnl_pct', 0.0):.2f}%")

    if int(state.get("consecutive_losses", 0)) >= max_consecutive_losses:
        return RiskDecision(False, f"Max consecutive losses reached: {state.get('consecutive_losses')}")

    trade_count_today = int(state.get("trade_count_today", 0))
    if trade_count_today >= max_daily_trades:
        return RiskDecision(False, f"Max daily trades reached: {trade_count_today}/{max_daily_trades}")

    open_positions = state.get("open_positions", {}) or {}
    if symbol and symbol in open_positions:
        return RiskDecision(False, f"Open position already exists for {symbol}")
    if len(open_positions) >= max_open_positions:
        return RiskDecision(False, f"Max open positions reached: {len(open_positions)}/{max_open_positions}")

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



def record_trade(signal: dict, pnl_pct: float | None = None):
    state = reset_daily_if_needed(load_state())
    state["trade_count_today"] = int(state.get("trade_count_today", 0)) + 1
    state["last_trade_time"] = datetime.utcnow().isoformat()
    state["last_signal_symbol"] = signal.get("symbol")
    state["last_signal_action"] = signal.get("action")
    if pnl_pct is not None:
        state["daily_realized_pnl_pct"] = float(state.get("daily_realized_pnl_pct", 0.0)) + float(pnl_pct)
        if pnl_pct < 0:
            state["consecutive_losses"] = int(state.get("consecutive_losses", 0)) + 1
        else:
            state["consecutive_losses"] = 0
    save_state(state)
