from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pfa.db.models import MerchantRuleModel, TransactionModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.errors import ImportRowError
from pfa.domain.money import Money
from pfa.domain.transactions import (
    ClassificationSource,
    SpendingCategory,
    TransactionKind,
    TransferPurpose,
)
from pfa.observability import TimedOperation

from .candidates import (
    DUPLICATE_ROW,
    ERROR,
    INVALID_AMOUNT,
    INVALID_DATE,
    MISSING_DESCRIPTION,
    UNKNOWN_CATEGORY,
    UNKNOWN_KIND,
    UNKNOWN_TRANSFER_PURPOSE,
    UNSUPPORTED_CURRENCY,
    WARNING,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
)
from .categorizer import Classification, classify_known
from .extractors.csv import CsvStatementExtractor
from .fingerprint import transaction_fingerprint
from .normalizer import merchant_from_description, normalize_description


class Classifier(Protocol):
    def classify(self, description: str, signed_amount_minor: int) -> Classification | None: ...


@dataclass(slots=True)
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    requires_classification: int = 0
    errors: list[str] = field(default_factory=list)


def _parse_date(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ImportRowError(f"invalid date {value!r}")


def _parse_amount(value: str) -> tuple[int, int]:
    try:
        decimal = Decimal(value.replace(",", "").replace("£", "").strip())
    except InvalidOperation as exc:
        raise ImportRowError(f"invalid amount {value!r}") from exc
    sign = -1 if decimal < 0 else 1
    return sign, Money.from_major(abs(decimal)).minor


def _non_member(value: str, enum: type[StrEnum]) -> str | None:
    """Returns the ValueError message when value is not a member of enum."""
    try:
        enum(value)
    except ValueError as exc:
        return str(exc)
    return None


def _classification(
    candidate: CandidateTransaction, sign: int, classifier: Classifier | None
) -> Classification:
    if candidate.kind:
        return Classification(
            TransactionKind(candidate.kind.lower()),
            SpendingCategory(candidate.category) if candidate.category else None,
            TransferPurpose(candidate.transfer_purpose) if candidate.transfer_purpose else None,
            ClassificationSource.IMPORT,
            None,
            "source-provided classification",
        )
    known = classify_known(candidate.raw_description)
    if known:
        return known
    if classifier:
        result = classifier.classify(
            candidate.raw_description, sign * (candidate.amount_minor or 0)
        )
        if result:
            return result
    return Classification(
        TransactionKind.EXPENSE if sign < 0 else TransactionKind.INCOME,
        source=ClassificationSource.UNKNOWN,
        confidence=None,
        reason="requires review",
    )


def _classification_from_rule(rule: MerchantRuleModel) -> Classification:
    return Classification(
        kind=TransactionKind(rule.kind or TransactionKind.UNKNOWN.value),
        category=SpendingCategory(rule.category) if rule.category else None,
        transfer_purpose=TransferPurpose(rule.transfer_purpose) if rule.transfer_purpose else None,
        source=ClassificationSource.RULE,
        confidence=1.0,
        reason="persisted merchant rule",
    )


def _validate_candidate(candidate: CandidateTransaction) -> None:
    try:
        _parse_date(candidate.transaction_date or "")
    except ImportRowError as exc:
        candidate.add_issue(INVALID_DATE, str(exc))
        return
    if not candidate.raw_description:
        candidate.add_issue(MISSING_DESCRIPTION, "missing description")
        return
    if candidate.amount_minor is None:
        try:
            sign, amount_minor = _parse_amount(candidate.raw_fields.get("amount", ""))
        except ImportRowError as exc:
            candidate.add_issue(INVALID_AMOUNT, str(exc))
            return
        candidate.amount_minor = amount_minor
        candidate.direction = "debit" if sign < 0 else "credit"
    candidate.normalized_description = normalize_description(candidate.raw_description)
    if candidate.currency != "GBP":
        candidate.add_issue(
            UNSUPPORTED_CURRENCY,
            f"unsupported currency {candidate.currency!r}; PFA v0.1 supports GBP only",
        )
        return
    if candidate.posted_date:
        try:
            _parse_date(candidate.posted_date)
        except ImportRowError as exc:
            candidate.add_issue(INVALID_DATE, str(exc))
            return
    if candidate.kind and _non_member(candidate.kind.lower(), TransactionKind):
        candidate.add_issue(UNKNOWN_KIND, f"unknown transaction kind {candidate.kind.lower()!r}")
        return
    for value, enum, code in (
        (candidate.category, SpendingCategory, UNKNOWN_CATEGORY),
        (candidate.transfer_purpose, TransferPurpose, UNKNOWN_TRANSFER_PURPOSE),
    ):
        message = _non_member(value, enum) if value else None
        if message:
            candidate.add_issue(code, message)
            return


class ImportService:
    def __init__(self, unit_of_work: UnitOfWork, classifier: Classifier | None = None):
        self.uow = unit_of_work
        self.classifier = classifier

    def validate(self, candidates: Sequence[CandidateTransaction]) -> None:
        """Attaches issues, never raises. A row stops at its first blocking problem."""
        for candidate in candidates:
            if candidate.state != ERROR:
                _validate_candidate(candidate)

    def resolve_duplicates(self, candidates: Sequence[CandidateTransaction]) -> None:
        """Fingerprints valid rows, occurrence-aware, and matches them against the ledger."""
        occurrences: dict[tuple[str, str, int, str, str], int] = {}
        for candidate in candidates:
            candidate.issues = [i for i in candidate.issues if i.code != DUPLICATE_ROW]
            signed = candidate.signed_amount_minor
            if candidate.state == ERROR or signed is None:
                continue
            key = (
                candidate.account_hint or "Main account",
                candidate.transaction_date or "",
                signed,
                candidate.currency,
                candidate.normalized_description,
            )
            occurrences[key] = occurrences.get(key, 0) + 1
            candidate.fingerprint = transaction_fingerprint(
                *key,
                candidate.external_id,
                1 if candidate.external_id else occurrences[key],
            )
            existing = self.uow.transactions.find_fingerprint(candidate.fingerprint)
            candidate.duplicate_of = existing.id if existing else None
            if existing:
                candidate.add_issue(
                    DUPLICATE_ROW, "already in the ledger; it will not be imported", WARNING
                )

    def commit(
        self,
        candidates: Sequence[CandidateTransaction],
        *,
        source_label: str,
        dry_run: bool = False,
    ) -> list[TransactionModel]:
        """Persists included, non-duplicate, non-error rows."""
        committed: list[TransactionModel] = []
        for candidate in candidates:
            if not candidate.included or candidate.state == ERROR:
                continue
            if candidate.duplicate_of is not None or candidate.amount_minor is None:
                continue
            sign = -1 if candidate.direction == "debit" else 1
            rule = self.uow.rules.match(candidate.normalized_description)
            classification = (
                _classification_from_rule(rule)
                if rule
                else _classification(candidate, sign, self.classifier)
            )
            account = self.uow.accounts.get_or_create(
                candidate.account_hint or "Main account", candidate.currency
            )
            transaction = TransactionModel(
                external_id=candidate.external_id,
                account_id=account.id,
                transaction_date=_parse_date(candidate.transaction_date or ""),
                posted_date=_parse_date(candidate.posted_date) if candidate.posted_date else None,
                raw_description=candidate.raw_description,
                normalized_description=candidate.normalized_description,
                merchant=merchant_from_description(candidate.raw_description),
                amount_minor=candidate.amount_minor,
                flow_direction="debit" if sign < 0 else "credit",
                currency=candidate.currency,
                kind=classification.kind.value,
                category=classification.category.value if classification.category else None,
                transfer_purpose=classification.transfer_purpose.value
                if classification.transfer_purpose
                else None,
                classification_source=str(classification.source),
                classification_confidence=classification.confidence,
                classification_reason=classification.reason,
                import_source=source_label,
                fingerprint=candidate.fingerprint or "",
            )
            if not dry_run:
                self.uow.transactions.add(transaction)
            committed.append(transaction)
        return committed

    def import_result(
        self,
        extraction: ExtractionResult,
        *,
        source_label: str,
        account_override: str | None = None,
        dry_run: bool = False,
    ) -> ImportResult:
        candidates = extraction.candidates
        if account_override:
            for candidate in candidates:
                candidate.account_hint = account_override
        with TimedOperation("import_result", extractor=extraction.extractor, dry_run=dry_run):
            self.validate(candidates)
            self.resolve_duplicates(candidates)
            committed = self.commit(candidates, source_label=source_label, dry_run=dry_run)
        if dry_run:
            self.uow.session.rollback()
        return ImportResult(
            imported=len(committed),
            duplicates=sum(1 for candidate in candidates if candidate.duplicate_of is not None),
            requires_classification=sum(
                1
                for transaction in committed
                if transaction.kind == TransactionKind.UNKNOWN.value
                or (
                    transaction.category is None
                    and transaction.kind == TransactionKind.EXPENSE.value
                )
            ),
            errors=[issue.message for issue in extraction.issues if issue.severity == ERROR]
            + [
                f"row {candidate.source_line or '?'}: {error.message}"
                for candidate in candidates
                if (error := candidate.first_error())
            ],
        )

    def import_csv(self, path: Path, dry_run: bool = False) -> ImportResult:
        source = StatementSource(path=path, original_filename=path.name, media_type="text/csv")
        extraction = CsvStatementExtractor().extract(source)
        return self.import_result(extraction, source_label=str(path), dry_run=dry_run)
