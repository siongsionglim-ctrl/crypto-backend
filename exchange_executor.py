from __future__ import annotations

import time
import ccxt

_BALANCE_CACHE: dict[tuple[str, str, str, str, bool], tuple[float, float]] = {}


def build_exchange(exchange_name, api_key, secret, passphrase=None, testnet=True, market_type="future"):
    name = exchange_name.lower()
    default_type = "future" if market_type == "future" else "spot"

    common = {
        "apiKey": api_key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": default_type, "adjustForTimeDifference": True},
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


def get_available_balance_usdt(
    exchange_name,
    api_key,
    secret,
    passphrase=None,
    testnet=True,
    market_type="future",
    cache_ttl_seconds: int = 8,
) -> float:
    cache_key = (exchange_name.lower(), api_key or "", secret or "", passphrase or "", bool(testnet))
    cached = _BALANCE_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] <= max(0, int(cache_ttl_seconds or 0)):
        return float(cached[1])

    ex = build_exchange(
        exchange_name=exchange_name,
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        testnet=testnet,
        market_type=market_type,
    )
    value = _balance_usdt(ex)
    _BALANCE_CACHE[cache_key] = (now, value)
    return value


def _resolve_market(ex, market_symbol: str):
    ex.load_markets()
    return ex.market(market_symbol)


def _round_amount(ex, market_symbol: str, amount: float) -> float:
    rounded = float(ex.amount_to_precision(market_symbol, amount))
    if rounded < 0:
        rounded = 0.0
    return rounded


def _round_price(ex, market_symbol: str, price: float | None) -> float | None:
    if price is None:
        return None
    return float(ex.price_to_precision(market_symbol, float(price)))


def _min_notional(exchange_name: str, market: dict, market_type: str) -> float:
    min_cost = (((market.get("limits") or {}).get("cost") or {}).get("min")) or 0.0
    if min_cost:
        return float(min_cost)
    if exchange_name.lower() == "binance" and market_type == "future":
        return 100.0
    return 0.0


def _ensure_minimums(ex, exchange_name: str, market_symbol: str, amount: float, entry_price: float | None, market_type: str) -> tuple[float, float]:
    market = _resolve_market(ex, market_symbol)
    min_amount = (((market.get("limits") or {}).get("amount") or {}).get("min")) or 0.0
    min_cost = _min_notional(exchange_name, market, market_type)

    target = max(float(amount), float(min_amount or 0.0))
    if entry_price and min_cost:
        target = max(target, float(min_cost) / float(entry_price))

    rounded = _round_amount(ex, market_symbol, target)
    notional = rounded * float(entry_price or 0.0)
    return rounded, notional


def _compute_dynamic_amount(ex, exchange_name: str, market_symbol: str, entry_price: float | None, stop_loss: float | None, risk_per_trade_pct: float, leverage: int, market_type: str, auto_leverage: bool) -> tuple[float, int, float, float]:
    balance_usdt = _balance_usdt(ex)
    if balance_usdt <= 0:
        raise ValueError("Unable to determine available USDT balance.")

    entry = float(entry_price or 0.0)
    stop = float(stop_loss or 0.0)
    if entry <= 0:
        raise ValueError("Missing entry price for dynamic sizing.")

    market = _resolve_market(ex, market_symbol)
    min_notional = _min_notional(exchange_name, market, market_type)
    max_leverage = max(1, int(leverage or 1))
    applied_leverage = 1 if market_type == "spot" else max_leverage

    risk_pct = max(0.1, float(risk_per_trade_pct)) / 100.0
    risk_amount_usdt = balance_usdt * risk_pct
    stop_distance = abs(entry - stop)

    if stop_distance <= 0:
        raw_amount = (balance_usdt * risk_pct * max(1, applied_leverage)) / entry
    else:
        raw_amount = risk_amount_usdt / stop_distance

    if market_type == "future" and entry > 0 and applied_leverage > 0:
        max_affordable_amount = (balance_usdt * 0.95 * applied_leverage) / entry
        raw_amount = min(raw_amount, max_affordable_amount)

    amount, notional = _ensure_minimums(ex, exchange_name, market_symbol, raw_amount, entry, market_type)

    if market_type == "future" and min_notional and notional < min_notional and auto_leverage:
        for lev in range(max(1, applied_leverage), max_leverage + 1):
            max_affordable_amount = (balance_usdt * 0.95 * lev) / entry
            candidate_amount, candidate_notional = _ensure_minimums(
                ex, exchange_name, market_symbol, min(max_affordable_amount, max(raw_amount, amount)), entry, market_type
            )
            if candidate_notional >= min_notional:
                amount = candidate_amount
                notional = candidate_notional
                applied_leverage = lev
                break

    if market_type == "future" and min_notional and notional < min_notional:
        raise ValueError(
            f"Calculated notional {notional:.2f} USDT is below futures minimum {min_notional:.2f} USDT. "
            f"Increase risk %, leverage cap, or account balance."
        )

    if amount <= 0:
        raise ValueError("Order amount rounded to zero. Increase risk % or account balance.")
    return amount, applied_leverage, notional, balance_usdt


