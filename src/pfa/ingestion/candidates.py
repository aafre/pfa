"""Format-neutral extraction contracts shared by extractors, services and the API.

Nothing here may import a parsing library or the ORM, so library objects never cross the
extractor boundary. The header table and the two field parsers live here for the same
reason the issue codes do: every extractor needs them, and a vocabulary that is only
half-shared is a vocabulary the two extractors will drift apart on.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from pfa.domain.errors import ImportRowError
from pfa.domain.money import minor_units

ERROR = "error"
WARNING = "warning"
VALID = "valid"

# Row-level issue codes.
INVALID_DATE = "INVALID_DATE"
MISSING_DESCRIPTION = "MISSING_DESCRIPTION"
INVALID_AMOUNT = "INVALID_AMOUNT"
AMBIGUOUS_SIGN = "AMBIGUOUS_SIGN"
UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
CURRENCY_ACCOUNT_MISMATCH = "CURRENCY_ACCOUNT_MISMATCH"
STATEMENT_YEAR_INFERRED = "STATEMENT_YEAR_INFERRED"
UNKNOWN_KIND = "UNKNOWN_KIND"
UNKNOWN_CATEGORY = "UNKNOWN_CATEGORY"
UNKNOWN_TRANSFER_PURPOSE = "UNKNOWN_TRANSFER_PURPOSE"
DUPLICATE_ROW = "DUPLICATE_ROW"
OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE"  # blocking: OCR text feeding a financial field is unsure
OCR_EXTRACTED = "OCR_EXTRACTED"  # warning: row came from OCR, not native text - review it

# Batch-level issue codes.
NO_HEADER_ROW = "NO_HEADER_ROW"
HEADERLESS_CSV = "HEADERLESS_CSV"  # warning: no header row, columns were read by position
UNREADABLE_FILE = "UNREADABLE_FILE"
PDF_ENCRYPTED = "PDF_ENCRYPTED"
PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
PDF_NOT_EXTRACTABLE = "PDF_NOT_EXTRACTABLE"
TOO_MANY_ROWS = "TOO_MANY_ROWS"
OCR_UNAVAILABLE = "OCR_UNAVAILABLE"

# Upload and batch-lifecycle issue codes (T3).
UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
INVALID_SIGNATURE = "INVALID_SIGNATURE"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
UPLOAD_FAILED = "UPLOAD_FAILED"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
EXTRACTION_TIMEOUT = "EXTRACTION_TIMEOUT"
NO_USABLE_ROWS = "NO_USABLE_ROWS"
BATCH_NOT_FOUND = "BATCH_NOT_FOUND"
BATCH_EXPIRED = "BATCH_EXPIRED"
BATCH_NOT_EDITABLE = "BATCH_NOT_EDITABLE"
BATCH_HAS_BLOCKING_ERRORS = "BATCH_HAS_BLOCKING_ERRORS"
BATCH_ALREADY_COMMITTED = "BATCH_ALREADY_COMMITTED"


# One header vocabulary for every extractor. A bank's wording is added once, here, rather
# than in whichever extractor happened to meet that bank first. Cells match verbatim once
# lowercased and whitespace-collapsed - the spec forbids fuzzy guessing.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "transaction_date"),
    "description": ("description", "details", "narrative", "merchant"),
    "debit": ("debit", "paid out", "paid_out", "withdrawn", "money out", "money_out"),
    "credit": ("credit", "paid in", "paid_in", "received", "money in", "money_in"),
    "amount": ("amount", "value"),
    "balance": ("balance",),
    "reference": ("reference", "transaction id"),
}


def match_header_alias(cell_text: str) -> str | None:
    """Maps one header cell onto its canonical field name, or None if nothing matches."""
    normalized = " ".join(cell_text.strip().lower().split())
    for field_name, aliases in HEADER_ALIASES.items():
        if normalized in aliases:
            return field_name
    return None


_YEARLESS_DATE_PATTERNS: tuple[str, ...] = ("%b%d", "%b %d", "%d %b")


def _year_bearing_date_patterns(date_order: str) -> tuple[str, ...]:
    if date_order == "month_first":
        return (
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m-%d-%Y",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%b %d %Y",
            "%b %d, %Y",
            "%d %b %Y",
            "%d %b %y",
            "%d/%m/%y",
            "%m/%d/%y",
        )
    return (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %b %y",
        "%b %d %Y",
        "%b %d, %Y",
        "%d/%m/%y",
        "%m/%d/%Y",
        "%m-%d-%Y",
    )


def is_year_bearing_date(value: str, date_order: str = "day_first") -> bool:
    """True when `value` carries its own year, rather than needing one assumed for it."""
    cleaned = value.strip()
    for pattern in _year_bearing_date_patterns(date_order):
        try:
            datetime.strptime(cleaned, pattern)
            return True
        except ValueError:
            continue
    return False


def parse_date(
    value: str,
    date_order: str = "day_first",
    statement_year: int | None = None,
) -> date:
    cleaned = value.strip()
    for pattern in _year_bearing_date_patterns(date_order):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue

    # Year-less format attempts like 'Jul31', 'Jul 31', '21 Jul'. `statement_year` should
    # always come from other year-bearing dates in the same statement (see
    # ingestion.batches._normalize_dates) - falling back to today's year here is a last
    # resort for a caller that never supplied one.
    year = statement_year or date.today().year
    for pattern in _YEARLESS_DATE_PATTERNS:
        try:
            dt = datetime.strptime(cleaned, pattern)
            return dt.replace(year=year).date()
        except ValueError:
            continue

    raise ImportRowError(f"invalid date {value!r}")


def parse_amount(value: str, currency: str = "GBP") -> tuple[int, int, bool]:
    """Parses a signed amount. Returns (sign, minor_units, was_an_explicit_credit_marker).

    The third element tells the caller the row's direction came from a CR/CREDIT marker in
    the text itself, not from the statement's general sign convention - so a later
    convention choice (e.g. "debit positive") must never override it.
    """
    cleaned = (
        value.replace(",", "")
        .replace("£", "")
        .replace("$", "")
        .replace("€", "")
        .replace("₹", "")
        .replace("�", "")
        .strip()
    )
    is_cr = False
    upper = cleaned.upper()
    if upper.endswith("CR."):
        cleaned = cleaned[:-3].strip()
        is_cr = True
    elif upper.endswith("CR"):
        cleaned = cleaned[:-2].strip()
        is_cr = True
    elif upper.startswith("CR"):
        cleaned = cleaned[2:].strip()
        is_cr = True
    try:
        decimal = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ImportRowError(f"invalid amount {value!r}") from exc
    sign = 1 if is_cr else (-1 if decimal < 0 else 1)
    return sign, minor_units(abs(decimal), currency), is_cr


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
    # True once `direction` was read from an explicit marker (a CR/CREDIT suffix, or a
    # debit/credit column) rather than the statement's general sign convention. A later
    # amount-sign convention choice must never overwrite a row already resolved this way.
    direction_explicit: bool = False
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


def candidates_to_json(candidates: list[CandidateTransaction]) -> str:
    """Serialize candidates for the ImportBatchModel.candidates_json blob."""
    return json.dumps([asdict(candidate) for candidate in candidates])


def candidates_from_json(payload: str) -> list[CandidateTransaction]:
    """Inverse of candidates_to_json. Round-trips a candidate list losslessly."""
    rows = json.loads(payload)
    result = []
    for row in rows:
        issues = [CandidateIssue(**issue) for issue in row.pop("issues")]
        result.append(CandidateTransaction(**row, issues=issues))
    return result
