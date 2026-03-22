from __future__ import annotations

import math
from typing import Any, Optional
import ccxt


class ExchangeExecutionError(RuntimeError):
    pass



def build_exchange(exchange_name: str, api_key: str, secret: str, passphrase: str | None = None, testnet: bool = True, market_type: str = "future"):
    name = exchange_name.lower().strip()
    market_type = market_type.lower().strip()
    options = {"defaultType": "swap" if market_type == "future" else "spot"}

    if name == "binance":
        ex = ccxt.binance({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "options": options})
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    if name == "bybit":
        ex = ccxt.bybit({"apiKey": api_key, "secret": secret, "enableRateLimit": True, "options": options})
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    if name == "okx":
        ex = ccxt.okx({"apiKey": api_key, "secret": secret, "password": passphrase or "", "enableRateLimit": True, "options": options})
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    raise ExchangeExecutionError(f"Unsupported exchange: {exchange_name}")



def to_market_symbol(exchange_name: str, symbol: str, market_type: str = "future") -> str:
    name = exchange_name.lower().strip()
    market_type = market_type.lower().strip()

    if name in {"binance", "bybit"}:
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT" if market_type == "future" else f"{base}/USDT"
        if symbol.endswith("USDC"):
            base = symbol[:-4]
            return f"{base}/USDC:USDC" if market_type == "future" else f"{base}/USDC"

    if name == "okx":
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT" if market_type == "future" else f"{base}/USDT"

    return symbol



def _round_amount(ex, market_symbol: str, amount: float) -> float:
    try:
        ex.load_markets()
        precision = ex.markets[market_symbol].get("precision", {}).get("amount")
        if precision is None:
            return amount
        factor = 10 ** int(precision)
        return math.floor(amount * factor) / factor
    except Exception:
        return amount



def _set_leverage_if_supported(ex, market_symbol: str, leverage: int):
    try:
        ex.set_leverage(leverage, market_symbol)
    except Exception:
        pass



def _stop_side(side: str) -> str:
    return "sell" if side.lower() == "buy" else "buy"



def place_market_order(
    exchange_name: str,
    api_key: str,
    secret: str,
    symbol: str,
    side: str,
    amount: float,
    passphrase: str | None = None,
    testnet: bool = True,
    market_type: str = "future",
    leverage: int = 3,
    reduce_only: bool = False,
):
    ex = build_exchange(exchange_name, api_key, secret, passphrase=passphrase, testnet=testnet, market_type=market_type)
    market_symbol = to_market_symbol(exchange_name, symbol, market_type=market_type)
    amount = _round_amount(ex, market_symbol, amount)
    if amount <= 0:
        raise ExchangeExecutionError("Order amount rounded to zero. Increase amount.")

    if market_type == "future":
        _set_leverage_if_supported(ex, market_symbol, leverage)

    params: dict[str, Any] = {}
    if reduce_only and market_type == "future":
        params["reduceOnly"] = True

    return ex.create_market_order(symbol=market_symbol, side=side.lower(), amount=amount, params=params)



def place_protective_orders(
    exchange_name: str,
    api_key: str,
    secret: str,
    symbol: str,
    side: str,
    amount: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    passphrase: str | None = None,
    testnet: bool = True,
    market_type: str = "future",
    leverage: int = 3,
):
    if market_type != "future":
        return []

    ex = build_exchange(exchange_name, api_key, secret, passphrase=passphrase, testnet=testnet, market_type=market_type)
    market_symbol = to_market_symbol(exchange_name, symbol, market_type=market_type)
    amount = _round_amount(ex, market_symbol, amount)
    if amount <= 0:
        return []
    _set_leverage_if_supported(ex, market_symbol, leverage)

    exit_side = _stop_side(side)
    created = []
    if stop_loss:
        try:
            created.append(ex.create_order(market_symbol, "stop_market", exit_side, amount, None, {"stopPrice": stop_loss, "reduceOnly": True, "triggerPrice": stop_loss}))
        except Exception as e:
            created.append({"warning": f"stop_loss_failed: {e}"})
    if take_profit:
        try:
            created.append(ex.create_order(market_symbol, "take_profit_market", exit_side, amount, None, {"stopPrice": take_profit, "reduceOnly": True, "triggerPrice": take_profit}))
        except Exception as e:
            created.append({"warning": f"take_profit_failed: {e}"})
    return created



def execute_trade_bundle(
    exchange_name: str,
    api_key: str,
    secret: str,
    symbol: str,
    side: str,
    amount: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    passphrase: str | None = None,
    testnet: bool = True,
    market_type: str = "future",
    leverage: int = 3,
):
    entry = place_market_order(
        exchange_name=exchange_name,
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        symbol=symbol,
        side=side,
        amount=amount,
        testnet=testnet,
        market_type=market_type,
        leverage=leverage,
    )
    protective = place_protective_orders(
        exchange_name=exchange_name,
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        symbol=symbol,
        side=side,
        amount=amount,
        stop_loss=stop_loss,
        take_profit=take_profit,
        testnet=testnet,
        market_type=market_type,
        leverage=leverage,
    )
    return {"entry_order": entry, "protective_orders": protective}
