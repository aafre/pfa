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
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from pfa.config import Settings
from pfa.db.models import AccountModel, ImportBatchModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.accounts import AccountType
from pfa.domain.errors import BatchError, ImportRowError
from pfa.ingestion.service import ImportService

from .candidates import (
    ACCOUNT_CURRENCY_MISMATCH,
    ACCOUNT_INACTIVE,
    ACCOUNT_NOT_FOUND,
    ACCOUNT_REQUIRED,
    ACCOUNT_TYPE_MISMATCH,
    AMBIGUOUS_SIGN,
    BATCH_ALREADY_COMMITTED,
    BATCH_EXPIRED,
    BATCH_HAS_BLOCKING_ERRORS,
    BATCH_NOT_EDITABLE,
    BATCH_NOT_FOUND,
    ERROR,
    EXTRACTION_FAILED,
    EXTRACTION_TIMEOUT,
    GENERIC_SIGN_CONFIRMATION_REQUIRED,
    INVALID_ACCOUNT_DRAFT,
    NO_USABLE_ROWS,
    RECONCILIATION_INCOMPLETE,
    RECONCILIATION_MISMATCH,
    STATEMENT_YEAR_INFERRED,
    TOO_MANY_ROWS,
    VALID,
    WARNING,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementExtractor,
    StatementSource,
    candidates_from_json,
    candidates_to_json,
    is_year_bearing_date,
    parse_date,
)
from .dialects import DIALECTS, Dialect, detect_adapter
from .extractors.csv import CsvStatementExtractor
from .extractors.ocr import OcrFallbackPdfExtractor
from .extractors.pdf import clean_amount_text

logger = logging.getLogger("pfa")

# Batches in these statuses still hold candidate rows and are subject to the TTL.
_LIVE_STATUSES = ("preview_ready", "blocked")

# How to read a positive figure in the statement's single amount column. Never inferred
# from the data: an all-positive statement is genuinely ambiguous between a credit card
# and a month with no refunds, so the user states the convention before committing.
AMOUNT_SIGN_CONVENTIONS = ("as_written", "debit_positive")


@dataclass(slots=True)
class NewAccountDraft:
    name: str
    account_type: str = AccountType.CURRENT.value
    currency: str = "GBP"
    institution: str | None = None
    last4: str | None = None
    opening_balance_minor: int = 0
    opening_balance_as_of: date | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "account_type": self.account_type,
            "currency": self.currency,
            "institution": self.institution,
            "last4": self.last4,
            "opening_balance_minor": self.opening_balance_minor,
            "opening_balance_as_of": self.opening_balance_as_of.isoformat()
            if self.opening_balance_as_of
            else None,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> NewAccountDraft:
        as_of = value.get("opening_balance_as_of")
        opening = value.get("opening_balance_minor", 0)
        return cls(
            name=str(value.get("name", "")),
            account_type=str(value.get("account_type", AccountType.CURRENT.value)),
            currency=str(value.get("currency", "GBP")),
            institution=str(value["institution"]) if value.get("institution") else None,
            last4=str(value["last4"]) if value.get("last4") else None,
            opening_balance_minor=int(str(opening)),
            opening_balance_as_of=date.fromisoformat(str(as_of)) if as_of else None,
        )


@dataclass(slots=True)
class BatchPatch:
    account: str | None = None  # deprecated label compatibility
    destination_account_id: int | None = None
    new_account: NewAccountDraft | None = None
    excluded_candidate_ids: list[str] | None = None
    amount_mode: str | None = None
    amount_sign: str | None = None


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


