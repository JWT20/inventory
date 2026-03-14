import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import inspect, text

from app.auth import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.events import init_producer, shutdown_producer
from app.routers import auth, orders, receiving, skus, vision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _migrate_is_admin_to_role():
    """One-time migration: convert is_admin boolean column to role string column."""
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "is_admin" not in columns:
        return  # already migrated or fresh install

    logger.info("Migrating users table: is_admin → role ...")
    with engine.begin() as conn:
        if "role" not in columns:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'courier'"
            ))
        conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = true"))
        conn.execute(text("UPDATE users SET role = 'courier' WHERE is_admin = false"))
        conn.execute(text("ALTER TABLE users DROP COLUMN is_admin"))
    logger.info("Migration complete: users.is_admin → users.role")


def _migrate_embedding_dimension():
    """One-time migration: change embedding vector dimension (e.g. 1536 → 768).

    Detects a mismatch between the current column dimension and the expected
    EMBEDDING_DIM, then drops and recreates the column.  Also truncates
    reference_images and skus because old embeddings are incompatible.
    """
    from app.models import EMBEDDING_DIM

    inspector = inspect(engine)
    if "reference_images" not in inspector.get_table_names():
        return  # fresh install, nothing to migrate

    # pgvector stores dimension in the column's UDT; query pg catalog
    with engine.begin() as conn:
        row = conn.execute(text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'reference_images'::regclass AND attname = 'embedding'"
        )).first()
        if row is None:
            return  # column doesn't exist yet
        current_dim = row[0]
        if current_dim == EMBEDDING_DIM:
            return  # dimensions match, nothing to do

        logger.info(
            "Embedding dimension changed (%d → %d). Clearing old data and recreating column...",
            current_dim, EMBEDDING_DIM,
        )
        conn.execute(text("TRUNCATE reference_images CASCADE"))
        conn.execute(text("TRUNCATE skus CASCADE"))
        conn.execute(text("ALTER TABLE reference_images DROP COLUMN embedding"))
        conn.execute(text(
            f"ALTER TABLE reference_images ADD COLUMN embedding vector({EMBEDDING_DIM})"
        ))
        logger.info("Embedding migration complete — old SKUs and images cleared")


