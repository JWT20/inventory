"""FastAPI-Users configuration: UserManager, JWT backend, dependency helpers."""

import logging
from collections.abc import AsyncGenerator
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_async_session
from app.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database adapter
# ---------------------------------------------------------------------------

async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


# ---------------------------------------------------------------------------
# User manager (business logic layer)
# ---------------------------------------------------------------------------

class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def authenticate(self, credentials) -> Optional[User]:
        """Override: authenticate by *username* instead of email."""
        session: AsyncSession = self.user_db.session
        result = await session.execute(
            select(User).where(User.username == credentials.username)
        )
        user = result.scalar_one_or_none()

        if user is None:
            # Run the hasher to mitigate timing attacks
            self.password_helper.hash(credentials.password)
            return None

        verified, updated_hash = self.password_helper.verify_and_update(
            credentials.password, user.hashed_password
        )
        if not verified:
            return None

        if updated_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_hash})

        return user

    async def on_after_register(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        logger.info("User %s (id=%d) registered.", user.username, user.id)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


# ---------------------------------------------------------------------------
# JWT authentication backend
# ---------------------------------------------------------------------------

bearer_transport = BearerTransport(tokenUrl="/api/auth/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
        secret=settings.secret_key,
        lifetime_seconds=settings.access_token_expire_minutes * 60,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


# ---------------------------------------------------------------------------
# Central FastAPIUsers instance & dependency shortcuts
# ---------------------------------------------------------------------------

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
