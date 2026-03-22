from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException

from models import SignalRequest, TradeRequest, BotConfigRequest, ScanRequest
from engine.trading_engine import generate_signal
from engine.scanner_engine import scan_symbols
from exchange_executor import place_market_order, build_exchange
from auto_trade import run_auto_trade
from auto_hunter import run_auto_hunter
from config_store import save_config, load_config, sanitize_config
from risk_manager import get_state

app = FastAPI()

_SCAN_CACHE: dict = {
    "data": None,
    "created_at": 0.0,
    "params": None,
}

BOT_META_FILE = Path("bot_runtime_meta.json")
_BOT_TASK: asyncio.Task | None = None
_BOT_LAST_HEARTBEAT: float | None = None


def _log(message: str) -> None:
    print(f"[BOT] {message}", flush=True)


def _load_meta() -> dict:
    if not BOT_META_FILE.exists():
        return {
            "running": False,
            "loop_active": False,
            "last_result": None,
            "last_started_at": None,
            "last_stopped_at": None,
            "last_cycle_at": None,
            "last_error": None,
            "bot_cycle_seconds": 5,
        }
    try:
        raw = json.loads(BOT_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    base = {
        "running": False,
        "loop_active": False,
        "last_result": None,
        "last_started_at": None,
        "last_stopped_at": None,
        "last_cycle_at": None,
        "last_error": None,
        "bot_cycle_seconds": 5,
    }
    base.update(raw or {})
    return base


def _save_meta(data: dict) -> dict:
    BOT_META_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _scan_cache_fresh(ttl_seconds: int = 20) -> bool:
    return _SCAN_CACHE["data"] is not None and (time.time() - _SCAN_CACHE["created_at"] <= ttl_seconds)


def _build_scan_params_from_config(config: dict) -> dict:
    return {
        "symbols": config.get("scan_symbols"),
        "min_confidence_pct": float(config.get("min_confidence_pct", 50.0)),
        "min_rr_ratio": float(config.get("min_rr_ratio", 1.2)),
        "limit": int(config.get("scan_limit", 20)),
        "exchange": config.get("scan_exchange") or config.get("exchange", "binance"),
        "timeframe": config.get("scan_timeframe") or config.get("timeframe", "1m"),
        "market_type": config.get("scan_market_type") or config.get("market_type", "future"),
        "testnet": bool(config.get("testnet", True)),
    }


def _run_and_cache_scan(*, symbols=None, min_confidence_pct=50.0, min_rr_ratio=1.2, limit=20, exchange="binance", timeframe="1m", market_type="future", testnet=True) -> dict:
    _log(f"scan exchange={exchange} market={market_type} timeframe={timeframe} limit={limit}")
    result = scan_symbols(
        symbols=symbols,
        min_confidence_pct=min_confidence_pct,
        min_rr_ratio=min_rr_ratio,
        limit=limit,
        exchange=exchange,
        timeframe=timeframe,
        market_type=market_type,
        testnet=testnet,
    )
    _SCAN_CACHE["data"] = result
    _SCAN_CACHE["created_at"] = time.time()
    _SCAN_CACHE["params"] = {
        "symbols": symbols,
        "min_confidence_pct": min_confidence_pct,
        "min_rr_ratio": min_rr_ratio,
        "limit": limit,
        "exchange": exchange,
        "timeframe": timeframe,
        "market_type": market_type,
        "testnet": testnet,
    }
    return result


def _run_bot_cycle(config: dict) -> dict:
    if config.get("hunter_enabled", False):
        ttl = int(config.get("scan_cache_ttl_seconds", 12))
        params = _build_scan_params_from_config(config)
        if _scan_cache_fresh(ttl) and _SCAN_CACHE["params"] == params:
            _log("using cached scan result")
            scan_result = _SCAN_CACHE["data"]
        else:
            scan_result = _run_and_cache_scan(**params)
        result = run_auto_hunter(config, scan_result=scan_result)
    else:
        result = run_auto_trade(config)
    _log(f"cycle result mode={result.get('mode')} reason={result.get('reason')}")
    return result


def _compute_cycle_seconds(config: dict) -> int:
    if config.get("bot_cycle_seconds"):
        return max(2, int(config.get("bot_cycle_seconds", 5)))
    tf = str(config.get("scan_timeframe") or config.get("timeframe") or "1m").lower()
    mapping = {
        "1m": 3,
        "3m": 4,
        "5m": 5,
        "15m": 8,
        "30m": 10,
        "1h": 15,
        "4h": 30,
        "1d": 60,
    }
    return mapping.get(tf, 5)


async def _bot_loop() -> None:
    global _BOT_LAST_HEARTBEAT
    _log("background loop started")
    while True:
        meta = _load_meta()
        if not meta.get("running"):
            meta["loop_active"] = False
            _save_meta(meta)
            _log("background loop exiting")
            return
        try:
            config = load_config()
            _BOT_LAST_HEARTBEAT = time.time()
            result = _run_bot_cycle(config)
            meta = _load_meta()
            meta.update({
                "running": True,
                "loop_active": True,
                "last_result": result,
                "last_cycle_at": time.time(),
                "last_error": None,
                "bot_cycle_seconds": _compute_cycle_seconds(config),
            })
            _save_meta(meta)
        except Exception as e:
            meta = _load_meta()
            meta.update({
                "running": True,
                "loop_active": True,
                "last_cycle_at": time.time(),
                "last_error": str(e),
            })
            _save_meta(meta)
            _log(f"cycle error: {e}")
        await asyncio.sleep(_compute_cycle_seconds(load_config()))


@app.on_event("startup")
async def _startup() -> None:
    meta = _load_meta()
    if meta.get("running"):
        global _BOT_TASK
        if _BOT_TASK is None or _BOT_TASK.done():
            _BOT_TASK = asyncio.create_task(_bot_loop())
            _log("restored background loop on startup")


@app.get("/")
def root():
    return {"status": "AI Trading Backend Running"}


@app.post("/signal")
def get_signal(req: SignalRequest):
    return generate_signal(
        req.symbol,
        exchange=req.exchange,
        timeframe=req.timeframe,
        market_type=req.market_type,
        testnet=req.testnet,
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
            leverage=req.leverage,
            auto_leverage=req.auto_leverage,
            risk_per_trade_pct=req.risk_per_trade_pct,
            entry_price=req.entry_price,
            stop_loss=req.stop_loss,
        )
        return {"ok": True, "order": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/config")
def set_bot_config(req: BotConfigRequest):
    data = req.model_dump()
    saved = save_config(data)
    return {"ok": True, "config": sanitize_config(saved)}


@app.get("/bot/config")
def get_bot_config():
    config = load_config()
    return {"ok": True, "config": config}


@app.post("/bot/run")
def bot_run():
    config = load_config()
    if not config:
        raise HTTPException(status_code=400, detail="Bot config not found")
    try:
        result = _run_bot_cycle(config)
        meta = _load_meta()
        meta["last_result"] = result
        meta["last_cycle_at"] = time.time()
        _save_meta(meta)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/start")
async def bot_start(req: BotConfigRequest):
    global _BOT_TASK
    try:
        config = save_config(req.model_dump())
        meta = _load_meta()
        meta.update({
            "running": True,
            "loop_active": True,
            "last_started_at": time.time(),
            "last_error": None,
            "bot_cycle_seconds": _compute_cycle_seconds(config),
        })
        _save_meta(meta)
        if _BOT_TASK is None or _BOT_TASK.done():
            _BOT_TASK = asyncio.create_task(_bot_loop())
        return {"ok": True, "running": True, "config": sanitize_config(config), "bot_cycle_seconds": _compute_cycle_seconds(config)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/stop")
def bot_stop():
    meta = _load_meta()
    meta.update({"running": False, "loop_active": False, "last_stopped_at": time.time()})
    _save_meta(meta)
    _log("bot stopped")
    return {"ok": True, "running": False, "reason": "Bot stopped"}


@app.get("/bot/status")
def bot_status():
    meta = _load_meta()
    state = get_state()
    config = load_config()
    last_result = meta.get("last_result") or {}
    signal = last_result.get("signal") or last_result.get("best_signal") or {}
    order_wrap = last_result.get("order") if isinstance(last_result.get("order"), dict) else {}
    return {
        "ok": True,
        "running": bool(meta.get("running", False)),
        "loop_active": bool(meta.get("loop_active", False)),
        "hunter_enabled": bool(config.get("hunter_enabled", False)),
        "exchange": config.get("exchange"),
        "market_type": config.get("market_type", "future"),
        "timeframe": config.get("timeframe", "1h"),
        "scan_timeframe": config.get("scan_timeframe") or config.get("timeframe", "1h"),
        "symbol": signal.get("symbol") or config.get("symbol"),
        "scan_limit": int(config.get("scan_limit", 12)),
        "scan_symbols": config.get("scan_symbols") or [],
        "bot_cycle_seconds": meta.get("bot_cycle_seconds") or _compute_cycle_seconds(config),
        "last_cycle_at": meta.get("last_cycle_at"),
        "last_error": meta.get("last_error"),
        "heartbeat_age_seconds": None if _BOT_LAST_HEARTBEAT is None else round(time.time() - _BOT_LAST_HEARTBEAT, 1),
        "trade_count_today": int(state.get("trade_count_today", 0)),
        "last_trade_time": state.get("last_trade_time"),
        "open_positions": state.get("open_positions", {}),
        "last_reason": last_result.get("reason"),
        "last_mode": last_result.get("mode"),
        "last_order": order_wrap,
        "last_signal": signal,
    }


@app.post("/bot/test-connection")
def bot_test_connection(req: BotConfigRequest):
    try:
        ex = place_market_order  # keep import used
        client = build_exchange(req.exchange, req.api_key, req.secret, req.passphrase, req.testnet, req.market_type)  # type: ignore[name-defined]
        if hasattr(client, "fetch_balance"):
            bal = client.fetch_balance()
        else:
            bal = {"ok": True}
        return {"ok": True, "message": "Connection successful", "sample": str(bal)[:500]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    }
    if not req.force_refresh and _scan_cache_fresh(int(load_config().get("scan_cache_ttl_seconds", 12))) and _SCAN_CACHE["params"] == params:
        _log("scan request served from cache")
        return _SCAN_CACHE["data"]
    return _run_and_cache_scan(**params)