def _migrate_order_tables():
    """Recreate order tables if they exist with an outdated schema.

    Previous versions had a different orders schema.  ``create_all`` skips
    tables that already exist, so we detect stale tables and drop them first.
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    if "orders" not in existing:
        return  # fresh install, create_all will handle it

    columns = {c["name"] for c in inspector.get_columns("orders")}
    if "merchant_id" in columns:
        return  # current schema, nothing to do

    logger.info("Detected outdated order tables — dropping and recreating...")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS bookings CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS order_lines CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS orders CASCADE"))
    logger.info("Old order tables dropped — create_all will recreate them")


def _migrate_sku_wine_fields():
    """Add wine-specific columns to skus table if missing."""
    inspector = inspect(engine)
    if "skus" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("skus")}
    new_cols = {
        "producent": "VARCHAR(150)",
        "wijnaam": "VARCHAR(150)",
        "wijntype": "VARCHAR(50)",
        "jaargang": "VARCHAR(10)",
        "volume": "VARCHAR(20)",
    }
    to_add = {k: v for k, v in new_cols.items() if k not in columns}
    if not to_add:
        return
    logger.info("Adding wine fields to skus table: %s", ", ".join(to_add))
    with engine.begin() as conn:
        for col, dtype in to_add.items():
            conn.execute(text(f"ALTER TABLE skus ADD COLUMN {col} {dtype}"))


def _migrate_order_line_klant():
    """Add klant column to order_lines if missing."""
    inspector = inspect(engine)
    if "order_lines" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("order_lines")}
    if "klant" in columns:
        return
    logger.info("Adding klant column to order_lines table")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE order_lines ADD COLUMN klant VARCHAR(150) NOT NULL DEFAULT ''"))


def _migrate_reference_image_processing_status():
    """Add processing_status column to reference_images if missing and make embedding nullable."""
    inspector = inspect(engine)
    if "reference_images" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("reference_images")}
    if "processing_status" not in columns:
        logger.info("Adding processing_status column to reference_images table")
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE reference_images ADD COLUMN processing_status VARCHAR(20) NOT NULL DEFAULT 'done'"
            ))
            # Allow NULL embeddings for images still being processed
            conn.execute(text(
                "ALTER TABLE reference_images ALTER COLUMN embedding DROP NOT NULL"
            ))


def _migrate_users_for_fastapi_users():
    """Migrate users table to FastAPI-Users compatible schema.

    Adds: email, hashed_password, is_superuser, is_verified.
    Renames: password_hash → hashed_password.
    """
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("users")}

    with engine.begin() as conn:
        # Rename password_hash → hashed_password
        if "password_hash" in columns and "hashed_password" not in columns:
            logger.info("Renaming users.password_hash → users.hashed_password")
            conn.execute(text(
                "ALTER TABLE users RENAME COLUMN password_hash TO hashed_password"
            ))
            # Widen column to 1024 chars for FastAPI-Users compatibility
            conn.execute(text(
                "ALTER TABLE users ALTER COLUMN hashed_password TYPE VARCHAR(1024)"
            ))

        # Add email column
        if "email" not in columns:
            logger.info("Adding users.email column")
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN email VARCHAR(320) NOT NULL DEFAULT ''"
            ))
            conn.execute(text(
                "UPDATE users SET email = username || '@local' WHERE email = ''"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_users_email ON users (email)"
            ))

        # Add is_superuser column
        if "is_superuser" not in columns:
            logger.info("Adding users.is_superuser column")
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN is_superuser BOOLEAN NOT NULL DEFAULT false"
            ))
            conn.execute(text(
                "UPDATE users SET is_superuser = true WHERE role = 'admin'"
            ))

        # Add is_verified column
        if "is_verified" not in columns:
            logger.info("Adding users.is_verified column")
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT false"
            ))
            # Mark all existing users as verified
            conn.execute(text("UPDATE users SET is_verified = true"))


def _cleanup_old_scans():
    """Delete scan images older than 30 days to prevent disk bloat."""
    scan_dir = os.path.join(settings.upload_dir, "scans")
    if not os.path.isdir(scan_dir):
        return
    cutoff = time.time() - 30 * 86400
    removed = 0
    for filename in os.listdir(scan_dir):
        filepath = os.path.join(scan_dir, filename)
        if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
            os.remove(filepath)
            removed += 1
    if removed:
        logger.info("Scan cleanup: removed %d images older than 30 days", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    _migrate_is_admin_to_role()
    _migrate_users_for_fastapi_users()
    _migrate_embedding_dimension()
    _migrate_order_tables()
    _migrate_sku_wine_fields()
    _migrate_order_line_klant()
    _migrate_reference_image_processing_status()

    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # Seed admin account if no users exist yet
    db = SessionLocal()
    try:
        if db.query(User).first() is None:
            admin = User(
                username="admin",
                email="admin@local",
                hashed_password=hash_password(settings.admin_password),
                role="admin",
                is_superuser=True,
                is_verified=True,
            )
            db.add(admin)
            db.commit()
            logger.info("Created default admin user — change the password!")
    finally:
        db.close()

    init_producer()
    _cleanup_old_scans()

    yield

    # --- shutdown ---
    shutdown_producer()


app = FastAPI(
    title="Warehouse Receiving API",
    description="Vision-based product identification for warehouse receiving and labeling",
    version="2.0.0",
    lifespan=lifespan,
)

cors_origins = ["http://localhost:5173"]  # Vite dev server
if settings.domain:
    cors_origins = [f"https://{settings.domain}"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(skus.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(receiving.router, prefix="/api")
app.include_router(vision.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
