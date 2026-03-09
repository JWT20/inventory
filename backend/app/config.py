from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str  # required — no default
    secret_key: str  # required — no default
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    match_threshold: float = 0.92
    upload_dir: str = "/app/uploads"

    # Kafka
    kafka_bootstrap_servers: str = ""

    # Auth
    admin_password: str  # required — no default
    token_expire_days: int = 90

    # Domain (used for CORS)
    domain: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
