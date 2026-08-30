from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import UsageLimits
from starlette.requests import Request
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from pfa.ai.agents.advisor import build_advisor
from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.ai.deps import FinanceDependencies
from pfa.ai.models import available_models
from pfa.ai.schemas import ChatRequest, ImportRequest
from pfa.analytics.service import cash_position
from pfa.config import Settings, get_settings
from pfa.db.models import (
    ImportBatchModel,
    TransferEventModel,
    TransferMatchDecisionModel,
)
from pfa.domain.accounts import AccountType
from pfa.domain.errors import BatchError, UploadRejected
from pfa.domain.transactions import TransferLegRole, TransferPurpose, signed_minor
from pfa.ingestion.batches import (
    BatchPatch,
    NewAccountDraft,
    apply_patch,
    batch_candidates,
    batch_committed_transaction_ids,
    batch_counts,
    batch_issues,
    batch_semantic_totals,
    commit_batch,
    create_batch,
    discard_batch,
    load_batch,
    sweep_expired_batches,
    undo_batch,
)
from pfa.ingestion.candidates import FILE_TOO_LARGE, CandidateIssue, CandidateTransaction
from pfa.ingestion.service import ImportService
from pfa.ingestion.transfers import (
    accept_suggestion,
    create_manual_link,
    dismiss_suggestion,
)
from pfa.ingestion.upload import stage_upload, sweep_upload_dir
from pfa.observability import TimedOperation
from pfa.services.answers import deterministic_answer
from pfa.services.fx import fetch_and_store_fx_rates
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
    signed_amount_minor: int
    account_id: int


class AccountResponse(BaseModel):
    id: int
    name: str
    account_type: str
    currency: str
    institution: str | None = None
    last4: str | None = None
    opening_balance_minor: int = 0
    opening_balance_as_of: date | None = None
    active: bool = True


class NewAccountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    account_type: AccountType = AccountType.CURRENT
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    institution: str | None = Field(default=None, max_length=120)
    last4: str | None = Field(default=None, min_length=4, max_length=4, pattern=r"^\d{4}$")
    opening_balance_minor: int = 0
    opening_balance_as_of: date | None = None
    opening_balance_confirmed: bool = False


class AccountMetadataUpdateRequest(BaseModel):
    institution: Literal["hdfc_bank"]


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
    direction_explicit: bool
    currency: str
    account_hint: str | None
    account_id: int | None
    signed_amount_minor: int | None
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
    destination_account_id: int | None
    new_account: NewAccountRequest | None
    adapter_id: str | None
    detection_confidence: float | None
    detection_reason_codes: list[str]
    detected_institution: str | None
    detected_account_hint: str | None
    suggested_currency: str | None
    currency_evidence: str | None
    compatible_account_types: list[str]
    reconciliation: dict[str, object] | None
    semantic_totals: dict[str, int]
    amount_sign: str | None
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
    account: str | None = None  # deprecated label compatibility
    destination_account_id: int | None = Field(default=None, gt=0)
    new_account: NewAccountRequest | None = None
    account_metadata_update: AccountMetadataUpdateRequest | None = None
    excluded_candidate_ids: list[str] | None = None

    @model_validator(mode="after")
    def one_binding(self) -> ImportBatchPatchRequest:
        if self.destination_account_id is not None and self.new_account is not None:
            raise ValueError("choose destination_account_id or new_account, not both")
        if self.account is not None and (
            self.destination_account_id is not None
            or self.new_account is not None
            or self.account_metadata_update is not None
        ):
            raise ValueError("account is a legacy alias; use one stable binding")
        if self.account_metadata_update is not None and self.destination_account_id is None:
            raise ValueError("account_metadata_update requires destination_account_id")
        return self

    # Both are closed sets: an unrecognised value is a 422, not a silent no-op.
    amount_mode: Literal["debit", "credit"] | None = None
    amount_sign: Literal["as_written", "debit_positive"] | None = None


class UndoImportRequest(BaseModel):
    confirm_changed: bool = False


class TransferLegRequest(BaseModel):
    transaction_id: int = Field(gt=0)
    role: TransferLegRole


class TransferLinkRequest(BaseModel):
    legs: list[TransferLegRequest] = Field(min_length=2)
    purpose: str = TransferPurpose.OTHER.value


