"""Auth endpoints: login, token refresh, and user management.

Login uses FastAPI-Users' UserManager.authenticate() for username-based auth.
Access tokens are created via FastAPI-Users' JWTStrategy; refresh tokens are
handled by our custom logic in auth.py.
"""

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import (
    _check_rate_limit,
    _clear_failed_attempts,
    _record_failed_attempt,
    create_access_token_for_user,
    create_refresh_token,
    decode_refresh_token,
    get_current_user,
    hash_password,
    require_admin,
    revoke_refresh_token,
    verify_password,
)
from app.database import get_db
from app.events import publish_event
from app.models import User
from app.schemas import (
    AdminResetPassword,
    ChangeOwnPassword,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RefreshResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.users import UserManager, get_user_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@dataclass(frozen=True, slots=True)
class _LoginCredentials:
    """Adapter between our JSON login body and UserManager.authenticate()."""
    username: str
    password: str


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

    user = await user_manager.authenticate(
        _LoginCredentials(username=body.username, password=body.password)
    )

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

    # Create access token via FastAPI-Users' JWT strategy (includes exp)
    access_token = await create_access_token_for_user(user)

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
async def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    """Exchange a valid refresh token for new access + refresh tokens (rotation)."""
    user_id, jti, exp = decode_refresh_token(body.refresh_token)
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or inactive")
    # Revoke the old refresh token
    revoke_refresh_token(jti, exp)
    access_token = await create_access_token_for_user(user)
    new_refresh_token = create_refresh_token(user.id)
    return RefreshResponse(access_token=access_token, refresh_token=new_refresh_token)


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
    # Prevent deleting the last admin
    if user.role == "admin":
        admin_count = db.query(User).filter(
            User.role == "admin", User.is_active == True  # noqa: E712
        ).count()
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the last admin account")
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


# --- Password management ---

@router.put("/users/{user_id}/password", status_code=204)
def admin_reset_password(
    user_id: int,
    data: AdminResetPassword,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Admin resets any user's password."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.hashed_password = hash_password(data.new_password)
    db.commit()
    logger.info("Admin '%s' reset password for '%s'", admin.username, user.username)
    publish_event(
        "password_reset",
        details={"target_username": user.username},
        user=admin,
        resource_type="user",
        resource_id=user.id,
    )


@router.put("/me/password", status_code=204)
def change_own_password(
    data: ChangeOwnPassword,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Authenticated user changes their own password."""
    if not verify_password(data.current_password, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    user.hashed_password = hash_password(data.new_password)
    db.commit()
    logger.info("User '%s' changed their password", user.username)
    publish_event(
        "password_changed",
        details={"username": user.username},
        user=user,
        resource_type="user",
        resource_id=user.id,
    )


# --- Logout ---

@router.post("/logout", status_code=204)
def logout(
    data: LogoutRequest,
    user: User = Depends(get_current_user),
):
    """Revoke the refresh token server-side."""
    try:
        _user_id, jti, exp = decode_refresh_token(data.refresh_token)
        revoke_refresh_token(jti, exp)
    except HTTPException:
        pass  # Token already invalid/expired — still clear client-side
    logger.info("User '%s' logged out", user.username)
    publish_event(
        "user_logout",
        details={"username": user.username},
        user=user,
        resource_type="auth",
    )
