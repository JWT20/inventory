"""Authentication: password hashing, JWT tokens (access + refresh), FastAPI dependencies."""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per-process)
# ---------------------------------------------------------------------------
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5 minutes

_failed_attempts: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str) -> None:
    """Raise 429 if too many failed login attempts within the lockout window."""
    now = time.monotonic()
    # Prune old entries
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
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return pwd_context.hash(plain[:72])


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain[:72], hashed)


# ---------------------------------------------------------------------------
# JWT tokens (access + refresh)
# ---------------------------------------------------------------------------
def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "type": "access"},
        settings.secret_key,
        algorithm="HS256",
    )


def create_refresh_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "type": "refresh"},
        settings.secret_key,
        algorithm="HS256",
    )


def _decode_token(token: str, expected_type: str) -> int:
    """Decode a JWT and return the user_id. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        user_id = payload.get("sub")
        token_type = payload.get("type", "access")
        if user_id is None or token_type != expected_type:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        return int(user_id)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


# Backwards compatibility alias
def create_token(user_id: int) -> str:
    return create_access_token(user_id)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """Verify access token → return User."""
    user_id = _decode_token(token, "access")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Must be admin."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def require_warehouse(user: User = Depends(get_current_user)) -> User:
    """Must be admin or courier (warehouse workers who scan & book)."""
    if user.role not in ("admin", "courier"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Warehouse access required")
    return user


def require_product_manager(user: User = Depends(get_current_user)) -> User:
    """Must be admin or merchant."""
    if not user.can_manage_products:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Merchant or admin access required")
    return user