def _close_side_for_entry(side: str) -> str:
    s = str(side or "").lower().strip()
    return "sell" if s == "buy" else "buy"


def _build_trigger_params(exchange_name: str, kind: str, trigger_price: float, close_side: str) -> tuple[str, dict]:
    name = exchange_name.lower()
    trigger_price = float(trigger_price)
    if kind == "tp":
        order_type = "TAKE_PROFIT_MARKET"
    else:
        order_type = "STOP_MARKET"

    params = {
        "stopPrice": trigger_price,
        "triggerPrice": trigger_price,
        "reduceOnly": True,
        "workingType": "MARK_PRICE",
        "timeInForce": "GTC",
    }

    if name == "binance":
        params["priceProtect"] = True
    elif name == "bybit":
        params["triggerBy"] = "MarkPrice"
    elif name == "okx":
        params["reduceOnly"] = True
        params["tdMode"] = "cross"

    params["side"] = close_side
    return order_type, params


def place_protective_orders(
    ex,
    exchange_name: str,
    market_symbol: str,
    entry_side: str,
    amount: float,
    take_profit: float | None = None,
    stop_loss: float | None = None,
):
    close_side = _close_side_for_entry(entry_side)
    placed: dict[str, dict] = {}
    warnings: list[str] = []

    for key, price in (("take_profit", take_profit), ("stop_loss", stop_loss)):
        if price is None:
            continue
        kind = "tp" if key == "take_profit" else "sl"
        try:
            rounded_price = _round_price(ex, market_symbol, float(price))
            order_type, params = _build_trigger_params(exchange_name, kind, rounded_price, close_side)
            order = ex.create_order(
                symbol=market_symbol,
                type=order_type,
                side=close_side,
                amount=float(amount),
                price=None,
                params=params,
            )
            placed[key] = {
                "order": order,
                "trigger_price": rounded_price,
                "close_side": close_side,
                "type": order_type,
            }
        except Exception as e:
            warnings.append(f"{key} order placement failed: {e}")

    return {"orders": placed, "warnings": warnings}


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
    auto_leverage=True,
    risk_per_trade_pct: float | None = None,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
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
    requested_leverage = max(1, int(leverage or 1))
    applied_leverage = 1 if market_type == "spot" else requested_leverage

    final_amount = float(amount or 0.0)
    notional_estimate = final_amount * float(entry_price or 0.0)
    available_balance_usdt = None

    if risk_per_trade_pct is not None and risk_per_trade_pct > 0:
        final_amount, applied_leverage, notional_estimate, available_balance_usdt = _compute_dynamic_amount(
            ex,
            exchange_name,
            market_symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_per_trade_pct=risk_per_trade_pct,
            leverage=requested_leverage,
            market_type=market_type,
            auto_leverage=bool(auto_leverage),
        )
    else:
        final_amount, notional_estimate = _ensure_minimums(ex, exchange_name, market_symbol, final_amount, entry_price, market_type)
        available_balance_usdt = _balance_usdt(ex)
        if final_amount <= 0:
            raise ValueError("Order amount rounded to zero. Increase amount.")

    if market_type == "future" and hasattr(ex, "set_leverage"):
        try:
            ex.set_leverage(int(applied_leverage), market_symbol)
        except Exception:
            pass

    params = {}
    if market_type == "future" and exchange_name.lower() in {"binance", "bybit", "okx"}:
        params.update({"reduceOnly": False})

    order = ex.create_market_order(
        symbol=market_symbol,
        side=side.lower(),
        amount=final_amount,
        params=params,
    )

    exit_orders = {"orders": {}, "warnings": []}
    if market_type == "future" and (take_profit is not None or stop_loss is not None):
        exit_orders = place_protective_orders(
            ex=ex,
            exchange_name=exchange_name,
            market_symbol=market_symbol,
            entry_side=side.lower(),
            amount=final_amount,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )

    return {
        "requested_symbol": symbol,
        "market_symbol": market_symbol,
        "market_type": market_type,
        "amount": final_amount,
        "risk_per_trade_pct": risk_per_trade_pct,
        "requested_leverage": requested_leverage,
        "applied_leverage": applied_leverage,
        "auto_leverage": bool(auto_leverage),
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "notional_estimate": notional_estimate,
        "available_balance_usdt": available_balance_usdt,
        "order": order,
        "exit_orders": exit_orders.get("orders") or {},
        "exit_order_warnings": exit_orders.get("warnings") or [],
    }


