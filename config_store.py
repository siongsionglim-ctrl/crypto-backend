from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_FILE = Path("user_config.json")

_SECRET_KEYS = {"api_key", "secret", "passphrase"}


def _default_config() -> dict[str, Any]:
    return {
        "exchange": "binance",
        "symbol": "BTCUSDT",
        "api_key": "",
        "secret": "",
        "passphrase": "",
        "auto_trade": False,
        "amount": 0.001,
        "testnet": True,
        "market_type": "future",
        "timeframe": "15m",
        "higher_timeframe": "4h",
        "scan_exchange": "binance",
        "scan_timeframe": "15m",
        "scan_market_type": "future",
        "leverage": 3,
        "auto_leverage": True,
        "risk_per_trade_pct": 1.0,
        "max_daily_trades": 2,
        "min_confidence_pct": 75.0,
        "min_rr_ratio": 1.2,
        "cooldown_minutes": 30,
        "symbol_cooldown_minutes": 45,
        "allowed_sides": ["BUY", "SELL"],
        "max_daily_loss_pct": 3.0,
        "max_open_positions": 1,
        "max_consecutive_losses": 2,
        "max_stop_loss_pct": 3.0,
        "max_sl_pct": 3.0,
        "hunter_enabled": False,
        "scan_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "SUIUSDT"],
        "scan_limit": 5,
        "scan_cache_ttl_seconds": 45,
        "auto_scan_enabled": True,
        "auto_scan_limit": 20,
        "auto_scan_quote_asset": "USDT",
        "auto_scan_min_quote_volume": 10000000.0,
        "fallback_symbol": "BTCUSDT",
    }


def save_config(data: dict) -> dict:
    merged = _default_config()
    merged.update(data or {})
    CONFIG_FILE.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return _default_config()
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _default_config()

    merged = _default_config()
    merged.update(raw or {})
    return merged


def sanitize_config(config: dict) -> dict:
    clean = dict(config or {})
    for key in _SECRET_KEYS:
        if clean.get(key):
            value = str(clean[key])
            clean[key] = f"{value[:4]}***{value[-4:]}" if len(value) > 8 else "***"
    return clean
