import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.auth import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers import auth, labels, receiving, skus, vision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Warehouse Receiving API",
    description="Vision-based product identification for warehouse receiving and labeling",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(skus.router, prefix="/api")
app.include_router(receiving.router, prefix="/api")
app.include_router(labels.router, prefix="/api")
app.include_router(vision.router, prefix="/api")


@app.on_event("startup")
def on_startup():
    logger.info("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # Seed admin account if no users exist yet
    db = SessionLocal()
    try:
        if db.query(User).first() is None:
            admin = User(
                username="admin",
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
            )
            db.add(admin)
            db.commit()
            logger.info("Created default admin user — change the password!")
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"status": "ok"}
