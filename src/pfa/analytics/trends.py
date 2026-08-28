from __future__ import annotations

import calendar
from datetime import date, timedelta

from pfa.db.models import TransactionModel


def _spending(row: TransactionModel) -> int:
    if row.kind in {"expense", "fee"}:
        return row.amount_minor
    return -row.amount_minor if row.kind == "refund" else 0


def category_trend(
    transactions: list[TransactionModel], category: str, as_of: date, months: int = 6
) -> list[dict[str, int | str]]:
    points: list[dict[str, int | str]] = []
    cursor = as_of.replace(day=1)
    for _ in range(months):
        start = cursor.replace(day=1)
        end = cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
        total = sum(
            _spending(row)
            for row in transactions
            if row.category == category and start <= row.transaction_date <= end
        )
        points.append({"period": start.strftime("%Y-%m"), "total_minor": total})
        cursor = (start - timedelta(days=1)).replace(day=1)
    return list(reversed(points))
