from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from pfa.domain.errors import ImportRowError


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if row.get(name, "").strip():
            return row[name].strip()
    return ""


def read_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ImportRowError("CSV has no header row")
        for line_number, raw in enumerate(reader, start=2):
            row = {str(key).strip().lower(): (value or "") for key, value in raw.items()}
            row["_line"] = str(line_number)
            yield {
                "date": _value(row, "date", "transaction_date", "transaction date"),
                "posted_date": _value(row, "posted_date", "posted date"),
                "description": _value(row, "description", "details", "narrative", "merchant"),
                "amount": _value(row, "amount", "value"),
                "currency": _value(row, "currency") or "GBP",
                "kind": _value(row, "kind", "transaction_kind"),
                "category": _value(row, "category"),
                "transfer_purpose": _value(row, "transfer_purpose"),
                "account": _value(row, "account", "account_name") or "Main account",
                "external_id": _value(row, "external_id", "id", "transaction_id"),
                "_line": row["_line"],
            }
