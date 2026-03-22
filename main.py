from __future__ import annotations

import threading
import time
from typing import Any
from fastapi import FastAPI, HTTPException

from models import SignalRequest, TradeRequest, BotConfigRequest, ScanRequest, StartBotRequest
from engine.trading_engine import generate_signal
from engine.scanner_engine import scan_symbols
from exchange_executor import execute_trade_bundle
from auto_trade import run_auto_trade
from auto_hunter import run_auto_hunter
from config_store import save_config, load_config, sanitize_config
from risk_manager import get_state

app = FastAPI(title="AI Trading Backend")

BOT_RUNTIME: dict[str, Any] = {
    "running": False,
    "thread": None,
    "interval_seconds": 20,
    "last_result": None,
    "last_error": None,
    "last_run_at": None,
}


@app.get("/")
def root():
    return {"status": "AI Trading Backend Running"}


@app.post("/signal")
def get_signal(req: SignalRequest):
    return generate_signal(req.symbol, exchange=req.exchange, timeframe=req.timeframe, market_type=req.market_type, testnet=req.testnet)


@app.post("/trade")
def trade(req: TradeRequest):
    try:
        result = execute_trade_bundle(
            exchange_name=req.exchange,
            api_key=req.api_key,
            secret=req.secret,
            passphrase=req.passphrase,
            symbol=req.symbol,
            side=str(req.side).lower(),
            amount=req.amount,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            testnet=req.testnet,
            market_type=req.market_type,
            leverage=req.leverage,
        )
        return {"ok": True, "order": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/config")
def set_bot_config(req: BotConfigRequest):
    data = save_config(req.model_dump())
    return {"ok": True, "config": sanitize_config(data)}


@app.get("/bot/config")
def get_bot_config():
    return {"ok": True, "config": sanitize_config(load_config())}


@app.post("/bot/run")
def bot_run():
    config = load_config()
    try:
        result = run_auto_hunter(config) if config.get("hunter_enabled", False) else run_auto_trade(config)
        BOT_RUNTIME["last_result"] = result
        BOT_RUNTIME["last_error"] = None
        BOT_RUNTIME["last_run_at"] = time.time()
        return result
    except Exception as e:
        BOT_RUNTIME["last_error"] = str(e)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/start")
def bot_start(req: StartBotRequest = StartBotRequest()):
    if BOT_RUNTIME["running"]:
        return {"ok": True, "status": "already_running", "interval_seconds": BOT_RUNTIME["interval_seconds"]}

    BOT_RUNTIME["running"] = True
    BOT_RUNTIME["interval_seconds"] = req.interval_seconds

    def worker():
        while BOT_RUNTIME["running"]:
            try:
                config = load_config()
                result = run_auto_hunter(config) if config.get("hunter_enabled", False) else run_auto_trade(config)
                BOT_RUNTIME["last_result"] = result
                BOT_RUNTIME["last_error"] = None
                BOT_RUNTIME["last_run_at"] = time.time()
            except Exception as e:
                BOT_RUNTIME["last_error"] = str(e)
            time.sleep(BOT_RUNTIME["interval_seconds"])

    thread = threading.Thread(target=worker, daemon=True)
    BOT_RUNTIME["thread"] = thread
    thread.start()
    return {"ok": True, "status": "started", "interval_seconds": BOT_RUNTIME["interval_seconds"]}


@app.post("/bot/stop")
def bot_stop():
    BOT_RUNTIME["running"] = False
    return {"ok": True, "status": "stopped"}


@app.get("/bot/status")
def bot_status():
    last = BOT_RUNTIME.get("last_result") or {}
    best_signal = last.get("best_signal") or last.get("signal") or {}
    return {
        "ok": True,
        "running": BOT_RUNTIME["running"],
        "interval_seconds": BOT_RUNTIME["interval_seconds"],
        "last_run_at": BOT_RUNTIME["last_run_at"],
        "last_error": BOT_RUNTIME["last_error"],
        "current_symbol": best_signal.get("symbol"),
        "action": best_signal.get("action"),
        "confidence": best_signal.get("confidence_pct"),
        "today_state": get_state(),
        "last_result": last,
    }


@app.get("/bot/state")
def bot_state():
    return {"ok": True, "state": get_state()}


@app.post("/scan")
def scan(req: ScanRequest):
    return scan_symbols(
        symbols=req.symbols,
        min_confidence_pct=req.min_confidence_pct,
        min_rr_ratio=req.min_rr_ratio,
        limit=req.limit,
        exchange=req.exchange,
        timeframe=req.timeframe,
        market_type=req.market_type,
        testnet=req.testnet,
    )
