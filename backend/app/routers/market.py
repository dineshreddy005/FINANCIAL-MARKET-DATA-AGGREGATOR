"""
Read endpoints -- public, but rate-limited, and cached in Redis for
performance (every dashboard refresh shouldn't re-hit Postgres for data
that's at most a few seconds stale).
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_get_or_set, cache_stats, get_redis
from app.config import get_settings
from app.database import get_db
from app.security import rate_limit_dependency

router = APIRouter(prefix="/api/market", tags=["market"])
settings = get_settings()


@router.get("/latest", dependencies=[Depends(rate_limit_dependency)])
async def latest_prices(
    asset_type: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    redis_client = await get_redis()
    cache_key = f"resp:latest:{asset_type}:{limit}"
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    query = """
        SELECT symbol, asset_type, source, event_time, price, volume
        FROM (
            SELECT a.symbol, a.asset_type, p.source, p.event_time, p.price, p.volume,
                   ROW_NUMBER() OVER (PARTITION BY a.symbol ORDER BY p.event_time DESC) as rn
            FROM price_ticks p
            JOIN assets a ON a.id = p.asset_id
            WHERE (:asset_type IS NULL OR a.asset_type = :asset_type)
        ) t
        WHERE rn = 1
        LIMIT :limit
    """
    result = await db.execute(text(query), {"asset_type": asset_type, "limit": limit})
    rows = [dict(r._mapping) for r in result]

    await redis_client.set(cache_key, json.dumps(rows, default=str), ex=settings.cache_ttl_live_seconds)
    return rows


@router.get("/history/{symbol}", dependencies=[Depends(rate_limit_dependency)])
async def price_history(
    symbol: str,
    hours: int = Query(default=24, le=24 * 30),
    db: AsyncSession = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = """
        SELECT p.event_time, p.price, p.volume, p.source
        FROM price_ticks p
        JOIN assets a ON a.id = p.asset_id
        WHERE a.symbol = :symbol AND p.event_time >= :since
        ORDER BY p.event_time ASC
    """
    result = await db.execute(text(query), {"symbol": symbol.upper(), "since": since})
    return [dict(r._mapping) for r in result]


@router.get("/profile/{symbol}", dependencies=[Depends(rate_limit_dependency)])
async def asset_profile(symbol: str, response: Response, db: AsyncSession = Depends(get_db)):
    """
    Requirement 6 in its purest form: `sector`, `market_cap`, `description`,
    and the 52-week range are non-volatile -- they don't change between two
    requests a minute apart -- so this endpoint is a textbook cache-aside
    candidate with a long (1 hour default) TTL, instead of hitting Postgres
    on every dashboard load. `X-Cache` tells you which path served the
    response; watch it flip from MISS to HIT on a second call.
    """
    symbol = symbol.upper()
    cache_key = f"meta:profile:{symbol}"

    async def _fetch_from_db():
        result = await db.execute(
            text(
                """
                SELECT symbol, asset_type, display_name, currency, sector, market_cap,
                       description, week52_high, week52_low, profile_updated_at
                FROM assets WHERE symbol = :symbol
                """
            ),
            {"symbol": symbol},
        )
        row = result.mappings().first()
        if row is None:
            raise HTTPException(404, f"No profile found for {symbol}")
        return dict(row)

    value, hit = await cache_get_or_set(cache_key, settings.cache_ttl_static_seconds, _fetch_from_db)
    response.headers["X-Cache"] = "HIT" if hit else "MISS"
    return value


@router.get("/cache-stats")
async def cache_performance():
    """Live hit/miss counters for the dashboard's caching panel -- proof the
    Redis layer is actually absorbing read traffic, not just configured."""
    return await cache_stats()


@router.get("/circuit-status")
async def circuit_status():
    """Exposes live breaker state for the dashboard's resilience panel."""
    from app.circuit_breaker import get_breaker

    redis_client = await get_redis()
    out = {}
    for provider in ("coingecko", "yfinance"):
        breaker = get_breaker(provider, redis_client)
        out[provider] = (await breaker.get_state()).value
    return out
