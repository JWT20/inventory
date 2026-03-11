from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULT = "INSECURE-DEV-KEY-CHANGE-IN-PRODUCTION"


class Settings(BaseSettings):
    database_url: str = "postgresql://wijnpick:wijnpick@db:5432/wijnpick"
    secret_key: str = _INSECURE_DEFAULT
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    match_threshold: float = 0.80
    upload_dir: str = "/app/uploads"

    # Kafka
    kafka_bootstrap_servers: str = ""

    # Auth
    admin_password: str = _INSECURE_DEFAULT
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Domain (used for CORS)
    domain: str = ""

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

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
