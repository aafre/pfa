from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Defaults keep PFA local and usable out of the box."""

    model_config = SettingsConfigDict(env_prefix="PFA_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/pfa.db"
    ollama_base_url: str = "http://localhost:11434"
    model: str = "qwen3.5:4b"
    log_level: str = "INFO"
    agent_retries: int = Field(default=1, ge=0, le=3)
    agent_tool_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    agent_request_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    agent_request_limit: int = Field(default=8, ge=1, le=20)
    agent_output_token_limit: int = Field(default=1024, ge=128, le=4096)
    max_pdf_pages: int = Field(default=100, ge=1, le=1000)
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ocr_min_confidence: float = Field(default=80.0, ge=0, le=100)


@lru_cache
def get_settings() -> Settings:
    return Settings()
