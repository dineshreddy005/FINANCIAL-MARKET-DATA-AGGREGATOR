"""
Resilience & Rate-Limiting Strategy (Circuit Breaker)
=======================================================
Wraps every call to a free upstream API (yfinance, CoinGecko) with:

  1. A token-bucket style request counter in Redis, so we self-throttle
     BEFORE hitting the provider's own rate limit (proactive).
  2. A classic three-state circuit breaker (CLOSED -> OPEN -> HALF_OPEN)
     so that once a provider starts failing/limiting us, we stop hammering
     it, serve cached data instead, and only cautiously probe it again
     after a cooldown (reactive).
  3. `tenacity` for the actual retry-with-backoff of transient errors
     (timeouts, 5xx) within a single call attempt, BEFORE the circuit
     breaker's failure counter even gets involved.

State lives in Redis (not in-process memory) so it survives restarts and is
shared across every worker process/replica.
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

import redis.asyncio as redis
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

logger = logging.getLogger("fmda.circuit_breaker")
settings = get_settings()


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # normal operation, calls go through
    OPEN = "OPEN"            # tripped -- calls are short-circuited, cache used instead
    HALF_OPEN = "HALF_OPEN"  # cooldown elapsed -- allow exactly one probe call through


class CircuitOpenError(Exception):
    """Raised when a call is short-circuited because the breaker is OPEN."""


class TransientProviderError(Exception):
    """Raise this from inside provider clients for retryable failures
    (timeouts, connection errors, HTTP 429/5xx). tenacity retries on this;
    anything else (e.g. a 400 bad request) is NOT retried and is treated
    as a hard failure that still counts toward the breaker."""


class RedisCircuitBreaker:
    """
    One instance per upstream provider (e.g. "coingecko", "yfinance").
    All state is namespaced under `cb:{provider}:*` keys in Redis.
    """

    def __init__(self, provider: str, redis_client: redis.Redis):
        self.provider = provider
        self.redis = redis_client
        self.failure_threshold = settings.cb_failure_threshold
        self.open_seconds = settings.cb_open_seconds
        self.window_seconds = settings.cb_window_seconds

    # -- Redis key helpers ---------------------------------------------------
    @property
    def _state_key(self) -> str:
        return f"cb:{self.provider}:state"

    @property
    def _failures_key(self) -> str:
        return f"cb:{self.provider}:failures"

    @property
    def _opened_at_key(self) -> str:
        return f"cb:{self.provider}:opened_at"

    @property
    def _rate_key(self) -> str:
        return f"cb:{self.provider}:rate_count"

    @property
    def _cache_key_prefix(self) -> str:
        return f"cb:{self.provider}:cache:"

    # -- Public state machine -----------------------------------------------
    async def get_state(self) -> CircuitState:
        raw = await self.redis.get(self._state_key)
        if raw is None:
            return CircuitState.CLOSED
        state = CircuitState(raw)
        if state == CircuitState.OPEN:
            opened_at = await self.redis.get(self._opened_at_key)
            if opened_at and (time.time() - float(opened_at)) >= self.open_seconds:
                # cooldown elapsed -> allow exactly one probe through
                await self._transition(CircuitState.HALF_OPEN, "cooldown elapsed, probing")
                return CircuitState.HALF_OPEN
        return state

    async def _transition(self, new_state: CircuitState, reason: str) -> None:
        old = await self.redis.get(self._state_key)
        await self.redis.set(self._state_key, new_state.value)
        logger.warning(
            "circuit[%s] %s -> %s (%s)", self.provider, old or "CLOSED", new_state.value, reason
        )
        if new_state == CircuitState.OPEN:
            await self.redis.set(self._opened_at_key, str(time.time()))

    async def record_success(self) -> None:
        state = await self.get_state()
        if state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            await self._transition(CircuitState.CLOSED, "probe succeeded")
        await self.redis.delete(self._failures_key)

    async def record_failure(self) -> None:
        failures = await self.redis.incr(self._failures_key)
        await self.redis.expire(self._failures_key, self.window_seconds)
        state = await self.get_state()
        if state == CircuitState.HALF_OPEN:
            # probe failed -> straight back to OPEN, no second chances
            await self._transition(CircuitState.OPEN, "probe failed")
        elif failures >= self.failure_threshold:
            await self._transition(CircuitState.OPEN, f"{failures} failures in window")

    # -- Proactive rate limiting (separate from failure-based breaking) -----
    async def check_self_rate_limit(self, limit: int, window_seconds: int) -> bool:
        """Returns True if we're still within budget; increments the counter."""
        count = await self.redis.incr(self._rate_key)
        if count == 1:
            await self.redis.expire(self._rate_key, window_seconds)
        return count <= limit

    # -- Fallback cache -------------------------------------------------------
    async def cache_result(self, key: str, value: str, ttl_seconds: int = 300) -> None:
        await self.redis.set(f"{self._cache_key_prefix}{key}", value, ex=ttl_seconds)

    async def get_cached(self, key: str) -> Optional[str]:
        return await self.redis.get(f"{self._cache_key_prefix}{key}")

    # -- The main entry point -------------------------------------------------
    async def call(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args,
        cache_key: Optional[str] = None,
        rate_limit: Optional[int] = None,
        rate_window: Optional[int] = None,
        **kwargs,
    ) -> Any:
        """
        Execute `fn(*args, **kwargs)` protected by the circuit breaker.
        On CircuitOpenError or exhausted retries, falls back to the last
        cached value for `cache_key` if one exists, otherwise re-raises.
        """
        state = await self.get_state()

        if state == CircuitState.OPEN:
            logger.info("circuit[%s] OPEN -- short-circuiting, using cache", self.provider)
            return await self._fallback_or_raise(cache_key, CircuitOpenError(
                f"{self.provider} circuit is OPEN"
            ))

        if rate_limit and not await self.check_self_rate_limit(rate_limit, rate_window or 60):
            logger.info("circuit[%s] self rate-limit exceeded -- using cache", self.provider)
            return await self._fallback_or_raise(cache_key, CircuitOpenError(
                f"{self.provider} self-imposed rate limit exceeded"
            ))

        try:
            result = await self._call_with_retry(fn, *args, **kwargs)
            await self.record_success()
            if cache_key is not None:
                import json
                await self.cache_result(cache_key, json.dumps(result, default=str))
            return result
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: ANY failure trips the breaker
            await self.record_failure()
            logger.error("circuit[%s] call failed: %s", self.provider, exc)
            return await self._fallback_or_raise(cache_key, exc)

    async def _fallback_or_raise(self, cache_key: Optional[str], exc: Exception) -> Any:
        if cache_key is not None:
            cached = await self.get_cached(cache_key)
            if cached is not None:
                logger.info("circuit[%s] serving stale cache for key=%s", self.provider, cache_key)
                return cached
        raise exc

    @staticmethod
    @retry(
        retry=retry_if_exception_type(TransientProviderError),
        wait=wait_exponential(multiplier=0.5, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _call_with_retry(fn: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
        return await fn(*args, **kwargs)


_breakers: dict[str, RedisCircuitBreaker] = {}


def get_breaker(provider: str, redis_client: redis.Redis) -> RedisCircuitBreaker:
    if provider not in _breakers:
        _breakers[provider] = RedisCircuitBreaker(provider, redis_client)
    return _breakers[provider]
