from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

from pfa.db.models import TransactionModel
from pfa.domain.transactions import SpendingCategory, TransactionKind

_RECURRING_CATEGORIES = {
    SpendingCategory.HOUSING.value,
    SpendingCategory.UTILITIES.value,
    SpendingCategory.SUBSCRIPTIONS.value,
    SpendingCategory.INSURANCE.value,
    SpendingCategory.DEBT_PAYMENT.value,
    SpendingCategory.FEES.value,
}


def detect_recurring(transactions: list[TransactionModel]) -> list[dict[str, object]]:
    groups: dict[str, list[TransactionModel]] = defaultdict(list)
    for transaction in transactions:
        if transaction.kind in {TransactionKind.EXPENSE.value, TransactionKind.FEE.value} and (
            transaction.category is None or transaction.category in _RECURRING_CATEGORIES
        ):
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
        average = Decimal(sum(amounts)) / len(amounts)
        similar = sum(
            Decimal(abs(Decimal(amount) - average)) / max(average, Decimal(1)) <= Decimal("0.15")
            for amount in amounts
        )
        similarity_ratio = Decimal(similar) / len(amounts)
        if similarity_ratio < Decimal("0.67"):
            continue
        found.append(
            {
                "merchant": merchant,
                "likely_recurring": True,
                "confidence": "high" if len(observations) >= 4 else "moderate",
                "cadence": cadence,
                "observations": len(observations),
                "median_gap_days": typical_gap,
                "amount_similarity_ratio": float(
                    similarity_ratio.quantize(Decimal("0.01"), ROUND_HALF_UP)
                ),
                "average_amount_minor": int(average.quantize(Decimal("1"), ROUND_HALF_UP)),
                "last_seen": max(item.transaction_date for item in observations).isoformat(),
            }
        )
    return sorted(found, key=lambda item: str(item["merchant"]))