def batch_semantic_totals(batch: ImportBatchModel) -> dict[str, int]:
    """Calculate preview figures from candidate signs, never from model-generated text."""
    spending = refunds = transfers = repayments = money_in = 0
    for candidate in batch_candidates(batch):
        signed = candidate.signed_amount_minor
        if signed is None or not candidate.included:
            continue
        description = candidate.raw_description.upper()
        kind = candidate.kind
        if kind is None:
            if (
                batch.adapter_id in {"amex_uk_csv", "amex_uk_pdf"}
                and signed > 0
                and "PAYMENT RECEIVED" in description
            ):
                kind = "transfer"
            else:
                kind = "expense" if signed < 0 else "income"
        if kind in {"expense", "fee"}:
            spending += abs(signed)
        elif kind == "refund":
            refunds += abs(signed)
            spending -= abs(signed)
        elif kind == "transfer":
            transfers += abs(signed)
            if "CREDIT_CARD_PAYMENT" in (candidate.transfer_purpose or "").upper() or (
                "PAYMENT RECEIVED" in description and signed > 0
            ):
                repayments += abs(signed)
        elif kind == "income":
            money_in += max(signed, 0)
    return {
        "money_in_minor": money_in,
        "spending_minor": spending,
        "refunds_minor": refunds,
        "transfers_minor": transfers,
        "repayments_minor": repayments,
    }


def _extractor_for(
    source: StatementSource,
    settings: Settings,
    dialect: Dialect,
    account_currency: str = "GBP",
) -> StatementExtractor:
    """Picks only the extraction engine; statement semantics come from content detection."""
    if source.path.suffix.lower() == ".pdf":
        return OcrFallbackPdfExtractor(
            settings=settings,
            max_pdf_pages=settings.max_pdf_pages,
            max_candidate_rows=settings.max_candidate_rows,
            dialect=dialect,
            currency=account_currency,
        )
    return CsvStatementExtractor(dialect=dialect, currency=account_currency)


def _run_extraction(
    extractor: StatementExtractor, source: StatementSource, settings: Settings
) -> ExtractionResult:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future: concurrent.futures.Future[ExtractionResult] = pool.submit(extractor.extract, source)
    try:
        return future.result(timeout=settings.extraction_timeout_seconds)
    except concurrent.futures.TimeoutError:
        # ponytail: a running parser thread cannot be killed and still holds the staged
        # file open, so the request's own unlink can lose to it. Hand cleanup to the
        # worker's completion rather than leaking the statement; upgrade path is a
        # cancellable out-of-process extractor if nominal timeouts stop being enough.
        future.add_done_callback(lambda _: source.path.unlink(missing_ok=True))
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _fail(batch: ImportBatchModel, uow: UnitOfWork, code: str, message: str) -> ImportBatchModel:
    batch.status = "failed"
    batch.issues_json = json.dumps([{"code": code, "message": message, "severity": ERROR}])
    batch.counts_json = json.dumps(_counts([]))
    batch.updated_at = _now()
    return uow.import_batches.add(batch)


def _normalize_dates(
    candidates: list[CandidateTransaction],
    dialect: Dialect,
    statement_year: int | None = None,
) -> CandidateIssue | None:
    """Resolves every year-less date (`Jul31`, `21 Jul`) against the year the rest of this
    statement's dates carry, then rewrites the candidate's date string to ISO so every later
    parse - validation, commit - sees that same resolved year, never whatever year the
    import happens to run in.

    Returns a warning issue when no row in the statement carried a year of its own, so the
    fallback to today's year is visible in the preview rather than silent.
    """
    years_seen: list[int] = []
    for candidate in candidates:
        text = candidate.transaction_date
        if text and is_year_bearing_date(text, dialect.date_order):
            try:
                years_seen.append(parse_date(text, dialect.date_order).year)
            except ImportRowError:
                continue
    inferred_year = statement_year or (
        Counter(years_seen).most_common(1)[0][0] if years_seen else date.today().year
    )

    used_fallback = False
    for candidate in candidates:
        for attr in ("transaction_date", "posted_date"):
            text = getattr(candidate, attr)
            if not text:
                continue
            try:
                resolved = parse_date(text, dialect.date_order, inferred_year)
            except ImportRowError:
                continue
            if not is_year_bearing_date(text, dialect.date_order):
                used_fallback = True
            setattr(candidate, attr, resolved.isoformat())

    if years_seen or not used_fallback:
        return None
    return CandidateIssue(
        STATEMENT_YEAR_INFERRED,
        f"no date in this statement carried its own year; {inferred_year} was assumed for "
        "year-less dates - check the preview before committing",
        WARNING,
    )


