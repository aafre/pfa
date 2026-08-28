from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from pydantic_ai import UsageLimits
from starlette.requests import Request
from starlette.responses import Response

from pfa.ai.agents.advisor import build_advisor
from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.ai.deps import FinanceDependencies
from pfa.ai.schemas import ChatRequest, ImportRequest
from pfa.config import Settings, get_settings
from pfa.ingestion.service import ImportService
from pfa.observability import TimedOperation
from pfa.services.answers import deterministic_answer
from pfa.services.health import health_report
from pfa.services.review import monthly_review_evidence
from pfa.services.runtime import close_services, open_services


class TransactionResponse(BaseModel):
    id: int
    date: date
    description: str
    merchant: str | None
    amount_minor: int
    currency: str
    kind: str
    category: str | None
    classification_source: str


class ScenarioRequest(BaseModel):
    cost_minor: int = Field(ge=0)
    horizon_months: int = Field(default=3, ge=1, le=120)
    month: str | None = None


def _month(value: str | None) -> date:
    if value is None:
        today = date.today()
        return today.replace(day=1)
    try:
        year, month = (int(item) for item in value.split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="month must use YYYY-MM") from exc


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        engine, services = open_services(active_settings)
        yield
        close_services(engine, services)

    app = FastAPI(title="PFA", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        with TimedOperation("http_request", method=request.method, path=request.url.path):
            return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, object]:
        return health_report(active_settings)

    @app.post("/imports")
    def import_csv(request: ImportRequest) -> dict[str, object]:
        path = Path(request.path)
        if not path.is_file() or path.suffix.lower() != ".csv":
            raise HTTPException(status_code=400, detail="path must identify a local CSV file")
        engine, services = open_services(active_settings)
        try:
            report = ImportService(
                services.uow, LocalTransactionClassifier(active_settings)
            ).import_csv(path, request.dry_run)
            close_services(engine, services, not request.dry_run)
            return {
                "imported": report.imported,
                "duplicates": report.duplicates,
                "requires_classification": report.requires_classification,
                "errors": report.errors,
            }
        except Exception:
            close_services(engine, services, False)
            raise

    @app.get("/transactions", response_model=list[TransactionResponse])
    def transactions(limit: int = 100) -> list[TransactionResponse]:
        engine, services = open_services(active_settings)
        try:
            rows = services.uow.transactions.all()[-max(1, min(limit, 500)) :]
            return [
                TransactionResponse(
                    id=row.id,
                    date=row.transaction_date,
                    description=row.raw_description,
                    merchant=row.merchant,
                    amount_minor=row.amount_minor,
                    currency=row.currency,
                    kind=row.kind,
                    category=row.category,
                    classification_source=row.classification_source,
                )
                for row in rows
            ]
        finally:
            close_services(engine, services)

    @app.get("/analytics/monthly")
    def monthly(month: str | None = None) -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            return services.analytics.monthly_summary(_month(month)).model_dump()
        finally:
            close_services(engine, services)

    @app.get("/analytics/categories")
    def categories(month: str | None = None) -> list[dict[str, object]]:
        engine, services = open_services(active_settings)
        try:
            return [
                item.model_dump() for item in services.analytics.category_spending(_month(month))
            ]
        finally:
            close_services(engine, services)

    @app.get("/budgets")
    def budgets(month: str | None = None) -> list[dict[str, object]]:
        engine, services = open_services(active_settings)
        try:
            return [item.model_dump() for item in services.analytics.budget_status(_month(month))]
        finally:
            close_services(engine, services)

    @app.get("/goals")
    def goals() -> list[dict[str, object]]:
        engine, services = open_services(active_settings)
        try:
            return [item.model_dump() for item in services.analytics.goal_progress()]
        finally:
            close_services(engine, services)

    @app.post("/scenarios/purchase")
    def purchase(request: ScenarioRequest) -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            return services.planning.simulate_purchase(
                request.cost_minor, request.horizon_months, _month(request.month)
            ).model_dump()
        finally:
            close_services(engine, services)

    @app.post("/chat")
    def chat(request: ChatRequest) -> dict[str, str]:
        engine, services = open_services(active_settings)
        try:
            deterministic = deterministic_answer(
                services.analytics, services.planning, request.message
            )
            if deterministic:
                return {"answer": deterministic}
            try:
                result = build_advisor(active_settings).run_sync(
                    request.message,
                    deps=FinanceDependencies(services.analytics, services.planning),
                    usage_limits=UsageLimits(request_limit=active_settings.agent_request_limit),
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="local model unavailable; deterministic endpoints remain available",
                ) from exc
            return {"answer": result.output}
        finally:
            close_services(engine, services)

    @app.get("/reviews/monthly")
    def review(month: str | None = None) -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            return monthly_review_evidence(services.analytics, _month(month))
        finally:
            close_services(engine, services)

    return app


app = create_app()
