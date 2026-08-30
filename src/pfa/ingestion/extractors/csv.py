from __future__ import annotations

import csv
from collections.abc import Callable, Iterator
from pathlib import Path

from pfa.domain.errors import ImportRowError
from pfa.ingestion.candidates import (
    AMBIGUOUS_SIGN,
    HEADER_ALIASES,
    HEADERLESS_CSV,
    NO_HEADER_ROW,
    UNREADABLE_FILE,
    WARNING,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
    match_header_alias,
    parse_amount,
    parse_date,
)
from pfa.ingestion.dialects import GENERIC, Dialect

DATE_ALIASES = HEADER_ALIASES["date"]
DESCRIPTION_ALIASES = HEADER_ALIASES["description"]
DEBIT_ALIASES = HEADER_ALIASES["debit"]
CREDIT_ALIASES = HEADER_ALIASES["credit"]
AMOUNT_ALIASES = HEADER_ALIASES["amount"]

# The order assumed for a file that has no header row at all.
POSITIONAL_COLUMNS = ("date", "description", "amount")


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if row.get(name, "").strip():
            return row[name].strip()
    return ""


def _delimiter(path: Path) -> str:
    """Comma unless a sample of the file unambiguously says otherwise."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = "".join(handle.readline() for _ in range(5))
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ","


def _parses(value: str, parse: Callable[[str], object]) -> bool:
    try:
        parse(value)
    except ImportRowError:
        return False
    return True


def _is_headerless(cells: list[str]) -> bool:
    """True when the row DictReader took for a header is really the first transaction.

    HSBC exports ship no header at all, so the first purchase is eaten as the column names
    and every remaining row then fails validation. The test has to be narrow enough that a
    genuine header with unusual wording never trips it, so it demands three things at once:
    not one cell resembles a known column name, the row has exactly the positional shape,
    and its outer cells actually parse as a date and an amount. A description that parses as
    a number is rejected too - that is a numeric table, not a statement.
    """
    if len(cells) != len(POSITIONAL_COLUMNS):
        return False
    if any(match_header_alias(cell) for cell in cells):
        return False
    date_cell, description_cell, amount_cell = cells
    return (
        _parses(date_cell, parse_date)
        and bool(description_cell.strip())
        and not _parses(description_cell, parse_amount)
        and _parses(amount_cell, parse_amount)
    )


def read_csv_rows(
    path: Path, default_currency: str = "GBP", dialect: Dialect = GENERIC
) -> Iterator[dict[str, str]]:
    delimiter = _delimiter(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if not reader.fieldnames:
            raise ImportRowError("CSV has no header row")
        headerless = _is_headerless([str(name) for name in reader.fieldnames])
        if headerless:
            handle.seek(0)  # the first line is data, so read the whole file again
            reader = csv.DictReader(handle, fieldnames=POSITIONAL_COLUMNS, delimiter=delimiter)
        columns = {str(name).strip().lower() for name in reader.fieldnames or ()}
        two_column = not columns.intersection(AMOUNT_ALIASES) and bool(
            columns.intersection(DEBIT_ALIASES + CREDIT_ALIASES)
        )
        for raw in reader:
            row = {str(key).strip().lower(): (value or "") for key, value in raw.items()}
            yield {
                "date": _value(row, *DATE_ALIASES),
                "posted_date": _value(row, "posted_date", "posted date"),
                "description": _value(row, *DESCRIPTION_ALIASES),
                "amount": _value(row, *AMOUNT_ALIASES),
                "debit": _value(row, *DEBIT_ALIASES),
                "credit": _value(row, *CREDIT_ALIASES),
                "currency": _value(row, "currency") or default_currency or "GBP",
                "kind": _value(row, "kind", "transaction_kind"),
                "category": _value(row, "category"),
                "transfer_purpose": _value(row, "transfer_purpose"),
                "account": _value(row, "account", "account_name", "account name") or "Main account",
                "external_id": _value(row, "external_id", "id", "transaction_id", "transaction id"),
                "_line": str(reader.line_num),
                "_mode": "debit_credit" if two_column else "amount",
                "_positional": "1" if headerless else "",
            }


def _amount(row: dict[str, str]) -> tuple[str, CandidateIssue | None]:
    """Resolve one signed amount string; never infer a sign silently."""
    if row["_mode"] == "amount":
        return row["amount"], None
    if bool(row["debit"]) == bool(row["credit"]):
        detail = "both hold a value" if row["debit"] else "neither holds a usable value"
        return "", CandidateIssue(
            AMBIGUOUS_SIGN, f"debit and credit columns {detail}; sign cannot be determined"
        )
    if row["debit"]:
        return "-" + row["debit"].lstrip("+-"), None
    return row["credit"], None


def _candidate(candidate_id: str, row: dict[str, str]) -> CandidateTransaction:
    amount, issue = _amount(row)
    raw_fields = {key: value for key, value in row.items() if value and not key.startswith("_")}
    if row["_mode"] == "debit_credit":
        raw_fields["amount"] = amount
    candidate = CandidateTransaction(
        candidate_id=candidate_id,
        transaction_date=row["date"] or None,
        posted_date=row["posted_date"] or None,
        raw_description=row["description"],
        currency=row["currency"].upper(),
        account_hint=row["account"],
        external_id=row["external_id"] or None,
        kind=row["kind"] or None,
        category=row["category"] or None,
        transfer_purpose=row["transfer_purpose"] or None,
        source_format="csv",
        source_line=int(row["_line"]),
        extraction_method="csv",
        raw_fields=raw_fields,
    )
    if issue:
        candidate.issues.append(issue)
    return candidate


class CsvStatementExtractor:
    """Reads a delimited statement into candidate rows. Never touches the ledger."""

    name = "csv/1"

    def __init__(self, dialect: Dialect = GENERIC, currency: str = "GBP") -> None:
        self.dialect = dialect
        self.currency = currency

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = ExtractionResult(extractor=self.name)
        positional = False
        try:
            for index, row in enumerate(
                read_csv_rows(source.path, default_currency=self.currency, dialect=self.dialect),
                start=1,
            ):
                positional = positional or bool(row["_positional"])
                result.candidates.append(_candidate(f"c{index}", row))
        except ImportRowError as exc:
            result.issues.append(CandidateIssue(NO_HEADER_ROW, str(exc)))
        except UnicodeDecodeError:
            result.issues.append(
                CandidateIssue(UNREADABLE_FILE, "file is not UTF-8 text; export it as UTF-8 CSV")
            )
        if positional:
            result.issues.append(
                CandidateIssue(
                    HEADERLESS_CSV,
                    "no header row was found; columns were read in order as "
                    + ", ".join(POSITIONAL_COLUMNS)
                    + " - check the preview before committing",
                    WARNING,
                )
            )
        currencies = {candidate.currency for candidate in result.candidates}
        accounts = {candidate.account_hint for candidate in result.candidates}
        result.detected_currency = currencies.pop() if len(currencies) == 1 else None
        result.detected_account = accounts.pop() if len(accounts) == 1 else None
        return result
