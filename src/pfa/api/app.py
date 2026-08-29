from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from pydantic_ai import UsageLimits
from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from pfa.ai.agents.advisor import build_advisor
from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.ai.deps import FinanceDependencies
from pfa.ai.models import available_models
from pfa.ai.schemas import ChatRequest, ImportRequest
from pfa.config import Settings, get_settings
from pfa.db.models import ImportBatchModel
from pfa.domain.errors import BatchError, UploadRejected
from pfa.ingestion.batches import (
    BatchPatch,
    apply_patch,
    batch_candidates,
    batch_committed_transaction_ids,
    batch_counts,
    batch_issues,
    commit_batch,
    create_batch,
    discard_batch,
    load_batch,
    sweep_expired_batches,
)
from pfa.ingestion.candidates import FILE_TOO_LARGE, CandidateIssue, CandidateTransaction
from pfa.ingestion.service import ImportService
from pfa.ingestion.upload import stage_upload, sweep_upload_dir
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
    flow_direction: str
    currency: str
    kind: str
    category: str | None
    classification_source: str


class AccountResponse(BaseModel):
    id: int
    name: str
    account_type: str
    currency: str


class CandidateIssueResponse(BaseModel):
    code: str
    message: str
    severity: str


class CandidateResponse(BaseModel):
    candidate_id: str
    transaction_date: str | None
    posted_date: str | None
    raw_description: str
    normalized_description: str
    amount_minor: int | None
    direction: str | None
    currency: str
    account_hint: str | None
    external_id: str | None
    kind: str | None
    category: str | None
    transfer_purpose: str | None
    source_format: str
    source_line: int | None
    source_page: int | None
    extraction_method: str
    raw_fields: dict[str, str]
    issues: list[CandidateIssueResponse]
    duplicate_of: int | None
    included: bool
    state: str


class ImportBatchResponse(BaseModel):
    id: str
    status: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    extractor: str
    destination_account: str | None
    detected_account: str | None
    detected_currency: str | None
    statement_start: date | None
    statement_end: date | None
    page_count: int | None
    counts: dict[str, int]
    issues: list[CandidateIssueResponse]
    candidates: list[CandidateResponse]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    committed_at: datetime | None
    committed_transaction_ids: list[int]


class ImportBatchPatchRequest(BaseModel):
    account: str | None = None
    excluded_candidate_ids: list[str] | None = None
    amount_mode: str | None = None


class ScenarioRequest(BaseModel):
    cost_minor: int = Field(ge=0)
    horizon_months: int = Field(default=3, ge=1, le=120)
    month: str | None = None


def _issue_response(issue: CandidateIssue) -> CandidateIssueResponse:
    return CandidateIssueResponse(code=issue.code, message=issue.message, severity=issue.severity)


def _candidate_response(candidate: CandidateTransaction) -> CandidateResponse:
    return CandidateResponse(
        candidate_id=candidate.candidate_id,
        transaction_date=candidate.transaction_date,
        posted_date=candidate.posted_date,
        raw_description=candidate.raw_description,
        normalized_description=candidate.normalized_description,
        amount_minor=candidate.amount_minor,
        direction=candidate.direction,
        currency=candidate.currency,
        account_hint=candidate.account_hint,
        external_id=candidate.external_id,
        kind=candidate.kind,
        category=candidate.category,
        transfer_purpose=candidate.transfer_purpose,
        source_format=candidate.source_format,
        source_line=candidate.source_line,
        source_page=candidate.source_page,
        extraction_method=candidate.extraction_method,
        raw_fields=candidate.raw_fields,
        issues=[_issue_response(issue) for issue in candidate.issues],
        duplicate_of=candidate.duplicate_of,
        included=candidate.included,
        state=candidate.state,
    )


def _batch_response(batch: ImportBatchModel) -> ImportBatchResponse:
    return ImportBatchResponse(
        id=batch.id,
        status=batch.status,
        original_filename=batch.original_filename,
        media_type=batch.media_type,
        size_bytes=batch.size_bytes,
        sha256=batch.sha256,
        extractor=batch.extractor,
        destination_account=batch.destination_account,
        detected_account=batch.detected_account,
        detected_currency=batch.detected_currency,
        statement_start=batch.statement_start,
        statement_end=batch.statement_end,
        page_count=batch.page_count,
        counts=batch_counts(batch),
        issues=[_issue_response(issue) for issue in batch_issues(batch)],
        candidates=[_candidate_response(candidate) for candidate in batch_candidates(batch)],
        created_at=batch.created_at,
        updated_at=batch.updated_at,
        expires_at=batch.expires_at,
        committed_at=batch.committed_at,
        committed_transaction_ids=batch_committed_transaction_ids(batch),
    )


