from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


settings = Settings()
