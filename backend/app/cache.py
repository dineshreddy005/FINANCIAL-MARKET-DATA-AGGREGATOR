"""
Requirement 6: High-Performance Caching Layer
================================================
Single shared async Redis client used by four different concerns in this
codebase: the circuit breaker's state machine, the rate limiter, the hot
"latest prices" response cache (already existed), and -- new here -- a
generic cache-aside helper purpose-built for NON-VOLATILE market metadata
(asset profiles, sectors, 52-week ranges, AI-generated commentary). That
data changes at most a few times a day, so it gets a long TTL and sits in
front of Postgres on every read, turning what would be a join-heavy query
into a single Redis GET for the overwhelming majority of requests.

Two TTL tiers, matched to data volatility:
  - LIVE  (`CACHE_TTL_LIVE_SECONDS`,    default  5s): tick-level prices that
    must look "live" on the dashboard.
  - STATIC (`CACHE_TTL_STATIC_SECONDS`, default 3600s): asset profiles, AI
    insights, anything that's expensive to compute/join and barely changes.

Hit/miss counters are tracked in Redis itself (cheap INCRs) so the
dashboard's "cache performance" panel can show a live hit ratio without a
separate metrics stack.
"""
import json
from typing import Any, Awaitable, Callable

from app.config import get_settings

settings = get_settings()

if settings.mock_services:
    class MockRedis:
        def __init__(self):
            self.data = {}
        async def get(self, key: str) -> Any:
            return self.data.get(key)
        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.data[key] = str(value)
        async def delete(self, key: str) -> None:
            self.data.pop(key, None)
        async def incr(self, key: str) -> int:
            val = int(self.data.get(key) or 0) + 1
            self.data[key] = str(val)
            return val
        async def expire(self, key: str, seconds: int) -> None:
            pass
        async def mget(self, *keys) -> list[Any]:
            return [self.data.get(k) for k in keys]

    redis_client = MockRedis()
else:
    import redis.asyncio as redis
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)

_STATS_HITS_KEY = "cache:stats:hits"
_STATS_MISSES_KEY = "cache:stats:misses"


async def get_redis() -> redis.Redis:
    return redis_client


async def cache_get_or_set(
    key: str,
    ttl_seconds: int,
    fetch_fn: Callable[[], Awaitable[Any]],
) -> tuple[Any, bool]:
    """
    The cache-aside pattern, in one place so every endpoint that needs it
    (asset profiles, AI insights, anything static) gets identical
    behaviour: check Redis -> on miss, call `fetch_fn()` (the real
    Postgres/compute path) -> store the result with `ttl_seconds` -> return
    it. Returns `(value, was_cache_hit)` so callers can surface an
    `X-Cache: HIT/MISS` header for observability.
    """
    cached = await redis_client.get(key)
    if cached is not None:
        await redis_client.incr(_STATS_HITS_KEY)
        return json.loads(cached), True

    await redis_client.incr(_STATS_MISSES_KEY)
    value = await fetch_fn()
    await redis_client.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    return value, False


async def cache_invalidate(key: str) -> None:
    """Explicit invalidation for write paths that must not serve stale data
    until the next TTL expiry (e.g. an admin edits an asset profile)."""
    await redis_client.delete(key)


async def cache_stats() -> dict[str, Any]:
    hits, misses = await redis_client.mget(_STATS_HITS_KEY, _STATS_MISSES_KEY)
    hits, misses = int(hits or 0), int(misses or 0)
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "total_lookups": total,
        "hit_ratio": round(hits / total, 4) if total else 0.0,
    }
