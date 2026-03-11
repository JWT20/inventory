"""Auth endpoints: login, token refresh, and user management.

Login uses FastAPI-Users' UserManager.authenticate() for username-based auth.
Access tokens are created via FastAPI-Users' JWTStrategy; refresh tokens are
handled by our custom logic in auth.py.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import (
    _check_rate_limit,
    _clear_failed_attempts,
    _record_failed_attempt,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.database import get_db
from app.events import publish_event
from app.models import User
from app.schemas import (
    LoginRequest,
    RefreshRequest,
    RefreshResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.users import UserManager, get_jwt_strategy, get_user_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    user_manager: UserManager = Depends(get_user_manager),
):
    # Rate limit by IP + username combination
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{body.username}"
    _check_rate_limit(rate_key)

    # Build a credentials-like object for UserManager.authenticate()
    class _Creds:
        username = body.username
        password = body.password

    user = await user_manager.authenticate(_Creds())

    if user is None:
        _record_failed_attempt(rate_key)
        publish_event(
            "user_login",
            details={"username": body.username, "success": False},
            resource_type="auth",
        )
        raise HTTPException(401, "Incorrect username or password")

    if not user.is_active:
        raise HTTPException(403, "Account is disabled")

    _clear_failed_attempts(rate_key)

    # Create access token via FastAPI-Users' JWT strategy
    strategy = get_jwt_strategy()
    access_token = await strategy.write_token(user)

    publish_event(
        "user_login",
        details={"username": user.username, "success": True},
        user=user,
        resource_type="auth",
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=create_refresh_token(user.id),
        username=user.username,
        role=user.role,
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """Exchange a valid refresh token for a new access token."""
    user_id = decode_refresh_token(body.refresh_token)
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    return RefreshResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse)
def get_me(user: User = Depends(get_current_user)):
    return user


# --- Admin-only user management ---

@router.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, f"Username '{data.username}' already taken")
    user = User(
        username=data.username,
        email=f"{data.username}@local",
        hashed_password=hash_password(data.password),
        role=data.role,
        is_superuser=(data.role == "admin"),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Created user '%s'", user.username)
    publish_event(
        "user_created",
        details={"username": user.username, "role": user.role},
        user=admin,
        resource_type="user",
        resource_id=user.id,
    )
    return user


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    username = user.username
    db.delete(user)
    db.commit()
    logger.info("Deleted user '%s'", username)
    publish_event(
        "user_deleted",
        details={"username": username},
        user=admin,
        resource_type="user",
        resource_id=user_id,
    )
