"""
Multi-Format Data Ingestion Engine -- streaming half.
======================================================
True server-push websockets don't exist on yfinance/CoinGecko's free tiers,
so the realistic free-stack pattern (and the one actually used in
production by lightweight aggregators) is:

    background task polls the provider on a short interval
        -> normalizes the response
        -> idempotently upserts it into Postgres
        -> broadcasts the normalized tick to every connected browser
           over OUR OWN websocket (FastAPI WebSocket)

This gives end users a genuine live-streaming experience even though the
upstream source is polled, and every call to the upstream is protected by
the circuit breaker + self-imposed rate limit from circuit_breaker.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Iterable

import httpx
import websockets
from fastapi import WebSocket

from app.cache import get_redis
from app.circuit_breaker import TransientProviderError, get_breaker
from app.config import get_settings
from app.database import session_scope
from app.normalizer import normalize_coingecko_quote, normalize_finnhub_trade, upsert_ticks

logger = logging.getLogger("fmda.stream")
settings = get_settings()


class ConnectionManager:
    """Tracks live websocket clients and fan-outs ticks to all of them."""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead = []
        payload = json.dumps(message, default=str)
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Upstream fetchers. Each raises TransientProviderError on retryable
# failures so tenacity (inside the circuit breaker) knows to retry them.
# ---------------------------------------------------------------------------

async def _fetch_coingecko(symbols: Iterable[str]) -> list[dict]:
    ids = ",".join(symbols)
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {"vs_currency": "usd", "ids": ids}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientProviderError(f"coingecko returned {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        for row in data:
            row["last_updated"] = row.get("last_updated") or datetime.now(timezone.utc).isoformat()
        return data
    except httpx.TransportError as exc:
        raise TransientProviderError(str(exc)) from exc


async def finnhub_stream_loop(symbols: list[str]) -> None:
    if not settings.finnhub_api_key or not symbols:
        logger.warning("Finnhub stream disabled: missing API key or symbols")
        return

    uri = f"wss://ws.finnhub.io?token={settings.finnhub_api_key}"
    while True:
        try:
            async with websockets.connect(uri) as websocket:
                # Subscribe to all requested symbols
                for symbol in symbols:
                    await websocket.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                
                async for message in websocket:
                    data = json.loads(message)
                    if data.get("type") == "trade":
                        ticks = [normalize_finnhub_trade(t) for t in data.get("data", [])]
                        if ticks:
                            async with session_scope() as session:
                                await upsert_ticks(session, ticks)
                            for t in ticks:
                                await manager.broadcast(t.model_dump())
        except Exception as exc:
            logger.error("Finnhub websocket error: %s", exc)
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# The polling loop -- this is what `main.py` schedules at app startup.
# ---------------------------------------------------------------------------

async def stream_loop(stock_symbols: list[str], crypto_ids: list[str], interval_seconds: int = 5) -> None:
    redis_client = await get_redis()
    coingecko_breaker = get_breaker("coingecko", redis_client)
    
    # Start the Finnhub background websocket for stocks
    if settings.finnhub_api_key and stock_symbols:
        asyncio.create_task(finnhub_stream_loop(stock_symbols))

    # The while loop is now just for CoinGecko polling
    while True:
        try:
            if crypto_ids:
                try:
                    raw_crypto = await coingecko_breaker.call(
                        _fetch_coingecko, crypto_ids,
                        cache_key="markets:" + ",".join(crypto_ids),
                        rate_limit=settings.coingecko_rate_limit,
                        rate_window=settings.coingecko_rate_window_seconds,
                    )
                    if isinstance(raw_crypto, str):  # came back from cache as a JSON string
                        raw_crypto = json.loads(raw_crypto)
                    ticks = [normalize_coingecko_quote(r) for r in raw_crypto]
                    async with session_scope() as session:
                        await upsert_ticks(session, ticks)
                    for t in ticks:
                        await manager.broadcast(t.model_dump())
                except Exception as exc:
                    logger.warning("crypto streaming update failed: %s", exc)

        except Exception as exc:  # noqa: BLE001 -- never let the loop die
            logger.error("stream_loop iteration failed: %s", exc)

        await asyncio.sleep(interval_seconds)
