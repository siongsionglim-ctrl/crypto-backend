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


def run_auto_hunter(config: dict, scan_result: dict | None = None):
    symbols = config.get("scan_symbols") or [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "SUIUSDT",
    ]

    if scan_result is None:
        scan_result = scan_symbols(
            symbols=symbols,
            min_confidence_pct=float(config.get("min_confidence_pct", 60.0)),
            min_rr_ratio=float(config.get("min_rr_ratio", 1.2)),
            limit=int(config.get("scan_limit", 12)),
            exchange=config.get("scan_exchange") or config.get("exchange", "binance"),
            timeframe=config.get("scan_timeframe") or config.get("timeframe", "1h"),
            market_type=config.get("scan_market_type") or config.get("market_type", "future"),
            testnet=bool(config.get("testnet", True)),
        )

    top = scan_result.get("top", [])
    if not top:
        fallback_symbol = config.get("fallback_symbol")
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "scan_result": scan_result,
            "fallback_symbol": fallback_symbol,
            "reason": "No qualified opportunities found in scan",
        }

    best = top[0]
    symbol = best.get("symbol")
    if not symbol or not isinstance(symbol, str):
        return {
            "ok": False,
            "mode": "hunter_error",
            "scan_result": scan_result,
            "reason": f"Invalid symbol from scanner: {symbol}",
        }

    action = best.get("action")
    side = normalize_side(action)

    if not side:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "scan_result": scan_result,
            "reason": "Top setup is not executable (no valid BUY/SELL action)",
        }

    risk = evaluate_risk(
        signal=best,
        max_daily_trades=int(config.get("max_daily_trades", 3)),
        min_confidence_pct=float(config.get("min_confidence_pct", 60.0)),
        min_rr_ratio=float(config.get("min_rr_ratio", 1.2)),
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
            "mode": "hunter_signal_only",
            "best_signal": best,
            "scan_result": scan_result,
            "reason": risk.reason or "Risk check failed",
        }

    if not config.get("auto_trade", False):
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "scan_result": scan_result,
            "reason": "Auto trade disabled in config",
        }

    entry_price = best.get("entry") or best.get("price")
    stop_loss = best.get("sl") or best.get("stop_loss")
    take_profit = best.get("tp") or best.get("take_profit")

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
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
    except Exception as e:
        return {
            "ok": False,
            "mode": "hunter_error",
            "best_signal": best,
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
        },
    )
    record_trade(best)

    return {
        "ok": True,
        "mode": "hunter_auto_trade",
        "best_signal": best,
        "scan_result": scan_result,
        "order": order,
        "reason": "Auto Hunter trade executed successfully with TP/SL",
    }