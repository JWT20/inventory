"""Auth endpoints: login, token refresh, and user management.

Login uses FastAPI-Users' UserManager.authenticate() for username-based auth.
Access tokens are created via FastAPI-Users' JWTStrategy; refresh tokens are
handled by our custom logic in auth.py.
"""

import json
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
from app.models import Customer, Organization, User
from app.schemas import (
    AdminResetPassword,
    ChangeOwnPassword,
    LoginRequest,
    LogoutRequest,
    OrganizationCreate,
    OrganizationResponse,
    OrganizationUpdate,
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


def _user_to_response(user: User, db: Session | None = None) -> UserResponse:
    # Resolve org name + custom_label: use relationship if already loaded, else query via db
    org_name = None
    org_slug = None
    custom_label = None
    if user.organization_id:
        try:
            if user.organization:
                org_name = user.organization.name
                org_slug = user.organization.slug
                custom_label = user.organization.custom_label
        except Exception:
            if db:
                org = db.get(Organization, user.organization_id)
                if org:
                    org_name = org.name
                    org_slug = org.slug
                    custom_label = org.custom_label
    # Resolve customer name
    customer_name = None
    if user.customer_id:
        try:
            customer_name = user.customer.name if user.customer else None
        except Exception:
            if db:
                cust = db.get(Customer, user.customer_id)
                customer_name = cust.name if cust else None
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_platform_admin=user.is_platform_admin,
        organization_id=user.organization_id,
        organization_name=org_name,
        organization_slug=org_slug,
        custom_label=custom_label,
        customer_id=user.customer_id,
        customer_name=customer_name,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
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

    # Resolve organization name + custom_label via sync session (async lazy-load not possible)
    org_name = None
    org_slug = None
    custom_label = None
    if user.organization_id:
        org = db.get(Organization, user.organization_id)
        if org:
            org_name = org.name
            org_slug = org.slug
            custom_label = org.custom_label

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
        is_platform_admin=user.is_platform_admin,
        organization_id=user.organization_id,
        organization_name=org_name,
        organization_slug=org_slug,
        custom_label=custom_label,
        customer_id=user.customer_id,
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
def get_me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _user_to_response(user, db)


# --- Admin-only user management ---

@router.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.username).all()
    return [_user_to_response(u, db) for u in users]


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, f"Username '{data.username}' already taken")

    # Validate organization_id for org-bound roles
    if data.role in ("owner", "member", "customer"):
        if not data.organization_id:
            raise HTTPException(400, f"Role '{data.role}' requires an organization_id")
        org = db.get(Organization, data.organization_id)
        if not org:
            raise HTTPException(404, f"Organization with id {data.organization_id} not found")
    elif data.role == "courier":
        if data.organization_id:
            raise HTTPException(400, "Couriers cannot be linked to an organization")

    # Validate customer_id for customer role
    customer_id = None
    if data.role == "customer" and data.customer_id:
        cust = db.get(Customer, data.customer_id)
        if not cust:
            raise HTTPException(404, f"Klant met id {data.customer_id} niet gevonden")
        if cust.organization_id != data.organization_id:
            raise HTTPException(400, "Klant hoort niet bij de geselecteerde organisatie")
        customer_id = data.customer_id
    elif data.role != "customer" and data.customer_id:
        raise HTTPException(400, "Alleen gebruikers met rol 'customer' kunnen aan een klant gekoppeld worden")

    user = User(
        username=data.username,
        email=f"{data.username}@local",
        hashed_password=hash_password(data.password),
        role=data.role,
        organization_id=data.organization_id,
        customer_id=customer_id,
        is_platform_admin=False,
        is_superuser=False,
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
    return _user_to_response(user)


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
    # Prevent deleting the last platform admin
    if user.is_platform_admin:
        admin_count = db.query(User).filter(
            User.is_platform_admin == True,  # noqa: E712
            User.is_active == True,  # noqa: E712
        ).count()
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the last platform admin")
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


# --- Organization management ---

@router.get("/organizations", response_model=list[OrganizationResponse])
def list_organizations(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.is_platform_admin or user.role == "courier":
        orgs = db.query(Organization).order_by(Organization.name).all()
    elif user.organization_id and user.role in ("owner", "member"):
        orgs = (
            db.query(Organization)
            .filter(Organization.id == user.organization_id)
            .order_by(Organization.name)
            .all()
        )
    else:
        raise HTTPException(403, "Organization access required")
    return [
        OrganizationResponse(
            id=o.id,
            name=o.name,
            slug=o.slug,
            custom_label=o.custom_label,
            enabled_modules=o.modules,
            auto_inactivate_no_images=o.auto_inactivate_no_images,
            created_at=o.created_at,
        )
        for o in orgs
    ]


@router.post("/organizations", response_model=OrganizationResponse, status_code=201)
def create_organization(
    data: OrganizationCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.query(Organization).filter(Organization.slug == data.slug).first():
        raise HTTPException(400, f"Slug '{data.slug}' is already taken")
    org = Organization(
        name=data.name,
        slug=data.slug,
        custom_label=data.custom_label,
        enabled_modules=json.dumps(data.enabled_modules),
        auto_inactivate_no_images=data.auto_inactivate_no_images,
    )
    db.add(org)
    db.commit()
    db.refresh(org)
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        custom_label=org.custom_label,
        enabled_modules=org.modules,
        auto_inactivate_no_images=org.auto_inactivate_no_images,
        created_at=org.created_at,
    )


@router.patch("/organizations/{org_id}", response_model=OrganizationResponse)
def update_organization(
    org_id: int,
    data: OrganizationUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "Organization not found")

    if data.name is not None:
        org.name = data.name

    if data.custom_label is not None:
        org.custom_label = data.custom_label if data.custom_label else None

    if data.slug is not None:
        conflict = (
            db.query(Organization)
            .filter(Organization.slug == data.slug, Organization.id != org_id)
            .first()
        )
        if conflict:
            raise HTTPException(400, f"Slug '{data.slug}' is already taken")
        org.slug = data.slug

    if data.enabled_modules is not None:
        org.modules = data.enabled_modules

    if data.auto_inactivate_no_images is not None:
        org.auto_inactivate_no_images = data.auto_inactivate_no_images

    db.commit()
    db.refresh(org)
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        custom_label=org.custom_label,
        enabled_modules=org.modules,
        auto_inactivate_no_images=org.auto_inactivate_no_images,
        created_at=org.created_at,
    )


@router.delete("/organizations/{org_id}", status_code=204)
def delete_organization(
    org_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "Organization not found")
    # Check no users are linked
    user_count = db.query(User).filter(User.organization_id == org_id).count()
    if user_count > 0:
        raise HTTPException(400, f"Organization still has {user_count} users — remove them first")
    db.delete(org)
    db.commit()