_BINDING_CODES = {
    ACCOUNT_CURRENCY_MISMATCH,
    ACCOUNT_INACTIVE,
    ACCOUNT_NOT_FOUND,
    ACCOUNT_REQUIRED,
    ACCOUNT_TYPE_MISMATCH,
    INVALID_ACCOUNT_DRAFT,
    GENERIC_SIGN_CONFIRMATION_REQUIRED,
    RECONCILIATION_INCOMPLETE,
    RECONCILIATION_MISMATCH,
}


def _draft_from_batch(batch: ImportBatchModel) -> NewAccountDraft | None:
    if not batch.new_account_json:
        return None
    try:
        value = json.loads(batch.new_account_json)
        return NewAccountDraft.from_dict(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _binding_issues(
    batch: ImportBatchModel, uow: UnitOfWork, dialect: Dialect
) -> list[CandidateIssue]:
    issues: list[CandidateIssue] = []
    account: AccountModel | None = None
    draft = _draft_from_batch(batch)
    if batch.destination_account_id is not None:
        account = uow.accounts.get(batch.destination_account_id)
        if account is None:
            issues.append(CandidateIssue(ACCOUNT_NOT_FOUND, "select an existing account"))
        elif not account.active:
            issues.append(CandidateIssue(ACCOUNT_INACTIVE, "the selected account is inactive"))
    elif draft is None and dialect.compatible_account_types:
        issues.append(
            CandidateIssue(
                ACCOUNT_REQUIRED,
                "select a compatible account or create one before committing this statement",
            )
        )

    if draft is not None:
        try:
            account_type = AccountType(draft.account_type)
        except ValueError:
            issues.append(CandidateIssue(INVALID_ACCOUNT_DRAFT, "choose a valid account type"))
        else:
            if not draft.name.strip() or draft.currency.upper() not in {
                "GBP",
                "INR",
                "USD",
                "EUR",
                "JPY",
            }:
                issues.append(
                    CandidateIssue(INVALID_ACCOUNT_DRAFT, "account name and currency are invalid")
                )
            if draft.last4 is not None and (len(draft.last4) != 4 or not draft.last4.isdigit()):
                issues.append(
                    CandidateIssue(
                        INVALID_ACCOUNT_DRAFT, "last four must contain exactly four digits"
                    )
                )
            if (
                dialect.compatible_account_types
                and account_type not in dialect.compatible_account_types
            ):
                expected = ", ".join(
                    sorted(item.value for item in dialect.compatible_account_types)
                )
                issues.append(
                    CandidateIssue(
                        ACCOUNT_TYPE_MISMATCH,
                        f"this statement requires a {expected} account",
                    )
                )
            for existing in uow.accounts.by_name(draft.name.strip()):
                if (
                    existing.account_type != account_type.value
                    or existing.currency.upper() != draft.currency.upper()
                ):
                    issues.append(
                        CandidateIssue(
                            ACCOUNT_CURRENCY_MISMATCH,
                            "an account with this name has a conflicting type or "
                            "currency; choose another name",
                        )
                    )
                    break
    if account is not None:
        if (
            dialect.compatible_account_types
            and AccountType(account.account_type) not in dialect.compatible_account_types
        ):
            expected = ", ".join(sorted(item.value for item in dialect.compatible_account_types))
            issues.append(
                CandidateIssue(
                    ACCOUNT_TYPE_MISMATCH,
                    f"this statement is for {expected}; selected account is {account.account_type}",
                )
            )
        detected_currency = (batch.detected_currency or "GBP").upper()
        if account.currency.upper() != detected_currency:
            issues.append(
                CandidateIssue(
                    ACCOUNT_CURRENCY_MISMATCH,
                    f"statement currency {detected_currency} does not match "
                    f"account currency {account.currency}",
                )
            )
    if (
        batch.adapter_id == "generic"
        and batch.amount_sign is None
        and batch.destination_account is None
    ):
        # Preserve signed generic imports for the legacy API; unsigned rows still need a
        # deliberate convention before they can be committed.
        candidates = batch_candidates(batch)
        if candidates and all(c.amount_minor is None or c.direction != "debit" for c in candidates):
            issues.append(
                CandidateIssue(
                    GENERIC_SIGN_CONFIRMATION_REQUIRED,
                    "confirm how positive statement amounts should be interpreted",
                )
            )
    return issues


def _reconciliation_account_type(batch: ImportBatchModel, uow: UnitOfWork) -> AccountType:
    if batch.destination_account_id is not None:
        account = uow.accounts.get(batch.destination_account_id)
        if account is not None:
            try:
                return AccountType(account.account_type)
            except ValueError:
                return AccountType.CURRENT
    draft = _draft_from_batch(batch)
    if draft is not None:
        try:
            return AccountType(draft.account_type)
        except ValueError:
            return AccountType.CURRENT
    return AccountType.CURRENT


def _set_reconciliation(
    batch: ImportBatchModel,
    candidates: list[CandidateTransaction],
    uow: UnitOfWork,
) -> list[CandidateIssue]:
    from .reconciliation import reconcile_candidates

    result = reconcile_candidates(candidates, _reconciliation_account_type(batch, uow))
    batch.reconciliation_json = json.dumps(result)
    if batch.adapter_id not in (None, "generic"):
        if result["status"] == "mismatch":
            return [CandidateIssue(RECONCILIATION_MISMATCH, "statement balances do not reconcile")]
        if result["status"] == "incomplete":
            return [
                CandidateIssue(RECONCILIATION_INCOMPLETE, "not every statement row is included")
            ]
    return []


def _set_batch_issues(
    batch: ImportBatchModel, uow: UnitOfWork, dialect: Dialect, base: list[CandidateIssue]
) -> None:
    issues = [issue for issue in base if issue.code not in _BINDING_CODES]
    issues.extend(_binding_issues(batch, uow, dialect))
    issues.extend(_set_reconciliation(batch, batch_candidates(batch), uow))
    batch.issues_json = json.dumps([asdict(issue) for issue in issues])
    batch.status = (
        "blocked" if any(issue.severity == ERROR for issue in issues) else "preview_ready"
    )


def create_batch(
    uow: UnitOfWork,
    source: StatementSource,
    settings: Settings,
    *,
    account: str | None = None,
    destination_account_id: int | None = None,
    new_account: NewAccountDraft | None = None,
) -> ImportBatchModel:
    now = _now()
    selected = (
        uow.accounts.get(destination_account_id) if destination_account_id is not None else None
    )
    if account and selected is None:
        selected = uow.accounts.get_by_name(account)
        destination_account_id = selected.id if selected is not None else None
    account_currency = (
        selected.currency
        if selected is not None
        else (new_account.currency if new_account else "GBP")
    )

    detection = detect_adapter(source.path, source.media_type)
    dialect = detection.dialect
    extractor = _extractor_for(source, settings, dialect, account_currency=account_currency)

    batch = ImportBatchModel(
        id=uuid.uuid4().hex,
        original_filename=source.original_filename,
        media_type=source.media_type,
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        extractor=extractor.name,
        status="extracting",
        destination_account=selected.name
        if selected is not None
        else (new_account.name if new_account else account),
        destination_account_id=destination_account_id,
        new_account_json=json.dumps(new_account.as_dict()) if new_account else None,
        adapter_id=dialect.adapter_id,
        detection_confidence=detection.confidence,
        detection_reason_codes_json=json.dumps(list(detection.reason_codes)),
        detected_institution=detection.institution,
        detected_account_hint=detection.account_hint,
        amount_sign=dialect.default_sign,
        issues_json="[]",
        counts_json=json.dumps(_counts([])),
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=settings.import_batch_ttl_hours),
    )

    try:
        extraction = _run_extraction(extractor, source, settings)
    except concurrent.futures.TimeoutError:
        return _fail(batch, uow, EXTRACTION_TIMEOUT, "extraction took too long; try a smaller file")
    except Exception as exc:  # parser crash: sanitize, never leak paths/text/tracebacks
        logger.warning("extraction_failed exception_type=%s", type(exc).__name__)
        return _fail(batch, uow, EXTRACTION_FAILED, "could not process the uploaded file")

    candidates = extraction.candidates
    if destination_account_id is not None and selected is not None:
        for candidate in candidates:
            candidate.account_hint = selected.name
            candidate.account_id = selected.id
    elif new_account is not None:
        for candidate in candidates:
            candidate.account_hint = new_account.name
    elif account:
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

    year_issue = _normalize_dates(candidates, dialect, extraction.statement_year)
    if year_issue:
        extraction.issues.append(year_issue)

    service = ImportService(uow)
    service.validate(candidates)
    if batch.amount_sign:
        for candidate in candidates:
            _apply_amount_sign(candidate, batch.amount_sign)
    service.resolve_duplicates(candidates)

    if not candidates and not any(issue.severity == ERROR for issue in extraction.issues):
        extraction.issues.append(CandidateIssue(NO_USABLE_ROWS, "no transactions were found"))

    parsed_dates: list[date] = []
    for candidate in candidates:
        if not candidate.transaction_date:
            continue
        try:
            parsed_dates.append(date.fromisoformat(candidate.transaction_date))
        except ValueError:
            continue
    if parsed_dates:
        batch.statement_start = min(parsed_dates)
        batch.statement_end = max(parsed_dates)

    batch.detected_account = extraction.detected_account
    batch.detected_currency = extraction.detected_currency or account_currency
    batch.detected_institution = extraction.detected_institution or detection.institution
    batch.detected_account_hint = extraction.detected_account_hint or detection.account_hint
    batch.page_count = extraction.page_count
    batch.candidates_json = candidates_to_json(candidates)
    _set_batch_issues(batch, uow, dialect, extraction.issues)
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
        # The purge is a TTL/privacy boundary, not part of the caller's transaction: the
        # request that discovers expiry goes on to fail with 410 and would roll it back.
        uow.session.commit()
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


