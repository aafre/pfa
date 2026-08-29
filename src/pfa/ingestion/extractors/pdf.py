"""Native PDF statement extraction: pdfplumber tables/words in, candidates out.

Renderer decision: pdfplumber 0.11+ renders pages via pypdfium2 (Apache-2.0/BSD-3) through
its own ``Page.to_image(resolution=...)``. This extractor never needs to render a page, so
it never touches that API directly -- it's recorded here because OCR (a follow-up task)
is the first caller and will reach the renderer only through pdfplumber.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.page import Page
from pdfplumber.pdf import PDF

from pfa.config import get_settings
from pfa.domain.money import Money
from pfa.ingestion.candidates import (
    AMBIGUOUS_SIGN,
    ERROR,
    OCR_EXTRACTED,
    OCR_LOW_CONFIDENCE,
    PDF_ENCRYPTED,
    PDF_NOT_EXTRACTABLE,
    PDF_TOO_MANY_PAGES,
    TOO_MANY_ROWS,
    UNJOINED_CONTINUATION,
    WARNING,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
)

# ponytail: max_candidate_rows is T3's setting (src/pfa/config.py, landing in a parallel
# branch). Mirrors the plan's stated default until that lands; swap for
# get_settings().max_candidate_rows once it exists.
_DEFAULT_MAX_CANDIDATE_ROWS = 10_000

# Header aliases are matched verbatim (lowercased, whitespace-collapsed) - no fuzzy guessing.
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date",),
    "description": ("description", "details", "narrative"),
    "debit": ("debit", "paid out", "withdrawn"),
    "credit": ("credit", "paid in", "received"),
    "amount": ("amount", "value"),
    "balance": ("balance",),
    "reference": ("reference", "transaction id"),
}

_LINE_TOLERANCE = 3.0  # points; words within this many points of `top` share a line
_CELL_GAP = 10.0  # points; a horizontal gap larger than this starts a new cell/column
_DEFAULT_LINE_HEIGHT = 12.0  # points; used only when a page has under two lines to measure
_CONTINUATION_FACTOR = 1.6
_CURRENCY_CHARS = "£$€"  # £ $ €
_UNICODE_MINUS = "−"
# Fields an OCR_LOW_CONFIDENCE check applies to - the financial fields a wrong OCR glyph
# would silently corrupt. Description/reference are never blocking: a misread word there is
# a cosmetic problem, not a wrong amount or date.
_OCR_CRITICAL_FIELDS = ("date", "amount", "debit", "credit")

Word = dict[str, Any]
WordProvider = Callable[[Page], list[Word]]


def _native_words(page: Page) -> list[Word]:
    """Default page-word provider. T6 swaps this for an OCR path on eligible pages."""
    return page.extract_words()


def _match_header(cell_text: str) -> str | None:
    normalized = " ".join(cell_text.strip().lower().split())
    for field_name, aliases in _HEADER_ALIASES.items():
        if normalized in aliases:
            return field_name
    return None


@dataclass(slots=True)
class _RawRow:
    """One candidate-shaped row before amount normalization and continuation joining."""

    source_page: int
    position: int
    top: float | None  # None for table rows: they never participate in continuation joins
    fields: dict[str, str]
    raw_text: str
    is_ocr: bool = False  # words carried a "conf" key - this row came from the OCR fallback
    field_conf: dict[str, float] = field(default_factory=dict)  # min OCR conf per field name


def _table_header(table: list[list[str | None]]) -> dict[int, str] | None:
    mapping: dict[int, str] = {}
    for index, cell in enumerate(table[0]):
        matched = _match_header(cell or "")
        if matched:
            mapping[index] = matched
    return mapping if "date" in mapping.values() else None


def _table_rows(
    table: list[list[str | None]], mapping: dict[int, str], page_number: int
) -> list[_RawRow]:
    rows: list[_RawRow] = []
    for position, raw_row in enumerate(table[1:], start=1):
        fields = {
            name: " ".join((raw_row[index] or "").split())
            for index, name in mapping.items()
            if index < len(raw_row)
        }
        if not any(fields.values()):
            continue
        raw_text = " | ".join((cell or "").strip() for cell in raw_row)
        rows.append(
            _RawRow(
                source_page=page_number,
                position=position,
                top=None,
                fields=fields,
                raw_text=raw_text,
            )
        )
    return rows


def _group_lines(words: list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return lines


def _split_cells(line_words: list[Word]) -> list[tuple[float, str, float | None]]:
    """Groups a line's words into cells. The third element is the cell's minimum OCR
    confidence (None for native-text words, which never carry a "conf" key)."""
    ordered = sorted(line_words, key=lambda w: w["x0"])
    cells: list[list[Word]] = []
    for word in ordered:
        if cells and word["x0"] - cells[-1][-1]["x1"] <= _CELL_GAP:
            cells[-1].append(word)
        else:
            cells.append([word])
    result: list[tuple[float, str, float | None]] = []
    for cell in cells:
        confidences = [w["conf"] for w in cell if "conf" in w]
        text = " ".join(w["text"] for w in cell)
        result.append((cell[0]["x0"], text, min(confidences) if confidences else None))
    return result


def _header_columns(
    cells: list[tuple[float, str, float | None]],
) -> list[tuple[float, str]] | None:
    columns = [(x0, matched) for x0, text, _ in cells if (matched := _match_header(text))]
    if "date" not in {name for _, name in columns}:
        return None
    return columns


def _assign_cells(
    cells: list[tuple[float, str, float | None]], columns: list[tuple[float, str]]
) -> tuple[dict[str, str], dict[str, float]]:
    fields: dict[str, str] = {}
    field_conf: dict[str, float] = {}
    for x0, text, conf in cells:
        _, name = min(columns, key=lambda column: abs(column[0] - x0))
        fields[name] = f"{fields[name]} {text}".strip() if name in fields else text
        if conf is not None:
            field_conf[name] = min(field_conf[name], conf) if name in field_conf else conf
    return fields, field_conf


def _word_rows(words: list[Word], page_number: int) -> tuple[list[_RawRow], float | None]:
    """Returns the page's data rows plus the header line's `top` (or None if no header).

    The header-to-first-data-row gap is a reliable one-line baseline for the continuation
    check below, even on a page with too few data rows to measure a gap between two of
    them.
    """
    lines = _group_lines(words)
    columns: list[tuple[float, str]] | None = None
    header_top: float | None = None
    rows: list[_RawRow] = []
    position = 0
    for line in lines:
        cells = _split_cells(line)
        if columns is None:
            columns = _header_columns(cells)
            if columns is not None:
                header_top = line[0]["top"]
            continue  # header line itself, or noise above it - never a data row
        position += 1
        fields, field_conf = _assign_cells(cells, columns)
        raw_text = " | ".join(text for _, text, _ in cells)
        rows.append(
            _RawRow(
                source_page=page_number,
                position=position,
                top=line[0]["top"],
                fields=fields,
                raw_text=raw_text,
                is_ocr=any("conf" in word for word in line),
                field_conf=field_conf,
            )
        )
    return rows, header_top


def _has_date_or_amount(row: _RawRow) -> bool:
    return bool(row.fields.get("date", "").strip()) or bool(
        row.fields.get("amount", "").strip()
        or row.fields.get("debit", "").strip()
        or row.fields.get("credit", "").strip()
    )


def _line_height(rows: list[_RawRow], header_top: float | None) -> float:
    """The smallest line-to-line gap on the page - a good proxy for one text line.

    Using the minimum (rather than e.g. the median) keeps a single large gap - the very
    thing a continuation check needs to measure against - from inflating the baseline.
    The header-to-first-row gap is included as a reliable one-line reference point.
    """
    tops = [row.top for row in rows if row.top is not None]
    if header_top is not None:
        tops = [header_top, *tops]
    diffs = [b - a for a, b in zip(tops, tops[1:], strict=False) if b - a > 0]
    return min(diffs) if diffs else _DEFAULT_LINE_HEIGHT


def _merge_continuations(
    rows: list[_RawRow], header_top: float | None
) -> list[tuple[_RawRow, bool]]:
    """Joins wrapped description lines into the row above them, in place.

    Returns the rows that remain their own candidates, each paired with whether it is an
    orphan continuation line (no date, no amount, too far from the row above to join) -
    those are never dropped, only flagged.
    """
    threshold = _line_height(rows, header_top) * _CONTINUATION_FACTOR
    kept: list[tuple[_RawRow, bool]] = []
    last: _RawRow | None = None
    for row in rows:
        if row.top is None or _has_date_or_amount(row):
            kept.append((row, False))
            last = row
            continue
        joined = row.fields.get("description", "").strip() or row.raw_text.strip()
        if last is not None and last.top is not None and abs(row.top - last.top) <= threshold:
            if joined:
                existing = last.fields.get("description", "")
                last.fields["description"] = f"{existing} {joined}".strip()
            last.raw_text = f"{last.raw_text} / {row.raw_text}"
            last.top = row.top  # chain distance from the most recently joined line
            continue
        kept.append((row, True))
        last = row
    return kept


@dataclass(slots=True)
class _AmountResult:
    minor: int | None = None
    direction: str | None = None
    issue: CandidateIssue | None = None


def _clean_amount_text(text: str) -> tuple[str, bool]:
    """Strips sign/currency/thousands markers. Returns (digits-and-dot, is_negative)."""
    cleaned = text.strip()
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()
    if cleaned.endswith("-"):
        negative = True
        cleaned = cleaned[:-1].strip()
    if cleaned.startswith("-") or cleaned.startswith(_UNICODE_MINUS):
        negative = True
        cleaned = cleaned[1:].strip()
    for char in _CURRENCY_CHARS:
        cleaned = cleaned.replace(char, "")
    cleaned = cleaned.replace(",", "").replace(_UNICODE_MINUS, "").strip()
    return cleaned, negative


def _signed_minor(text: str) -> tuple[int, bool] | None:
    cleaned, negative = _clean_amount_text(text)
    if not cleaned:
        return None
    try:
        decimal = Decimal(cleaned)
    except InvalidOperation:
        return None
    return Money.from_major(abs(decimal)).minor, negative


def _resolve_amount(fields: dict[str, str]) -> _AmountResult:
    """Resolves one signed amount. Two disagreeing sign sources block, never guess.

    Balance is intentionally never read here - it is provenance only, never a transaction
    amount. ponytail: reconciling running balance against amount deltas (the spec allows
    this to surface warnings only) is deferred - no test or issue code calls for it yet;
    add a RECONCILIATION_MISMATCH warning code and compare deltas here if that's needed.
    """
    debit_text = fields.get("debit", "").strip()
    credit_text = fields.get("credit", "").strip()
    amount_text = fields.get("amount", "").strip()

    if amount_text:
        parsed = _signed_minor(amount_text)
        if parsed is None:
            return _AmountResult()
        minor, negative = parsed
        return _AmountResult(minor=minor, direction="debit" if negative else "credit")

    if debit_text and credit_text:
        return _AmountResult(
            issue=CandidateIssue(
                AMBIGUOUS_SIGN,
                "debit and credit columns both hold a value; sign cannot be determined",
            )
        )

    if debit_text or credit_text:
        implied_direction = "debit" if debit_text else "credit"
        parsed = _signed_minor(debit_text or credit_text)
        if parsed is None:
            return _AmountResult()
        minor, negative = parsed
        if negative and implied_direction == "credit":
            return _AmountResult(
                issue=CandidateIssue(
                    AMBIGUOUS_SIGN,
                    "credit column holds a negative/parenthesised value; sign cannot be determined",
                )
            )
        return _AmountResult(minor=minor, direction=implied_direction)

    return _AmountResult()


def _build_candidate(
    index: int, row: _RawRow, orphan: bool, ocr_min_confidence: float
) -> CandidateTransaction:
    fields = row.fields
    raw_fields = {name: value for name, value in fields.items() if value.strip()}
    raw_fields["raw_text"] = row.raw_text
    candidate = CandidateTransaction(
        candidate_id=f"p{index}",
        transaction_date=fields.get("date", "").strip() or None,
        raw_description=fields.get("description", "").strip(),
        currency="GBP",
        external_id=fields.get("reference", "").strip() or None,
        source_format="pdf",
        source_page=row.source_page,
        source_line=row.position,
        extraction_method="ocr" if row.is_ocr else "pdf",
        raw_fields=raw_fields,
    )
    amount = _resolve_amount(fields)
    if amount.issue:
        candidate.issues.append(amount.issue)
    else:
        candidate.amount_minor = amount.minor
        candidate.direction = amount.direction
    if orphan:
        candidate.add_issue(
            UNJOINED_CONTINUATION,
            "line has no date or amount and sits too far from the row above it to join",
            WARNING,
        )
    if row.is_ocr:
        candidate.add_issue(
            OCR_EXTRACTED,
            "this row was read by OCR, not native PDF text - confirm it before committing",
            WARNING,
        )
        unsure = [
            name
            for name in _OCR_CRITICAL_FIELDS
            if row.field_conf.get(name, 100.0) < ocr_min_confidence
        ]
        if unsure:
            candidate.add_issue(
                OCR_LOW_CONFIDENCE,
                f"OCR confidence below {ocr_min_confidence:.0f}% in {', '.join(unsure)}; "
                "confirm the correct value or exclude this row before committing",
                ERROR,
            )
    return candidate


class PdfStatementExtractor:
    """Reads a native/digital PDF statement into candidate rows. Never touches the ledger."""

    name = "pdf/1"

    def __init__(
        self,
        *,
        max_pdf_pages: int | None = None,
        max_candidate_rows: int | None = None,
        word_provider: WordProvider | None = None,
        ocr_min_confidence: float | None = None,
    ) -> None:
        self._max_pages = (
            max_pdf_pages if max_pdf_pages is not None else get_settings().max_pdf_pages
        )
        self._max_rows = (
            max_candidate_rows if max_candidate_rows is not None else _DEFAULT_MAX_CANDIDATE_ROWS
        )
        self._word_provider = word_provider or _native_words
        self._ocr_min_confidence = (
            ocr_min_confidence
            if ocr_min_confidence is not None
            else get_settings().ocr_min_confidence
        )

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = ExtractionResult(extractor=self.name)
        try:
            with pdfplumber.open(source.path) as pdf:
                return self._extract(pdf, result)
        except PDFPasswordIncorrect:
            result.issues.append(
                CandidateIssue(
                    PDF_ENCRYPTED,
                    "PDF is password-protected; remove the password and re-upload",
                )
            )
            return result
        except Exception:  # a corrupt/unsupported PDF becomes a sanitized batch issue
            result.issues.append(
                CandidateIssue(
                    PDF_NOT_EXTRACTABLE,
                    "could not read this PDF; try a CSV download or a better-quality statement",
                )
            )
            return result

    def _extract(self, pdf: PDF, result: ExtractionResult) -> ExtractionResult:
        result.page_count = len(pdf.pages)
        if result.page_count > self._max_pages:
            result.issues.append(
                CandidateIssue(
                    PDF_TOO_MANY_PAGES,
                    f"PDF has {result.page_count} pages; the limit is {self._max_pages}",
                )
            )
            return result

        kept: list[tuple[_RawRow, bool]] = []
        for page in pdf.pages:
            page_rows, header_top = self._page_rows(page)
            kept.extend(_merge_continuations(page_rows, header_top))

        candidates = [
            _build_candidate(index, row, orphan, self._ocr_min_confidence)
            for index, (row, orphan) in enumerate(kept, start=1)
        ]
        if len(candidates) > self._max_rows:
            candidates = candidates[: self._max_rows]
            result.issues.append(
                CandidateIssue(
                    TOO_MANY_ROWS,
                    f"more than {self._max_rows} candidate rows were extracted; only the "
                    f"first {self._max_rows} are shown",
                )
            )
        result.candidates = candidates
        if not candidates:
            result.issues.append(
                CandidateIssue(
                    PDF_NOT_EXTRACTABLE,
                    "no transaction rows could be recognized in this PDF; try a CSV "
                    "download or a better-quality statement",
                )
            )
        return result

    def _page_rows(self, page: Page) -> tuple[list[_RawRow], float | None]:
        table = page.extract_table()
        if table and len(table) >= 2:
            mapping = _table_header(table)
            if mapping:
                return _table_rows(table, mapping, page.page_number), None
        return _word_rows(self._word_provider(page), page.page_number)
