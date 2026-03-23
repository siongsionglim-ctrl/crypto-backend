from pydantic import BaseModel, Field


class SignalRequest(BaseModel):
    symbol: str
    exchange: str = "binance"
    timeframe: str = "1m"
    market_type: str = "future"
    testnet: bool = False
    websocket_enabled: bool = True


class TradeRequest(BaseModel):
    exchange: str
    symbol: str
    api_key: str
    secret: str
    passphrase: str | None = None
    auto_trade: bool = False
    side: str | None = None
    amount: float = 0.001
    testnet: bool = False
    market_type: str = "future"


class BotConfigRequest(BaseModel):
    exchange: str
    symbol: str
    api_key: str
    secret: str
    passphrase: str | None = None
    auto_trade: bool = False
    amount: float = 0.001
    testnet: bool = False
    market_type: str = "future"
    timeframe: str = "1m"
    leverage: int = 3
    risk_per_trade_pct: float = 1.0
    websocket_enabled: bool = True
    max_daily_trades: int = 3
    min_confidence_pct: float = 70.0
    min_rr_ratio: float = 1.5
    cooldown_minutes: int = 15
    allowed_sides: list[str] = ["BUY", "SELL"]
    hunter_enabled: bool = False
    scan_symbols: list[str] = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
        "SUIUSDT", "TRXUSDT", "LTCUSDT", "BCHUSDT", "APTUSDT",
    ]
    scan_limit: int = 12
    scan_cache_ttl_seconds: int = 15
    scan_timeframe: str = "1m"
    scan_market_type: str = "future"


class ScanRequest(BaseModel):
    symbols: list[str] | None = None
    min_confidence_pct: float = 55.0
    min_rr_ratio: float = 1.0
    limit: int = Field(default=12, ge=1, le=100)
    force_refresh: bool = False
    exchange: str = "binance"
    timeframe: str = "1m"
    market_type: str = "future"
    testnet: bool = False
    websocket_enabled: bool = True
