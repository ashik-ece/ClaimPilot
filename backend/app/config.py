from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(str(_ROOT / ".env"), ".env"), extra="ignore")

    database_url: str = (
        "postgresql+psycopg://claimpilot_app:claimpilot_app@localhost:5433/claimpilot"
    )
    migration_database_url: str = (
        "postgresql+psycopg://claimpilot:claimpilot@localhost:5433/claimpilot"
    )
    app_db_password: str = "claimpilot_app"

    s3_endpoint_url: str = "http://localhost:9090"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "claimpilot-documents"

    jwt_secret: str = "dev-only-change-me-please-use-32-chars-min"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # Release label stamped on review sessions and feedback so pilot cycles stay separable.
    release: str = "v1.0"

    # Origins allowed to call the API from a browser (the console). Comma separated.
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")
    # live: call Gemini. record: call Gemini and save each response. replay: never call out.
    llm_mode: str = "live"
    llm_cache_dir: str = str(_ROOT / "backend" / "llm_cache")
    llm_fast_model: str = "gemini-3.1-flash-lite"
    llm_strong_model: str = "gemini-3.5-flash-lite"
    qdrant_url: str = "http://localhost:6335"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 4


settings = Settings()
