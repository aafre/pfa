from __future__ import annotations

import httpx
from sqlalchemy import text

from pfa.config import Settings


def health_report(settings: Settings, database_url: str | None = None) -> dict[str, object]:
    database = "healthy"
    try:
        from pfa.db.engine import make_engine

        engine = make_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        database = "unhealthy"
    ollama = "healthy"
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
    except Exception:
        ollama = "unavailable"
    status = (
        "healthy"
        if database == "healthy" and ollama == "healthy"
        else "degraded"
        if database == "healthy"
        else "unhealthy"
    )
    return {
        "status": status,
        "application": "healthy",
        "database": database,
        "ollama": ollama,
        "configured_model": settings.model,
        "database_url": database_url or settings.database_url,
    }
