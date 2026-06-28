"""
Requirement 5: Comprehensive Audit Trail & Temporal Logging -- the read
side. The writes happen entirely in Postgres triggers (sql/schema.sql,
`fn_audit_log()`); this router just exposes them, admin-only, for the
compliance/dashboard view. Nothing here ever writes to audit_logs -- it is
intentionally append-only and immutable from the API's perspective.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.rbac import CurrentUser, require_admin

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/logs")
async def list_audit_logs(
    table_name: Optional[str] = Query(default=None, description="Filter by table, e.g. price_ticks"),
    changed_by: Optional[str] = Query(default=None, description="Filter by acting user/service id"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = """
        SELECT id, table_name, record_id, operation, changed_by, old_data, new_data, changed_at
        FROM audit_logs
        WHERE (:table_name IS NULL OR table_name = :table_name)
          AND (:changed_by IS NULL OR changed_by = :changed_by)
        ORDER BY changed_at DESC
        LIMIT :limit OFFSET :offset
    """
    result = await db.execute(
        text(query),
        {"table_name": table_name, "changed_by": changed_by, "limit": limit, "offset": offset},
    )
    return [dict(r._mapping) for r in result]


@router.get("/logs/{record_table}/{record_id}")
async def record_history(
    record_table: str,
    record_id: str,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Full temporal history for one specific row -- "what did this record
    look like at every point in time" -- answering exactly the compliance
    question requirement 5 calls out."""
    query = """
        SELECT id, operation, changed_by, old_data, new_data, changed_at
        FROM audit_logs
        WHERE table_name = :table_name AND record_id = :record_id
        ORDER BY changed_at ASC
    """
    result = await db.execute(text(query), {"table_name": record_table, "record_id": record_id})
    return [dict(r._mapping) for r in result]


@router.get("/summary")
async def audit_summary(current_user: CurrentUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Quick counts for the dashboard's compliance panel header."""
    query = """
        SELECT table_name, operation, count(*) AS event_count, max(changed_at) AS last_event
        FROM audit_logs
        GROUP BY table_name, operation
        ORDER BY table_name, operation
    """
    result = await db.execute(text(query))
    return [dict(r._mapping) for r in result]
