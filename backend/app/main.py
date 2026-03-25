import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import sentry_sdk
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.auth import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.events import init_producer, shutdown_producer
from app.services.langfuse_client import get_langfuse, shutdown_langfuse
from app.services.storage import storage, LocalStorage
from app.routers import auth, customers, orders, receiving, skus, vision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
    )
    logger.info("Sentry initialized")


def _run_migrations() -> None:
    """Run Alembic migrations to bring the database up to date.

    On first deploy after adopting Alembic, detects a pre-existing database
    (tables exist but no alembic_version table) and stamps the baseline
    revision so that ``upgrade head`` doesn't try to recreate tables.
    """
    from sqlalchemy import inspect as sa_inspect

    backend_dir = Path(__file__).resolve().parent.parent  # backend/
    alembic_cfg = AlembicConfig(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    # Auto-stamp existing databases that predate Alembic adoption
    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())
    has_existing_schema = "users" in tables
    has_alembic_version = "alembic_version" in tables

    if has_existing_schema and not has_alembic_version:
        logger.info("Existing database detected without Alembic history — stamping baseline...")
        command.stamp(alembic_cfg, "001")

    logger.info("Running Alembic migrations...")
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic migrations complete")


def _cleanup_old_scans():
    """Delete scan images older than 30 days to prevent disk bloat."""
    cutoff = time.time() - 30 * 86400
    removed = 0
    for key, mtime in storage.list_keys("scans"):
        if mtime < cutoff:
            storage.delete(key)
            removed += 1
    if removed:
        logger.info("Scan cleanup: removed %d images older than 30 days", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    _run_migrations()

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
    get_langfuse()  # Initialize Langfuse client (no-op if not configured)
    _cleanup_old_scans()

    # For local storage, also mount StaticFiles as a fallback for legacy
    # /api/uploads/ URLs (existing bookings may reference old paths).
    if isinstance(storage, LocalStorage):
        os.makedirs(settings.upload_dir, exist_ok=True)
        app.mount("/api/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")

    yield

    # --- shutdown ---
    shutdown_producer()
    shutdown_langfuse()


app = FastAPI(
    title="Warehouse Receiving API",
    description="Vision-based product identification for warehouse receiving and labeling",
    version="2.0.0",
    lifespan=lifespan,
)

cors_origins = ["http://localhost:5173"]  # Vite dev server
if settings.domain:
    cors_origins = [f"https://{settings.domain}", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(skus.router, prefix="/api")
app.include_router(customers.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(receiving.router, prefix="/api")
app.include_router(vision.router, prefix="/api")


@app.get("/api/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "up"
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "unhealthy", "db": "down"}, status_code=503)
    return {"status": "ok", "db": db_status}


@app.get("/api/files/{file_path:path}")
def serve_file(file_path: str):
    """Serve uploaded files via the storage backend.

    For local storage: streams the file directly.
    For S3 storage: redirects to a presigned URL.
    """
    if isinstance(storage, LocalStorage):
        import os as _os
        from fastapi.responses import FileResponse
        full_path = _os.path.join(settings.upload_dir, file_path)
        if not _os.path.isfile(full_path):
            from fastapi import HTTPException
            raise HTTPException(404, "File not found")
        return FileResponse(full_path)

    # S3: redirect to presigned URL
    url = storage.url(file_path)
    return RedirectResponse(url=url, status_code=307)