class TransferSuggestionResponse(BaseModel):
    id: int
    left_transaction_id: int
    right_transaction_id: int
    state: str
    confidence: float
    reason_codes: list[str]
    event_id: int | None


class TransferLegResponse(BaseModel):
    transaction_id: int
    role: str


class TransferEventResponse(BaseModel):
    id: int
    purpose: str
    match_method: str
    legs: list[TransferLegResponse]


class ScenarioRequest(BaseModel):
    cost_minor: int = Field(ge=0)
    horizon_months: int = Field(default=3, ge=1, le=120)
    month: str | None = None
    currency: str = "GBP"


class FxRateResponse(BaseModel):
    id: int
    base_currency: str
    quote_currency: str
    rate: str  # decimal string - never float; see domain/fx.py
    effective_at: date
    source: str | None = None


class FxRateSetRequest(BaseModel):
    base_currency: str
    quote_currency: str
    rate: str  # decimal string - never float; see domain/fx.py
    effective_at: date | None = None


class FxFetchRequest(BaseModel):
    base_currency: str = "GBP"
    on_date: date | None = None


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
        direction_explicit=candidate.direction_explicit,
        currency=candidate.currency,
        account_hint=candidate.account_hint,
        account_id=candidate.account_id,
        signed_amount_minor=candidate.signed_amount_minor,
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
        destination_account_id=batch.destination_account_id,
        new_account=(
            NewAccountRequest(**json.loads(batch.new_account_json))
            if batch.new_account_json
            else None
        ),
        adapter_id=batch.adapter_id,
        detection_confidence=batch.detection_confidence,
        detection_reason_codes=(
            json.loads(batch.detection_reason_codes_json)
            if batch.detection_reason_codes_json
            else []
        ),
        detected_institution=batch.detected_institution,
        detected_account_hint=batch.detected_account_hint,
        suggested_currency=batch.suggested_currency,
        currency_evidence=batch.currency_evidence,
        compatible_account_types=(
            json.loads(batch.compatible_account_types_json)
            if batch.compatible_account_types_json
            else []
        ),
        reconciliation=(
            json.loads(batch.reconciliation_json) if batch.reconciliation_json else None
        ),
        semantic_totals=batch_semantic_totals(batch),
        amount_sign=batch.amount_sign,
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
        destination_account_id: int | None = Form(None),  # noqa: B008
        new_account_name: str | None = Form(None),  # noqa: B008
        new_account_type: AccountType = Form(AccountType.CURRENT),  # noqa: B008
        new_account_currency: str = Form("GBP"),  # noqa: B008
    ) -> ImportBatchResponse:
        content_length = request.headers.get("content-length")
        # A header the client controls must not be able to turn a bad request into a 500;
        # an unparseable one just means the size cap falls back to the copy loop.
        declared_size = int(content_length) if content_length and content_length.isdigit() else None
        try:
            source = stage_upload(file, active_settings, declared_size)
        except UploadRejected as exc:
            status_code = _UPLOAD_ERROR_STATUS.get(exc.code, 422)
            raise HTTPException(
                status_code=status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

        try:
            engine, services = open_services(active_settings)
            try:
                draft = (
                    NewAccountRequest(
                        name=new_account_name,
                        account_type=new_account_type,
                        currency=new_account_currency,
                    )
                    if new_account_name
                    else None
                )
                batch = create_batch(
                    services.uow,
                    source,
                    active_settings,
                    account=account,
                    destination_account_id=destination_account_id,
                    new_account=NewAccountDraft(**draft.model_dump()) if draft else None,
                )
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
                    destination_account_id=request.destination_account_id,
                    new_account=(
                        NewAccountDraft(**request.new_account.model_dump())
                        if request.new_account
                        else None
                    ),
                    excluded_candidate_ids=request.excluded_candidate_ids,
                    account_metadata_update=(
                        request.account_metadata_update.model_dump()
                        if request.account_metadata_update
                        else None
                    ),
                    amount_mode=request.amount_mode,
                    amount_sign=request.amount_sign,
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

    @app.post("/imports/{batch_id}/undo", response_model=ImportBatchResponse)
    def undo_import_batch(
        batch_id: str, request: UndoImportRequest | None = None
    ) -> ImportBatchResponse:
        engine, services = open_services(active_settings)
        try:
            batch = undo_batch(
                services.uow,
                batch_id,
                confirm_changed=request.confirm_changed if request else False,
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
                    id=acc.id,
                    name=acc.name,
                    account_type=acc.account_type,
                    currency=acc.currency,
                    institution=acc.institution,
                    last4=acc.last4,
                    opening_balance_minor=acc.opening_balance_minor,
                    opening_balance_as_of=acc.opening_balance_as_of,
                    active=acc.active,
                )
                for acc in services.uow.accounts.all()
            ]
        finally:
            close_services(engine, services)

    @app.post("/accounts", response_model=AccountResponse)
    def create_account(request: NewAccountRequest) -> AccountResponse:
        engine, services = open_services(active_settings)
        try:
            account = services.uow.accounts.create(
                request.name,
                request.currency,
                request.account_type.value,
                institution=request.institution,
                last4=request.last4,
                opening_balance_minor=request.opening_balance_minor,
                opening_balance_as_of=request.opening_balance_as_of,
            )
            response = AccountResponse(
                id=account.id,
                name=account.name,
                account_type=account.account_type,
                currency=account.currency,
                institution=account.institution,
                last4=account.last4,
                opening_balance_minor=account.opening_balance_minor,
                opening_balance_as_of=account.opening_balance_as_of,
                active=account.active,
            )
            close_services(engine, services)
            return response
        except ValueError as exc:
            close_services(engine, services, False)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            close_services(engine, services, False)
            raise

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
                    signed_amount_minor=(signed_minor(row.amount_minor, row.flow_direction)),
                    account_id=row.account_id,
                )
                for row in rows
            ]
        finally:
            close_services(engine, services)

    def _suggestion_response(
        decision: TransferMatchDecisionModel,
    ) -> TransferSuggestionResponse:
        return TransferSuggestionResponse(
            id=decision.id,
            left_transaction_id=decision.left_transaction_id,
            right_transaction_id=decision.right_transaction_id,
            state=decision.state,
            confidence=decision.confidence,
            reason_codes=json.loads(decision.reason_codes_json),
            event_id=decision.event_id,
        )

    def _event_response(event: TransferEventModel) -> TransferEventResponse:
        return TransferEventResponse(
            id=event.id,
            purpose=event.purpose,
            match_method=event.match_method,
            legs=[
                TransferLegResponse(transaction_id=leg.transaction_id, role=leg.role)
                for leg in event.legs
            ],
        )

    @app.get("/analytics/cash")
    def cash(currency: str = "GBP", as_of: date | None = None) -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            position = cash_position(
                services.uow.accounts.all(), services.uow.transactions.all(), currency, as_of
            )
            return {
                "currency": position.currency,
                "as_of": as_of,
                "cash_minor": position.total_minor,
                "known_subtotal_minor": position.known_subtotal_minor,
                "coverage_status": position.coverage_status,
                "missing_account_ids": list(position.missing_account_ids),
            }
        finally:
            close_services(engine, services)

    @app.get("/transfers/suggestions", response_model=list[TransferSuggestionResponse])
    def transfer_suggestions() -> list[TransferSuggestionResponse]:
        engine, services = open_services(active_settings)
        try:
            return [_suggestion_response(item) for item in services.uow.transfers.suggestions()]
        finally:
            close_services(engine, services)

    @app.post("/transfers/suggestions/{decision_id}/accept", response_model=TransferEventResponse)
    def accept_transfer_suggestion(decision_id: int) -> TransferEventResponse:
        engine, services = open_services(active_settings)
        try:
            event = accept_suggestion(services.uow, decision_id)
            response = _event_response(event)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @app.post(
        "/transfers/suggestions/{decision_id}/dismiss", response_model=TransferSuggestionResponse
    )
    def dismiss_transfer_suggestion(decision_id: int) -> TransferSuggestionResponse:
        engine, services = open_services(active_settings)
        try:
            decision = dismiss_suggestion(services.uow, decision_id)
            response = _suggestion_response(decision)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @app.post("/transfers/link", response_model=TransferEventResponse)
    def link_transfer(request: TransferLinkRequest) -> TransferEventResponse:
        engine, services = open_services(active_settings)
        try:
            event = create_manual_link(
                services.uow,
                [(leg.transaction_id, leg.role.value) for leg in request.legs],
                request.purpose,
            )
            response = _event_response(event)
            close_services(engine, services)
            return response
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @app.delete("/transfers/events/{event_id}")
    def unlink_transfer(event_id: int) -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            event = services.uow.transfers.get_event(event_id)
            if event is None:
                raise BatchError("TRANSFER_EVENT_NOT_FOUND", "transfer event not found", 404)
            services.uow.transfers.delete_event(event_id)
            close_services(engine, services)
            return {"id": event_id, "unlinked": True}
        except BatchError as exc:
            close_services(engine, services, False)
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}
            ) from exc

    @app.get("/fx/rates", response_model=list[FxRateResponse])
    def get_fx_rates(
        base: str | None = None,
        quote: str | None = None,
    ) -> list[FxRateResponse]:
        engine, services = open_services(active_settings)
        try:
            rates = services.uow.fx_rates.all()
            if base:
                rates = [r for r in rates if r.base_currency == base.upper()]
            if quote:
                rates = [r for r in rates if r.quote_currency == quote.upper()]
            return [
                FxRateResponse(
                    id=r.id,
                    base_currency=r.base_currency,
                    quote_currency=r.quote_currency,
                    rate=r.rate,
                    effective_at=r.effective_at,
                    source=r.source,
                )
                for r in rates
            ]
        finally:
            close_services(engine, services)

    @app.post("/fx/rates", response_model=FxRateResponse)
    def set_fx_rate(request: FxRateSetRequest) -> FxRateResponse:
        try:
            rate = Decimal(request.rate)
        except InvalidOperation as exc:
            raise HTTPException(status_code=422, detail=f"invalid rate {request.rate!r}") from exc
        engine, services = open_services(active_settings)
        try:
            effective_at = request.effective_at or date.today()
            model = services.uow.fx_rates.set_rate(
                request.base_currency.upper(),
                request.quote_currency.upper(),
                rate,
                effective_at=effective_at,
            )
            response = FxRateResponse(
                id=model.id,
                base_currency=model.base_currency,
                quote_currency=model.quote_currency,
                rate=model.rate,
                effective_at=model.effective_at,
                source=model.source,
            )
            close_services(engine, services)
            return response
        except Exception:
            close_services(engine, services, False)
            raise

    @app.post("/fx/fetch", response_model=list[FxRateResponse])
    def fetch_fx_rates(request: FxFetchRequest) -> list[FxRateResponse]:
        engine, services = open_services(active_settings)
        try:
            models = fetch_and_store_fx_rates(
                services.uow,
                base_currency=request.base_currency.upper(),
                on_date=request.on_date or date.today(),
            )
            response = [
                FxRateResponse(
                    id=m.id,
                    base_currency=m.base_currency,
                    quote_currency=m.quote_currency,
                    rate=m.rate,
                    effective_at=m.effective_at,
                    source=m.source,
                )
                for m in models
            ]
            close_services(engine, services)
            return response
        except Exception:
            close_services(engine, services, False)
            raise

    @app.get("/analytics/monthly")
    def monthly(month: str | None = None, currency: str = "GBP") -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            return services.analytics.monthly_summary(_month(month), currency=currency).model_dump()
        finally:
            close_services(engine, services)

    @app.get("/analytics/categories")
    def categories(month: str | None = None, currency: str = "GBP") -> list[dict[str, object]]:
        engine, services = open_services(active_settings)
        try:
            return [
                item.model_dump()
                for item in services.analytics.category_spending(_month(month), currency=currency)
            ]
        finally:
            close_services(engine, services)

    @app.get("/budgets")
    def budgets(month: str | None = None, currency: str = "GBP") -> list[dict[str, object]]:
        engine, services = open_services(active_settings)
        try:
            return [
                item.model_dump()
                for item in services.analytics.budget_status(_month(month), currency=currency)
            ]
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
                request.cost_minor,
                request.horizon_months,
                _month(request.month),
                currency=request.currency,
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
    def review(month: str | None = None, currency: str = "GBP") -> dict[str, object]:
        engine, services = open_services(active_settings)
        try:
            return monthly_review_evidence(services.analytics, _month(month), currency=currency)
        finally:
            close_services(engine, services)

    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        html = (web_root / "index.html").read_text(encoding="utf-8")
        return Response(html, media_type="text/html")

    return app


app = create_app()
