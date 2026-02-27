"""Authentication endpoints: login, register, token refresh, user management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
    _decode_token,
)
from app.database import get_db
from app.models import User
from app.schemas import (
    TokenRefreshRequest,
    TokenResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """Authenticate with username + password, receive access & refresh tokens."""
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )
    logger.info("User '%s' logged in", user.username)
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(body: TokenRefreshRequest, db: Session = Depends(get_db)):
    """Exchange a valid refresh token for a new access + refresh token pair."""
    payload = _decode_token(body.refresh_token, "refresh")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    user = db.get(User, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
    )


# ---------------------------------------------------------------------------
# Authenticated endpoints
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user


# ---------------------------------------------------------------------------
# Admin-only user management
# ---------------------------------------------------------------------------
@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Create a new user (admin only)."""
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, f"Username '{data.username}' already exists")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, f"Email '{data.email}' already registered")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info("Admin created user '%s' with role '%s'", user.username, user.role)
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Update a user's email, role, or active status (admin only)."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    db.refresh(user)
    logger.info("Admin updated user '%s'", user.username)
    return user
