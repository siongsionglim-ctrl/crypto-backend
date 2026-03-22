from fastapi import FastAPI, HTTPException
from models import SignalRequest, TradeRequest, BotConfigRequest
from engine.trading_engine import generate_signal
from exchange_executor import place_market_order
from auto_trade import run_auto_trade
from config_store import save_config, load_config
from risk_manager import get_state

from models import SignalRequest, TradeRequest, BotConfigRequest, ScanRequest
from engine.scanner_engine import scan_symbols
from auto_hunter import run_auto_hunter

app = FastAPI()


@app.get("/")
def root():
    return {"status": "AI Trading Backend Running"}


@app.post("/signal")
def get_signal(req: SignalRequest):
    return generate_signal(req.symbol)


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
        )
        return {"ok": True, "order": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/bot/config")
def set_bot_config(req: BotConfigRequest):
    data = req.model_dump()
    save_config(data)
    return {"ok": True, "config": data}


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
        if config.get("hunter_enabled", False):
            return run_auto_hunter(config)
        return run_auto_trade(config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    )