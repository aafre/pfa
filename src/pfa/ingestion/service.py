from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from pfa.db.models import TransactionModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.errors import ImportRowError
from pfa.domain.money import Money
from pfa.domain.transactions import (
    ClassificationSource,
    SpendingCategory,
    TransactionKind,
    TransferPurpose,
)

from .categorizer import Classification, classify_known
from .fingerprint import transaction_fingerprint
from .normalizer import merchant_from_description, normalize_description
from .parsers.csv import read_csv_rows


class Classifier(Protocol):
    def classify(self, description: str, amount_minor: int) -> Classification | None: ...


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


def _classification(
    row: dict[str, str], sign: int, amount_minor: int, classifier: Classifier | None
) -> Classification:
    explicit_kind = row["kind"].lower()
    if explicit_kind:
        try:
            kind = TransactionKind(explicit_kind)
        except ValueError as exc:
            raise ImportRowError(f"unknown transaction kind {explicit_kind!r}") from exc
        category = SpendingCategory(row["category"]) if row["category"] else None
        purpose = TransferPurpose(row["transfer_purpose"]) if row["transfer_purpose"] else None
        return Classification(
            kind,
            category,
            purpose,
            ClassificationSource.IMPORT,
            None,
            "source-provided classification",
        )
    known = classify_known(row["description"])
    if known:
        return known
    if classifier:
        result = classifier.classify(row["description"], amount_minor)
        if result:
            return result
    return Classification(
        TransactionKind.EXPENSE if sign < 0 else TransactionKind.INCOME,
        source=ClassificationSource.UNKNOWN,
        confidence=None,
        reason="requires review",
    )


class ImportService:
    def __init__(self, unit_of_work: UnitOfWork, classifier: Classifier | None = None):
        self.uow = unit_of_work
        self.classifier = classifier

    def import_csv(self, path: Path, dry_run: bool = False) -> ImportResult:
        result = ImportResult()
        for row in read_csv_rows(path):
            try:
                transaction_date = _parse_date(row["date"])
                if not row["description"]:
                    raise ImportRowError("missing description")
                sign, amount_minor = _parse_amount(row["amount"])
                normalized = normalize_description(row["description"])
                fingerprint = transaction_fingerprint(
                    row["account"],
                    row["date"],
                    amount_minor,
                    row["currency"],
                    normalized,
                    row["external_id"] or None,
                )
                if self.uow.transactions.find_fingerprint(fingerprint):
                    result.duplicates += 1
                    continue
                classification = _classification(row, sign, amount_minor, self.classifier)
                account = self.uow.accounts.get_or_create(row["account"], row["currency"])
                transaction = TransactionModel(
                    external_id=row["external_id"] or None,
                    account_id=account.id,
                    transaction_date=transaction_date,
                    posted_date=_parse_date(row["posted_date"]) if row["posted_date"] else None,
                    raw_description=row["description"],
                    normalized_description=normalized,
                    merchant=merchant_from_description(row["description"]),
                    amount_minor=amount_minor,
                    currency=row["currency"].upper(),
                    kind=classification.kind.value,
                    category=classification.category.value if classification.category else None,
                    transfer_purpose=classification.transfer_purpose.value
                    if classification.transfer_purpose
                    else None,
                    classification_source=str(classification.source),
                    classification_confidence=classification.confidence,
                    classification_reason=classification.reason,
                    import_source=str(path),
                    fingerprint=fingerprint,
                )
                if not dry_run:
                    self.uow.transactions.add(transaction)
                result.imported += 1
                if (
                    classification.kind == TransactionKind.UNKNOWN
                    or classification.category is None
                    and classification.kind == TransactionKind.EXPENSE
                ):
                    result.requires_classification += 1
            except (ImportRowError, ValueError) as exc:
                result.errors.append(f"row {row.get('_line', '?')}: {exc}")
        if dry_run:
            self.uow.session.rollback()
        return result
