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
        "notional_estimate": notional_estimate,
        "order": order,
    }


def _normalize_symbol(exchange_name: str, market_symbol: str) -> str:
    s = str(market_symbol or '').replace('/', '').replace(':USDT', '').replace(':USDC', '')
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
    requested_symbols = [to_market_symbol(exchange_name, s) for s in (symbols or []) if s]
    normalized = {}

    if market_type == "future":
        positions = []
        if hasattr(ex, 'fetch_positions'):
            try:
                positions = ex.fetch_positions(requested_symbols or None) or []
            except Exception:
                positions = ex.fetch_positions() or []
        else:
            raise ValueError(f"{exchange_name} client does not support fetch_positions for futures sync")

        for pos in positions:
            contracts = pos.get('contracts')
            if contracts is None:
                contracts = pos.get('positionAmt') or pos.get('contracts') or 0
            try:
                contracts = float(contracts or 0)
            except Exception:
                contracts = 0.0
            if abs(contracts) <= 0:
                continue

            raw_symbol = pos.get('symbol') or pos.get('info', {}).get('symbol') or ''
            symbol = _normalize_symbol(exchange_name, raw_symbol)
            side = str(pos.get('side') or '').upper()
            if not side:
                side = 'BUY' if contracts > 0 else 'SELL'
            entry = pos.get('entryPrice') or pos.get('entry_price') or pos.get('average') or pos.get('markPrice')
            try:
                entry = float(entry) if entry is not None else None
            except Exception:
                entry = None

            normalized[symbol] = {
                'symbol': symbol,
                'market_symbol': raw_symbol,
                'side': 'BUY' if side in {'LONG', 'BUY'} or contracts > 0 else 'SELL',
                'amount': abs(float(contracts)),
                'entry': entry,
                'source': 'exchange',
                'market_type': market_type,
            }
    else:
        balance = ex.fetch_balance()
        totals = balance.get('total') if isinstance(balance.get('total'), dict) else balance
        for asset, value in (totals or {}).items():
            if asset in {'USDT', 'USDC', 'USD'}:
                continue
            try:
                qty = float(value or 0)
            except Exception:
                continue
            if qty <= 0:
                continue
            symbol = f"{str(asset).upper()}USDT"
            if symbols and symbol not in {s.upper() for s in symbols}:
                continue
            normalized[symbol] = {
                'symbol': symbol,
                'market_symbol': to_market_symbol(exchange_name, symbol),
                'side': 'BUY',
                'amount': qty,
                'entry': None,
                'source': 'exchange',
                'market_type': market_type,
            }

    return normalized


_TICKER_CACHE: dict[tuple[str, bool, str], tuple[float, list[str]]] = {}

def discover_scan_symbols(
    exchange_name: str,
    market_type: str = "future",
    testnet: bool = False,
    quote_asset: str = "USDT",
    limit: int = 12,
    min_quote_volume: float = 10000000.0,
    cache_ttl_seconds: int = 300,
) -> list[str]:
    """Discover a liquid symbol universe automatically using public market data.

    Returns normalized symbols like BTCUSDT.
    """
    cache_key = (exchange_name.lower(), bool(testnet), market_type)
    now = time.time()
    cached = _TICKER_CACHE.get(cache_key)
    if cached and now - cached[0] <= max(0, int(cache_ttl_seconds or 0)):
        return list(cached[1][: max(1, int(limit or 1))])

    ex = build_exchange(
        exchange_name=exchange_name,
        api_key="",
        secret="",
        passphrase=None,
        testnet=testnet,
        market_type=market_type,
    )

    symbols: list[tuple[str, float]] = []
    quote_asset = str(quote_asset or "USDT").upper()

    try:
        markets = ex.load_markets()
        ticker_map = {}
        try:
            ticker_map = ex.fetch_tickers() or {}
        except Exception:
            ticker_map = {}

        for market_symbol, market in markets.items():
            try:
                if not market.get("active", True):
                    continue
                if str(market.get("quote") or "").upper() != quote_asset:
                    continue
                if market_type == "future" and not (market.get("swap") or market.get("future")):
                    continue
                if market_type != "future" and not market.get("spot"):
                    continue
                norm = _normalize_symbol(exchange_name, market_symbol)
                if not norm.endswith(quote_asset):
                    continue
                ticker = ticker_map.get(market_symbol) or ticker_map.get(norm) or {}
                quote_volume = (
                    ticker.get("quoteVolume")
                    or ticker.get("baseVolume") and ticker.get("last") and float(ticker.get("baseVolume") or 0) * float(ticker.get("last") or 0)
                    or market.get("info", {}).get("quoteVolume")
                    or market.get("info", {}).get("turnover24h")
                    or 0
                )
                try:
                    qv = float(quote_volume or 0)
                except Exception:
                    qv = 0.0
                if qv < float(min_quote_volume or 0):
                    continue
                symbols.append((norm, qv))
            except Exception:
                continue
    except Exception:
        symbols = []

    if not symbols:
        fallback = [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
            "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "SUIUSDT", "TRXUSDT",
        ]
        _TICKER_CACHE[cache_key] = (now, fallback)
        return fallback[: max(1, int(limit or 1))]

    symbols.sort(key=lambda item: item[1], reverse=True)
    unique = []
    seen = set()
    for sym, _ in symbols:
        if sym in seen:
            continue
        seen.add(sym)
        unique.append(sym)
    _TICKER_CACHE[cache_key] = (now, unique)
    return unique[: max(1, int(limit or 1))]
