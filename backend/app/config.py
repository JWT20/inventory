from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://wijnpick:changeme_in_production@db:5432/wijnpick"
    secret_key: str = "changeme_in_production"
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    match_threshold: float = 0.75
    upload_dir: str = "/app/uploads"

    # JWT
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # First admin user (seeded on startup if no users exist)
    admin_username: str = "admin"
    admin_password: str = "changeme_in_production"
    admin_email: str = "admin@wijnpick.local"

    # CORS – comma-separated origins, or "*" for development
    cors_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()
