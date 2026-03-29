from pydantic import BaseModel, Field


class SignalRequest(BaseModel):
    symbol: str
    exchange: str = "binance"
    timeframe: str = "15m"
    market_type: str = "future"
    testnet: bool = True


class TradeRequest(BaseModel):
    exchange: str
    symbol: str
    api_key: str
    secret: str
    passphrase: str | None = None
    auto_trade: bool = False
    side: str | None = None
    amount: float = 0.01
    testnet: bool = True
    market_type: str = "future"
    leverage: int = 10
    auto_leverage: bool = True
    risk_per_trade_pct: float = 1.0
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None


class BotConfigRequest(BaseModel):
    exchange: str
    symbol: str
    api_key: str
    secret: str
    passphrase: str | None = None
    auto_trade: bool = False
    amount: float = 0.001
    testnet: bool = True

    market_type: str = "future"
    timeframe: str = "15m"
    higher_timeframe: str = "4h"
    leverage: int = 3
    auto_leverage: bool = True
    risk_per_trade_pct: float = 1.0
    min_available_balance_usdt: float = 5.0
    balance_cache_ttl_seconds: int = 25
    loop_interval_sec: int = 60

    max_daily_trades: int = 50
    min_confidence_pct: float = 80.0
    min_rr_ratio: float = 1.5
    cooldown_minutes: int = 30
    symbol_cooldown_minutes: int = 45
    allowed_sides: list[str] = ["BUY", "SELL"]
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 1
    max_consecutive_losses: int = 3
    max_stop_loss_pct: float = 5.0
    max_sl_pct: float = 5.0

    hunter_enabled: bool = False
    scan_exchange: str = "binance"
    scan_timeframe: str = "15m"
    scan_market_type: str = "future"
    scan_symbols: list[str] = [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "BNBUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "DOTUSDT",
        "SUIUSDT",
        "TRXUSDT",
        "LTCUSDT",
        "BCHUSDT",
        "APTUSDT",
    ]
    scan_limit: int = 12
    scan_cache_ttl_seconds: int = 45

    auto_scan_enabled: bool = True
    auto_scan_limit: int = 20
    auto_scan_quote_asset: str = "USDT"
    auto_scan_min_quote_volume: float = 10000000.0
    fallback_symbol: str = "BTCUSDT"

    hunter_strong_threshold: float = 72.0
    hunter_medium_threshold: float = 60.0
    hunter_mode_preset: str = "balanced"
    hunter_min_volume_ratio: float = 1.1
    hunter_min_rr: float = 1.4

    hunter_version: str = "v4_futures"
    hunter_mode: str = "balanced"
    hunter_htf_timeframe: str = "1h"
    hunter_enable_htf_confirm: bool = True
    hunter_enable_regime_filter: bool = True
    hunter_wait_pullback_enabled: bool = True
    hunter_overextension_penalty: float = 15.0
    hunter_momentum_trigger_pct: float = 65.0
    hunter_momentum_volume_ratio: float = 1.1


class ScanRequest(BaseModel):
    symbols: list[str] | None = None
    exchange: str = "binance"
    timeframe: str = "15m"
    market_type: str = "future"
    testnet: bool = True
    min_confidence_pct: float = 75.0
    min_rr_ratio: float = 1.2
    limit: int = Field(default=12, ge=1, le=100)
    force_refresh: bool = False
