"""
Requirement 4: Zero-Trust Access Control (RBAC half)
=====================================================
"Zero trust" here means two concrete things:

  1. The API never trusts a role/identity claim that didn't come from a
     signed JWT minted by THIS server at login (app/routers/auth.py). The
     frontend can render whatever it wants, but every protected endpoint
     re-derives `CurrentUser` from the verified token on every request --
     there is no server-side session, no "trust the last request" shortcut.

  2. Authorization is enforced server-side, per-endpoint, via explicit
     `Depends(require_role(...))` declarations -- never inferred from what
     the client *says* it is, and never left to the frontend to hide a
     button. A client-role JWT pointed straight at curl gets exactly the
     same masking and 403s as it would through the dashboard.

The companion module, app/masking.py, handles the *field-level* half of
zero trust: even endpoints both roles can call return different payloads
depending on the verified role.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from fastapi import Depends, HTTPException, status
from jose import JWTError, jwt

from app.config import get_settings
from app.security import oauth2_scheme

settings = get_settings()


class Role(str, Enum):
    ADMIN = "admin"
    CLIENT = "client"
    SERVICE = "service"  # machine-to-machine credential for the ingest pipeline


@dataclass(frozen=True)
class CurrentUser:
    user_id: int
    username: str
    role: Role


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> CurrentUser:
    """
    Decodes and verifies the bearer JWT, returning the caller's identity and
    role exactly as the server signed them at login. This is the ONE place
    a role claim is allowed to exist in the system.
    """
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = payload.get("uid")
        username = payload.get("sub")
        role = payload.get("role")
        if user_id is None or username is None or role not in Role.__members__.values():
            raise JWTError("malformed claims")
        return CurrentUser(user_id=int(user_id), username=username, role=Role(role))
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc


def require_role(*allowed: Role):
    """
    Dependency factory: `Depends(require_role(Role.ADMIN))` rejects every
    caller whose verified role isn't in `allowed`, with a 403 (not a 404 --
    we don't hide the existence of admin endpoints, we deny them outright,
    which is the more honest zero-trust posture for an audited system).
    """
    allowed_set: Iterable[Role] = allowed

    async def _dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in allowed_set:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{current_user.role.value}' is not permitted to access this resource",
            )
        return current_user

    return _dependency


# Convenience dependency for "is this either a logged-in human role" (admin or
# client) -- used by read endpoints that are shared but field-masked rather
# than role-gated outright.
require_authenticated = require_role(Role.ADMIN, Role.CLIENT)
require_admin = require_role(Role.ADMIN)
