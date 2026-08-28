from typing import Any

import httpx
from alembic import command
from alembic.config import Config

from pfa.config import Settings
from pfa.services.health import health_report


class OllamaResponse:
    def __init__(self, model: str):
        self.model = model

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, list[dict[str, str]]]:
        return {"models": [{"name": self.model}]}


def migrate(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def test_health_requires_migrated_schema_and_does_not_disclose_database_url(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'empty.db'}", model="qwen3.5:4b")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: OllamaResponse(settings.model))

    report = health_report(settings)

    assert report["status"] == "unhealthy"
    assert report["database"] == "unhealthy"
    assert report["ollama"] == "healthy"
    assert "database_url" not in report


def test_health_distinguishes_healthy_and_ai_degraded_modes(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pfa.db'}", model="qwen3.5:4b")
    migrate(settings.database_url)
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: OllamaResponse(settings.model))
    assert health_report(settings)["status"] == "healthy"

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", unavailable)
    degraded = health_report(settings)
    assert degraded["status"] == "degraded"
    assert degraded["database"] == "healthy"
    assert degraded["ollama"] == "unavailable"
