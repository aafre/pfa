from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Defaults keep PFA local and usable out of the box."""

    model_config = SettingsConfigDict(env_prefix="PFA_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/pfa.db"
    ollama_base_url: str = "http://localhost:11434"
    model: str = "qwen3:4b"
    log_level: str = "INFO"
    agent_retries: int = Field(default=1, ge=0, le=3)
    agent_tool_timeout_seconds: float = Field(default=20.0, gt=0, le=120)


@lru_cache
def get_settings() -> Settings:
    return Settings()
