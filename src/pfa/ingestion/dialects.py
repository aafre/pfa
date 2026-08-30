from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, replace
from pathlib import Path

from pfa.domain.accounts import AccountType


@dataclass(frozen=True, slots=True)
class Dialect:
    name: str = "generic"
    adapter_id: str = "generic"
    date_formats: tuple[str, ...] = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %b %y",
        "%b %d %Y",
        "%b %d, %Y",
        "%d/%m/%y",
    )
    date_order: str = "day_first"
    credit_markers: tuple[str, ...] = ("CR", "CREDIT", "CR.")
    default_sign: str | None = None
    two_column: bool = False
    compatible_account_types: frozenset[AccountType] = frozenset()
    institution: str | None = None
    suggested_currency: str | None = None
    currency_evidence: str | None = None
    explicit_source_direction: bool = False
    header_signature: tuple[str, ...] = ()

    def header_matches(self, cells: list[str]) -> bool:
        if not self.header_signature:
            return False
        normalized = tuple(re.sub(r"\s+", " ", cell.strip()).casefold() for cell in cells)
        return normalized == self.header_signature


@dataclass(frozen=True, slots=True)
class AdapterDetection:
    dialect: Dialect
    confidence: float
    reason_codes: tuple[str, ...]
    institution: str | None = None
    account_hint: str | None = None
    currency: str | None = None
    suggested_currency: str | None = None
    currency_evidence: str | None = None


HDFC_HEADERS = (
    "date",
    "narration",
    "value dat",
    "debit amount",
    "credit amount",
    "chq/ref number",
    "closing balance",
)

GENERIC = Dialect()

AMEX_UK_CSV = replace(
    GENERIC,
    name="amex_uk_csv",
    adapter_id="amex_uk_csv",
    date_formats=("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d") + GENERIC.date_formats,
    default_sign="debit_positive",
    compatible_account_types=frozenset({AccountType.CREDIT_CARD}),
    institution="American Express",
)
AMEX_UK_PDF = replace(
    AMEX_UK_CSV,
    name="amex_uk_pdf",
    adapter_id="amex_uk_pdf",
)
HSBC_UK_CARD = replace(
    GENERIC,
    name="hsbc_uk_card",
    adapter_id="hsbc_uk_card",
    date_order="day_first",
    compatible_account_types=frozenset({AccountType.CREDIT_CARD}),
    institution="HSBC",
)
HSBC_UK_CURRENT = replace(
    GENERIC,
    name="hsbc_uk_current",
    adapter_id="hsbc_uk_current",
    date_order="day_first",
    compatible_account_types=frozenset(
        {AccountType.CURRENT, AccountType.SAVINGS, AccountType.CASH}
    ),
    institution="HSBC",
)

# Backwards-compatible names used by the original extractor API.
HSBC = HSBC_UK_CURRENT
AMEX_CARD = AMEX_UK_CSV
BARCLAYCARD = replace(
    GENERIC,
    name="barclaycard",
    adapter_id="barclaycard",
    date_formats=GENERIC.date_formats + ("%d %b %y", "%d %b %Y"),
    two_column=True,
    compatible_account_types=frozenset({AccountType.CREDIT_CARD}),
    institution="Barclaycard",
)
HDFC_IN_DELIMITED = replace(
    GENERIC,
    name="hdfc_in_delimited",
    adapter_id="hdfc_in_delimited_v1",
    date_formats=("%d/%m/%Y", "%d/%m/%y"),
    compatible_account_types=frozenset({AccountType.CURRENT, AccountType.SAVINGS}),
    institution="hdfc_bank",
    suggested_currency="INR",
    currency_evidence="adapter_suggestion",
    explicit_source_direction=True,
    header_signature=HDFC_HEADERS,
)

DIALECTS: dict[str, Dialect] = {
    "generic": GENERIC,
    "amex_uk_csv": AMEX_UK_CSV,
    "amex_uk_pdf": AMEX_UK_PDF,
    "amex": AMEX_UK_CSV,
    "hsbc_uk_card": HSBC_UK_CARD,
    "hsbc_uk_current": HSBC_UK_CURRENT,
    "hsbc": HSBC_UK_CURRENT,
    "barclaycard": BARCLAYCARD,
    "hdfc_in_delimited_v1": HDFC_IN_DELIMITED,
}


def dialect_for_name(name: str | None) -> Dialect:
    """Legacy compatibility only. Import batches use :func:`detect_adapter`."""
    if not name:
        return GENERIC
    clean = name.strip().lower()
    for key, dialect in DIALECTS.items():
        if key in clean:
            return dialect
    return GENERIC


def _csv_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")[:100_000]
    except (OSError, UnicodeDecodeError):
        return ""


def _csv_detection(path: Path) -> AdapterDetection:
    text = _csv_text(path)
    lower = text.lower()
    try:
        reader = csv.reader(io.StringIO(text))
        header = next((row for row in reader if any(cell.strip() for cell in row)), [])
    except csv.Error:
        header = []
    if HDFC_IN_DELIMITED.header_matches(header):
        return AdapterDetection(
            HDFC_IN_DELIMITED,
            0.99,
            ("hdfc_delimited_header", "explicit_source_columns"),
            institution="hdfc_bank",
            suggested_currency="INR",
            currency_evidence="adapter_suggestion",
        )
    headers = {" ".join(cell.strip().lower().split()) for cell in header}
    if (
        "card member" in lower
        or "membership number" in lower
        or "payment received - thank you" in lower
        or ("american express" in lower and "card" in lower)
    ):
        return AdapterDetection(
            AMEX_UK_CSV,
            0.98,
            ("amex_marker", "csv_headers"),
            institution="American Express",
        )
    if "hsbc" in lower and (
        {"paid out", "paid in"}.issubset(headers) or {"money out", "money in"}.issubset(headers)
    ):
        return AdapterDetection(HSBC_UK_CURRENT, 0.95, ("hsbc_marker", "two_column_cash_headers"))
    if "credit card" in lower and (" cr" in lower or "credit" in lower):
        return AdapterDetection(HSBC_UK_CARD, 0.9, ("card_marker",))
    return AdapterDetection(GENERIC, 0.0, ("generic_format",))


def _pdf_detection(path: Path) -> AdapterDetection:
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages[:3])
    except Exception:
        return AdapterDetection(GENERIC, 0.0, ("unreadable_content",))
    lower = text.lower()
    if (
        "card member" in lower
        or "membership number" in lower
        or ("american express" in lower and "card" in lower)
    ):
        return AdapterDetection(
            AMEX_UK_PDF,
            0.98,
            ("amex_marker", "pdf_text"),
            institution="American Express",
        )
    if "hsbc" in lower and any(
        marker in lower for marker in ("credit card", "visa", "available credit")
    ):
        return AdapterDetection(HSBC_UK_CARD, 0.95, ("hsbc_marker", "card_marker"))
    if {"paid out", "paid in"}.issubset(set(lower.split())) or (
        "paid out" in lower and "paid in" in lower and "balance" in lower
    ):
        return AdapterDetection(HSBC_UK_CURRENT, 0.95, ("cash_headers", "balance_column"))
    return AdapterDetection(GENERIC, 0.0, ("generic_format",))


def detect_adapter(path: Path, media_type: str | None = None) -> AdapterDetection:
    """Detect a statement adapter from bytes/content, never its filename or account label."""
    return _pdf_detection(path) if path.suffix.lower() == ".pdf" else _csv_detection(path)
