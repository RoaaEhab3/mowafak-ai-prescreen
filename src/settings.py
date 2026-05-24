"""Central settings — all config from env with sensible defaults."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    gemini_api_key: str = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL")

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
