from pydantic import field_validator
from pydantic_settings import BaseSettings

_INSECURE_DEFAULT = "INSECURE-DEV-KEY-CHANGE-IN-PRODUCTION"


class Settings(BaseSettings):
    database_url: str = _INSECURE_DEFAULT
    secret_key: str = _INSECURE_DEFAULT
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_extraction_model: str = "gemini-2.5-pro"  # stronger model for document extraction
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_max_concurrent: int = 5  # max concurrent Gemini API requests
    gemini_extraction_max_dimension: int = 2048  # px – longest side for pakbon/invoice extraction (table digits need more resolution than box classification)
    match_threshold: float = 0.80
    ambiguity_margin: float = 0.05
    upload_dir: str = "/app/uploads"

    # Kafka
    kafka_bootstrap_servers: str = ""

    # Langfuse (LLM observability) — leave empty to disable
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Sentry (optional — leave empty to disable)
    sentry_dsn: str = ""

    # Redis (optional — used for rate limiting; empty = in-memory fallback)
    redis_url: str = ""

    # Storage backend ("local" or "s3")
    storage_backend: str = "local"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = ""
    s3_presigned_url_expiry: int = 3600  # seconds

    # Auth
    admin_password: str = _INSECURE_DEFAULT
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Domain (used for CORS)
    domain: str = ""

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        if v == _INSECURE_DEFAULT:
            raise ValueError(
                "DATABASE_URL is not set. Provide a PostgreSQL connection string, e.g. postgresql://user:pass@host:5432/dbname"
            )
        return v

    @field_validator("secret_key")
    @classmethod
    def _check_secret_key(cls, v: str) -> str:
        if v == _INSECURE_DEFAULT:
            raise ValueError(
                "SECRET_KEY is not set. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @field_validator("admin_password")
    @classmethod
    def _check_admin_password(cls, v: str) -> str:
        if v == _INSECURE_DEFAULT:
            raise ValueError(
                "ADMIN_PASSWORD is not set. Choose a strong password and set it as an environment variable."
            )
        return v

    class Config:
        env_file = ".env"


settings = Settings()
