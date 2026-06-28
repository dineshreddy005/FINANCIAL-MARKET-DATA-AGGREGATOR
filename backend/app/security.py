"""
Security & Performance defaults
================================
Security:
  - JWT bearer auth for write/ingest endpoints (read endpoints are public
    but still rate-limited). Tokens carry `uid` + `role` claims minted at
    login -- see app/rbac.py for how those claims are turned into
    enforced, per-endpoint access control (requirement 4).
  - Password hashing via PBKDF2-HMAC-SHA256 (stdlib `hashlib`, no extra
    dependency) -- 260k iterations, random 16-byte salt per password,
    constant-time comparison on verify.
  - Redis-backed sliding-window rate limiting per client IP/API key, so a
    single noisy client can't degrade the service for everyone else.
  - Passwords/secrets never logged; JWT secret loaded from env only.

Performance:
  - Rate limiter and circuit breaker both use Redis so they work correctly
    across multiple uvicorn/gunicorn worker processes, not just in one
    process's memory.
"""
import binascii
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.cache import get_redis
from app.config import get_settings

settings = get_settings()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """`pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>` -- self-describing
    so the iteration count can be bumped later without breaking old hashes."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${binascii.hexlify(salt).decode()}${binascii.hexlify(dk).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_s, salt_hex, hash_hex = stored_hash.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = binascii.unhexlify(salt_hex)
        expected = binascii.unhexlify(hash_hex)
    except (ValueError, binascii.Error):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def create_access_token(
    subject: str,
    *,
    user_id: int,
    role: str,
    expires_minutes: Optional[int] = None,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "uid": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_subject(token: Optional[str] = Depends(oauth2_scheme)) -> str:
    """Legacy dependency kept for any endpoint that only needs an identity
    string, not a verified role -- prefer `app.rbac.get_current_user` /
    `require_role(...)` for anything that gates access or shapes a response."""
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        subject = payload.get("sub")
        if subject is None:
            raise JWTError("missing subject")
        return subject
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc


async def rate_limit_dependency(request: Request) -> None:
    """
    Sliding-window-ish rate limit using a fixed-window counter in Redis.
    Cheap (one INCR + one EXPIRE) and good enough for protecting our API;
    swap for a token-bucket Lua script if you need smoother limiting later.
    """
    redis_client = await get_redis()
    client_id = request.headers.get("x-api-key") or (request.client.host if request.client else "anon")
    key = f"ratelimit:{client_id}:{int(datetime.now().timestamp() // 60)}"  # one bucket per minute
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 60)
    if count > settings.rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit exceeded: {settings.rate_limit_per_minute} requests/minute",
        )
