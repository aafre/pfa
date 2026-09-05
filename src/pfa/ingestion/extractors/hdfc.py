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
from ..normalizer import merchant_from_description


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
        normalized_description=merchant_from_description(narration.strip()),
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
    """Reads HDFC Delimited CSV, fixed-width formatted text, and legacy binary .xls workbooks."""

    name = "hdfc_in_delimited_v1"

    def __init__(self, *, max_candidate_rows: int = 10_000, dialect: Dialect = HDFC_IN_DELIMITED):
        self.max_candidate_rows = max_candidate_rows
        self.dialect = dialect

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = ExtractionResult(extractor=self.name, detected_institution="hdfc_bank")
        if source.path.suffix.lower() == ".xls":
            return self._extract_xls(source, result)

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

        if "Date" in text and "Withdrawal" in text and "--------" in text:
            return self._extract_fixed_width(text, result)

        return self._extract_csv(text, result)

    def _extract_xls(self, source: StatementSource, result: ExtractionResult) -> ExtractionResult:
        try:
            import xlrd

            wb = xlrd.open_workbook(source.path)
            sheet = wb.sheet_by_index(0)
        except Exception:
            result.issues.append(CandidateIssue(UNREADABLE_FILE, "could not read HDFC .xls workbook"))
            return result

        candidates: list[CandidateTransaction] = []
        in_data = False
        line_number = 0
        for r in range(sheet.nrows):
            vals = [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
            if not in_data:
                if vals and vals[0].lower() == "date" and any("narration" in v.lower() for v in vals):
                    in_data = True
                continue
            all_text = " ".join(vals).lower()
            if not any(vals) or "***" in vals[0]:
                continue
            if "statement summary" in all_text or "generated on" in all_text:
                break
            if len(vals) >= 7 and (vals[4] or vals[5]):
                line_number += 1
                date_val, narr, ref, val_dt, w_amt, d_amt, bal = vals[:7]
                # Format: date, narration, value_date, debit, credit, reference, closing
                cells = [date_val, narr, val_dt, w_amt, d_amt, ref, bal]
                candidate = _candidate(cells, line_number, self.dialect)
                candidates.append(candidate)
                if len(candidates) >= self.max_candidate_rows:
                    result.issues.append(
                        CandidateIssue(
                            TOO_MANY_ROWS,
                            f"the statement exceeds the {self.max_candidate_rows}-row limit",
                        )
                    )
                    break
        result.candidates = candidates
        return result

    def _extract_fixed_width(self, text: str, result: ExtractionResult) -> ExtractionResult:
        lines = text.splitlines()
        candidates: list[CandidateTransaction] = []
        in_table = False
        pending: list[str] | None = None
        line_num = 0
        col_spans: list[tuple[int, int]] = []

        for line in lines:
            if "Date" in line and "Narration" in line and "Withdrawal" in line:
                in_table = True
                continue
            if not in_table:
                continue
            if re.match(r"^[\s\-]{15,}$", line) and "--" in line:
                col_spans = [m.span() for m in re.finditer(r"-+", line)]
                continue
            if line.startswith("********") or "STATEMENT SUMMARY" in line:
                if pending:
                    line_num += 1
                    candidates.append(_candidate(pending, line_num, self.dialect))
                    pending = None
                break
            if not line.strip():
                continue

            date_slice = col_spans[0] if len(col_spans) > 0 else (0, 10)
            date_part = line[date_slice[0] : date_slice[1]].strip() if len(line) > date_slice[0] else ""
            if len(date_part) in (8, 10) and date_part[2] == "/" and date_part[5] == "/":
                if pending:
                    line_num += 1
                    candidates.append(_candidate(pending, line_num, self.dialect))

                def _get_col(idx: int, default_slice: tuple[int, int | None]) -> str:
                    s, e = col_spans[idx] if len(col_spans) > idx else default_slice
                    return line[s:e].strip() if len(line) > s else ""

                narr = _get_col(1, (10, 50))
                ref = _get_col(2, (52, 68))
                val_dt = _get_col(3, (70, 78))
                w_amt = _get_col(4, (80, 98))
                d_amt = _get_col(5, (100, 118))
                bal = _get_col(6, (120, None))
                pending = [date_part, narr, val_dt, w_amt, d_amt, ref, bal]
            elif pending:
                narr_slice = col_spans[1] if len(col_spans) > 1 else (10, 50)
                cont_narr = line[narr_slice[0] : narr_slice[1]].strip() if len(line) > narr_slice[0] else ""
                if cont_narr:
                    pending[1] = f"{pending[1]} {cont_narr}"

        if pending:
            line_num += 1
            candidates.append(_candidate(pending, line_num, self.dialect))

        result.candidates = candidates[: self.max_candidate_rows]
        return result

    def _extract_csv(self, text: str, result: ExtractionResult) -> ExtractionResult:
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
                if len(candidates) >= self.max_candidate_rows:
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

        result.candidates = candidates
        return result


def hdfc_delimited_header(cells: list[str]) -> bool:
    return HDFC_IN_DELIMITED.header_matches(cells)
