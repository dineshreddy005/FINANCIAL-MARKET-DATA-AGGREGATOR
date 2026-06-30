"""
Finnhub Webhook Receiver
=========================
Finnhub can push real-time events (trades, earnings, news, IPOs, etc.) to
a publicly-reachable endpoint via HTTP POST. Each request carries an
``X-Finnhub-Secret`` header that MUST match our configured secret for
authentication.

This router:
  1. Validates the shared secret from the header.
  2. Normalises incoming trade data through the existing pipeline.
  3. Upserts normalized ticks into the database (idempotently).
  4. Broadcasts the ticks to all connected websocket clients in real time.

Configure the following env vars:
  FINNHUB_WEBHOOK_SECRET  – the secret shown on the Finnhub webhook dashboard
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import get_settings
from app.database import session_scope
from app.ingestion.websocket_feed import manager
from app.normalizer import normalize_finnhub_trade, upsert_ticks
from app.schemas import NormalizedTick

logger = logging.getLogger("fmda.routers.webhook")
settings = get_settings()

router = APIRouter(prefix="/api/webhook", tags=["webhook"])


def _validate_secret(received: str | None) -> None:
    """Reject requests that don't carry the correct Finnhub shared secret."""
    expected = settings.finnhub_webhook_secret
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Webhook secret not configured on the server",
        )
    if received != expected:
        logger.warning("Webhook auth failed — bad or missing X-Finnhub-Secret")
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _normalize_webhook_event(event: dict) -> NormalizedTick | None:
    """
    Map a single Finnhub webhook event dict into a NormalizedTick.

    Finnhub webhooks send different event shapes depending on the type.
    For *trade* events the payload mirrors the websocket format::

        {"s": "AAPL", "p": 150.25, "v": 100, "t": 1700000000000, ...}

    For other event types (earnings, news, etc.) we log and skip -- easy
    to extend later by adding more branches here.
    """
    try:
        # Trade-style event (has symbol, price, timestamp)
        if all(k in event for k in ("s", "p", "t")):
            return normalize_finnhub_trade(event)

        # Quote / price update style (alternative webhook format)
        if "symbol" in event and "price" in event:
            ts = event.get("timestamp") or event.get("t")
            if isinstance(ts, (int, float)):
                event_time = datetime.fromtimestamp(
                    ts / 1000.0 if ts > 1e12 else ts,
                    tz=timezone.utc,
                ).isoformat()
            else:
                event_time = ts or datetime.now(timezone.utc).isoformat()

            return NormalizedTick(
                symbol=event["symbol"],
                asset_type="equity",
                source="finnhub",
                event_time=event_time,
                price=event["price"],
                volume=event.get("volume"),
                currency=event.get("currency", "USD"),
            )

        logger.debug("Unrecognised webhook event shape, skipping: %s", event)
        return None

    except Exception as exc:
        logger.warning("Failed to normalise webhook event: %s — %s", event, exc)
        return None


@router.post("/finnhub", status_code=200)
async def finnhub_webhook(
    request: Request,
    x_finnhub_secret: str | None = Header(default=None),
):
    """
    Receive a Finnhub webhook POST.

    Finnhub sends ``X-Finnhub-Secret: <your_secret>`` on every request.
    The body is JSON — either a single event dict or a list of events.
    We must return 2xx quickly to acknowledge receipt.
    """
    _validate_secret(x_finnhub_secret)

    body = await request.json()

    # Normalise: Finnhub may POST a single object or a list
    events: list[dict] = body if isinstance(body, list) else [body]

    ticks: list[NormalizedTick] = []
    for event in events:
        # If the payload wraps trade data in a "data" array (websocket-style)
        if event.get("type") == "trade" and "data" in event:
            for trade in event["data"]:
                tick = _normalize_webhook_event(trade)
                if tick:
                    ticks.append(tick)
        else:
            tick = _normalize_webhook_event(event)
            if tick:
                ticks.append(tick)

    if not ticks:
        logger.info("Webhook received %d event(s) but none were trade data", len(events))
        return {"status": "ok", "processed": 0}

    # Persist + broadcast
    async with session_scope() as session:
        stats = await upsert_ticks(session, ticks, actor="webhook:finnhub")

    for t in ticks:
        await manager.broadcast(t.model_dump())

    logger.info(
        "Webhook processed: %d ticks (inserted=%d updated=%d deduped=%d)",
        stats.received, stats.inserted, stats.updated, stats.deduped,
    )
    return {
        "status": "ok",
        "processed": stats.received,
        "inserted": stats.inserted,
        "updated": stats.updated,
        "deduped": stats.deduped,
    }
