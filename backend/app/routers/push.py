"""Authenticated Web Push subscription management."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import PushSubscription, User
from app.schemas import (
    PushConfigResponse,
    PushSubscriptionDelete,
    PushSubscriptionUpsert,
)

router = APIRouter(prefix="/push", tags=["push"])


def _require_push_recipient(user: User) -> None:
    if user.is_platform_admin or user.role not in ("owner", "member", "courier"):
        raise HTTPException(403, "Pushmeldingen zijn niet beschikbaar voor dit account")


@router.get("/config", response_model=PushConfigResponse)
def push_config(user: User = Depends(get_current_user)):
    _require_push_recipient(user)
    return PushConfigResponse(
        enabled=settings.push_enabled,
        public_key=settings.vapid_public_key if settings.push_enabled else "",
    )


@router.post("/subscriptions", status_code=204)
def upsert_push_subscription(
    body: PushSubscriptionUpsert,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_push_recipient(user)
    if not settings.push_enabled:
        raise HTTPException(503, "Pushmeldingen zijn niet geconfigureerd")

    subscription = (
        db.query(PushSubscription)
        .filter(PushSubscription.endpoint == body.endpoint)
        .first()
    )
    if subscription is not None and subscription.user_id != user.id:
        # A browser endpoint can outlive a login. Recreate it so queued messages
        # for the previous account are removed before binding this device again.
        db.delete(subscription)
        db.flush()
        subscription = None
    if subscription is None:
        subscription = PushSubscription(user_id=user.id, endpoint=body.endpoint)
        db.add(subscription)

    subscription.p256dh = body.keys.p256dh
    subscription.auth = body.keys.auth
    subscription.user_agent = request.headers.get("user-agent")
    db.commit()
    return Response(status_code=204)


@router.delete("/subscriptions", status_code=204)
def delete_push_subscription(
    body: PushSubscriptionDelete,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _require_push_recipient(user)
    subscription = (
        db.query(PushSubscription)
        .filter(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.user_id == user.id,
        )
        .first()
    )
    if subscription is not None:
        db.delete(subscription)
        db.commit()
    return Response(status_code=204)
