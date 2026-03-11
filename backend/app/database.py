from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# ---------------------------------------------------------------------------
# Sync engine (used by existing routers, migrations, etc.)
# ---------------------------------------------------------------------------
engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Async engine (used by FastAPI-Users)
# ---------------------------------------------------------------------------
_async_url = settings.database_url.replace(
    "postgresql://", "postgresql+asyncpg://"
).replace(
    "postgresql+psycopg2://", "postgresql+asyncpg://"
)

async_engine = create_async_engine(_async_url)
async_session_maker = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