def _apply_amount_sign(candidate: CandidateTransaction, convention: str) -> None:
    """Re-reads a row's flow direction from its single amount column under the statement's
    sign convention. A credit-card export writes a purchase as a positive figure, which
    `as_written` books as income.

    The direction is re-derived from the raw text rather than flipped, so sending a
    convention twice - or switching back - always lands on the same answer. Rows whose
    source stated the direction in its own debit/credit column - or an explicit CR/CREDIT
    marker, own-line or inline - are left alone: their convention is not in doubt, and the
    extractor already resolved it.
    """
    if candidate.direction is None:
        return
    if candidate.direction_explicit:
        return
    if "debit" in candidate.raw_fields or "credit" in candidate.raw_fields:
        return
    amount = candidate.raw_fields.get("amount", "")
    if not amount.strip():
        return
    _, negative = clean_amount_text(amount)
    if convention == "debit_positive":
        candidate.direction = "credit" if negative else "debit"
    else:
        candidate.direction = "debit" if negative else "credit"


def _batch_dialect(batch: ImportBatchModel) -> Dialect:
    return DIALECTS.get(batch.adapter_id or "generic", DIALECTS["generic"])


def apply_patch(uow: UnitOfWork, batch_id: str, patch: BatchPatch) -> ImportBatchModel:
    batch = load_batch(uow, batch_id)
    if batch.status not in ("preview_ready", "blocked"):
        raise BatchError(BATCH_NOT_EDITABLE, f"batch is {batch.status}; nothing to modify", 409)
    if patch.destination_account_id is not None and patch.new_account is not None:
        raise BatchError(ACCOUNT_REQUIRED, "choose an existing account or create a new one", 422)
    if patch.amount_sign is not None and batch.adapter_id not in (None, "generic"):
        raise BatchError(
            GENERIC_SIGN_CONFIRMATION_REQUIRED,
            "recognized statement formats determine amount signs automatically",
            422,
        )

    candidates = batch_candidates(batch)

    if patch.account is not None:
        selected = uow.accounts.get_by_name(patch.account)
        batch.destination_account = patch.account
        batch.destination_account_id = selected.id if selected is not None else None
        batch.new_account_json = (
            None
            if selected is not None
            else json.dumps(NewAccountDraft(name=patch.account).as_dict())
        )
        for candidate in candidates:
            candidate.account_hint = patch.account
            candidate.account_id = selected.id if selected is not None else None

    if patch.destination_account_id is not None:
        selected = uow.accounts.get(patch.destination_account_id)
        batch.destination_account_id = patch.destination_account_id
        batch.destination_account = selected.name if selected is not None else None
        batch.new_account_json = None
        for candidate in candidates:
            candidate.account_hint = selected.name if selected is not None else None
            candidate.account_id = patch.destination_account_id

    if patch.new_account is not None:
        batch.destination_account_id = None
        batch.destination_account = patch.new_account.name
        batch.new_account_json = json.dumps(patch.new_account.as_dict())
        for candidate in candidates:
            candidate.account_hint = patch.new_account.name
            candidate.account_id = None

    if patch.amount_mode in ("debit", "credit"):
        for candidate in candidates:
            if any(issue.code == AMBIGUOUS_SIGN for issue in candidate.issues):
                _resolve_ambiguous_amount(candidate, patch.amount_mode)

    if patch.excluded_candidate_ids is not None:
        excluded = set(patch.excluded_candidate_ids)
        for candidate in candidates:
            candidate.included = candidate.candidate_id not in excluded

    if patch.amount_sign in AMOUNT_SIGN_CONVENTIONS:
        batch.amount_sign = patch.amount_sign

    service = ImportService(uow)
    service.validate(candidates)
    if batch.amount_sign:
        for candidate in candidates:
            # Re-applied from the stored convention on every patch, not only the one
            # that set it. Directions do survive in the candidate blob today, but only
            # because validate() happens to skip rows it has already parsed; anything
            # that clears an amount would quietly hand that row back to as_written.
            # Still after validate (which sets direction) and before duplicate
            # resolution, whose fingerprint covers the signed amount.
            _apply_amount_sign(candidate, batch.amount_sign)
    service.resolve_duplicates(candidates)

    batch.candidates_json = candidates_to_json(candidates)
    batch.counts_json = json.dumps(_counts(candidates))
    base_issues = batch_issues(batch)
    _set_batch_issues(batch, uow, _batch_dialect(batch), base_issues)
    batch.updated_at = _now()
    return uow.import_batches.add(batch)


