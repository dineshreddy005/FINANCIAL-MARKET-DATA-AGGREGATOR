"""
Authentication against the real `users` table (requirement 4).

Login is the ONE place a role claim is allowed to originate: the server
looks the username up in Postgres, verifies the password hash, and mints a
JWT carrying the role it found in the database -- never a role the client
asserted. Everything downstream (`app/rbac.py`) treats that signed claim as
ground truth and never re-checks the database on a hot path, which is what
keeps RBAC cheap to enforce on every request.
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.rbac import CurrentUser, get_current_user
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """
    OAuth2-password-flow shaped (so Swagger's "Authorize" button works out
    of the box), but backed by the real `users` table instead of a shared
    secret. Works for human roles (admin/client) and the `ingest-svc`
    machine account alike -- a JWT's role claim is what gates access, not
    which login path issued it.
    """
    result = await db.execute(
        text("SELECT id, username, password_hash, role, is_active FROM users WHERE username = :u"),
        {"u": form.username},
    )
    row = result.mappings().first()
    if row is None or not row["is_active"] or not verify_password(form.password, row["password_hash"]):
        raise HTTPException(401, "Invalid username or password")

    await db.execute(
        text("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = :id"), {"id": row["id"]}
    )
    await db.commit()

    token = create_access_token(subject=row["username"], user_id=row["id"], role=row["role"])
    return {"access_token": token, "token_type": "bearer", "role": row["role"], "username": row["username"]}


@router.get("/me")
async def me(current_user: CurrentUser = Depends(get_current_user)):
    """Lets the frontend confirm who the verified token actually belongs to
    (role badge, account name) without trusting anything it stored locally."""
    return {"user_id": current_user.user_id, "username": current_user.username, "role": current_user.role.value}
