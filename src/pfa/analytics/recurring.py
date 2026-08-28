from __future__ import annotations

from collections import defaultdict
from statistics import median

from pfa.db.models import TransactionModel
from pfa.domain.transactions import TransactionKind


def detect_recurring(transactions: list[TransactionModel]) -> list[dict[str, object]]:
    groups: dict[str, list[TransactionModel]] = defaultdict(list)
    for transaction in transactions:
        if transaction.kind in {TransactionKind.EXPENSE.value, TransactionKind.FEE.value}:
            groups[transaction.merchant or transaction.normalized_description].append(transaction)
    found: list[dict[str, object]] = []
    for merchant, observations in groups.items():
        observations.sort(key=lambda item: item.transaction_date)
        if len(observations) < 3:
            continue
        gaps = [
            (b.transaction_date - a.transaction_date).days
            for a, b in zip(observations, observations[1:], strict=False)
        ]
        typical_gap = median(gaps)
        if 25 <= typical_gap <= 35:
            cadence = "monthly"
        elif 6 <= typical_gap <= 8:
            cadence = "weekly"
        elif 80 <= typical_gap <= 100:
            cadence = "quarterly"
        else:
            continue
        amounts = [item.amount_minor for item in observations]
        average = sum(amounts) / len(amounts)
        similar = sum(abs(amount - average) / max(average, 1) <= 0.15 for amount in amounts)
        if similar / len(amounts) < 0.67:
            continue
        found.append(
            {
                "merchant": merchant,
                "likely_recurring": True,
                "cadence": cadence,
                "observations": len(observations),
                "average_amount_minor": round(average),
                "last_seen": max(item.transaction_date for item in observations).isoformat(),
            }
        )
    return sorted(found, key=lambda item: str(item["merchant"]))
