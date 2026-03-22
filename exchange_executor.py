import ccxt


def build_exchange(exchange_name, api_key, secret, passphrase=None, testnet=True):
    name = exchange_name.lower()

    if name == "binance":
        ex = ccxt.binance({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    if name == "bybit":
        ex = ccxt.bybit({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if testnet:
            ex.set_sandbox_mode(True)
        return ex

    if name == "okx":
        ex = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase or "",
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })

        # OKX demo / sandbox
        if testnet:
            ex.set_sandbox_mode(True)

        return ex

    raise ValueError(f"Unsupported exchange: {exchange_name}")


def to_market_symbol(exchange_name: str, symbol: str) -> str:
    name = exchange_name.lower()

    # BTCUSDT -> BTC/USDT
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        quote = "USDT"
        return f"{base}/{quote}"

    if symbol.endswith("USDC"):
        base = symbol[:-4]
        quote = "USDC"
        return f"{base}/{quote}"

    return symbol


def place_market_order(
    exchange_name,
    api_key,
    secret,
    symbol,
    side,
    amount,
    passphrase=None,
    testnet=True,
):
    ex = build_exchange(
        exchange_name=exchange_name,
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        testnet=testnet,
    )

    market_symbol = to_market_symbol(exchange_name, symbol)

    order = ex.create_market_order(
        symbol=market_symbol,
        side=side.lower(),
        amount=amount,
    )
    return order