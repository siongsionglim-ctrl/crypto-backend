from pydantic import BaseModel, Field


class SignalRequest(BaseModel):
    symbol: str
    exchange: str = "binance"
    timeframe: str = "1h"
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
    amount: float = 0.001
    testnet: bool = True
    market_type: str = "future"
    leverage: int = 3
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
    timeframe: str = "1h"
    higher_timeframe: str = "4h"
    leverage: int = 3
    auto_leverage: bool = True
    risk_per_trade_pct: float = 1.0

    max_daily_trades: int = 3
    min_confidence_pct: float = 70.0
    min_rr_ratio: float = 1.5
    cooldown_minutes: int = 15
    symbol_cooldown_minutes: int = 15
    allowed_sides: list[str] = ["BUY", "SELL"]
    max_daily_loss_pct: float = 5.0
    max_open_positions: int = 1
    max_consecutive_losses: int = 3
    max_stop_loss_pct: float = 5.0
    min_available_balance_usdt: float = 5.0
    balance_cache_ttl_seconds: int = 8

    hunter_enabled: bool = False
    scan_exchange: str = "binance"
    scan_timeframe: str = "1h"
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


class ScanRequest(BaseModel):
    symbols: list[str] | None = None
    exchange: str = "binance"
    timeframe: str = "1h"
    market_type: str = "future"
    testnet: bool = True
    min_confidence_pct: float = 55.0
    min_rr_ratio: float = 1.0
    limit: int = Field(default=12, ge=1, le=100)
    force_refresh: bool = False
