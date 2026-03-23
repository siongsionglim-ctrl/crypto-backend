from __future__ import annotations

import time
from fastapi import FastAPI, HTTPException

from models import SignalRequest, TradeRequest, BotConfigRequest, ScanRequest
from engine.trading_engine import generate_signal
from engine.scanner_engine import scan_symbols
from exchange_executor import place_market_order
from auto_trade import run_auto_trade
from auto_hunter import run_auto_hunter
from config_store import save_config, load_config, sanitize_config
from risk_manager import get_state
from market_data_ws import ensure_binance_feed, get_ws_status
from exchange_executor import build_exchange_client


app = FastAPI()
_SCAN_CACHE: dict = {"data": None, "created_at": 0.0, "params": None}


def _scan_cache_fresh(ttl_seconds: int = 15) -> bool:
    return _SCAN_CACHE["data"] is not None and (time.time() - _SCAN_CACHE["created_at"] <= ttl_seconds)


def _build_scan_params_from_config(config: dict) -> dict:
    return {
        "symbols": config.get("scan_symbols"),
        "min_confidence_pct": float(config.get("min_confidence_pct", 55.0)),
        "min_rr_ratio": float(config.get("min_rr_ratio", 1.0)),
        "limit": int(config.get("scan_limit", 12)),
        "exchange": config.get("exchange", "binance"),
        "timeframe": config.get("scan_timeframe") or config.get("timeframe", "1m"),
        "market_type": config.get("scan_market_type") or config.get("market_type", "future"),
        "testnet": bool(config.get("testnet", False)),
        "websocket_enabled": bool(config.get("websocket_enabled", True)),
    }


def _warm_ws(params: dict) -> None:
    if params.get("exchange", "binance").lower() != "binance":
        return
    if not params.get("websocket_enabled", True):
        return
    ensure_binance_feed(
        symbols=params.get("symbols") or [],
        timeframe=params.get("timeframe", "1m"),
        market_type=params.get("market_type", "future"),
        testnet=bool(params.get("testnet", False)),
        limit=max(int(params.get("limit", 12)), 300),
    )


def _run_and_cache_scan(**params) -> dict:
    _warm_ws(params)
    result = scan_symbols(**params)
    _SCAN_CACHE["data"] = result
    _SCAN_CACHE["created_at"] = time.time()
    _SCAN_CACHE["params"] = params
    return result


@app.on_event("startup")
def startup_event():
    config = load_config()
    params = _build_scan_params_from_config(config)
    try:
        _warm_ws(params)
    except Exception:
        pass


@app.get("/")
def root():
    return {"status": "AI Trading Backend Running", "transport": "websocket+rest"}


@app.get("/ws/status")
def ws_status():
    return get_ws_status()


@app.post("/signal")
def get_signal(req: SignalRequest):
    return generate_signal(
        req.symbol,
        exchange=req.exchange,
        timeframe=req.timeframe,
        market_type=req.market_type,
        testnet=req.testnet,
        websocket_enabled=req.websocket_enabled,
    )


@app.post("/trade")
def trade(req: TradeRequest):
    try:
        result = place_market_order(
            exchange_name=req.exchange,
            api_key=req.api_key,
            secret=req.secret,
            passphrase=req.passphrase,
            symbol=req.symbol,
            side=req.side or "buy",
            amount=req.amount,
            testnet=req.testnet,
            market_type=req.market_type,
        )
        return {"ok": True, "order": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/config")
def set_bot_config(req: BotConfigRequest):
    data = req.model_dump()
    save_config(data)
    return {"ok": True, "config": sanitize_config(data)}


@app.get("/bot/config")
def get_bot_config():
    config = load_config()
    return {"ok": True, "config": sanitize_config(config)}


@app.post("/bot/run")
def bot_run():
    config = load_config()
    if not config:
        raise HTTPException(status_code=400, detail="Bot config not found")
    try:
        if config.get("hunter_enabled", False):
            ttl = int(config.get("scan_cache_ttl_seconds", 15))
            params = _build_scan_params_from_config(config)
            if _scan_cache_fresh(ttl) and _SCAN_CACHE["params"] == params:
                scan_result = _SCAN_CACHE["data"]
            else:
                scan_result = _run_and_cache_scan(**params)
            return run_auto_hunter(config, scan_result=scan_result)
        return run_auto_trade(config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/bot/state")
def bot_state():
    return {"ok": True, "state": get_state()}


@app.post("/scan")
def scan(req: ScanRequest):
    params = {
        "symbols": req.symbols,
        "min_confidence_pct": req.min_confidence_pct,
        "min_rr_ratio": req.min_rr_ratio,
        "limit": req.limit,
        "exchange": req.exchange,
        "timeframe": req.timeframe,
        "market_type": req.market_type,
        "testnet": req.testnet,
        "websocket_enabled": req.websocket_enabled,
    }
    if not req.force_refresh and _scan_cache_fresh() and _SCAN_CACHE["params"] == params:
        result = _SCAN_CACHE["data"]
    else:
        result = _run_and_cache_scan(**params)
    return result


@app.get("/scan/latest")
def scan_latest():
    if not _SCAN_CACHE["data"]:
        return {"ok": True, "has_cache": False, "result": None}
    return {
        "ok": True,
        "has_cache": True,
        "cached_at": _SCAN_CACHE["created_at"],
        "params": _SCAN_CACHE["params"],
        "result": _SCAN_CACHE["data"],
    }

@app.post("/bot/test-connection")
async def bot_test_connection():
    cfg = load_config()

    exchange = (cfg.get("exchange") or "binance").lower()
    api_key = cfg.get("api_key") or ""
    secret = cfg.get("secret") or ""
    passphrase = cfg.get("passphrase")
    testnet = bool(cfg.get("testnet", False))
    market_type = (cfg.get("market_type") or "future").lower()

    if not api_key or not secret:
        return {"ok": True, "message": "Signal-only mode: no API credentials provided."}

    try:
        ex = build_exchange_client(
            exchange=exchange,
            api_key=api_key,
            secret=secret,
            passphrase=passphrase,
            testnet=testnet,
            market_type=market_type,
        )

        # lightweight private auth check
        if exchange == "binance":
            if market_type == "future":
                balance = ex.fetch_balance(params={"type": "future"})
            else:
                balance = ex.fetch_balance()
        else:
            balance = ex.fetch_balance()

        return {
            "ok": True,
            "message": "Connection test passed",
            "exchange": exchange,
            "market_type": market_type,
            "testnet": testnet,
            "has_balance_payload": balance is not None,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))