_UPLOAD_ERROR_STATUS = {FILE_TOO_LARGE: 413}


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
        sweep_upload_dir(active_settings)
        engine, services = open_services(active_settings)
        sweep_expired_batches(services.uow)
        services.uow.session.commit()
        yield
        close_services(engine, services)

    app = FastAPI(title="PFA", version="0.1.0", lifespan=lifespan)

    web_root = Path(__file__).resolve().parent.parent / "web"
    app.mount("/static", StaticFiles(directory=web_root), name="static")

    @app.middleware("http")
    async def observe_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        with TimedOperation("http_request", method=request.method, path=request.url.path):
            return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, object]:
        return health_report(active_settings)

    @app.post("/imports", deprecated=True)
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

    @app.post("/imports/preview", response_model=ImportBatchResponse)
    def imports_preview(
        request: Request,
        file: UploadFile = File(...),  # noqa: B008 - FastAPI's dependency-injection idiom
        account: str | None = Form(None),  # noqa: B008
    ) -> ImportBatchResponse:
        content_length = request.headers.get("content-length")
        try:
            source = stage_upload(
                file, active_settings, int(content_length) if content_length else None
            )
        except UploadRejected as exc:
            status_code = _UPLOAD_ERROR_STATUS.get(exc.code, 422)
            raise HTTPException(
                status_code=status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

        try:
            engine, services = open_services(active_settings)
            try:
                batch = create_batch(services.uow, source, active_settings, account=account)
                response = _batch_response(batch)
                close_services(engine, services)
                return response
            except Exception:
                close_services(engine, services, False)
                raise
        finally:
            # open_services() belongs inside this boundary: a database that won't open
            # must not strand the staged statement. The unlink can still lose to a
            # timed-out extraction thread holding the file open (Windows); _run_extraction
            # owns cleanup in that case, so a failed unlink here is not an error.
            with suppress(OSError):
                source.path.unlink(missing_ok=True)

    @app.get("/imports/{batch_id}", response_model=ImportBatchResponse)
    def get_import_batch(batch_id: str) -> ImportBatchResponse:
        engine, services = open_services(active_settings)
        try:
            batch = load_batch(services.uow, batch_id)
            response = _batch_response(batch)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc
        except Exception:
            close_services(engine, services, False)
            raise

    @app.patch("/imports/{batch_id}", response_model=ImportBatchResponse)
    def patch_import_batch(batch_id: str, request: ImportBatchPatchRequest) -> ImportBatchResponse:
        engine, services = open_services(active_settings)
        try:
            batch = apply_patch(
                services.uow,
                batch_id,
                BatchPatch(
                    account=request.account,
                    excluded_candidate_ids=request.excluded_candidate_ids,
                    amount_mode=request.amount_mode,
                ),
            )
            response = _batch_response(batch)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc
        except Exception:
            close_services(engine, services, False)
            raise

    @app.post("/imports/{batch_id}/commit", response_model=ImportBatchResponse)
    def commit_import_batch(batch_id: str) -> ImportBatchResponse:
        engine, services = open_services(active_settings)
        try:
            batch = commit_batch(services.uow, batch_id, active_settings)
            response = _batch_response(batch)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc
        except Exception:
            close_services(engine, services, False)
            raise

    @app.delete("/imports/{batch_id}", response_model=ImportBatchResponse)
    def delete_import_batch(batch_id: str) -> ImportBatchResponse:
        engine, services = open_services(active_settings)
        try:
            batch = discard_batch(services.uow, batch_id)
            response = _batch_response(batch)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc
        except Exception:
            close_services(engine, services, False)
            raise

    @app.get("/accounts", response_model=list[AccountResponse])
    def accounts() -> list[AccountResponse]:
        engine, services = open_services(active_settings)
        try:
            return [
                AccountResponse(
                    id=acc.id, name=acc.name, account_type=acc.account_type, currency=acc.currency
                )
                for acc in services.uow.accounts.all()
            ]
        finally:
            close_services(engine, services)

    @app.get("/transactions", response_model=list[TransactionResponse])
    def transactions(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[TransactionResponse]:
        engine, services = open_services(active_settings)
        try:
            rows = services.uow.transactions.all()[-limit:]
            return [
                TransactionResponse(
                    id=row.id,
                    date=row.transaction_date,
                    description=row.raw_description,
                    merchant=row.merchant,
                    amount_minor=row.amount_minor,
                    flow_direction=row.flow_direction,
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
            names = available_models(active_settings)
            if names is None or active_settings.model not in names:
                raise HTTPException(
                    status_code=503,
                    detail="local model unavailable; deterministic endpoints remain available",
                )
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

    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        html = (web_root / "index.html").read_text(encoding="utf-8")
        return Response(html, media_type="text/html")

    return app


app = create_app()
