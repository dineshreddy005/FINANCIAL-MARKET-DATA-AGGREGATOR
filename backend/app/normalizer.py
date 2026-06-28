"""
Idempotent Normalization Pipeline
==================================
Every ingestion path (websocket stream, CoinGecko poller, EOD batch upload)
ends here. Two guarantees this module exists to provide:

1. NORMALIZATION: raw, source-specific payloads (yfinance's `regularMarketPrice`,
   CoinGecko's `current_price`, a CSV's `Close`/`Adj Close`) are mapped into a
   single `NormalizedTick` shape (see schemas.py) before they ever touch SQL.

2. IDEMPOTENCY: re-processing the exact same payload twice (a websocket
   reconnect that re-sends the last tick, a retried HTTP upload, an
   at-least-once queue redelivery) must not create duplicate rows and must
   not corrupt data with stale values arriving out of order.

   We get this from a Postgres UPSERT keyed on the natural key
   (asset_id, source, event_time):
     - If no row exists for that key -> INSERT.
     - If a row exists with the SAME payload_hash -> DO NOTHING (it's a
       byte-for-byte retry of something we already applied).
     - If a row exists with a DIFFERENT payload_hash -> DO UPDATE (it's a
       legitimate correction/revision for the same point in time, e.g. a
       vendor restating EOD close after hours).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Iterable, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_audit_actor
from app.schemas import NormalizedTick

logger = logging.getLogger("fmda.normalizer")


# ---------------------------------------------------------------------------
# Source-specific normalizers -- one function per upstream shape. Adding a
# new data source means adding one function here, nothing else changes.
# ---------------------------------------------------------------------------

def normalize_yfinance_quote(raw: dict) -> NormalizedTick:
    """raw is one yfinance `fast_info`/ticker.info-style dict for one symbol."""
    return NormalizedTick(
        symbol=raw["symbol"],
        asset_type="equity",
        source="yfinance",
        event_time=raw["timestamp"],
        price=raw["last_price"],
        volume=raw.get("volume"),
        open=raw.get("open"),
        high=raw.get("day_high"),
        low=raw.get("day_low"),
        currency=raw.get("currency", "USD"),
    )


def normalize_coingecko_quote(raw: dict) -> NormalizedTick:
    """raw is one element of CoinGecko's /coins/markets response."""
    return NormalizedTick(
        symbol=raw["symbol"],
        asset_type="crypto",
        source="coingecko",
        event_time=raw["last_updated"],
        price=raw["current_price"],
        volume=raw.get("total_volume"),
        high=raw.get("high_24h"),
        low=raw.get("low_24h"),
        currency="USD",
    )


def normalize_eod_row(row: dict, *, fmt: Literal["eod_csv", "eod_json"] = "eod_csv") -> NormalizedTick:
    """
    A single row from an EOD batch file. Handles the common column-name
    variants seen across free EOD providers (Date/date, Close/close/Adj Close).
    """
    def pick(*keys):
        for k in keys:
            if k in row and row[k] not in (None, ""):
                return row[k]
        return None

    return NormalizedTick(
        symbol=pick("Symbol", "symbol", "Ticker"),
        asset_type=row.get("asset_type", "equity"),
        source=fmt,  # type: ignore[arg-type]
        event_time=pick("Date", "date", "Datetime"),
        price=pick("Close", "close", "Adj Close", "adj_close"),
        volume=pick("Volume", "volume"),
        open=pick("Open", "open"),
        high=pick("High", "high"),
        low=pick("Low", "low"),
        close=pick("Close", "close"),
        currency=row.get("currency", "USD"),
    )


