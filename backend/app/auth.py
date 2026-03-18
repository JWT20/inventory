"""Authentication helpers: rate limiting, refresh tokens, role-based dependencies.

Access-token creation & validation is handled by FastAPI-Users (see users.py).
This module keeps:
 - in-memory login rate limiting (with optional Redis backend)
 - refresh-token creation / validation with rotation & revocation
 - password strength validation
 - role-based FastAPI dependencies (require_admin, require_warehouse, …)
"""

import abc
import logging
import re
import time
import uuid
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
# Password strength validation
# ---------------------------------------------------------------------------
def validate_password_strength(password: str) -> list[str]:
    """Return a list of violation messages. Empty list means the password is valid."""
    errors: list[str] = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least 1 uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least 1 lowercase letter")
    if not re.search(r"\d", password):
        errors.append("Password must contain at least 1 digit")
    return errors


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes


class RateLimiter(abc.ABC):
    @abc.abstractmethod
    def check(self, key: str) -> None:
        """Raise 429 if rate limit exceeded."""

    @abc.abstractmethod
    def record_failure(self, key: str) -> None:
        """Record a failed attempt."""

    @abc.abstractmethod
    def clear(self, key: str) -> None:
        """Clear failures for the key (e.g. on successful login)."""


class InMemoryRateLimiter(RateLimiter):
    def __init__(self) -> None:
        self._failed_attempts: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        now = time.monotonic()
        self._failed_attempts[key] = [
            t for t in self._failed_attempts[key] if now - t < LOCKOUT_SECONDS
        ]
        if len(self._failed_attempts[key]) >= MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Te veel inlogpogingen. Probeer opnieuw over {LOCKOUT_SECONDS // 60} minuten.",
            )

    def record_failure(self, key: str) -> None:
        self._failed_attempts[key].append(time.monotonic())

    def clear(self, key: str) -> None:
        self._failed_attempts.pop(key, None)


class RedisRateLimiter(RateLimiter):
    def __init__(self, redis_url: str) -> None:
        import redis as redis_lib
        self._redis = redis_lib.from_url(redis_url)

    def check(self, key: str) -> None:
        rkey = f"rate_limit:{key}"
        count = self._redis.get(rkey)
        if count is not None and int(count) >= MAX_LOGIN_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Te veel inlogpogingen. Probeer opnieuw over {LOCKOUT_SECONDS // 60} minuten.",
            )

    def record_failure(self, key: str) -> None:
        rkey = f"rate_limit:{key}"
        pipe = self._redis.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, LOCKOUT_SECONDS)
        pipe.execute()

    def clear(self, key: str) -> None:
        self._redis.delete(f"rate_limit:{key}")


def _create_rate_limiter() -> RateLimiter:
    if settings.redis_url:
        try:
            limiter = RedisRateLimiter(settings.redis_url)
            logger.info("Using Redis-backed rate limiter")
            return limiter
        except Exception:
            logger.warning("Failed to connect to Redis, falling back to in-memory rate limiter")
    return InMemoryRateLimiter()


_rate_limiter = _create_rate_limiter()

# Keep backward-compatible references used by conftest.py
_failed_attempts = (
    _rate_limiter._failed_attempts
    if isinstance(_rate_limiter, InMemoryRateLimiter)
    else defaultdict(list)
)


def _check_rate_limit(key: str) -> None:
    _rate_limiter.check(key)


def _record_failed_attempt(key: str) -> None:
    _rate_limiter.record_failure(key)


def _clear_failed_attempts(key: str) -> None:
    _rate_limiter.clear(key)


# ---------------------------------------------------------------------------
# Refresh token revocation (in-memory blocklist with TTL)
# ---------------------------------------------------------------------------
_revoked_tokens: dict[str, float] = {}  # jti → expiry timestamp


def _prune_revoked() -> None:
    """Remove expired entries from the blocklist."""
    now = datetime.now(timezone.utc).timestamp()
    expired = [jti for jti, exp in _revoked_tokens.items() if exp < now]
    for jti in expired:
        del _revoked_tokens[jti]


def revoke_refresh_token(jti: str, exp: float) -> None:
    """Add a token's jti to the blocklist until its expiry."""
    _prune_revoked()
    _revoked_tokens[jti] = exp


def _is_token_revoked(jti: str) -> bool:
    _prune_revoked()
    return jti in _revoked_tokens


# ---------------------------------------------------------------------------
# Refresh tokens (custom — FastAPI-Users only handles access tokens)
# ---------------------------------------------------------------------------
def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    return jwt.encode(
        {
            "sub": str(user_id),
            "exp": expire,
            "type": "refresh",
            "jti": str(uuid.uuid4()),
        },
        settings.secret_key,
        algorithm=_JWT_ALGORITHM,
    )


def decode_refresh_token(token: str) -> tuple[int, str, float]:
    """Decode a refresh JWT. Returns (user_id, jti, exp). Raises HTTPException on failure."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[_JWT_ALGORITHM],
            options={"require": ["exp", "sub", "type", "jti"]},
        )
        user_id = payload.get("sub")
        token_type = payload.get("type", "")
        jti = payload.get("jti", "")
        exp = payload.get("exp", 0)
        if user_id is None or token_type != "refresh" or not jti:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
        if _is_token_revoked(jti):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token has been revoked")
        return int(user_id), jti, float(exp)
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
