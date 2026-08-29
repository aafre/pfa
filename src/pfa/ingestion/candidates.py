"""Format-neutral extraction contracts shared by extractors, services and the API.

Pure data: nothing here may import a parsing library or the ORM, so library objects
never cross the extractor boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

ERROR = "error"
WARNING = "warning"
VALID = "valid"

# Row-level issue codes.
INVALID_DATE = "INVALID_DATE"
MISSING_DESCRIPTION = "MISSING_DESCRIPTION"
INVALID_AMOUNT = "INVALID_AMOUNT"
AMBIGUOUS_SIGN = "AMBIGUOUS_SIGN"
UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
UNKNOWN_KIND = "UNKNOWN_KIND"
UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY"
UNKNOWN_TRANSFER_PURPOSE = "UNKNOWN_TRANSFER_PURPOSE"
DUPLICATE_ROW = "DUPLICATE_ROW"
UNJOINED_CONTINUATION = "UNJOINED_CONTINUATION"
OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE"  # blocking: OCR text feeding a financial field is unsure
OCR_EXTRACTED = "OCR_EXTRACTED"  # warning: row came from OCR, not native text - review it

# Batch-level issue codes.
NO_HEADER_ROW = "NO_HEADER_ROW"
UNREADABLE_FILE = "UNREADABLE_FILE"
PDF_ENCRYPTED = "PDF_ENCRYPTED"
PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
PDF_NOT_EXTRACTABLE = "PDF_NOT_EXTRACTABLE"
TOO_MANY_ROWS = "TOO_MANY_ROWS"
OCR_UNAVAILABLE = "OCR_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class StatementSource:
    """The only thing an extractor receives: a staged file with generated name."""

    path: Path
    original_filename: str
    media_type: str
    size_bytes: int = 0
    sha256: str = ""


@dataclass(slots=True)
class CandidateIssue:
    code: str
    message: str
    severity: str = ERROR


@dataclass(slots=True)
class CandidateTransaction:
    candidate_id: str
    transaction_date: str | None = None
    posted_date: str | None = None
    raw_description: str = ""
    normalized_description: str = ""
    amount_minor: int | None = None  # absolute magnitude, matches TransactionModel
    direction: str | None = None  # "debit" | "credit"
    currency: str = "GBP"
    account_hint: str | None = None
    external_id: str | None = None
    kind: str | None = None
    category: str | None = None
    transfer_purpose: str | None = None
    source_format: str = "csv"
    source_line: int | None = None
    source_page: int | None = None
    extraction_method: str = "csv"
    raw_fields: dict[str, str] = field(default_factory=dict)
    issues: list[CandidateIssue] = field(default_factory=list)
    duplicate_of: int | None = None
    fingerprint: str | None = None
    included: bool = True

    @property
    def state(self) -> str:
        severities = {issue.severity for issue in self.issues}
        if ERROR in severities:
            return ERROR
        return WARNING if WARNING in severities else VALID

    @property
    def signed_amount_minor(self) -> int | None:
        if self.amount_minor is None:
            return None
        return -self.amount_minor if self.direction == "debit" else self.amount_minor

    def add_issue(self, code: str, message: str, severity: str = ERROR) -> None:
        self.issues.append(CandidateIssue(code, message, severity))

    def first_error(self) -> CandidateIssue | None:
        return next((issue for issue in self.issues if issue.severity == ERROR), None)


@dataclass(slots=True)
class ExtractionResult:
    candidates: list[CandidateTransaction] = field(default_factory=list)
    extractor: str = ""
    page_count: int | None = None
    detected_account: str | None = None
    detected_currency: str | None = None
    issues: list[CandidateIssue] = field(default_factory=list)


class StatementExtractor(Protocol):
    name: str

    def extract(self, source: StatementSource) -> ExtractionResult: ...
