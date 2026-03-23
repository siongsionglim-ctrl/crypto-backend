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
    symbols = config.get("scan_symbols") or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "SUIUSDT"]
    if scan_result is None:
        scan_result = scan_symbols(
            symbols=symbols,
            min_confidence_pct=float(config.get("min_confidence_pct", 60.0)),
            min_rr_ratio=float(config.get("min_rr_ratio", 1.2)),
            limit=int(config.get("scan_limit", 12)),
            exchange=config.get("exchange", "binance"),
            timeframe=config.get("scan_timeframe") or config.get("timeframe", "1m"),
            market_type=config.get("scan_market_type") or config.get("market_type", "future"),
            testnet=bool(config.get("testnet", False)),
            websocket_enabled=bool(config.get("websocket_enabled", True)),
        )

    top = scan_result.get("top", [])
    if not top:
        return {"ok": True, "mode": "hunter_signal_only", "scan_result": scan_result, "reason": "No qualified opportunities found"}

    best = top[0]
    action = best.get("action")
    side = normalize_side(action)
    if not side:
        return {"ok": True, "mode": "hunter_signal_only", "best_signal": best, "scan_result": scan_result, "reason": "Top setup is not executable"}

    risk = evaluate_risk(
        signal=best,
        max_daily_trades=int(config.get("max_daily_trades", 3)),
        min_confidence_pct=float(config.get("min_confidence_pct", 60.0)),
        min_rr_ratio=float(config.get("min_rr_ratio", 1.2)),
        cooldown_minutes=int(config.get("cooldown_minutes", 15)),
        allowed_sides=tuple(config.get("allowed_sides", ["BUY", "SELL"])),
    )
    if not risk.allowed:
        return {"ok": True, "mode": "hunter_signal_only", "best_signal": best, "scan_result": scan_result, "reason": risk.reason}
    if not config.get("auto_trade", False):
        return {"ok": True, "mode": "hunter_signal_only", "best_signal": best, "scan_result": scan_result, "reason": "Auto trade disabled"}

    order = place_market_order(
        exchange_name=config["exchange"],
        api_key=config["api_key"],
        secret=config["secret"],
        passphrase=config.get("passphrase"),
        symbol=best["symbol"],
        side=side,
        amount=float(config.get("amount", 0.001)),
        testnet=bool(config.get("testnet", False)),
        market_type=config.get("market_type", "future"),
        leverage=int(config.get("leverage", 3)),
        risk_per_trade_pct=float(config.get("risk_per_trade_pct", 1.0)),
        entry_price=best.get("entry") or best.get("price"),
        stop_loss=best.get("sl"),
    )
    register_open_position(best["symbol"], side.upper(), float(order.get("amount") or config.get("amount", 0.001)), best.get("entry") or best.get("price"))
    record_trade(best)
    return {"ok": True, "mode": "hunter_auto_trade", "best_signal": best, "scan_result": scan_result, "order": order, "reason": "Auto Hunter trade executed"}
