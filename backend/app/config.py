from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://wijnpick:changeme_in_production@db:5432/wijnpick"
    secret_key: str = "changeme_in_production"
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    match_threshold: float = 0.92
    upload_dir: str = "/app/uploads"

    # Kafka
    kafka_bootstrap_servers: str = ""

    # Auth
    admin_password: str = "changeme_in_production"
    token_expire_days: int = 90

    # Domain (used for CORS)
    domain: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
