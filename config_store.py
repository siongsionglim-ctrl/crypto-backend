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
        "auto_trade": True,
        "amount": 0.001,
        "testnet": True,
        "market_type": "future",
        "timeframe": "5m",
        "higher_timeframe": "1h",
        "scan_exchange": "binance",
        "scan_timeframe": "5m",
        "scan_market_type": "future",
        "leverage": 3,
        "auto_leverage": True,
        "risk_per_trade_pct": 1.0,

        # Hunter v2 risk profile
        "max_daily_trades": 5,
        "min_confidence_pct": 48.0,
        "min_rr_ratio": 1.0,
        "cooldown_minutes": 5,
        "symbol_cooldown_minutes": 20,
        "allowed_sides": ["BUY", "SELL"],
        "max_daily_loss_pct": 5.0,
        "max_open_positions": 2,
        "max_consecutive_losses": 3,
        "max_stop_loss_pct": 4.0,
        "max_sl_pct": 4.0,

        # Hunter mode
        "hunter_enabled": True,

        # Scanner settings
        "scan_symbols": [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
            "SUIUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
            "DOTUSDT", "TRXUSDT", "LTCUSDT", "BCHUSDT", "APTUSDT"
        ],
        "scan_limit": 8,
        "scan_cache_ttl_seconds": 45,
        "auto_scan_enabled": True,
        "auto_scan_limit": 20,
        "auto_scan_quote_asset": "USDT",
        "auto_scan_min_quote_volume": 10000000.0,
        "fallback_symbol": "BTCUSDT",

        # Hunter v2 scanner thresholds
        "scanner_min_confidence_pct": 40.0,
        "scanner_min_rr_ratio": 0.7,
        "min_hunter_score": 50.0,

        "range_trading_enabled": True,
        "range_amount_multiplier": 0.7,
        "range_risk_multiplier": 0.7,
        "range_min_hunter_score": 48.0,

        # Hunter V3 config
        "hunter_strong_threshold": 60.0,
        "hunter_medium_threshold": 45.0,
        "hunter_mode_preset": "balanced",
        "hunter_min_volume_ratio": 1.1,
        "hunter_min_rr": 1.4,
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
