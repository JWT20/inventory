"""Shared fixtures for backend tests.

Uses SQLite in-memory so tests run without PostgreSQL/pgvector.
"""

import os

# Set required env vars before importing app code
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
from app.database import Base, get_db  # noqa: E402
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

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(db):
    user = User(
        username="admin",
        password_hash=hash_password("adminpass"),
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def merchant_user(db):
    user = User(
        username="merchant",
        password_hash=hash_password("merchantpass"),
        role="merchant",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def courier_user(db):
    user = User(
        username="courier",
        password_hash=hash_password("courierpass"),
        role="courier",
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
