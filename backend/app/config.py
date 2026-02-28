from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://wijnpick:changeme_in_production@db:5432/wijnpick"
    secret_key: str = "changeme_in_production"
    openai_api_key: str = ""
    openai_vision_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"
    match_threshold: float = 0.92
    upload_dir: str = "/app/uploads"

    # Auth
    admin_password: str = "changeme_in_production"
    token_expire_days: int = 90

    class Config:
        env_file = ".env"


settings = Settings()
