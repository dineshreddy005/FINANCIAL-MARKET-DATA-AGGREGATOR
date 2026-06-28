"""
Client accounts & holdings -- the concrete demonstration surface for
requirement 4 (masking) and the "manual adjustment by an administrator"
half of requirement 5 (audit trail).

Endpoints:
  GET   /api/accounts/me        any authenticated role; own account, masked
                                 per role (admin sees it unmasked too -- the
                                 mask function is a no-op for admin).
  GET   /api/accounts           admin-only; every account, unmasked.
  GET   /api/accounts/{id}      admin: full detail. client: only their own,
                                 masked, 404 for anyone else's (zero trust:
                                 a client probing other IDs learns nothing).
  PATCH /api/accounts/{id}      admin-only manual balance/detail adjustment
                                 -- triggers the Postgres audit trigger via
                                 set_audit_actor(), so the resulting
                                 audit_logs row is stamped with the real
                                 admin's username, not a generic "system".
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_audit_actor
from app.masking import mask_record
from app.rbac import CurrentUser, Role, get_current_user, require_admin

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_ACCOUNT_QUERY = """
    SELECT ca.id, ca.user_id, u.username, u.full_name, ca.account_number, ca.routing_number,
           ca.broker_name, ca.account_type, ca.cash_balance, ca.created_at, ca.updated_at
    FROM client_accounts ca
    JOIN users u ON u.id = ca.user_id
"""

_HOLDINGS_QUERY = """
    SELECT h.asset_id, a.symbol, a.asset_type, h.quantity, h.avg_cost
    FROM account_holdings h
    JOIN assets a ON a.id = h.asset_id
    WHERE h.account_id = :account_id
"""


async def _holdings_for(db: AsyncSession, account_id: int) -> list[dict]:
    result = await db.execute(text(_HOLDINGS_QUERY), {"account_id": account_id})
    return [dict(r._mapping) for r in result]


@router.get("/me")
async def my_account(current_user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(text(_ACCOUNT_QUERY + " WHERE ca.user_id = :uid"), {"uid": current_user.user_id})
    row = result.mappings().first()
    if row is None:
        raise HTTPException(404, "No brokerage account on file for this user")
    account = mask_record(dict(row), role=current_user.role)
    account["holdings"] = await _holdings_for(db, row["id"])
    return account


@router.get("")
async def list_accounts(current_user: CurrentUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Admin-only roster -- intentionally unmasked, this IS the privileged view."""
    result = await db.execute(text(_ACCOUNT_QUERY + " ORDER BY ca.id"))
    return [dict(r._mapping) for r in result]  # role is already verified ADMIN; no masking applied


@router.get("/{account_id}")
async def get_account(
    account_id: int,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(text(_ACCOUNT_QUERY + " WHERE ca.id = :id"), {"id": account_id})
    row = result.mappings().first()
    if row is None:
        raise HTTPException(404, "Account not found")

    # Zero trust applied at the ownership boundary too: a client role can
    # only ever resolve their OWN account id. Returning 404 (not 403) for
    # someone else's account avoids confirming that an id exists at all.
    if current_user.role == Role.CLIENT and row["user_id"] != current_user.user_id:
        raise HTTPException(404, "Account not found")

    account = mask_record(dict(row), role=current_user.role)
    account["holdings"] = await _holdings_for(db, account_id)
    return account


class AccountAdjustment(BaseModel):
    cash_balance: Optional[float] = Field(default=None, description="New cash balance, if adjusting it")
    account_type: Optional[str] = Field(default=None, pattern="^(individual|ira|corporate)$")
    reason: str = Field(..., min_length=3, description="Required compliance note for the audit trail")


@router.patch("/{account_id}")
async def adjust_account(
    account_id: int,
    body: AccountAdjustment,
    current_user: CurrentUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Manual administrator adjustment -- the second source of audit-logged
    writes required by requirement 5. `set_audit_actor` stamps this
    transaction with the admin's username BEFORE the UPDATE runs, so
    `fn_audit_log()` (sql/schema.sql) records exactly who made the change,
    alongside the full old/new row as JSONB.
    """
    if body.cash_balance is None and body.account_type is None:
        raise HTTPException(400, "Provide at least one field to adjust (cash_balance or account_type)")

    await set_audit_actor(db, current_user.username)

    fields, params = [], {"id": account_id}
    if body.cash_balance is not None:
        fields.append("cash_balance = :cash_balance")
        params["cash_balance"] = body.cash_balance
    if body.account_type is not None:
        fields.append("account_type = :account_type")
        params["account_type"] = body.account_type
    fields.append("updated_at = CURRENT_TIMESTAMP")

    result = await db.execute(
        text(f"UPDATE client_accounts SET {', '.join(fields)} WHERE id = :id RETURNING id"),
        params,
    )
    if result.first() is None:
        await db.rollback()
        raise HTTPException(404, "Account not found")
    await db.commit()

    return {"status": "adjusted", "account_id": account_id, "adjusted_by": current_user.username, "reason": body.reason}
