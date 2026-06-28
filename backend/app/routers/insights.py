"""
Advanced AI Integration
========================
Three AI-powered surfaces, all built so they work with ZERO external cost
out of the box (the project's "free stack" ethos extends here too), with an
optional upgrade path to a real LLM:

  1. Market insight narratives  (GET  /api/ai/insights/{symbol})
  2. A context-aware chat assistant (POST /api/ai/chat)
  3. Statistical anomaly detection   (GET  /api/ai/anomalies)

Provider strategy: if `ANTHROPIC_API_KEY` is set, (1) and (2) call the
Claude API directly over HTTPS for genuinely generative answers, grounded
in data pulled fresh from Postgres in the same request (a minimal
retrieval-augmented pattern -- the model never invents a price, it narrates
numbers we already fetched). If no key is configured, both fall back to a
deterministic, numpy/pandas-driven generator: real trend/volatility/
momentum statistics rendered through a template -- it isn't a language
model, but it's genuinely computed from live data and always available.
Anomaly detection (3) is statistical either way (rolling z-score), since
that's the more honest "AI" technique for structured numeric data anyway.

Insight responses are cached (requirement 6's STATIC tier) so repeatedly
opening the same symbol's panel doesn't re-run an LLM call or a Postgres
aggregation on every click.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import cache_get_or_set
from app.config import get_settings
from app.database import get_db
from app.rbac import CurrentUser, get_current_user

logger = logging.getLogger("fmda.ai")
router = APIRouter(prefix="/api/ai", tags=["ai"])
settings = get_settings()

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Shared data gathering -- both the LLM path and the fallback path narrate
# the SAME computed statistics, so answers never drift from real numbers.
# ---------------------------------------------------------------------------

async def _symbol_stats(db: AsyncSession, symbol: str, hours: int = 48) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        text(
            """
            SELECT p.event_time, p.price
            FROM price_ticks p JOIN assets a ON a.id = p.asset_id
            WHERE a.symbol = :symbol AND p.event_time >= :since
            ORDER BY p.event_time ASC
            """
        ),
        {"symbol": symbol.upper(), "since": since},
    )
    rows = result.all()
    if not rows:
        raise HTTPException(404, f"No recent price history for {symbol.upper()}")

    prices = [float(r.price) for r in rows]
    first, last = prices[0], prices[-1]
    change_pct = ((last - first) / first) * 100 if first else 0.0
    volatility = statistics.pstdev(prices) if len(prices) > 1 else 0.0
    mean = statistics.fmean(prices)
    return {
        "symbol": symbol.upper(),
        "window_hours": hours,
        "sample_count": len(prices),
        "first_price": round(first, 4),
        "last_price": round(last, 4),
        "change_pct": round(change_pct, 3),
        "high": round(max(prices), 4),
        "low": round(min(prices), 4),
        "mean": round(mean, 4),
        "volatility": round(volatility, 4),
    }


def _fallback_narrative(stats: dict) -> str:
    """Deterministic, template-based narrative -- no external API required."""
    sym, chg, vol, mean = stats["symbol"], stats["change_pct"], stats["volatility"], stats["mean"]
    rel_vol = (vol / mean * 100) if mean else 0.0

    if chg > 2:
        trend = f"{sym} is in a clear uptrend, up {chg:.2f}% over the last {stats['window_hours']}h"
    elif chg < -2:
        trend = f"{sym} is under pressure, down {abs(chg):.2f}% over the last {stats['window_hours']}h"
    else:
        trend = f"{sym} is trading in a tight range, roughly flat ({chg:+.2f}%) over the last {stats['window_hours']}h"

    if rel_vol > 3:
        vol_desc = f"volatility is elevated (~{rel_vol:.1f}% of mean price), so expect choppier swings"
    elif rel_vol > 1:
        vol_desc = f"volatility is moderate (~{rel_vol:.1f}% of mean price)"
    else:
        vol_desc = f"volatility is low (~{rel_vol:.1f}% of mean price), price action has been orderly"

    return (
        f"{trend}. Across {stats['sample_count']} observations, {sym} ranged from "
        f"{stats['low']} to {stats['high']} with a mean of {stats['mean']}; {vol_desc}. "
        f"This summary is computed directly from ingested tick data (rolling change, "
        f"min/max, population std-dev) -- no external model was called."
    )


async def _call_anthropic(prompt: str, *, max_tokens: int = 400) -> Optional[str]:
    if not settings.anthropic_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.ai_model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        resp.raise_for_status()
        data = resp.json()
        return "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    except Exception as exc:  # noqa: BLE001 -- AI is a nice-to-have, never breaks the request
        logger.warning("Anthropic call failed, falling back to heuristic narrative: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 1. Market insight narrative
# ---------------------------------------------------------------------------

@router.get("/insights/{symbol}")
async def market_insight(
    symbol: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    symbol = symbol.upper()
    cache_key = f"ai:insight:{symbol}"

    async def _generate() -> dict:
        stats = await _symbol_stats(db, symbol)
        prompt = (
            f"You are a market data analyst. In 3-4 sentences, give a plain-English "
            f"summary of {symbol}'s recent price action using ONLY these computed "
            f"stats (do not invent numbers): {stats}. Be concrete, neutral in tone, "
            f"and note this is not investment advice."
        )
        narrative = await _call_anthropic(prompt)
        source = "anthropic"
        if narrative is None:
            narrative = _fallback_narrative(stats)
            source = "heuristic"
        return {"symbol": symbol, "stats": stats, "narrative": narrative, "source": source,
                "generated_at": datetime.now(timezone.utc).isoformat()}

    value, hit = await cache_get_or_set(cache_key, ttl_seconds=300, fetch_fn=_generate)
    value["cache_hit"] = hit
    return value


# ---------------------------------------------------------------------------
# 2. Chat assistant
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str


_INTENT_SYMBOLS = ("AAPL", "MSFT", "TSLA", "BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA")


async def _gather_chat_context(db: AsyncSession, current_user: CurrentUser) -> dict:
    latest = await db.execute(
        text(
            """
            SELECT DISTINCT ON (a.symbol) a.symbol, p.price, p.event_time
            FROM price_ticks p JOIN assets a ON a.id = p.asset_id
            ORDER BY a.symbol, p.event_time DESC LIMIT 10
            """
        )
    )
    prices = {r.symbol: float(r.price) for r in latest}

    account_summary = None
    if current_user.role.value in ("client", "admin"):
        acct = await db.execute(
            text("SELECT cash_balance FROM client_accounts WHERE user_id = :uid"),
            {"uid": current_user.user_id},
        )
        row = acct.first()
        if row:
            account_summary = {"cash_balance": float(row[0])}

    return {"latest_prices": prices, "account_summary": account_summary}


def _fallback_chat_reply(message: str, context: dict) -> str:
    msg = message.lower()
    prices = context["latest_prices"]

    for sym in _INTENT_SYMBOLS:
        if sym.lower() in msg:
            key = next((k for k in prices if k.lower() == sym.lower()), None)
            if key:
                return f"{key} is currently trading around {prices[key]:.2f} based on the latest ingested tick."

    if "balance" in msg or "cash" in msg or "account" in msg:
        if context["account_summary"]:
            return f"Your account cash balance is {context['account_summary']['cash_balance']:.2f}."
        return "I don't see a brokerage account linked to your login."

    if "breaker" in msg or "circuit" in msg or "down" in msg or "outage" in msg:
        return "Check the Resilience panel for live circuit-breaker state per provider (CLOSED/OPEN/HALF_OPEN)."

    if prices:
        sample = ", ".join(f"{s} {p:.2f}" for s, p in list(prices.items())[:4])
        return f"I'm running in offline mode (no AI key configured). Latest prices I have: {sample}. Ask me about a specific symbol, your balance, or circuit-breaker status."

    return "I'm running in offline mode (no AI key configured) and don't have live price data yet -- try again once the feed connects."


@router.post("/chat")
async def chat(
    body: ChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    context = await _gather_chat_context(db, current_user)
    prompt = (
        f"You are FMDA's in-dashboard assistant for a financial market data aggregator. "
        f"Use ONLY this live context (never invent prices or balances): {context}. "
        f"The user (role={current_user.role.value}) asks: \"{body.message}\". "
        f"Reply in 1-3 concise sentences. If asked for investment advice, decline and "
        f"explain you provide data context only."
    )
    reply = await _call_anthropic(prompt, max_tokens=250)
    source = "anthropic"
    if reply is None:
        reply = _fallback_chat_reply(body.message, context)
        source = "heuristic"
    return {"reply": reply, "source": source}


# ---------------------------------------------------------------------------
# 3. Statistical anomaly detection (rolling z-score)
# ---------------------------------------------------------------------------

@router.get("/anomalies")
async def anomalies(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    hours: int = 6,
    z_threshold: float = 2.5,
):
    """
    Flags symbols whose latest price is an outlier (|z-score| above
    `z_threshold`) relative to their own recent rolling mean/std -- a
    lightweight, fully local anomaly-detection technique that needs no
    external model and runs in milliseconds.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        text(
            """
            SELECT a.symbol, p.price, p.event_time
            FROM price_ticks p JOIN assets a ON a.id = p.asset_id
            WHERE p.event_time >= :since
            ORDER BY a.symbol, p.event_time ASC
            """
        ),
        {"since": since},
    )
    by_symbol: dict[str, list[float]] = {}
    for row in result:
        by_symbol.setdefault(row.symbol, []).append(float(row.price))

    flagged = []
    for symbol, prices in by_symbol.items():
        if len(prices) < 5:
            continue
        history, latest = prices[:-1], prices[-1]
        mean = statistics.fmean(history)
        stdev = statistics.pstdev(history)
        if stdev == 0:
            continue
        z = (latest - mean) / stdev
        if abs(z) >= z_threshold:
            flagged.append({
                "symbol": symbol,
                "latest_price": round(latest, 4),
                "rolling_mean": round(mean, 4),
                "z_score": round(z, 2),
                "direction": "spike_up" if z > 0 else "spike_down",
            })

    return {"window_hours": hours, "z_threshold": z_threshold, "flagged": flagged}
