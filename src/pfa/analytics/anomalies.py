from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date

from pfa.db.models import TransactionModel

_SPENDING = {"expense", "fee", "cash_withdrawal"}


def _bounds(period: date) -> tuple[date, date]:
    return period.replace(day=1), period.replace(
        day=calendar.monthrange(period.year, period.month)[1]
    )


def _spending(row: TransactionModel) -> int:
    if row.kind in _SPENDING:
        return row.amount_minor
    return -row.amount_minor if row.kind == "refund" else 0


def unusual_transactions(
    transactions: list[TransactionModel], period: date
) -> list[dict[str, object]]:
    values = [_spending(row) for row in transactions if _spending(row) > 0]
    if not values:
        return []
    threshold = max(sum(values) / len(values) * 2.5, 10_000)
    start, end = _bounds(period)
    return [
        {
            "transaction_id": row.id,
            "merchant": row.merchant,
            "amount_minor": row.amount_minor,
            "reason": "large relative to transaction baseline",
        }
        for row in transactions
        if start <= row.transaction_date <= end and _spending(row) >= threshold
    ]


def category_spikes(
    transactions: list[TransactionModel], current: date, previous: date
) -> list[dict[str, object]]:
    def totals(period: date) -> dict[str, int]:
        start, end = _bounds(period)
        result: dict[str, int] = defaultdict(int)
        for row in transactions:
            if start <= row.transaction_date <= end and row.category:
                result[row.category] += _spending(row)
        return result

    now, before = totals(current), totals(previous)
    return [
        {
            "category": category,
            "current_minor": amount,
            "previous_minor": before.get(category, 0),
            "change_minor": amount - before.get(category, 0),
        }
        for category, amount in now.items()
        if amount > 0
        and amount - before.get(category, 0) >= 5_000
        and amount >= before.get(category, 0) * 1.25
    ]
