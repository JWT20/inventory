"""Authentication helpers: rate limiting, refresh tokens, role-based dependencies.

Access-token creation & validation is handled by FastAPI-Users (see users.py).
This module keeps:
 - in-memory login rate limiting
 - refresh-token creation / validation (FastAPI-Users does not support refresh
   tokens out of the box)
 - role-based FastAPI dependencies (require_admin, require_warehouse, …)
"""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi_users.password import PasswordHelper

from app.config import settings
from app.models import User
from app.users import current_active_user, get_jwt_strategy

logger = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per-process)
# ---------------------------------------------------------------------------
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes

_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str) -> None:
    """Raise 429 if too many failed login attempts within the lockout window."""
    now = time.monotonic()
    _failed_attempts[key] = [t for t in _failed_attempts[key] if now - t < LOCKOUT_SECONDS]
    if len(_failed_attempts[key]) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Te veel inlogpogingen. Probeer opnieuw over {LOCKOUT_SECONDS // 60} minuten.",
        )


def _record_failed_attempt(key: str) -> None:
    _failed_attempts[key].append(time.monotonic())


def _clear_failed_attempts(key: str) -> None:
    _failed_attempts.pop(key, None)


# ---------------------------------------------------------------------------
# Refresh tokens (custom — FastAPI-Users only handles access tokens)
# ---------------------------------------------------------------------------
def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "type": "refresh"},
        settings.secret_key,
        algorithm=_JWT_ALGORITHM,
    )


def decode_refresh_token(token: str) -> int:
    """Decode a refresh JWT and return the user_id. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[_JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type"]},
        )
        user_id = payload.get("sub")
        token_type = payload.get("type", "")
        if user_id is None or token_type != "refresh":
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
        return int(user_id)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")


# ---------------------------------------------------------------------------
# Password hashing (delegates to FastAPI-Users' PasswordHelper)
# ---------------------------------------------------------------------------
_password_helper = PasswordHelper()


def hash_password(plain: str) -> str:
    return _password_helper.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    verified, _ = _password_helper.verify_and_update(plain, hashed)
    return verified


# ---------------------------------------------------------------------------
# FastAPI dependencies — role guards
# ---------------------------------------------------------------------------
def get_current_user(user: User = Depends(current_active_user)) -> User:
    """Thin wrapper so existing ``Depends(get_current_user)`` calls still work."""
    return user


def require_admin(user: User = Depends(current_active_user)) -> User:
    """Must be admin."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def require_warehouse(user: User = Depends(current_active_user)) -> User:
    """Must be admin or courier (warehouse workers who scan & book)."""
    if user.role not in ("admin", "courier"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Warehouse access required")
    return user


def require_product_manager(user: User = Depends(current_active_user)) -> User:
    """Must be admin or merchant."""
    if not user.can_manage_products:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Merchant or admin access required")
    return user


# ---------------------------------------------------------------------------
# Token helpers (used by refresh endpoint + test fixtures)
# ---------------------------------------------------------------------------
async def create_access_token_for_user(user: User) -> str:
    """Create an access token using FastAPI-Users' JWTStrategy (includes exp)."""
    strategy = get_jwt_strategy()
    return await strategy.write_token(user)


def create_token(user_id: int) -> str:
    """Create an access token. Used by test fixtures only.

    Mirrors what ``JWTStrategy.write_token`` produces so the token is accepted
    by the ``current_active_user`` dependency during test runs.
    """
    from fastapi_users.jwt import generate_jwt

    strategy = get_jwt_strategy()
    return generate_jwt(
        {"sub": str(user_id), "aud": strategy.token_audience},
        strategy.encode_key,
        strategy.lifetime_seconds,
        algorithm=strategy.algorithm,
    )
