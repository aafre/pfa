from __future__ import annotations

from sqlalchemy import text

from pfa.ai.models import available_models
from pfa.config import Settings


def health_report(settings: Settings) -> dict[str, object]:
    database = "healthy"
    engine = None
    try:
        from pfa.db.engine import make_engine

        engine = make_engine(settings)
        with engine.connect() as connection:
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            connection.execute(text("SELECT 1 FROM accounts LIMIT 1"))
    except Exception:
        database = "unhealthy"
    finally:
        if engine is not None:
            engine.dispose()
    ollama = "healthy"
    model = "missing"
    names = available_models(settings)
    if names is None:
        ollama = "unavailable"
    else:
        model = "available" if settings.model in names else "missing"
    status = (
        "healthy"
        if database == "healthy" and ollama == "healthy" and model == "available"
        else "degraded"
        if database == "healthy"
        else "unhealthy"
    )
    return {
        "status": status,
        "application": "healthy",
        "database": database,
        "ollama": ollama,
        "model": model,
        "configured_model": settings.model,
    }
