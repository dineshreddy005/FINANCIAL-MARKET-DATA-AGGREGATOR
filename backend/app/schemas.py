"""
The canonical internal data model.

This is the single shape that yfinance streaming quotes, CoinGecko REST
payloads, and EOD batch CSV/JSON files all get mapped into BEFORE they touch
the database. Nothing downstream (storage, API responses, websocket
broadcast) ever has to know which source a tick came from.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


AssetType = Literal["equity", "crypto", "fx", "index"]
SourceName = Literal["yfinance", "coingecko", "finnhub", "eod_csv", "eod_json"]


class NormalizedTick(BaseModel):
    """One standardized price observation, regardless of where it came from."""

    symbol: str = Field(..., description="Canonical upper-cased ticker, e.g. AAPL, BTC")
    asset_type: AssetType
    source: SourceName
    event_time: datetime = Field(..., description="Timestamp carried by the data itself")
    price: Decimal
    volume: Optional[Decimal] = None
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    currency: str = "USD"

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("price")
    @classmethod
    def positive_price(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("price must be positive")
        return v


class TickOut(BaseModel):
    """What the API/websocket sends back to clients."""
    symbol: str
    asset_type: str
    source: str
    event_time: datetime
    price: Decimal
    volume: Optional[Decimal] = None

    class Config:
        from_attributes = True


class IngestBatchResult(BaseModel):
    rows_received: int
    rows_inserted: int
    rows_updated: int
    rows_deduped: int
    status: str
