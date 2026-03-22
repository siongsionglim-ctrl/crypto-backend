from __future__ import annotations

import math
import ccxt


def build_exchange(exchange_name, api_key, secret, passphrase=None, testnet=True, market_type="future"):
    name = exchange_name.lower()
    default_type = "future" if market_type == "future" else "spot"

    common = {
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": default_type},
    }

    if name == "binance":
        ex = ccxt.binance(common)
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    if name == "bybit":
        ex = ccxt.bybit(common)
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    if name == "okx":
        common["password"] = passphrase or ""
        ex = ccxt.okx(common)
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    raise ValueError(f"Unsupported exchange: {exchange_name}")


def to_market_symbol(exchange_name: str, symbol: str) -> str:
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        quote = "USDT"
        return f"{base}/{quote}"
    if symbol.endswith("USDC"):
        base = symbol[:-4]
        quote = "USDC"
        return f"{base}/{quote}"
    return symbol


def _balance_usdt(ex) -> float:
    balance = ex.fetch_balance()
    for bucket in ("free", "total"):
        data = balance.get(bucket)
        if isinstance(data, dict):
            value = data.get("USDT")
            if value is not None:
                return float(value)
    usdt = balance.get("USDT")
    if isinstance(usdt, dict):
        return float(usdt.get("free") or usdt.get("total") or 0.0)
    return 0.0


def _resolve_market(ex, market_symbol: str):
    ex.load_markets()
    market = ex.market(market_symbol)
    return market


def _round_amount(ex, market_symbol: str, amount: float) -> float:
    rounded = float(ex.amount_to_precision(market_symbol, amount))
    if rounded < 0:
        rounded = 0.0
    return rounded


def _ensure_minimums(ex, market_symbol: str, amount: float, entry_price: float | None) -> float:
    market = _resolve_market(ex, market_symbol)
    min_amount = (((market.get("limits") or {}).get("amount") or {}).get("min")) or 0.0
    min_cost = (((market.get("limits") or {}).get("cost") or {}).get("min")) or 0.0

    target = max(float(amount), float(min_amount or 0.0))
    if entry_price and min_cost:
        target = max(target, float(min_cost) / float(entry_price))

    return _round_amount(ex, market_symbol, target)


def _compute_dynamic_amount(ex, market_symbol: str, entry_price: float | None, stop_loss: float | None, risk_per_trade_pct: float, leverage: int) -> float:
    balance_usdt = _balance_usdt(ex)
    if balance_usdt <= 0:
        raise ValueError("Unable to determine available USDT balance.")

    entry = float(entry_price or 0.0)
    stop = float(stop_loss or 0.0)
    risk_pct = max(0.1, float(risk_per_trade_pct)) / 100.0
    risk_amount_usdt = balance_usdt * risk_pct

    stop_distance = abs(entry - stop)
    if entry <= 0 or stop_distance <= 0:
        # fallback: size by notional share if signal misses proper SL/entry
        raw_amount = (balance_usdt * risk_pct * max(1, leverage)) / max(entry, 1.0)
    else:
        raw_amount = risk_amount_usdt / stop_distance

    # keep margin requirement below available balance
    if entry > 0 and leverage > 0:
        max_affordable_amount = (balance_usdt * 0.95 * leverage) / entry
        raw_amount = min(raw_amount, max_affordable_amount)

    amount = _ensure_minimums(ex, market_symbol, raw_amount, entry if entry > 0 else None)
    if amount <= 0:
        raise ValueError("Order amount rounded to zero. Increase risk % or account balance.")
    return amount


def place_market_order(
    exchange_name,
    api_key,
    secret,
    symbol,
    side,
    amount=None,
    passphrase=None,
    testnet=True,
    market_type="future",
    leverage=3,
    risk_per_trade_pct: float | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
):
    ex = build_exchange(
        exchange_name=exchange_name,
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        testnet=testnet,
        market_type=market_type,
    )

    market_symbol = to_market_symbol(exchange_name, symbol)

    if market_type == "future" and hasattr(ex, "set_leverage"):
        try:
            ex.set_leverage(int(leverage), market_symbol)
        except Exception:
            pass

    final_amount = float(amount or 0.0)
    if risk_per_trade_pct is not None and risk_per_trade_pct > 0:
        final_amount = _compute_dynamic_amount(
            ex,
            market_symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_per_trade_pct=risk_per_trade_pct,
            leverage=leverage,
        )
    else:
        final_amount = _ensure_minimums(ex, market_symbol, final_amount, entry_price)
        if final_amount <= 0:
            raise ValueError("Order amount rounded to zero. Increase amount.")

    params = {}
    if market_type == "future" and exchange_name.lower() in {"binance", "bybit", "okx"}:
        params.update({"reduceOnly": False})

    order = ex.create_market_order(
        symbol=market_symbol,
        side=side.lower(),
        amount=final_amount,
        params=params,
    )
    return {
        "requested_symbol": symbol,
        "market_symbol": market_symbol,
        "amount": final_amount,
        "risk_per_trade_pct": risk_per_trade_pct,
        "order": order,
    }
