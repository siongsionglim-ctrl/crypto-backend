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
        timeframe=config.get("timeframe", "1h"),
        market_type=config.get("market_type", "future"),
        testnet=bool(config.get("testnet", True)),
    )
    action = signal.get("action")
    side = normalize_side(action)

    if not side:
        return {
            "ok": True,
            "mode": "signal_only",
            "signal": signal,
            "reason": "No executable signal",
        }

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
        return {
            "ok": True,
            "mode": "signal_only",
            "signal": signal,
            "reason": risk.reason,
        }

    if not auto_trade:
        return {
            "ok": True,
            "mode": "signal_only",
            "signal": signal,
            "reason": "Auto trade disabled",
        }

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
        entry_price=signal.get("entry") or signal.get("price"),
        stop_loss=signal.get("sl"),
        take_profit=signal.get("tp"),
        safe_mode=bool(config.get("safe_mode", True)),
    )

    register_open_position(
        symbol=symbol,
        side=side,
        amount=float(order.get("amount") or amount),
        entry=signal.get("entry") or signal.get("price"),
        stop_loss=signal.get("sl"),
        take_profit=signal.get("tp"),
        protected=bool(((order.get("protection") or {}).get("placed"))),
        protection_mode=(order.get("protection") or {}).get("mode"),
    )
    record_trade(signal)

    reason = f"Trade executed with dynamic sizing ({risk_per_trade_pct}% risk)"
    protection = order.get("protection") or {}
    if protection.get("placed"):
        reason += " and exchange SL/TP protection placed"
    elif protection.get("warning"):
        reason += f"; warning: {protection.get('warning')}"

    return {
        "ok": True,
        "mode": "auto_trade",
        "signal": signal,
        "order": order,
        "reason": reason,
    }
