from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException

from models import SignalRequest, TradeRequest, BotConfigRequest, ScanRequest
from engine.trading_engine import generate_signal
from engine.scanner_engine import scan_symbols
from exchange_executor import (
    place_market_order,
    build_exchange,
    fetch_live_positions,
    get_available_balance_usdt,
    discover_scan_symbols,
)
from auto_trade import run_auto_trade
from auto_hunter import run_auto_hunter
from config_store import save_config, load_config, sanitize_config
from risk_manager import (
    get_state,
    set_open_positions,
    register_closed_position,
    set_balance_snapshot,
)

BOT_THREAD = None
BOT_RUNNING = False

app = FastAPI()

_SCAN_CACHE: dict = {
    "data": None,
    "created_at": 0.0,
    "params": None,
}

BOT_META_FILE = Path("bot_runtime_meta.json")


def _log(message: str) -> None:
    """Safe logging that prevents NoneType errors"""
    if message is None:
        message = "[None]"
    else:
        message = str(message)
    print(f"[BOT] {message}", flush=True)


def _load_meta() -> dict:
    if not BOT_META_FILE.exists():
        return {"running": False, "last_result": None, "last_started_at": None, "last_stopped_at": None}
    try:
        raw = json.loads(BOT_META_FILE.read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    base = {"running": False, "last_result": None, "last_started_at": None, "last_stopped_at": None}
    base.update(raw or {})
    return base


def _save_meta(data: dict) -> dict:
    BOT_META_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def _bot_loop():
    global BOT_RUNNING

    _log("loop started")

    while BOT_RUNNING:
        meta = _load_meta()
        if not meta.get("running"):
            break

        config = load_config()
        if not config:
            _log("loop waiting: config not found")
            time.sleep(5)
            continue

        try:
            result = _run_bot_cycle(config)
            meta["last_result"] = result
            _save_meta(meta)
        except Exception as e:
            _log(f"loop error: {e}")

        interval = int(config.get("loop_interval_sec", 60))
        time.sleep(max(5, interval))

    _log("loop stopped")


def _scan_cache_fresh(ttl_seconds: int = 45) -> bool:
    return _SCAN_CACHE["data"] is not None and (time.time() - _SCAN_CACHE["created_at"] <= ttl_seconds)


def _has_exchange_credentials(config: dict) -> bool:
    return bool((config.get("api_key") or "").strip() and (config.get("secret") or "").strip())


def _seconds_since_iso(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        from datetime import datetime
        return max(0.0, time.time() - datetime.fromisoformat(ts).timestamp())
    except Exception:
        return None


def _sync_open_positions_with_exchange(config: dict, force: bool = False) -> dict:
    previous_state = get_state()
    previous_positions = previous_state.get("open_positions") or {}

    if not _has_exchange_credentials(config):
        return previous_state

    ttl_seconds = int(config.get("position_sync_ttl_seconds", 20))
    if not force:
        seconds_since_last = _seconds_since_iso(previous_state.get("last_position_sync_time"))
        if seconds_since_last is not None and seconds_since_last < max(5, ttl_seconds):
            return previous_state

    try:
        symbols = config.get("scan_symbols") or [config.get("symbol")]
        live_positions = fetch_live_positions(
            exchange_name=config.get("exchange", "binance"),
            api_key=config.get("api_key", ""),
            secret=config.get("secret", ""),
            passphrase=config.get("passphrase"),
            testnet=bool(config.get("testnet", True)),
            market_type=config.get("market_type", "future"),
            symbols=symbols,
        )

        closed_symbols = sorted(set(previous_positions.keys()) - set(live_positions.keys()))
        cooldown_minutes = int(config.get("symbol_cooldown_minutes", config.get("cooldown_minutes", 15)))
        for symbol in closed_symbols:
            register_closed_position(symbol, previous_positions.get(symbol) or {}, cooldown_minutes=cooldown_minutes)
            _log(f"position closed detected symbol={symbol} cooldown={cooldown_minutes}m")

        state = set_open_positions(live_positions, sync_error=None)
        _log(f"position sync complete count={len(live_positions)}")
        return state
    except Exception as e:
        return set_open_positions(previous_state.get("open_positions") or {}, sync_error=str(e))


def _build_scan_params_from_config(config: dict) -> dict:
    auto_scan_enabled = bool(config.get("auto_scan_enabled", True))
    symbols = config.get("scan_symbols")

    if auto_scan_enabled:
        symbols = discover_scan_symbols(
            exchange_name=config.get("scan_exchange") or config.get("exchange", "binance"),
            api_key=config.get("api_key", ""),
            secret=config.get("secret", ""),
            passphrase=config.get("passphrase"),
            testnet=bool(config.get("testnet", True)),
            market_type=config.get("scan_market_type") or config.get("market_type", "future"),
            quote_asset=config.get("auto_scan_quote_asset", "USDT"),
            limit=int(config.get("auto_scan_limit", 20)),
            min_quote_volume=float(config.get("auto_scan_min_quote_volume", 10000000.0)),
            cache_ttl_seconds=max(120, int(config.get("scan_cache_ttl_seconds", 45))),
        )

    if not symbols:
        fallback_symbol = config.get("fallback_symbol")
        symbols = [fallback_symbol] if fallback_symbol else config.get("scan_symbols")

    return {
        "symbols": symbols,
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
    # Sync positions first
    state = _sync_open_positions_with_exchange(config, force=True)
    
    min_balance = float(config.get("min_available_balance_usdt", 5.0))
    available_balance = _check_available_balance(config)

    result = None

    # === Hunter Mode ===
    if config.get("hunter_enabled", False):
        ttl = int(config.get("scan_cache_ttl_seconds", 45))
        params = _build_scan_params_from_config(config)
        
        if _scan_cache_fresh(ttl) and _SCAN_CACHE["params"] == params:
            _log("using cached scan result")
            scan_result = _SCAN_CACHE["data"]
        else:
            scan_result = _run_and_cache_scan(**params)

        if available_balance is not None and available_balance < min_balance:
            _log(f"balance gate active available={available_balance:.4f} min={min_balance:.4f}")
            return {
                "ok": True,
                "mode": "scan_only",
                "available_balance_usdt": available_balance,
                "min_available_balance_usdt": min_balance,
                "scan_result": scan_result,
                "open_positions": state.get("open_positions") or {},
                "reason": f"Available balance below {min_balance:.2f} USDT. Scanning only.",
            }

        try:
            result = run_auto_hunter(config, scan_result=scan_result)
        except Exception as e:
            _log(f"hunter error: {e}")
            result = {
                "ok": False,
                "mode": "hunter_error",
                "available_balance_usdt": available_balance,
                "min_available_balance_usdt": min_balance,
                "open_positions": state.get("open_positions") or {},
                "reason": f"Hunter error: {str(e)}",
            }

    # === Normal Auto Trade Mode ===
    else:
        try:
            result = run_auto_trade(config)
        except Exception as e:
            _log(f"trade error: {e}")
            result = {
                "ok": False,
                "mode": "trade_error",
                "available_balance_usdt": available_balance,
                "min_available_balance_usdt": min_balance,
                "open_positions": state.get("open_positions") or {},
                "reason": f"Trade execution error: {str(e)}",
            }

    # Final safety net - ensure result is always a valid dict with string reason
    if not isinstance(result, dict):
        result = {
            "ok": False,
            "mode": "unknown",
            "reason": "Invalid result from trade/hunter function"
        }

    result.setdefault("available_balance_usdt", available_balance)
    result.setdefault("min_available_balance_usdt", min_balance)

    # === ULTRA SAFE LOGGING ===
    mode = str(result.get("mode") or "unknown")
    reason = str(result.get("reason") or "no reason provided")
    
    _log(f"cycle result → mode={mode} | reason={reason}")

    return result

def _check_available_balance(config: dict) -> float | None:
    """Check available USDT balance safely"""
    if not _has_exchange_credentials(config):
        return None
    
    try:
        available = get_available_balance_usdt(
            exchange_name=config.get("exchange", "binance"),
            api_key=config.get("api_key", ""),
            secret=config.get("secret", ""),
            passphrase=config.get("passphrase"),
            testnet=bool(config.get("testnet", True)),
            market_type=config.get("market_type", "future"),
            cache_ttl_seconds=int(config.get("balance_cache_ttl_seconds", 25)),
        )
        
        # Update state snapshot
        set_balance_snapshot(available)
        return available
    except Exception as e:
        _log(f"balance check failed: {e}")
        # Return last known balance if available
        state = get_state()
        return state.get("balance_snapshot")


def _has_exchange_credentials(config: dict) -> bool:
    """Check if API keys are configured"""
    api_key = (config.get("api_key") or "").strip()
    secret = (config.get("secret") or "").strip()
    return bool(api_key and secret)

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
        _save_meta(meta)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/start")
def bot_start(req: BotConfigRequest):
    global BOT_THREAD, BOT_RUNNING

    try:
        config = save_config(req.model_dump())

        meta = _load_meta()
        if meta.get("running"):
            return {"ok": True, "running": True, "msg": "Bot already running"}

        meta.update({
            "running": True,
            "last_started_at": time.time(),
        })
        _save_meta(meta)

        BOT_RUNNING = True
        BOT_THREAD = threading.Thread(target=_bot_loop, daemon=True)
        BOT_THREAD.start()

        return {
            "ok": True,
            "running": True,
            "msg": "Bot loop started",
            "config": sanitize_config(config),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/stop")
def bot_stop():
    global BOT_RUNNING

    meta = _load_meta()
    meta.update({
        "running": False,
        "last_stopped_at": time.time()
    })
    _save_meta(meta)

    BOT_RUNNING = False
    _log("bot stopped")

    return {"ok": True, "running": False}


@app.get("/bot/status")
def bot_status():
    meta = _load_meta()
    config = load_config()
    state = get_state()
    available_balance = state.get("balance_snapshot")
    last_result = meta.get("last_result") or {}
    signal = last_result.get("signal") or last_result.get("best_signal") or {}
    order_wrap = last_result.get("order") if isinstance(last_result.get("order"), dict) else {}
    return {
        "ok": True,
        "running": bool(meta.get("running", False)),
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
        "available_balance_usdt": available_balance,
        "min_available_balance_usdt": float(config.get("min_available_balance_usdt", 5.0)),
        "open_positions": len((state.get("open_positions") or {})),
        "open_positions_detail": state.get("open_positions") or {},
        "last_closed_positions": state.get("last_closed_positions") or [],
        "symbol_cooldowns": state.get("symbol_cooldowns") or {},
        "last_position_sync_time": state.get("last_position_sync_time"),
        "last_position_sync_error": state.get("last_position_sync_error"),
        "trade_count_today": state.get("trade_count_today", 0),
        "daily_pnl": state.get("daily_realized_pnl_pct", 0.0),
        "last_trade_time": state.get("last_trade_time"),
        "auto_scan_enabled": bool(config.get("auto_scan_enabled", True)),
        "auto_scan_limit": int(config.get("auto_scan_limit", 20)),
        "fallback_symbol": config.get("fallback_symbol", "BTCUSDT"),
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
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/sync-positions")
def bot_sync_positions():
    config = load_config()
    if not config:
        raise HTTPException(status_code=400, detail="Bot config not found")
    state = _sync_open_positions_with_exchange(config, force=True)
    return {
        "ok": True,
        "open_positions": state.get("open_positions") or {},
        "count": len((state.get("open_positions") or {})),
        "last_closed_positions": state.get("last_closed_positions") or [],
        "symbol_cooldowns": state.get("symbol_cooldowns") or {},
        "last_position_sync_time": state.get("last_position_sync_time"),
        "last_position_sync_error": state.get("last_position_sync_error"),
    }


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
