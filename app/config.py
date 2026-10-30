from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/medical_db"

    max_clarification_turns: int = 3
    max_sql_repairs: int = 1
    reference_date: str = "2025-12-31"
    # Optional LLM helpers; rules remain authoritative when they are off.
    # ``ambiguity_llm_supplement`` is off by default: its items are non-blocking,
    # never asked and never reach the SQL prompt, so it only costs 5-20s.
    ambiguity_llm_supplement: bool = False
    clarify_llm_polish: bool = True
    # Per-call ceiling for the LangChain chat model, in seconds.
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 1
    sql_timeout_seconds: int = 30
    enable_query_logging: bool = True

    # Sessions, query index and graph checkpoints survive a restart; rows older
    # than ``session_ttl_hours`` are deleted at startup (and on request).
    session_db_path: str = "data/sessions.db"
    checkpoint_db_path: str = "data/checkpoints.db"
    session_ttl_hours: float = 24

    # SQLite mirror used by C's self-refine generator.
    sqlite_db_path: str = "data/synthetic/medical.db"
    max_repair_rounds: int = 2
    sql_llm_evaluate: bool = False
    sql_exec_timeout: int = 5
    sql_max_rows: int = 50
    sql_max_candidates: int = 3

    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
