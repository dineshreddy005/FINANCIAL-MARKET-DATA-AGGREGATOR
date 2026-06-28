"""
Requirement 4: Field Masking (the response-shaping half of zero trust)
========================================================================
Backend string-manipulation filters, applied right before a response leaves
the API -- never in the frontend, never optional, never bypassable by a
client that simply doesn't render a field. If the verified role on the
request's JWT isn't 'admin', sensitive identifiers are masked in-place
before the response model is even constructed.

Masking happens server-side and unconditionally for any non-admin role --
including a client looking at their OWN account -- because the contract this
module enforces is "what does THIS verified role get to see over the wire",
not "what does this person already know". That's what makes it zero-trust
rather than ownership-based access control.
"""
from __future__ import annotations

from typing import Any

from app.rbac import Role

# Field names treated as sensitive identifiers anywhere they appear in an
# API response. Add to this set as new sensitive columns are introduced --
# masking is opt-out by name, not opt-in per-endpoint, so a forgotten
# endpoint can't accidentally leak a field that's supposed to be protected.
SENSITIVE_FIELDS = {"account_number", "routing_number", "ssn", "tax_id"}


def mask_value(value: str | None, *, keep_last: int = 4) -> str | None:
    """'4400123456789104' -> '************9104'. Short/empty values are
    fully masked rather than reflecting their own length back to the caller
    (which would itself leak information)."""
    if not value:
        return value
    value = str(value)
    if len(value) <= keep_last:
        return "*" * len(value)
    return "*" * (len(value) - keep_last) + value[-keep_last:]


def mask_record(record: dict[str, Any], *, role: Role) -> dict[str, Any]:
    """Returns a shallow-masked copy of `record` for the given role. Admins
    get the record untouched; every other role gets sensitive fields
    redacted to a last-4 display string."""
    if role == Role.ADMIN:
        return record
    masked = dict(record)
    for field in SENSITIVE_FIELDS:
        if field in masked:
            masked[field] = mask_value(masked[field])
    return masked


def mask_records(records: list[dict[str, Any]], *, role: Role) -> list[dict[str, Any]]:
    return [mask_record(r, role=role) for r in records]
