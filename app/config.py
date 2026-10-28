from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = ""

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/medical_db"

    max_clarification_turns: int = 3
    sql_timeout_seconds: int = 30
    enable_query_logging: bool = True

    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
