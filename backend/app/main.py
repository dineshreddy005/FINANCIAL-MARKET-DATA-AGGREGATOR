"""
Financial Market Data Aggregator -- API entrypoint.

Run with:
    uvicorn app.main:app --reload --port 8000
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import get_settings
from app.ingestion.websocket_feed import stream_loop
from app.routers import accounts, audit, auth, ingest, insights, market

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()

app = FastAPI(
    title="Financial Market Data Aggregator",
    version="2.0.0",
    description="Multi-format ingestion, idempotent normalization, circuit-breaker resilience, "
                 "zero-trust RBAC with field masking, a Postgres-trigger audit trail, a Redis "
                 "caching layer, and AI-powered market insights for free-tier market data sources.",
)

# --- Performance / security middleware -------------------------------------
app.add_middleware(GZipMiddleware, minimum_size=1024)  # compress large JSON responses
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(market.router)
app.include_router(ingest.router)
app.include_router(ingest.ws_router)
app.include_router(accounts.router)
app.include_router(audit.router)
app.include_router(insights.router)

_stream_task: asyncio.Task | None = None


from app.database import get_db, init_db

# ... (other imports) ...

@app.on_event("startup")
async def start_background_ingestion():
    global _stream_task
    # Initialize SQLite database if running in mock fallback mode
    await init_db()
    # Demo symbol universe -- swap for a configurable watchlist later.
    stock_symbols = ["AAPL", "MSFT", "TSLA"]
    crypto_ids = ["bitcoin", "ethereum", "solana"]
    _stream_task = asyncio.create_task(stream_loop(stock_symbols, crypto_ids, interval_seconds=5))


@app.on_event("shutdown")
async def stop_background_ingestion():
    if _stream_task:
        _stream_task.cancel()


@app.get("/health")
async def health():
    return {"status": "ok"}
