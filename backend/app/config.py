from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://wijnpick:wijnpick@db:5432/wijnpick"
    secret_key: str = "INSECURE-DEV-KEY-CHANGE-IN-PRODUCTION"
    gemini_api_key: str = ""
    gemini_vision_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    match_threshold: float = 0.80
    upload_dir: str = "/app/uploads"

    # Kafka
    kafka_bootstrap_servers: str = ""

    # Auth
    admin_password: str = "INSECURE-DEV-KEY-CHANGE-IN-PRODUCTION"
    token_expire_days: int = 90

    # Domain (used for CORS)
    domain: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
