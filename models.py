from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


DEFAULT_SCAN_SYMBOLS = [
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


class SignalRequest(BaseModel):
    symbol: str
    exchange: str = "binance"
    timeframe: str = "1h"
    market_type: Literal["spot", "future"] = "future"
    testnet: bool = True


class TradeRequest(BaseModel):
    exchange: str
    symbol: str
    api_key: str
    secret: str
    passphrase: Optional[str] = None
    side: Literal["buy", "sell", "BUY", "SELL"]
    amount: float = Field(default=0.001, gt=0)
    testnet: bool = True
    leverage: int = Field(default=3, ge=1, le=125)
    market_type: Literal["spot", "future"] = "future"
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    reduce_only: bool = False


class ScanRequest(BaseModel):
    symbols: Optional[list[str]] = None
    exchange: str = "binance"
    timeframe: str = "1h"
    market_type: Literal["spot", "future"] = "future"
    testnet: bool = True
    min_confidence_pct: float = 55.0
    min_rr_ratio: float = 1.0
    limit: int = Field(default=10, ge=1, le=50)


class BotConfigRequest(BaseModel):
    exchange: str = "binance"
    symbol: str = "BTCUSDT"
    api_key: str = ""
    secret: str = ""
    passphrase: Optional[str] = None
    auto_trade: bool = False
    amount: float = Field(default=0.001, gt=0)
    testnet: bool = True
    market_type: Literal["spot", "future"] = "future"
    timeframe: str = "1h"
    higher_timeframe: str = "4h"
    leverage: int = Field(default=3, ge=1, le=125)

    max_daily_trades: int = Field(default=3, ge=1)
    min_confidence_pct: float = Field(default=70.0, ge=0, le=100)
    min_rr_ratio: float = Field(default=1.5, ge=0)
    cooldown_minutes: int = Field(default=15, ge=0)
    allowed_sides: list[str] = ["BUY", "SELL"]
    max_daily_loss_pct: float = Field(default=5.0, ge=0)
    max_open_positions: int = Field(default=1, ge=1)
    max_consecutive_losses: int = Field(default=3, ge=1)

    hunter_enabled: bool = False
    scan_symbols: list[str] = DEFAULT_SCAN_SYMBOLS.copy()
    scan_limit: int = Field(default=5, ge=1, le=20)


class StartBotRequest(BaseModel):
    interval_seconds: int = Field(default=20, ge=5, le=3600)


class ExecuteSignalRequest(BaseModel):
    signal: dict
    config: Optional[BotConfigRequest] = None