def _payload_hash(tick: NormalizedTick) -> str:
    """Stable hash of the normalized payload -- used to detect true duplicates."""
    canonical = (
        f"{tick.symbol}|{tick.source}|{tick.event_time.isoformat()}|{tick.price}|"
        f"{tick.volume}|{tick.open}|{tick.high}|{tick.low}|{tick.close}"
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class UpsertStats:
    received: int = 0
    inserted: int = 0
    updated: int = 0
    deduped: int = 0


async def _get_or_create_asset_id(session: AsyncSession, symbol: str, asset_type: str, currency: str) -> int:
    result = await session.execute(
        text("SELECT id FROM assets WHERE symbol = :symbol AND asset_type = :asset_type"),
        {"symbol": symbol, "asset_type": asset_type},
    )
    row = result.first()
    if row:
        return row[0]
    result = await session.execute(
        text(
            """
            INSERT INTO assets (symbol, asset_type, currency)
            VALUES (:symbol, :asset_type, :currency)
            ON CONFLICT (symbol, asset_type) DO UPDATE SET symbol = EXCLUDED.symbol
            RETURNING id
            """
        ),
        {"symbol": symbol, "asset_type": asset_type, "currency": currency},
    )
    return result.scalar_one()


UPSERT_SQL = text(
    """
    INSERT INTO price_ticks
        (asset_id, source, event_time, price, volume, open, high, low, close, payload_hash)
    VALUES
        (:asset_id, :source, :event_time, :price, :volume, :open, :high, :low, :close, :payload_hash)
    ON CONFLICT (asset_id, source, event_time)
    DO UPDATE SET
        price = EXCLUDED.price,
        volume = EXCLUDED.volume,
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        payload_hash = EXCLUDED.payload_hash,
        ingested_at = now()
    WHERE price_ticks.payload_hash IS DISTINCT FROM EXCLUDED.payload_hash
    RETURNING (xmax = 0) AS inserted
    """
)
# xmax = 0 is a Postgres trick: it's true only for the row version just
# created by THIS statement (a fresh INSERT), false when an UPDATE happened
# on an existing row. If the WHERE clause skips the write entirely (true
# duplicate), no row is returned at all -- which is how we detect "deduped".


from app.database import set_audit_actor, is_sqlite
from app.schemas import NormalizedTick

# ... (other imports/functions) ...

async def upsert_ticks(session: AsyncSession, ticks: Iterable[NormalizedTick], *, actor: str | None = None) -> UpsertStats:
    """
    `actor` identifies WHO/WHAT is performing this write for the audit
    trail (requirement 5) -- e.g. `feed:yfinance`, `feed:coingecko`,
    `batch:ops` (a human operator's username from the ingest JWT). It's
    derived from the first tick's source when not given explicitly, since
    every tick in one call comes from the same source/session.
    """
    stats = UpsertStats()
    asset_cache: dict[tuple[str, str], int] = {}

    ticks = list(ticks)
    if ticks:
        await set_audit_actor(session, actor or f"feed:{ticks[0].source}")

    for tick in ticks:
        stats.received += 1
        key = (tick.symbol, tick.asset_type)
        if key not in asset_cache:
            asset_cache[key] = await _get_or_create_asset_id(
                session, tick.symbol, tick.asset_type, tick.currency
            )
        asset_id = asset_cache[key]

        if is_sqlite:
            # Check existing payload_hash to count inserted vs updated vs deduped accurately
            result = await session.execute(
                text("SELECT id, payload_hash FROM price_ticks WHERE asset_id = :asset_id AND source = :source AND event_time = :event_time"),
                {"asset_id": asset_id, "source": tick.source, "event_time": tick.event_time}
            )
            row = result.first()
            new_hash = _payload_hash(tick)
            def to_float(val):
                return float(val) if val is not None else None

            if row is None:
                await session.execute(
                    text("""
                        INSERT INTO price_ticks (asset_id, source, event_time, price, volume, open, high, low, close, payload_hash, ingested_at)
                        VALUES (:asset_id, :source, :event_time, :price, :volume, :open, :high, :low, :close, :payload_hash, CURRENT_TIMESTAMP)
                    """),
                    {
                        "asset_id": asset_id,
                        "source": tick.source,
                        "event_time": tick.event_time,
                        "price": to_float(tick.price),
                        "volume": to_float(tick.volume),
                        "open": to_float(tick.open),
                        "high": to_float(tick.high),
                        "low": to_float(tick.low),
                        "close": to_float(tick.close),
                        "payload_hash": new_hash,
                    }
                )
                stats.inserted += 1
            else:
                existing_id, existing_hash = row[0], row[1]
                if existing_hash == new_hash:
                    stats.deduped += 1
                else:
                    await session.execute(
                        text("""
                            UPDATE price_ticks
                            SET price = :price, volume = :volume, open = :open, high = :high, low = :low, close = :close, payload_hash = :payload_hash, ingested_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {
                            "id": existing_id,
                            "price": to_float(tick.price),
                            "volume": to_float(tick.volume),
                            "open": to_float(tick.open),
                            "high": to_float(tick.high),
                            "low": to_float(tick.low),
                            "close": to_float(tick.close),
                            "payload_hash": new_hash,
                        }
                    )
                    stats.updated += 1
        else:
            result = await session.execute(
                UPSERT_SQL,
                {
                    "asset_id": asset_id,
                    "source": tick.source,
                    "event_time": tick.event_time,
                    "price": tick.price,
                    "volume": tick.volume,
                    "open": tick.open,
                    "high": tick.high,
                    "low": tick.low,
                    "close": tick.close,
                    "payload_hash": _payload_hash(tick),
                },
            )
            row = result.first()
            if row is None:
                stats.deduped += 1
            elif row[0] is True:
                stats.inserted += 1
            else:
                stats.updated += 1

    await session.commit()
    logger.info(
        "upsert complete: received=%s inserted=%s updated=%s deduped=%s",
        stats.received, stats.inserted, stats.updated, stats.deduped,
    )
    return stats
