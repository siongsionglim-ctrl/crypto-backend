from __future__ import annotations

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


def _resolve_take_profit(signal: dict) -> float | None:
    for key in ("tp", "take_profit", "takeProfit"):
        value = signal.get(key)
        if value is not None:
            try:
                return float(value)
            except Exception:
                return None

    entry = signal.get("entry") or signal.get("price")
    stop = signal.get("sl") or signal.get("stop_loss")
    rr = signal.get("rr_ratio")
    action = str(signal.get("action") or "").upper().strip()

    try:
        entry = float(entry)
        stop = float(stop)
        rr = float(rr)
    except Exception:
        return None

    if rr <= 0:
        return None

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    if action == "BUY":
        return entry + risk * rr
    if action == "SELL":
        return entry - risk * rr
    return None


def run_auto_trade(config: dict):
    symbol = config["symbol"]
    if not symbol or not isinstance(symbol, str):
         return {
            "ok": False,
            "mode": "trade_error",
            "reason": f"Invalid symbol: {symbol}",
         }
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
        max_stop_loss_pct=float(config.get("max_stop_loss_pct", config.get("max_sl_pct", 5.0))),
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

    entry_price = signal.get("entry") or signal.get("price")
    stop_loss = signal.get("sl") or signal.get("stop_loss")
    take_profit = _resolve_take_profit(signal)

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

    register_open_position(
        symbol,
        signal.get("action", side).upper(),
        float(order.get("amount") or amount),
        entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        exit_orders=order.get("exit_orders") or {},
        exit_order_warnings=order.get("exit_order_warnings") or [],
        applied_leverage=order.get("applied_leverage"),
        notional_estimate=order.get("notional_estimate"),
        order_meta={
            "market_symbol": order.get("market_symbol"),
            "requested_leverage": order.get("requested_leverage"),
            "applied_leverage": order.get("applied_leverage"),
        },
    )
    record_trade(signal)

    return {
        "ok": True,
        "mode": "auto_trade",
        "signal": signal,
        "order": order,
        "reason": f"Trade executed with dynamic sizing ({risk_per_trade_pct}% risk) and TP/SL placement",
    }
