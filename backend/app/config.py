from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://reconcilio:reconcilio@localhost:5432/reconcilio"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    # Host the *browser* uses to reach MinIO when PUTting/GETting a presigned
    # URL. Distinct from minio_endpoint because in docker-compose the API
    # reaches MinIO over the compose network as "minio:9000", but a browser
    # running on the host can't resolve that — it needs the mapped port on
    # localhost instead. Defaults to "localhost:9000" (the port docker-compose
    # maps to the host) regardless of what minio_endpoint is set to.
    minio_public_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "reconcilio"
    minio_secure: bool = False

    # Extraction: AWS credentials come from the standard boto3 env chain; an
    # empty openrouter_api_key disables LLM refinement (see llm.NullRefiner).
    aws_region: str = "us-east-1"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4o-mini"

    jwt_secret: str = "change-me-in-.env"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24

    cors_origins: list[str] = ["http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
