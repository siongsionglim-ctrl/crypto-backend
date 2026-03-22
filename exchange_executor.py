from __future__ import annotations

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


def _opposite_side(side: str) -> str:
    return "sell" if side.lower() == "buy" else "buy"


def _cancel_existing_protection_orders(ex, market_symbol: str) -> int:
    cancelled = 0
    try:
        for order in ex.fetch_open_orders(symbol=market_symbol):
            params = order.get("info") if isinstance(order.get("info"), dict) else {}
            is_reduce_only = bool(params.get("reduceOnly")) or bool(order.get("reduceOnly"))
            if is_reduce_only or order.get("type") in {"stop_market", "take_profit_market", "STOP_MARKET", "TAKE_PROFIT_MARKET"}:
                ex.cancel_order(order["id"], market_symbol)
                cancelled += 1
    except Exception:
        pass
    return cancelled


def _place_binance_futures_protection(ex, market_symbol: str, side: str, amount: float, stop_loss: float | None, take_profit: float | None) -> dict:
    exit_side = _opposite_side(side)
    protection: dict = {
        "placed": False,
        "mode": "binance_futures_reduce_only",
        "stop_loss_order": None,
        "take_profit_order": None,
        "warning": None,
    }
    if stop_loss is None and take_profit is None:
        protection["warning"] = "Missing SL/TP prices; no exchange protection orders placed."
        return protection

    cancelled = _cancel_existing_protection_orders(ex, market_symbol)
    if cancelled:
        protection["cancelled_existing_orders"] = cancelled

    try:
        if stop_loss is not None:
            protection["stop_loss_order"] = ex.create_order(
                market_symbol,
                "STOP_MARKET",
                exit_side,
                amount,
                None,
                {
                    "stopPrice": float(stop_loss),
                    "reduceOnly": True,
                    "workingType": "MARK_PRICE",
                },
            )
        if take_profit is not None:
            protection["take_profit_order"] = ex.create_order(
                market_symbol,
                "TAKE_PROFIT_MARKET",
                exit_side,
                amount,
                None,
                {
                    "stopPrice": float(take_profit),
                    "reduceOnly": True,
                    "workingType": "MARK_PRICE",
                },
            )
        protection["placed"] = bool(protection.get("stop_loss_order") or protection.get("take_profit_order"))
        return protection
    except Exception as exc:
        protection["warning"] = f"Failed to place exchange protection orders: {exc}"
        return protection


def _place_protection_orders(ex, exchange_name: str, market_type: str, market_symbol: str, side: str, amount: float, stop_loss: float | None, take_profit: float | None) -> dict:
    if market_type != "future":
        return {
            "placed": False,
            "mode": "unsupported_for_spot",
            "warning": "Exchange-level safe exits are only enabled for futures in this build.",
        }
    if exchange_name.lower() == "binance":
        return _place_binance_futures_protection(ex, market_symbol, side, amount, stop_loss, take_profit)
    return {
        "placed": False,
        "mode": "unsupported_exchange",
        "warning": f"Safe exit orders are not yet implemented for {exchange_name} in this build.",
    }


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
    safe_mode: bool = True,
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

    entry_price = float(entry_price) if entry_price is not None else None
    stop_loss = float(stop_loss) if stop_loss is not None else None
    take_profit = float(take_profit) if take_profit is not None else None

    final_amount = float(amount or 0.0)
    notional_estimate = final_amount * float(entry_price or 0.0)

    if risk_per_trade_pct is not None and risk_per_trade_pct > 0:
        final_amount, applied_leverage, notional_estimate, _ = _compute_dynamic_amount(
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

    protection = None
    if safe_mode:
        protection = _place_protection_orders(
            ex,
            exchange_name=exchange_name,
            market_type=market_type,
            market_symbol=market_symbol,
            side=side,
            amount=final_amount,
            stop_loss=_round_price(ex, market_symbol, stop_loss),
            take_profit=_round_price(ex, market_symbol, take_profit),
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
        "safe_mode": bool(safe_mode),
        "protection": protection,
        "order": order,
    }
