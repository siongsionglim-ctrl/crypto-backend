from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, Iterable

import websockets

from market_data import fetch_candles_rest, MarketDataError


@dataclass
class StreamStatus:
    running: bool
    connected: bool
    last_message_at: float | None
    last_error: str | None
    subscribed: list[str]
    market_type: str
    timeframe: str
    testnet: bool


class BinanceWsKlineFeed:
    def __init__(self, market_type: str, timeframe: str, testnet: bool, limit: int = 300) -> None:
        self.market_type = market_type.lower().strip()
        self.timeframe = timeframe.lower().strip()
        self.testnet = bool(testnet)
        self.limit = int(limit)
        self._symbols: set[str] = set()
        self._cache: Dict[str, deque] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wakeup: asyncio.Event | None = None
        self._lock = threading.RLock()
        self.connected = False
        self.last_message_at: float | None = None
        self.last_error: str | None = None

    def add_symbols(self, symbols: Iterable[str]) -> None:
        changed = False
        with self._lock:
            for symbol in symbols:
                sym = str(symbol or "").upper().strip()
                if not sym:
                    continue
                if sym not in self._symbols:
                    self._symbols.add(sym)
                    self._cache.setdefault(sym, deque(maxlen=self.limit))
                    changed = True
        if changed:
            self._signal_reload()

    def seed_symbol(self, symbol: str, candles: list[dict]) -> None:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return
        with self._lock:
            bucket = self._cache.setdefault(sym, deque(maxlen=self.limit))
            bucket.clear()
            bucket.extend(candles[-self.limit :])

    def get_candles(self, symbol: str) -> list[dict]:
        sym = str(symbol or "").upper().strip()
        with self._lock:
            return list(self._cache.get(sym, []))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name=f"binance-ws-{self.market_type}-{self.timeframe}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._signal_reload()

    def status(self) -> StreamStatus:
        with self._lock:
            return StreamStatus(
                running=bool(self._thread and self._thread.is_alive()) and not self._stop.is_set(),
                connected=self.connected,
                last_message_at=self.last_message_at,
                last_error=self.last_error,
                subscribed=sorted(self._symbols),
                market_type=self.market_type,
                timeframe=self.timeframe,
                testnet=self.testnet,
            )

    def _signal_reload(self) -> None:
        loop = self._loop
        wakeup = self._wakeup
        if loop and wakeup:
            loop.call_soon_threadsafe(wakeup.set)

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._wakeup = asyncio.Event()
        try:
            self._loop.run_until_complete(self._run_forever())
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                self._loop.close()
                self._loop = None
                self._wakeup = None
                self.connected = False

    async def _run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            streams = self._stream_names()
            if not streams:
                await asyncio.sleep(1.0)
                continue
            url = self._build_url(streams)
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
                    self.connected = True
                    self.last_error = None
                    backoff = 1.0
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=25)
                            self.last_message_at = time.time()
                            self._handle_message(raw)
                        except asyncio.TimeoutError:
                            await ws.ping()
                        new_streams = self._stream_names()
                        if new_streams != streams:
                            break
            except Exception as e:
                self.last_error = str(e)
            finally:
                self.connected = False
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 15.0)

    def _stream_names(self) -> list[str]:
        with self._lock:
            syms = sorted(self._symbols)
        interval = self.timeframe.lower()
        return [f"{sym.lower()}@kline_{interval}" for sym in syms]

    def _build_url(self, streams: list[str]) -> str:
        joined = "/".join(streams)
        if self.market_type == "future":
            base = "wss://stream.binancefuture.com/stream" if self.testnet else "wss://fstream.binance.com/stream"
        else:
            base = "wss://testnet.binance.vision/stream" if self.testnet else "wss://stream.binance.com:9443/stream"
        return f"{base}?streams={joined}"

    def _handle_message(self, raw: str) -> None:
        msg = json.loads(raw)
        payload = msg.get("data", msg)
        k = payload.get("k") or {}
        symbol = str((k.get("s") or payload.get("s") or "")).upper().strip()
        if not symbol:
            return
        candle = {
            "open": float(k.get("o") or 0.0),
            "high": float(k.get("h") or 0.0),
            "low": float(k.get("l") or 0.0),
            "close": float(k.get("c") or 0.0),
            "volume": float(k.get("v") or 0.0),
            "open_time": int(k.get("t") or 0),
            "close_time": int(k.get("T") or 0),
            "is_closed": bool(k.get("x")),
        }
        with self._lock:
            bucket = self._cache.setdefault(symbol, deque(maxlen=self.limit))
            if bucket and int(bucket[-1].get("open_time") or 0) == candle["open_time"]:
                bucket[-1] = candle
            else:
                bucket.append(candle)


_FEEDS: dict[tuple[str, str, bool], BinanceWsKlineFeed] = {}
_FEEDS_LOCK = threading.RLock()


def ensure_binance_feed(
    symbols: list[str],
    timeframe: str = "1m",
    market_type: str = "future",
    testnet: bool = False,
    limit: int = 300,
    bootstrap_limit: int = 250,
) -> BinanceWsKlineFeed:
    key = (market_type.lower().strip(), timeframe.lower().strip(), bool(testnet))
    with _FEEDS_LOCK:
        feed = _FEEDS.get(key)
        if feed is None:
            feed = BinanceWsKlineFeed(*key, limit=limit)
            _FEEDS[key] = feed
    feed.add_symbols(symbols)
    for symbol in symbols:
        if not feed.get_candles(symbol):
            try:
                seed = fetch_candles_rest(
                    symbol=symbol,
                    exchange="binance",
                    timeframe=timeframe,
                    limit=bootstrap_limit,
                    market_type=market_type,
                    testnet=testnet,
                )
                if seed:
                    feed.seed_symbol(symbol, seed)
            except Exception:
                pass
    feed.start()
    return feed


def get_binance_cached_candles(
    symbol: str,
    timeframe: str = "1m",
    market_type: str = "future",
    testnet: bool = False,
    min_bars: int = 50,
) -> list[dict]:
    key = (market_type.lower().strip(), timeframe.lower().strip(), bool(testnet))
    with _FEEDS_LOCK:
        feed = _FEEDS.get(key)
    if not feed:
        return []
    candles = feed.get_candles(symbol)
    if len(candles) < min_bars:
        return []
    return candles


def get_ws_status() -> dict:
    with _FEEDS_LOCK:
        feeds = list(_FEEDS.values())
    return {
        "ok": True,
        "feeds": [feed.status().__dict__ for feed in feeds],
    }