def _account_for_commit(batch: ImportBatchModel, uow: UnitOfWork) -> AccountModel | None:
    dialect = _batch_dialect(batch)
    issues = _binding_issues(batch, uow, dialect)
    if any(issue.severity == ERROR for issue in issues):
        issue = next(issue for issue in issues if issue.severity == ERROR)
        raise BatchError(issue.code, issue.message, 422)
    if batch.destination_account_id is not None:
        account = uow.accounts.get(batch.destination_account_id)
        if account is None:  # guarded above; keeps the type checker honest
            raise BatchError(ACCOUNT_NOT_FOUND, "select an existing account", 422)
        return account
    draft = _draft_from_batch(batch)
    if draft is not None:
        return uow.accounts.create(
            draft.name,
            draft.currency,
            draft.account_type,
            institution=draft.institution,
            last4=draft.last4,
            opening_balance_minor=draft.opening_balance_minor,
            opening_balance_as_of=draft.opening_balance_as_of,
        )
    return None


def commit_batch(uow: UnitOfWork, batch_id: str, settings: Settings) -> ImportBatchModel:
    batch = load_batch(uow, batch_id)
    if batch.status != "preview_ready":
        raise BatchError(BATCH_NOT_EDITABLE, f"batch is {batch.status}; nothing to commit", 409)

    candidates = batch_candidates(batch)
    account = _account_for_commit(batch, uow)
    if account is not None:
        for candidate in candidates:
            candidate.account_id = account.id
            candidate.account_hint = account.name
    service = ImportService(uow)
    service.validate(candidates)
    service.resolve_duplicates(candidates)  # recheck against the ledger right before commit

    blocking = [c for c in candidates if c.included and c.state == ERROR]
    if blocking:
        raise BatchError(
            BATCH_HAS_BLOCKING_ERRORS,
            f"{len(blocking)} included row(s) have blocking errors; exclude or fix them first",
            422,
        )

    committed = service.commit(
        candidates,
        source_label=f"upload:{batch.id}",
        destination_account_id=account.id if account is not None else None,
    )
    from .transfers import match_transfers

    match_transfers(uow)

    batch.status = "committed"
    batch.committed_at = _now()
    batch.committed_transaction_ids_json = json.dumps([t.id for t in committed])
    # Privacy boundary: staged rows are dropped once they are in the ledger, same as on
    # expiry. The counts and the committed transaction ids are the surviving receipt.
    batch.candidates_json = None
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
