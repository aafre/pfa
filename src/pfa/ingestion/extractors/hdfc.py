"""Strict HDFC India delimited statement extraction.

HDFC's Delimited export is intentionally kept separate from the permissive generic CSV
reader. The seven-column header is the format contract; a file that does not match it is
not allowed to fall through to positional parsing.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from pfa.domain.errors import ValidationError
from pfa.domain.money import minor_units

from ..candidates import (
    HDFC_AMOUNT_SIDES_INVALID,
    HDFC_HEADER_NOT_FOUND,
    HDFC_ROW_WIDTH_INVALID,
    INVALID_AMOUNT,
    NO_HEADER_ROW,
    TOO_MANY_ROWS,
    UNREADABLE_FILE,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
)
from ..dialects import HDFC_HEADERS, HDFC_IN_DELIMITED, Dialect


def _clean_decimal(value: str) -> tuple[Decimal, bool]:
    text = value.strip()
    negative = text.startswith("-") or text.startswith("−")
    if text.startswith(("-", "−")):
        text = text[1:].strip()
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    text = re.sub(r"(?i)^(?:inr|rs\.?|₹)", "", text).strip()
    text = text.replace(",", "").replace("₹", "").strip()
    try:
        return Decimal(text), negative
    except InvalidOperation as exc:
        raise ValueError from exc


def _magnitude(value: str) -> int:
    if not value.strip():
        return 0
    decimal, negative = _clean_decimal(value)
    if negative or decimal < 0:
        raise ValueError
    return minor_units(decimal, "INR")


def _balance(value: str) -> int:
    decimal, negative = _clean_decimal(value)
    amount = minor_units(decimal, "INR")
    return -amount if negative else amount


def _blank_row(cells: Iterable[str]) -> bool:
    return not any(cell.strip() for cell in cells)


def _candidate(
    cells: list[str], line_number: int, dialect: Dialect = HDFC_IN_DELIMITED
) -> CandidateTransaction:
    date_text, narration, value_date, debit, credit, reference, closing = cells
    raw_fields = {
        "value_date": value_date.strip(),
        "debit": debit.strip(),
        "credit": credit.strip(),
        "source_reference": reference.strip(),
        "closing_balance": closing.strip(),
    }
    candidate = CandidateTransaction(
        candidate_id=f"h{line_number}",
        transaction_date=date_text.strip() or None,
        posted_date=value_date.strip() or None,
        raw_description=narration.strip(),
        currency="INR",
        source_format="csv",
        source_line=line_number,
        extraction_method="hdfc_in_delimited_v1",
        raw_fields=raw_fields,
    )

    try:
        debit_minor = _magnitude(debit)
        credit_minor = _magnitude(credit)
        _balance(closing)
    except (ValueError, ValidationError):
        candidate.add_issue(
            INVALID_AMOUNT,
            "debit, credit, and closing balance must be valid INR amounts",
        )
        return candidate

    if (debit_minor > 0) == (credit_minor > 0):
        candidate.add_issue(
            HDFC_AMOUNT_SIDES_INVALID,
            "exactly one of debit amount or credit amount must be positive",
        )
        return candidate

    candidate.amount_minor = debit_minor or credit_minor
    candidate.direction = "debit" if debit_minor > 0 else "credit"
    candidate.direction_explicit = True
    # ``direction`` is PFA's legacy normalized money-out/money-in value. The raw
    # source columns remain in raw_fields for provenance, while the canonical sign
    # is supplied by CandidateTransaction.signed_minor.
    return candidate


class HdfcDelimitedExtractor:
    """Reads only the exact seven-column HDFC Delimited export."""

    name = "hdfc_in_delimited_v1"

    def __init__(self, *, max_candidate_rows: int = 10_000, dialect: Dialect = HDFC_IN_DELIMITED):
        self.max_candidate_rows = max_candidate_rows
        self.dialect = dialect

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = ExtractionResult(extractor=self.name, detected_institution="hdfc_bank")
        try:
            text = source.path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            result.issues.append(
                CandidateIssue(UNREADABLE_FILE, "file is not valid UTF-8 HDFC delimited text")
            )
            return result
        except OSError:
            result.issues.append(CandidateIssue(UNREADABLE_FILE, "could not read the statement"))
            return result

        reader = csv.reader(io.StringIO(text), strict=True)
        header: list[str] | None = None
        try:
            for row in reader:
                if not _blank_row(row):
                    header = row
                    break
        except csv.Error:
            result.issues.append(CandidateIssue(HDFC_HEADER_NOT_FOUND, "invalid CSV quoting"))
            return result
        if header is None:
            result.issues.append(CandidateIssue(NO_HEADER_ROW, "CSV has no header row"))
            return result
        if not self.dialect.header_matches(header):
            result.issues.append(
                CandidateIssue(
                    HDFC_HEADER_NOT_FOUND,
                    "HDFC Delimited header was not found; download the Delimited format",
                )
            )
            return result

        candidates: list[CandidateTransaction] = []
        try:
            for row in reader:
                line_number = reader.line_num
                if _blank_row(row):
                    continue
                if len(row) != len(HDFC_HEADERS):
                    candidate = CandidateTransaction(
                        candidate_id=f"h{line_number}",
                        source_format="csv",
                        source_line=line_number,
                        extraction_method=self.name,
                        raw_fields={"source_reference": row[5].strip() if len(row) > 5 else ""},
                    )
                    candidate.add_issue(
                        HDFC_ROW_WIDTH_INVALID,
                        "each HDFC Delimited data row must contain seven columns",
                    )
                else:
                    candidate = _candidate(row, line_number, self.dialect)
                candidates.append(candidate)
                if len(candidates) > self.max_candidate_rows:
                    result.issues.append(
                        CandidateIssue(
                            TOO_MANY_ROWS,
                            f"the statement exceeds the {self.max_candidate_rows}-row limit",
                        )
                    )
                    break
        except csv.Error:
            result.issues.append(CandidateIssue(HDFC_HEADER_NOT_FOUND, "invalid CSV quoting"))
            return result

        result.candidates = candidates[: self.max_candidate_rows]
        return result


def hdfc_delimited_header(cells: list[str]) -> bool:
    return HDFC_IN_DELIMITED.header_matches(cells)
