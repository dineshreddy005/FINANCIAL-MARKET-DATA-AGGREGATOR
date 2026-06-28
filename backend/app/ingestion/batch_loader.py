"""
Multi-Format Data Ingestion Engine -- batch half.
===================================================
Accepts EOD historical pricing as CSV or JSON. `pandas` does the heavy
lifting of parsing arbitrary column layouts; every row is normalized via
the SAME `normalize_eod_row` used by the streaming path's underlying
schema, then handed to the SAME idempotent `upsert_ticks` -- so re-uploading
the identical file twice (a common real-world accident) is a safe no-op.
"""
from __future__ import annotations

import io
import json
import logging

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.normalizer import normalize_eod_row, upsert_ticks, UpsertStats
from app.schemas import NormalizedTick

logger = logging.getLogger("fmda.batch")


def _read_csv(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content))


def _read_json(content: bytes) -> pd.DataFrame:
    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("data", data.get("records", [data]))
    return pd.json_normalize(data)


async def ingest_eod_file(session: AsyncSession, *, filename: str, content: bytes, actor: str | None = None) -> UpsertStats:
    if filename.lower().endswith(".csv"):
        df = _read_csv(content)
        fmt = "eod_csv"
    elif filename.lower().endswith(".json"):
        df = _read_json(content)
        fmt = "eod_json"
    else:
        raise ValueError("Unsupported file type -- expected .csv or .json")

    # Drop fully-empty rows/columns that pandas sometimes produces from
    # trailing newlines or BOM characters in vendor exports.
    df = df.dropna(how="all")

    ticks: list[NormalizedTick] = []
    errors = 0
    for _, row in df.iterrows():
        try:
            ticks.append(normalize_eod_row(row.to_dict(), fmt=fmt))
        except Exception as exc:  # noqa: BLE001 -- one bad row shouldn't kill the batch
            errors += 1
            logger.warning("skipping malformed row in %s: %s", filename, exc)

    if errors:
        logger.warning("%s: %d/%d rows failed normalization", filename, errors, len(df))

    return await upsert_ticks(session, ticks, actor=actor or f"batch:{fmt}")
