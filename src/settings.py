"""Central settings — all config from env with sensible defaults."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM. pydantic-settings already reads these from the environment / .env by
    # field name, so no os.getenv default is needed. They are typed Optional /
    # given a real default so that merely importing settings never raises a
    # ValidationError (previously `os.getenv(...)` returned None for a `str`
    # field, so `import src.settings` — and therefore `uvicorn backend.main:app`
    # — crashed at startup whenever the vars were unset). Missing/empty values
    # are validated where they are actually used (the agents), with a clear
    # message, instead of an opaque import-time crash.
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"

    # Whisper
    whisper_model: str = "base"  # base | small | medium

    # DB
    sqlite_path: str = "data/prescreen.db"

    # Audit
    audit_log_path: str = "responsible_ai/audit_log.jsonl"

    # App
    max_tool_calls: int = 4
    questions_per_interview: int = 5

    # Paths
    base_dir: Path = Path(__file__).parent.parent


settings = Settings()
