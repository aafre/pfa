"""Native PDF statement extraction: pdfplumber tables/words in, candidates out.

Renderer decision: pdfplumber 0.11+ renders pages via pypdfium2 (Apache-2.0/BSD-3) through
its own ``Page.to_image(resolution=...)``. This extractor never needs to render a page, so
it never touches that API directly -- it's recorded here because OCR (a follow-up task)
is the first caller and will reach the renderer only through pdfplumber.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.page import Page
from pdfplumber.pdf import PDF

from pfa.config import get_settings
from pfa.domain.money import minor_units
from pfa.ingestion.candidates import (
    AMBIGUOUS_SIGN,
    ERROR,
    OCR_EXTRACTED,
    OCR_LOW_CONFIDENCE,
    PDF_ENCRYPTED,
    PDF_NOT_EXTRACTABLE,
    PDF_TOO_MANY_PAGES,
    TOO_MANY_ROWS,
    WARNING,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
    match_header_alias,
    parse_date,
)
from pfa.ingestion.dialects import GENERIC, Dialect

# ponytail: max_candidate_rows is T3's setting (src/pfa/config.py, landing in a parallel
# branch). Mirrors the plan's stated default until that lands; swap for
# get_settings().max_candidate_rows once it exists.
_DEFAULT_MAX_CANDIDATE_ROWS = 10_000

_AMOUNT_FIELDS = ("amount", "debit", "credit")

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


def _has_transaction_header_fields(names: set[str]) -> bool:
    return "date" in names and bool(names.intersection(_AMOUNT_FIELDS))


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
    seen: set[str] = set()
    for index, cell in enumerate(table[0]):
        matched = match_header_alias(cell or "")
        if matched == "date" and matched in seen:
            matched = "posted_date"
        if matched:
            mapping[index] = matched
            seen.add(matched)
    return mapping if _has_transaction_header_fields(set(mapping.values())) else None


def _unpack_collapsed_table(
    table: list[list[str | None]], mapping: dict[int, str]
) -> list[list[str | None]]:
    if len(table) != 2:
        return table
    header = table[0]
    row = table[1]
    date_col = next((idx for idx, name in mapping.items() if name == "date"), None)
    if date_col is None or not any("\n" in (c or "") for c in row):
        return table
    dates = [d.strip() for d in (row[date_col] or "").split("\n") if d.strip()]
    if len(dates) <= 1:
        return table

    cols_lines = [(c or "").split("\n") for c in row]
    debit_col = next((idx for idx, name in mapping.items() if name == "debit"), None)
    credit_col = next((idx for idx, name in mapping.items() if name == "credit"), None)
    bal_col = next((idx for idx, name in mapping.items() if name == "balance"), None)
    desc_col = next((idx for idx, name in mapping.items() if name == "description"), None)
    ref_col = next((idx for idx, name in mapping.items() if name == "reference"), None)

    withs = [
        w.strip()
        for w in (
            cols_lines[debit_col] if debit_col is not None and debit_col < len(cols_lines) else []
        )
        if w.strip()
    ]
    deps = [
        d.strip()
        for d in (
            cols_lines[credit_col]
            if credit_col is not None and credit_col < len(cols_lines)
            else []
        )
        if d.strip()
    ]
    bals = [
        b.strip()
        for b in (cols_lines[bal_col] if bal_col is not None and bal_col < len(cols_lines) else [])
        if b.strip()
    ]
    narrs = [
        n.strip()
        for n in (
            cols_lines[desc_col] if desc_col is not None and desc_col < len(cols_lines) else []
        )
        if n.strip()
    ]
    refs = [
        r.strip()
        for r in (cols_lines[ref_col] if ref_col is not None and ref_col < len(cols_lines) else [])
        if r.strip()
    ]

    narr_per_date: list[str] = []
    current_narr: list[str] = []
    for n in narrs:
        if (
            any(n.startswith(p) for p in ("UPI", "ACH", "NEFT", "INW", "CHQ", "SALARY", "TRANSFER"))
            and current_narr
            and len(narr_per_date) < len(dates) - 1
        ):
            narr_per_date.append(" ".join(current_narr))
            current_narr = [n]
        else:
            current_narr.append(n)
    if current_narr:
        narr_per_date.append(" ".join(current_narr))
    while len(narr_per_date) < len(dates):
        narr_per_date.append("")

    w_idx = 0
    d_idx = 0
    unpacked = [header]

    for i, dt in enumerate(dates):
        debit_amt = ""
        credit_amt = ""
        try:
            cur_bal = float(bals[i].replace(",", "")) if i < len(bals) else 0.0
            if i == 0:
                if withs:
                    debit_amt = withs[0]
                    w_idx = 1
            else:
                prev_bal = float(bals[i - 1].replace(",", ""))
                delta = round(cur_bal - prev_bal, 2)
                if delta < 0 and w_idx < len(withs):
                    debit_amt = withs[w_idx]
                    w_idx += 1
                elif delta > 0 and d_idx < len(deps):
                    credit_amt = deps[d_idx]
                    d_idx += 1
        except Exception:
            if w_idx < len(withs):
                debit_amt = withs[w_idx]
                w_idx += 1

        new_row = list(row)
        if date_col is not None and date_col < len(new_row):
            new_row[date_col] = dt
        if desc_col is not None and desc_col < len(new_row):
            new_row[desc_col] = narr_per_date[i] if i < len(narr_per_date) else ""
        if ref_col is not None and ref_col < len(new_row):
            new_row[ref_col] = refs[i] if i < len(refs) else ""
        if debit_col is not None and debit_col < len(new_row):
            new_row[debit_col] = debit_amt
        if credit_col is not None and credit_col < len(new_row):
            new_row[credit_col] = credit_amt
        if bal_col is not None and bal_col < len(new_row):
            new_row[bal_col] = bals[i] if i < len(bals) else ""

        unpacked.append(new_row)

    return unpacked


def _table_rows(
    table: list[list[str | None]], mapping: dict[int, str], page_number: int
) -> list[_RawRow]:
    table = _unpack_collapsed_table(table, mapping)
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
    seen: set[str] = set()
    columns: list[tuple[float, str]] = []
    for x0, text, _ in cells:
        matched = match_header_alias(text)
        if matched == "date" and matched in seen:
            matched = "posted_date"
        if matched:
            columns.append((x0, matched))
            seen.add(matched)
    names = {name for _, name in columns}
    if not _has_transaction_header_fields(names):
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


def _is_date_text(text: str, dialect: Dialect = GENERIC) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    try:
        parse_date(cleaned, date_order=dialect.date_order)
        return True
    except Exception:
        return False


def _is_amount_text(text: str) -> bool:
    cleaned, _ = clean_amount_text(text)
    if not cleaned:
        return False
    try:
        Decimal(cleaned)
        return True
    except Exception:
        return False


def _is_lone_credit_marker(
    cells: list[tuple[float, str, float | None]], dialect: Dialect
) -> str | None:
    """The marker text when a line is nothing but a credit marker (own-line `CR`), else None.

    A statement that prints `CR` on its own line - visually attached to the amount above it
    but structurally its own row - would otherwise either vanish (no date, no amount pair to
    match) or become a spurious candidate with no date of its own. Folding it back onto the
    previous row as an explicit marker is what lets `_resolve_amount` read it correctly.
    """
    if len(cells) != 1:
        return None
    text = cells[0][1].strip().upper().rstrip(".")
    for marker in dialect.credit_markers:
        if text == marker.upper().rstrip("."):
            return cells[0][1].strip()
    return None


def _cluster_words_into_columns(words: list[Word]) -> list[list[Word]]:
    if not words:
        return []
    min_x = min(w["x0"] for w in words)
    max_x = max(w["x1"] for w in words)
    width = max_x - min_x
    if width < 150:
        return [words]
    split_x = min_x + width * 0.55
    left = [w for w in words if (w["x0"] + w["x1"]) / 2.0 < split_x]
    right = [w for w in words if (w["x0"] + w["x1"]) / 2.0 >= split_x]
    columns: list[list[Word]] = []
    if left:
        columns.append(left)
    if right:
        columns.append(right)
    return columns or [words]


def _process_lines_for_column(
    words: list[Word], page_number: int, dialect: Dialect = GENERIC
) -> tuple[list[_RawRow], float | None]:
    lines = _group_lines(words)
    columns: list[tuple[float, str]] | None = None
    header_top: float | None = None
    rows: list[_RawRow] = []
    pending_header: list[tuple[float, str, float | None]] = []
    pending_header_top: float | None = None
    position = 0
    for line in lines:
        cells = _split_cells(line)
        marker_text = _is_lone_credit_marker(cells, dialect)
        if marker_text is not None and rows:
            rows[-1].fields.setdefault("type", marker_text)
            rows[-1].raw_text = f"{rows[-1].raw_text} / {marker_text}"
            continue
        if columns is None:
            columns = _header_columns(cells)
            if columns is None and pending_header:
                columns = _header_columns([*pending_header, *cells])
            if columns is not None:
                header_top = pending_header_top or line[0]["top"]
                pending_header = []
                pending_header_top = None
                continue
            names = {match_header_alias(text) for _, text, _ in cells}
            names.discard(None)
            if names and names != {"date"}:
                pending_header = cells
                pending_header_top = line[0]["top"]
            if len(cells) >= 2:
                first_text = cells[0][1]
                last_text = cells[-1][1]
                if _is_date_text(first_text, dialect) and _is_amount_text(last_text):
                    position += 1
                    description = " ".join(c[1] for c in cells[1:-1] if c[1] != first_text)
                    fields = {
                        "date": first_text,
                        "description": description,
                        "amount": last_text,
                    }
                    raw_text = " | ".join(text for _, text, _ in cells)
                    rows.append(
                        _RawRow(
                            source_page=page_number,
                            position=position,
                            top=line[0]["top"],
                            fields=fields,
                            raw_text=raw_text,
                            is_ocr=any("conf" in word for word in line),
                        )
                    )
            continue
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


def _word_rows(
    words: list[Word], page_number: int, dialect: Dialect = GENERIC
) -> tuple[list[_RawRow], float | None]:
    """Returns the page's data rows plus the header line's `top` (or None if no header)."""
    if dialect.two_column:
        cols = _cluster_words_into_columns(words)
        all_rows: list[_RawRow] = []
        first_header: float | None = None
        for col_words in cols:
            rows, header_top = _process_lines_for_column(col_words, page_number, dialect)
            if rows:
                all_rows.extend(rows)
                if first_header is None:
                    first_header = header_top
        return all_rows, first_header
    return _process_lines_for_column(words, page_number, dialect)


def _has_parseable_date(fields: dict[str, str], dialect: Dialect = GENERIC) -> bool:
    value = fields.get("date", "").strip()
    if not value:
        return False
    try:
        parse_date(value, date_order=dialect.date_order)
        return True
    except Exception:
        return False


def _has_parseable_amount(fields: dict[str, str]) -> bool:
    return any(_signed_minor(fields.get(field, "")) is not None for field in _AMOUNT_FIELDS)


def _is_plausible_data_row(row: _RawRow, dialect: Dialect = GENERIC) -> bool:
    return _has_parseable_date(row.fields, dialect) and _has_parseable_amount(row.fields)


def _has_filled_transaction_cell(row: _RawRow) -> bool:
    return bool(row.fields.get("date", "").strip()) or any(
        row.fields.get(field, "").strip() for field in _AMOUNT_FIELDS
    )


def _line_height(rows: list[_RawRow], header_top: float | None) -> float:
    tops = [row.top for row in rows if row.top is not None]
    if header_top is not None:
        tops = [header_top, *tops]
    diffs = [b - a for a, b in zip(tops, tops[1:], strict=False) if b - a > 0]
    return min(diffs) if diffs else _DEFAULT_LINE_HEIGHT


def _merge_continuations(
    rows: list[_RawRow], header_top: float | None, dialect: Dialect = GENERIC
) -> list[_RawRow]:
    """Joins structurally empty wrapped description lines into the row above them,
    propagates active statement dates across multi-line transactions, and filters
    out non-transaction headers/footers."""
    kept: list[_RawRow] = []
    current_date: str | None = None
    pending_row: _RawRow | None = None

    for row in rows:
        if _is_balance_marker(row):
            pending_row = None
            continue

        date_val = row.fields.get("date", "").strip()
        has_date = _has_parseable_date(row.fields, dialect)
        has_amt = _has_parseable_amount(row.fields)

        desc = row.fields.get("description", "").lower()
        date_lower = date_val.lower()
        if any(
            date_lower.startswith(p)
            for p in ("total", "subtotal", "balance", "opening balance", "closing balance")
        ):
            pending_row = None
            continue

        if has_date:
            current_date = date_val

        if not has_date and not has_amt:
            joined = row.fields.get("description", "").strip() or row.raw_text.strip()
            if pending_row is not None and joined:
                existing = pending_row.fields.get("description", "")
                pending_row.fields["description"] = f"{existing} {joined}".strip()
                pending_row.raw_text = f"{pending_row.raw_text} / {row.raw_text}"
                if row.top is not None:
                    pending_row.top = row.top
            elif (
                kept
                and joined
                and abs((row.top or 0) - (kept[-1].top or 0))
                <= _line_height(rows, header_top) * _CONTINUATION_FACTOR
            ):
                existing = kept[-1].fields.get("description", "")
                kept[-1].fields["description"] = f"{existing} {joined}".strip()
                kept[-1].raw_text = f"{kept[-1].raw_text} / {row.raw_text}"
                if row.top is not None:
                    kept[-1].top = row.top
            continue

        if not has_amt:
            pending_row = row
            if current_date and not has_date:
                pending_row.fields["date"] = current_date
            continue

        if pending_row is not None:
            prev_desc = pending_row.fields.get("description", "")
            desc = f"{prev_desc} {row.fields.get('description', '')}".strip()
            row.fields["description"] = desc
            row.fields["date"] = pending_row.fields.get("date") or current_date or ""
            pending_row = None
        elif current_date and not row.fields.get("date"):
            row.fields["date"] = current_date

        if _has_parseable_date(row.fields, dialect) and _has_parseable_amount(row.fields):
            kept.append(row)

    return kept


@dataclass(slots=True)
class _AmountResult:
    minor: int | None = None
    direction: str | None = None
    direction_explicit: bool = False
    issue: CandidateIssue | None = None


def clean_amount_text(text: str) -> tuple[str, bool]:
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
    for char in _CURRENCY_CHARS + "₹\ufffd":
        cleaned = cleaned.replace(char, "")
    cleaned = cleaned.replace(",", "").replace(_UNICODE_MINUS, "").strip()
    upper = cleaned.upper()
    if upper.endswith("CR."):
        cleaned = cleaned[:-3].strip()
        negative = False
    elif upper.endswith("CR"):
        cleaned = cleaned[:-2].strip()
        negative = False
    elif upper.endswith("DR"):
        cleaned = cleaned[:-2].strip()
        negative = True
    elif upper.startswith("CR"):
        cleaned = cleaned[2:].strip()
        negative = False
    elif upper.startswith("DR"):
        cleaned = cleaned[2:].strip()
        negative = True
    # Strip any country/currency code prefix or suffix (e.g. "CA 15.11", "20.00USD")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        cleaned = match.group(1)
    return cleaned, negative


def _signed_minor(text: str, currency: str = "GBP") -> tuple[int, bool] | None:
    cleaned, negative = clean_amount_text(text)
    if not cleaned:
        return None
    try:
        decimal = Decimal(cleaned)
    except InvalidOperation:
        return None
    return minor_units(abs(decimal), currency), negative


def _resolve_amount(
    fields: dict[str, str], dialect: Dialect = GENERIC, currency: str = "GBP"
) -> _AmountResult:
    debit_text = fields.get("debit", "").strip()
    credit_text = fields.get("credit", "").strip()
    amount_text = fields.get("amount", "").strip()

    is_explicit_cr = False
    is_explicit_dr = False
    amount_upper = amount_text.upper()
    for marker in dialect.credit_markers:
        if (
            marker in amount_upper
            or fields.get("type", "").upper() == marker
            or fields.get("cr", "").upper() == marker
        ):
            is_explicit_cr = True
            break
    if amount_upper.endswith("DR") or fields.get("type", "").upper() == "DR":
        is_explicit_dr = True

    if amount_text:
        parsed = _signed_minor(amount_text, currency)
        if parsed is None:
            return _AmountResult()
        minor, negative = parsed
        if is_explicit_cr:
            return _AmountResult(minor=minor, direction="credit", direction_explicit=True)
        if is_explicit_dr:
            return _AmountResult(minor=minor, direction="debit", direction_explicit=True)
        if dialect.default_sign == "debit_positive":
            return _AmountResult(minor=minor, direction="credit" if negative else "debit")
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
        parsed = _signed_minor(debit_text or credit_text, currency)
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
        return _AmountResult(minor=minor, direction=implied_direction, direction_explicit=True)

    return _AmountResult()


_BALANCE_MARKERS = (
    "BALANCEBROUGHTFORWARD",
    "BALANCE BROUGHT FORWARD",
    "BALANCECARRIEDFORWARD",
    "BALANCE CARRIED FORWARD",
    "OPENING BALANCE",
    "CLOSING BALANCE",
)
_DATE_WITH_YEAR = re.compile(
    r"(?:\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b|"
    r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{2,4}\b)"
)


def _statement_year(pdf: PDF) -> int | None:
    for page in pdf.pages[:3]:
        match = _DATE_WITH_YEAR.search(page.extract_text() or "")
        if match:
            year_match = re.search(r"\d{2,4}$", match.group())
            if year_match:
                year = int(year_match.group())
                return year + 2000 if year < 100 else year
    return None


def _is_balance_marker(row: _RawRow) -> bool:
    description = " ".join(row.fields.get("description", "").upper().split())
    compact = description.replace(" ", "")
    return any(marker in description or marker in compact for marker in _BALANCE_MARKERS)


def _build_candidate(
    index: int,
    row: _RawRow,
    ocr_min_confidence: float,
    currency: str = "GBP",
    dialect: Dialect = GENERIC,
) -> CandidateTransaction:
    fields = row.fields
    raw_fields = {name: value for name, value in fields.items() if value.strip()}
    raw_fields["raw_text"] = row.raw_text
    candidate = CandidateTransaction(
        candidate_id=f"p{index}",
        transaction_date=fields.get("date", "").strip() or None,
        posted_date=fields.get("posted_date", "").strip() or None,
        raw_description=fields.get("description", "").strip(),
        currency=currency.upper(),
        external_id=fields.get("reference", "").strip() or None,
        source_format="pdf",
        source_page=row.source_page,
        source_line=row.position,
        extraction_method="ocr" if row.is_ocr else "pdf",
        raw_fields=raw_fields,
    )
    amount = _resolve_amount(fields, dialect, currency)
    if amount.issue:
        candidate.issues.append(amount.issue)
    else:
        candidate.amount_minor = amount.minor
        candidate.direction = amount.direction
        candidate.direction_explicit = amount.direction_explicit
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
        dialect: Dialect = GENERIC,
        currency: str = "GBP",
        password: str | None = None,
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
        self.dialect = dialect
        self.currency = currency
        self.password = password

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = ExtractionResult(extractor=self.name)
        password = getattr(source, "password", None) or self.password
        try:
            with pdfplumber.open(source.path, password=password) as pdf:
                return self._extract(pdf, result)
        except Exception as exc:
            is_password_err = isinstance(exc, PDFPasswordIncorrect)
            if not is_password_err and (
                isinstance(getattr(exc, "__cause__", None), PDFPasswordIncorrect)
                or any(isinstance(a, PDFPasswordIncorrect) for a in getattr(exc, "args", ()))
                or "password" in str(exc).lower()
            ):
                is_password_err = True
            if is_password_err:
                result.issues.append(
                    CandidateIssue(
                        PDF_ENCRYPTED,
                        "password-protected PDF; enter statement password to decrypt",
                    )
                )
                return result
            result.issues.append(
                CandidateIssue(
                    PDF_NOT_EXTRACTABLE,
                    "could not read this PDF; try a CSV download or a better-quality statement",
                )
            )
            return result

    def _extract(self, pdf: PDF, result: ExtractionResult) -> ExtractionResult:
        result.page_count = len(pdf.pages)
        result.statement_year = _statement_year(pdf)
        if result.page_count > self._max_pages:
            result.issues.append(
                CandidateIssue(
                    PDF_TOO_MANY_PAGES,
                    f"PDF has {result.page_count} pages; the limit is {self._max_pages}",
                )
            )
            return result

        all_page_rows: list[_RawRow] = []
        first_header_top: float | None = None
        for page in pdf.pages:
            page_rows, header_top = self._page_rows(page)
            if first_header_top is None and header_top is not None:
                first_header_top = header_top
            all_page_rows.extend(page_rows)

        kept = _merge_continuations(all_page_rows, first_header_top, self.dialect)

        transaction_rows = [row for row in kept if not _is_balance_marker(row)]
        candidates = [
            _build_candidate(index, row, self._ocr_min_confidence, self.currency, self.dialect)
            for index, row in enumerate(transaction_rows, start=1)
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
        return _word_rows(self._word_provider(page), page.page_number, self.dialect)