def _normalize_symbol(exchange_name: str, market_symbol: str) -> str:
    s = str(market_symbol or "").replace("/", "").replace(":USDT", "").replace(":USDC", "")
    return s.upper()


def fetch_live_positions(
    exchange_name,
    api_key,
    secret,
    passphrase=None,
    testnet=True,
    market_type="future",
    symbols=None,
):
    ex = build_exchange(
        exchange_name=exchange_name,
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        testnet=testnet,
        market_type=market_type,
    )

    ex.load_markets()
    normalized_symbols = {str(s).upper() for s in (symbols or []) if s}
    requested_symbols = [to_market_symbol(exchange_name, s) for s in normalized_symbols]
    normalized = {}

    if market_type == "future":
        positions = []
        if hasattr(ex, "fetch_positions"):
            try:
                positions = ex.fetch_positions(requested_symbols or None) or []
            except Exception:
                positions = ex.fetch_positions() or []
        else:
            raise ValueError(f"{exchange_name} client does not support fetch_positions for futures sync")

        for pos in positions:
            contracts = pos.get("contracts")
            if contracts is None:
                contracts = pos.get("positionAmt") or pos.get("contracts") or 0
            try:
                contracts = float(contracts or 0)
            except Exception:
                contracts = 0.0
            if abs(contracts) <= 0:
                continue

            raw_symbol = pos.get("symbol") or pos.get("info", {}).get("symbol") or ""
            symbol = _normalize_symbol(exchange_name, raw_symbol)
            if normalized_symbols and symbol not in normalized_symbols:
                continue

            side = str(pos.get("side") or "").upper()
            if not side:
                side = "BUY" if contracts > 0 else "SELL"
            entry = pos.get("entryPrice") or pos.get("entry_price") or pos.get("average") or pos.get("markPrice")
            try:
                entry = float(entry) if entry is not None else None
            except Exception:
                entry = None

            normalized[symbol] = {
                "symbol": symbol,
                "market_symbol": raw_symbol,
                "side": "BUY" if side in {"LONG", "BUY"} or contracts > 0 else "SELL",
                "amount": abs(float(contracts)),
                "entry": entry,
                "source": "exchange",
                "market_type": market_type,
            }
    else:
        balance = ex.fetch_balance()
        totals = balance.get("total") if isinstance(balance.get("total"), dict) else balance
        for asset, value in (totals or {}).items():
            if asset in {"USDT", "USDC", "USD"}:
                continue
            try:
                qty = float(value or 0)
            except Exception:
                continue
            if qty <= 0:
                continue
            symbol = f"{str(asset).upper()}USDT"
            if normalized_symbols and symbol not in normalized_symbols:
                continue
            normalized[symbol] = {
                "symbol": symbol,
                "market_symbol": to_market_symbol(exchange_name, symbol),
                "side": "BUY",
                "amount": qty,
                "entry": None,
                "source": "exchange",
                "market_type": market_type,
            }

    return normalized
