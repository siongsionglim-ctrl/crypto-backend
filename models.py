from pydantic import BaseModel


class SignalRequest(BaseModel):
    symbol: str


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


class BotConfigRequest(BaseModel):
    exchange: str
    symbol: str
    api_key: str
    secret: str
    passphrase: str | None = None
    auto_trade: bool = False
    amount: float = 0.001
    testnet: bool = True

    max_daily_trades: int = 3
    min_confidence_pct: float = 70.0
    min_rr_ratio: float = 1.5
    cooldown_minutes: int = 15
    allowed_sides: list[str] = ["BUY", "SELL"]