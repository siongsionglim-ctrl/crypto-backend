from __future__ import annotations

from engine.scanner_engine import scan_symbols
from exchange_executor import place_market_order
from risk_manager import evaluate_risk, record_trade


def normalize_side(action: str | None):
    if not action:
        return None
    a = action.upper().strip()
    if a == "BUY":
        return "buy"
    if a == "SELL":
        return "sell"
    return None


def run_auto_hunter(config: dict):
    symbols = config.get("scan_symbols") or [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "BNBUSDT",
        "SUIUSDT",
    ]

    scan_result = scan_symbols(
        symbols=symbols,
        min_confidence_pct=float(config.get("min_confidence_pct", 60.0)),
        min_rr_ratio=float(config.get("min_rr_ratio", 1.2)),
        limit=int(config.get("scan_limit", 5)),
    )

    top = scan_result.get("top", [])
    if not top:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "scan_result": scan_result,
            "reason": "No qualified opportunities found",
        }

    best = top[0]
    action = best.get("action")
    side = normalize_side(action)

    if not side:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "scan_result": scan_result,
            "reason": "Top setup is not executable",
        }

    risk = evaluate_risk(
        signal=best,
        max_daily_trades=int(config.get("max_daily_trades", 3)),
        min_confidence_pct=float(config.get("min_confidence_pct", 60.0)),
        min_rr_ratio=float(config.get("min_rr_ratio", 1.2)),
        cooldown_minutes=int(config.get("cooldown_minutes", 15)),
        allowed_sides=tuple(config.get("allowed_sides", ["BUY", "SELL"])),
    )

    if not risk.allowed:
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "scan_result": scan_result,
            "reason": risk.reason,
        }

    if not config.get("auto_trade", False):
        return {
            "ok": True,
            "mode": "hunter_signal_only",
            "best_signal": best,
            "scan_result": scan_result,
            "reason": "Auto trade disabled",
        }

    order = place_market_order(
        exchange_name=config["exchange"],
        api_key=config["api_key"],
        secret=config["secret"],
        passphrase=config.get("passphrase"),
        symbol=best["symbol"],
        side=side,
        amount=float(config.get("amount", 0.001)),
        testnet=bool(config.get("testnet", True)),
    )

    record_trade()

    return {
        "ok": True,
        "mode": "hunter_auto_trade",
        "best_signal": best,
        "scan_result": scan_result,
        "order": order,
        "reason": "Auto Hunter trade executed",
    }