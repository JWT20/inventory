"""Shared fixtures for backend tests.

Uses SQLite in-memory so tests run without PostgreSQL/pgvector.
Provides an async session wrapper so FastAPI-Users' async dependencies
work transparently with the sync SQLite test engine.
"""

import os

# Set required env vars before importing app code
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Register a SQLite compiler for pgvector's Vector type *before* importing
# models, so SQLAlchemy knows how to emit DDL for the embedding column.
from pgvector.sqlalchemy import Vector


@compiles(Vector, "sqlite")
def _compile_vector_sqlite(type_, compiler, **kw):
    return "TEXT"


from fastapi.testclient import TestClient  # noqa: E402

from app.auth import create_token, hash_password, _failed_attempts  # noqa: E402
from app.database import Base, get_db, get_async_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SKU, User  # noqa: E402

SQLALCHEMY_DATABASE_URL = "sqlite://"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# Async session wrapper (lets FastAPI-Users work with sync SQLite in tests)
# ---------------------------------------------------------------------------

class _AsyncSessionWrapper:
    """Thin async facade around a synchronous SQLAlchemy session.

    FastAPI-Users calls ``await session.execute(...)``, ``await session.commit()``
    etc.  This wrapper delegates to the underlying sync session, making those
    calls return immediately.
    """

    def __init__(self, sync_session):
        self._sync = sync_session

    async def execute(self, statement, *args, **kwargs):
        return self._sync.execute(statement, *args, **kwargs)

    async def commit(self):
        self._sync.commit()

    async def flush(self):
        self._sync.flush()

    async def refresh(self, instance, *args, **kwargs):
        self._sync.refresh(instance, *args, **kwargs)

    def add(self, instance):
        self._sync.add(instance)

    async def get(self, model, ident):
        return self._sync.get(model, ident)


# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _setup_db():
    """Create all tables before each test and drop them after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    _failed_attempts.clear()


@pytest.fixture
def db():
    """Yield a fresh SQLAlchemy session connected to the in-memory DB."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db):
    """TestClient wired to the test database.

    We create the client *without* using a context manager so that the app's
    startup events (which talk to the real Postgres) are never triggered.
    """

    def _override_get_db():
        yield db

    async def _override_get_async_session():
        yield _AsyncSessionWrapper(db)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_async_session] = _override_get_async_session
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(db):
    user = User(
        username="admin",
        email="admin@local",
        hashed_password=hash_password("adminpass"),
        role="admin",
        is_superuser=True,
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def merchant_user(db):
    user = User(
        username="merchant",
        email="merchant@local",
        hashed_password=hash_password("merchantpass"),
        role="merchant",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def courier_user(db):
    user = User(
        username="courier",
        email="courier@local",
        hashed_password=hash_password("courierpass"),
        role="courier",
        is_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Token fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_token(admin_user):
    return create_token(admin_user.id)


@pytest.fixture
def merchant_token(merchant_user):
    return create_token(merchant_user.id)


@pytest.fixture
def courier_token(courier_user):
    return create_token(courier_user.id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def auth_header(token: str) -> dict:
    """Return an Authorization header dict for the given JWT."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_sku(db):
    sku = SKU(sku_code="WINE-001", name="Test Wine", description="A test wine")
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return sku
