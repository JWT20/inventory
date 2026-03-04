"""Auth endpoints: login and user management."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import (
    create_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from app.database import get_db
from app.events import publish_event
from app.models import User
from app.schemas import LoginRequest, TokenResponse, UserCreate, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        publish_event(
            "user_login",
            details={"username": body.username, "success": False},
            resource_type="auth",
        )
        raise HTTPException(401, "Incorrect username or password")
    if not user.is_active:
        raise HTTPException(403, "Account is disabled")
    publish_event(
        "user_login",
        details={"username": user.username, "success": True},
        user=user,
        resource_type="auth",
    )
    return TokenResponse(
        access_token=create_token(user.id),
        username=user.username,
        role=user.role,
    )


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
        password_hash=hash_password(data.password),
        role=data.role,
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
