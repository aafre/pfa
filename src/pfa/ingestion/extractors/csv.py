from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from pfa.domain.errors import ImportRowError
from pfa.ingestion.candidates import (
    AMBIGUOUS_SIGN,
    NO_HEADER_ROW,
    UNREADABLE_FILE,
    CandidateIssue,
    CandidateTransaction,
    ExtractionResult,
    StatementSource,
)

DEBIT_ALIASES = ("debit", "paid out", "paid_out", "withdrawn", "money out", "money_out")
CREDIT_ALIASES = ("credit", "paid in", "paid_in", "received", "money in", "money_in")
AMOUNT_ALIASES = ("amount", "value")


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


def read_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=_delimiter(path))
        if not reader.fieldnames:
            raise ImportRowError("CSV has no header row")
        columns = {str(name).strip().lower() for name in reader.fieldnames}
        two_column = not columns.intersection(AMOUNT_ALIASES) and bool(
            columns.intersection(DEBIT_ALIASES + CREDIT_ALIASES)
        )
        for raw in reader:
            row = {str(key).strip().lower(): (value or "") for key, value in raw.items()}
            yield {
                "date": _value(row, "date", "transaction_date", "transaction date"),
                "posted_date": _value(row, "posted_date", "posted date"),
                "description": _value(row, "description", "details", "narrative", "merchant"),
                "amount": _value(row, *AMOUNT_ALIASES),
                "debit": _value(row, *DEBIT_ALIASES),
                "credit": _value(row, *CREDIT_ALIASES),
                "currency": _value(row, "currency") or "GBP",
                "kind": _value(row, "kind", "transaction_kind"),
                "category": _value(row, "category"),
                "transfer_purpose": _value(row, "transfer_purpose"),
                "account": _value(row, "account", "account_name", "account name") or "Main account",
                "external_id": _value(row, "external_id", "id", "transaction_id", "transaction id"),
                "_line": str(reader.line_num),
                "_mode": "debit_credit" if two_column else "amount",
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

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = ExtractionResult(extractor=self.name)
        try:
            for index, row in enumerate(read_csv_rows(source.path), start=1):
                result.candidates.append(_candidate(f"c{index}", row))
        except ImportRowError as exc:
            result.issues.append(CandidateIssue(NO_HEADER_ROW, str(exc)))
        except UnicodeDecodeError:
            result.issues.append(
                CandidateIssue(UNREADABLE_FILE, "file is not UTF-8 text; export it as UTF-8 CSV")
            )
        currencies = {candidate.currency for candidate in result.candidates}
        accounts = {candidate.account_hint for candidate in result.candidates}
        result.detected_currency = currencies.pop() if len(currencies) == 1 else None
        result.detected_account = accounts.pop() if len(accounts) == 1 else None
        return result
