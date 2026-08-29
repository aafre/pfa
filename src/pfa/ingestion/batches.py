"""Import batch lifecycle: create, patch, commit, discard, and lazy expiry.

Extraction is synchronous. There is no scheduler - expiry is checked (and applied) on
every read, patch, and commit, and a startup sweep catches whatever nobody revisits.

Commit atomicity is inherited from the existing session boundary (see
pfa.services.runtime.open_services/close_services): this module never opens a second
transaction and never swallows an exception raised while persisting rows.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from pfa.config import Settings
from pfa.db.models import ImportBatchModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.errors import BatchError
from pfa.ingestion.service import ImportService

from .candidates import (
    AMBIGUOUS_SIGN,
    BATCH_ALREADY_COMMITTED,
    BATCH_EXPIRED,
    BATCH_HAS_BLOCKING_ERRORS,
    BATCH_NOT_EDITABLE,
    BATCH_NOT_FOUND,
    ERROR,
    EXTRACTION_FAILED,
    EXTRACTION_TIMEOUT,
    NO_USABLE_ROWS,
    TOO_MANY_ROWS,
    VALID,
    WARNING,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
    candidates_from_json,
    candidates_to_json,
)
from .extractors.csv import CsvStatementExtractor

logger = logging.getLogger("pfa")

# Batches in these statuses still hold candidate rows and are subject to the TTL.
_LIVE_STATUSES = ("preview_ready", "blocked")


@dataclass(slots=True)
class BatchPatch:
    account: str | None = None
    excluded_candidate_ids: list[str] | None = None
    amount_mode: str | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _counts(candidates: list[CandidateTransaction]) -> dict[str, int]:
    return {
        "total": len(candidates),
        "valid": sum(1 for c in candidates if c.state == VALID),
        "warning": sum(1 for c in candidates if c.state == WARNING),
        "error": sum(1 for c in candidates if c.state == ERROR),
        "duplicate": sum(1 for c in candidates if c.duplicate_of is not None),
        "excluded": sum(1 for c in candidates if not c.included),
        "imported": 0,
    }


def batch_candidates(batch: ImportBatchModel) -> list[CandidateTransaction]:
    return candidates_from_json(batch.candidates_json) if batch.candidates_json else []


def batch_issues(batch: ImportBatchModel) -> list[CandidateIssue]:
    return [CandidateIssue(**item) for item in json.loads(batch.issues_json)]


def batch_counts(batch: ImportBatchModel) -> dict[str, int]:
    return json.loads(batch.counts_json) if batch.counts_json else {}


def batch_committed_transaction_ids(batch: ImportBatchModel) -> list[int]:
    if not batch.committed_transaction_ids_json:
        return []
    return list(json.loads(batch.committed_transaction_ids_json))


def _run_extraction(source: StatementSource, settings: Settings) -> ExtractionResult:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future: concurrent.futures.Future[ExtractionResult] = pool.submit(
        CsvStatementExtractor().extract, source
    )
    try:
        return future.result(timeout=settings.extraction_timeout_seconds)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _fail(batch: ImportBatchModel, uow: UnitOfWork, code: str, message: str) -> ImportBatchModel:
    batch.status = "failed"
    batch.issues_json = json.dumps([{"code": code, "message": message, "severity": ERROR}])
    batch.counts_json = json.dumps(_counts([]))
    batch.updated_at = _now()
    return uow.import_batches.add(batch)


def create_batch(
    uow: UnitOfWork,
    source: StatementSource,
    settings: Settings,
    *,
    account: str | None = None,
) -> ImportBatchModel:
    now = _now()
    batch = ImportBatchModel(
        id=uuid.uuid4().hex,
        original_filename=source.original_filename,
        media_type=source.media_type,
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        extractor=CsvStatementExtractor.name,
        status="extracting",
        destination_account=account,
        issues_json="[]",
        counts_json=json.dumps(_counts([])),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=settings.import_batch_ttl_hours),
    )

    try:
        extraction = _run_extraction(source, settings)
    except concurrent.futures.TimeoutError:
        return _fail(batch, uow, EXTRACTION_TIMEOUT, "extraction took too long; try a smaller file")
    except Exception as exc:  # parser crash: sanitize, never leak paths/text/tracebacks
        logger.warning("extraction_failed exception_type=%s", type(exc).__name__)
        return _fail(batch, uow, EXTRACTION_FAILED, "could not process the uploaded file")

    candidates = extraction.candidates
    if account:
        for candidate in candidates:
            candidate.account_hint = account
    if len(candidates) > settings.max_candidate_rows:
        candidates = candidates[: settings.max_candidate_rows]
        extraction.issues.append(
            CandidateIssue(
                TOO_MANY_ROWS,
                f"only the first {settings.max_candidate_rows} rows were loaded",
                WARNING,
            )
        )

    service = ImportService(uow)
    service.validate(candidates)
    service.resolve_duplicates(candidates)

    if not candidates and not any(issue.severity == ERROR for issue in extraction.issues):
        extraction.issues.append(CandidateIssue(NO_USABLE_ROWS, "no transactions were found"))

    batch.detected_account = extraction.detected_account
    batch.detected_currency = extraction.detected_currency
    batch.page_count = extraction.page_count
    blocked = any(issue.severity == ERROR for issue in extraction.issues)
    batch.status = "blocked" if blocked else "preview_ready"
    batch.candidates_json = candidates_to_json(candidates)
    batch.issues_json = json.dumps([asdict(issue) for issue in extraction.issues])
    batch.counts_json = json.dumps(_counts(candidates))
    return uow.import_batches.add(batch)


def expire_if_due(batch: ImportBatchModel, now: datetime | None = None) -> bool:
    """Flips a due batch to expired and drops its staged rows. Returns True if it just did."""
    now = now or _now()
    if batch.status in _LIVE_STATUSES and batch.expires_at <= now:
        batch.status = "expired"
        batch.candidates_json = None
        batch.updated_at = now
        return True
    return False


def load_batch(uow: UnitOfWork, batch_id: str) -> ImportBatchModel:
    batch = uow.import_batches.get(batch_id)
    if batch is None:
        raise BatchError(BATCH_NOT_FOUND, "import batch not found", 404)
    if expire_if_due(batch):
        uow.import_batches.add(batch)
    if batch.status == "expired":
        raise BatchError(BATCH_EXPIRED, "import batch expired; upload the statement again", 410)
    return batch


def _resolve_ambiguous_amount(candidate: CandidateTransaction, mode: str) -> None:
    """Lets the user pick a sign convention for a debit/credit row that left both or
    neither column populated. Amount is cleared so validate() re-parses it fresh.
    """
    value = candidate.raw_fields.get(mode, "")
    if not value:
        return
    candidate.raw_fields["amount"] = ("-" + value.lstrip("+-")) if mode == "debit" else value
    candidate.amount_minor = None
    candidate.direction = None
    candidate.issues = [issue for issue in candidate.issues if issue.code != AMBIGUOUS_SIGN]


def apply_patch(uow: UnitOfWork, batch_id: str, patch: BatchPatch) -> ImportBatchModel:
    batch = load_batch(uow, batch_id)
    if batch.status != "preview_ready":
        raise BatchError(BATCH_NOT_EDITABLE, f"batch is {batch.status}; nothing to modify", 409)

    candidates = batch_candidates(batch)

    if patch.account is not None:
        batch.destination_account = patch.account
        for candidate in candidates:
            candidate.account_hint = patch.account

    if patch.amount_mode in ("debit", "credit"):
        for candidate in candidates:
            if any(issue.code == AMBIGUOUS_SIGN for issue in candidate.issues):
                _resolve_ambiguous_amount(candidate, patch.amount_mode)

    if patch.excluded_candidate_ids is not None:
        excluded = set(patch.excluded_candidate_ids)
        for candidate in candidates:
            candidate.included = candidate.candidate_id not in excluded

    service = ImportService(uow)
    service.validate(candidates)
    service.resolve_duplicates(candidates)

    batch.candidates_json = candidates_to_json(candidates)
    batch.counts_json = json.dumps(_counts(candidates))
    batch.updated_at = _now()
    return uow.import_batches.add(batch)


def commit_batch(uow: UnitOfWork, batch_id: str, settings: Settings) -> ImportBatchModel:
    batch = load_batch(uow, batch_id)
    if batch.status != "preview_ready":
        raise BatchError(BATCH_NOT_EDITABLE, f"batch is {batch.status}; nothing to commit", 409)

    candidates = batch_candidates(batch)
    service = ImportService(uow)
    service.resolve_duplicates(candidates)  # recheck against the ledger right before commit

    blocking = [c for c in candidates if c.included and c.state == ERROR]
    if blocking:
        raise BatchError(
            BATCH_HAS_BLOCKING_ERRORS,
            f"{len(blocking)} included row(s) have blocking errors; exclude or fix them first",
            422,
        )

    committed = service.commit(candidates, source_label=f"upload:{batch.id}")

    batch.status = "committed"
    batch.committed_at = _now()
    batch.committed_transaction_ids_json = json.dumps([t.id for t in committed])
    batch.candidates_json = candidates_to_json(candidates)
    counts = _counts(candidates)
    counts["imported"] = len(committed)
    batch.counts_json = json.dumps(counts)
    batch.updated_at = _now()
    return uow.import_batches.add(batch)


def discard_batch(uow: UnitOfWork, batch_id: str) -> ImportBatchModel:
    batch = load_batch(uow, batch_id)
    if batch.status == "committed":
        raise BatchError(BATCH_ALREADY_COMMITTED, "a committed batch cannot be discarded", 409)
    batch.status = "discarded"
    batch.candidates_json = None
    batch.updated_at = _now()
    return uow.import_batches.add(batch)


def sweep_expired_batches(uow: UnitOfWork) -> int:
    """Startup catch-all for batches nobody revisited before their TTL passed."""
    now = _now()
    expired = uow.import_batches.list_expired(now)
    for batch in expired:
        batch.status = "expired"
        batch.candidates_json = None
        batch.updated_at = now
    return len(expired)
