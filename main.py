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
    }
    base.update(raw or {})
    return base


def _save_meta(data: dict) -> dict:
    BOT_META_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _scan_cache_fresh(ttl_seconds: int = 45) -> bool:
    return _SCAN_CACHE["data"] is not None and (time.time() - _SCAN_CACHE["created_at"] <= ttl_seconds)


def _build_scan_params_from_config(config: dict) -> dict:
    return {
        "symbols": config.get("scan_symbols"),
        "min_confidence_pct": float(config.get("min_confidence_pct", 55.0)),
        "min_rr_ratio": float(config.get("min_rr_ratio", 1.0)),
        "limit": int(config.get("scan_limit", 12)),
        "exchange": config.get("scan_exchange") or config.get("exchange", "binance"),
        "timeframe": config.get("scan_timeframe") or config.get("timeframe", "1h"),
        "market_type": config.get("scan_market_type") or config.get("market_type", "future"),
        "testnet": bool(config.get("testnet", True)),
    }


def _run_and_cache_scan(*, symbols=None, min_confidence_pct=55.0, min_rr_ratio=1.0, limit=12, exchange="binance", timeframe="1h", market_type="future", testnet=True) -> dict:
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
        ttl = int(config.get("scan_cache_ttl_seconds", 45))
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


async def _bot_loop() -> None:
    meta = _load_meta()
    meta["loop_active"] = True
    meta["last_error"] = None
    _save_meta(meta)
    _log("background loop started")
    try:
        while True:
            meta = _load_meta()
            if not meta.get("running", False):
                break
            try:
                config = load_config()
                result = _run_bot_cycle(config)
                meta = _load_meta()
                meta["last_result"] = result
                meta["last_cycle_at"] = time.time()
                meta["last_error"] = None
                _save_meta(meta)
            except Exception as exc:
                meta = _load_meta()
                meta["last_error"] = str(exc)
                meta["last_cycle_at"] = time.time()
                _save_meta(meta)
                _log(f"cycle error: {exc}")
            await asyncio.sleep(max(5, int(load_config().get("bot_cycle_seconds", 20))))
    finally:
        meta = _load_meta()
        meta["loop_active"] = False
        _save_meta(meta)
        _log("background loop stopped")


async def _ensure_bot_loop_started() -> None:
    global _BOT_TASK
    if _BOT_TASK is None or _BOT_TASK.done():
        _BOT_TASK = asyncio.create_task(_bot_loop())


@app.on_event("startup")
async def _startup() -> None:
    meta = _load_meta()
    if meta.get("running"):
        await _ensure_bot_loop_started()


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
            take_profit=req.take_profit,
            safe_mode=req.safe_mode,
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
        meta["last_error"] = None
        _save_meta(meta)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/start")
async def bot_start(req: BotConfigRequest):
    try:
        config = save_config(req.model_dump())
        meta = _load_meta()
        meta.update({
            "running": True,
            "last_started_at": time.time(),
            "last_error": None,
        })
        _save_meta(meta)
        await _ensure_bot_loop_started()
        return {"ok": True, "running": True, "config": sanitize_config(config), "safe_mode": bool(config.get("safe_mode", True))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/stop")
def bot_stop():
    meta = _load_meta()
    meta.update({"running": False, "last_stopped_at": time.time()})
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
    protection = order_wrap.get("protection") if isinstance(order_wrap, dict) else None
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
        "action": signal.get("action"),
        "confidence_pct": signal.get("confidence_pct"),
        "last_result": last_result,
        "last_trade": order_wrap.get("order") or last_result.get("order"),
        "position_size": order_wrap.get("amount"),
        "notional_estimate": order_wrap.get("notional_estimate"),
        "applied_leverage": order_wrap.get("applied_leverage"),
        "protection": protection,
        "safe_mode": bool(config.get("safe_mode", True)),
        "last_cycle_at": meta.get("last_cycle_at"),
        "last_error": meta.get("last_error"),
        "open_positions": len((state.get("open_positions") or {})),
        "trade_count_today": state.get("trade_count_today", 0),
        "daily_pnl": state.get("daily_realized_pnl_pct", 0.0),
        "last_trade_time": state.get("last_trade_time"),
        "state": state,
    }


@app.post("/bot/test-connection")
def bot_test_connection(req: BotConfigRequest | None = None):
    config = req.model_dump() if req is not None else load_config()
    try:
        ex = build_exchange(
            exchange_name=config.get("exchange", "binance"),
            api_key=config.get("api_key", ""),
            secret=config.get("secret", ""),
            passphrase=config.get("passphrase"),
            testnet=bool(config.get("testnet", True)),
            market_type=config.get("market_type", "future"),
        )
        ex.load_markets()
        balance = None
        try:
            balance = ex.fetch_balance()
        except Exception:
            pass

        return {
            "ok": True,
            "success": True,
            "exchange": config.get("exchange", "binance"),
            "market_type": config.get("market_type", "future"),
            "testnet": bool(config.get("testnet", True)),
            "markets_loaded": True,
            "balance_available": isinstance(balance, dict),
            "message": "Connection test passed",
            "safe_mode": bool(config.get("safe_mode", True)),
        }
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
