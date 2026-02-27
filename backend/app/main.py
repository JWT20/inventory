import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers import auth, orders, picks, skus, vision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="WijnPick API",
    description="Vision-based wine box identification for order picking",
    version="1.0.0",
)

# CORS — parse comma-separated origins from config, or allow all in dev mode
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(skus.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(picks.router, prefix="/api")
app.include_router(vision.router, prefix="/api")


def _seed_admin(db: Session) -> None:
    """Create the initial admin user if no users exist yet."""
    if db.query(User).first() is not None:
        return
    admin = User(
        username=settings.admin_username,
        email=settings.admin_email,
        password_hash=hash_password(settings.admin_password),
        role="admin",
    )
    db.add(admin)
    db.commit()
    logger.info(
        "Seeded initial admin user '%s' — change the password immediately!",
        admin.username,
    )


@app.on_event("startup")
def on_startup():
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    db = SessionLocal()
    try:
        _seed_admin(db)
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}
