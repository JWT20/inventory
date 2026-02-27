from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://wijnpick:changeme_in_production@db:5432/wijnpick"
    secret_key: str = "changeme_in_production"
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "openai"
    match_threshold: float = 0.75
    upload_dir: str = "/app/uploads"

    class Config:
        env_file = ".env"


settings = Settings()
