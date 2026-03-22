from __future__ import annotations

from engine.trading_engine import generate_signal
from exchange_executor import execute_trade_bundle
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



def run_auto_trade(config: dict):
    symbol = config["symbol"]
    signal = generate_signal(
        symbol,
        exchange=config.get("exchange", "binance"),
        timeframe=config.get("timeframe", "1h"),
        market_type=config.get("market_type", "future"),
        testnet=bool(config.get("testnet", True)),
    )

    side = normalize_side(signal.get("action"))
    if not side:
        return {"ok": True, "mode": "signal_only", "signal": signal, "reason": "No executable signal"}

    risk = evaluate_risk(
        signal=signal,
        max_daily_trades=int(config.get("max_daily_trades", 3)),
        min_confidence_pct=float(config.get("min_confidence_pct", 70.0)),
        min_rr_ratio=float(config.get("min_rr_ratio", 1.5)),
        cooldown_minutes=int(config.get("cooldown_minutes", 15)),
        allowed_sides=tuple(config.get("allowed_sides", ["BUY", "SELL"])),
        max_daily_loss_pct=float(config.get("max_daily_loss_pct", 5.0)),
        max_open_positions=int(config.get("max_open_positions", 1)),
        max_consecutive_losses=int(config.get("max_consecutive_losses", 3)),
    )
    if not risk.allowed:
        return {"ok": True, "mode": "signal_only", "signal": signal, "reason": risk.reason}

    if not config.get("auto_trade", False):
        return {"ok": True, "mode": "signal_only", "signal": signal, "reason": "Auto trade disabled"}

    result = execute_trade_bundle(
        exchange_name=config["exchange"],
        api_key=config["api_key"],
        secret=config["secret"],
        passphrase=config.get("passphrase"),
        symbol=symbol,
        side=side,
        amount=float(config.get("amount", 0.001)),
        stop_loss=signal.get("sl"),
        take_profit=signal.get("tp"),
        testnet=bool(config.get("testnet", True)),
        market_type=config.get("market_type", "future"),
        leverage=int(config.get("leverage", 3)),
    )
    register_open_position(symbol=symbol, side=side.upper(), amount=float(config.get("amount", 0.001)), entry=signal.get("entry") or signal.get("price"))
    record_trade(signal)
    return {"ok": True, "mode": "auto_trade", "signal": signal, "order": result, "reason": "Trade executed"}
