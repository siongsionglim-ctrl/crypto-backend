from engine.trading_engine import generate_signal
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



def run_auto_trade(config: dict):
    symbol = config["symbol"]
    auto_trade = config.get("auto_trade", False)
    amount = float(config.get("amount", 0.001))
    risk_per_trade_pct = float(config.get("risk_per_trade_pct", 1.0))

    signal = generate_signal(
        symbol,
        exchange=config.get("exchange", "binance"),
        timeframe=config.get("timeframe", "1m"),
        market_type=config.get("market_type", "future"),
        testnet=bool(config.get("testnet", False)),
        websocket_enabled=bool(config.get("websocket_enabled", True)),
    )
    action = signal.get("action")
    side = normalize_side(action)
    if not side:
        return {"ok": True, "mode": "signal_only", "signal": signal, "reason": "No executable signal"}

    risk = evaluate_risk(
        signal=signal,
        max_daily_trades=int(config.get("max_daily_trades", 3)),
        min_confidence_pct=float(config.get("min_confidence_pct", 70.0)),
        min_rr_ratio=float(config.get("min_rr_ratio", 1.5)),
        cooldown_minutes=int(config.get("cooldown_minutes", 15)),
        allowed_sides=tuple(config.get("allowed_sides", ["BUY", "SELL"])),
    )
    if not risk.allowed:
        return {"ok": True, "mode": "signal_only", "signal": signal, "reason": risk.reason}
    if not auto_trade:
        return {"ok": True, "mode": "signal_only", "signal": signal, "reason": "Auto trade disabled"}

    order = place_market_order(
        exchange_name=config["exchange"],
        api_key=config["api_key"],
        secret=config["secret"],
        passphrase=config.get("passphrase"),
        symbol=symbol,
        side=side,
        amount=amount,
        testnet=bool(config.get("testnet", False)),
        market_type=config.get("market_type", "future"),
        leverage=int(config.get("leverage", 3)),
        risk_per_trade_pct=risk_per_trade_pct,
        entry_price=signal.get("entry") or signal.get("price"),
        stop_loss=signal.get("sl"),
    )
    register_open_position(symbol, side.upper(), float(order.get("amount") or amount), signal.get("entry") or signal.get("price"))
    record_trade(signal)
    return {"ok": True, "mode": "auto_trade", "signal": signal, "order": order, "reason": f"Trade executed with dynamic sizing ({risk_per_trade_pct}% risk)"}
