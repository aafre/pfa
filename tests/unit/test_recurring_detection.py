from datetime import date

from pfa.analytics.recurring import detect_recurring
from pfa.db.models import TransactionModel


def row(day: date, merchant: str, amount: int, category: str) -> TransactionModel:
    return TransactionModel(
        transaction_date=day,
        raw_description=merchant,
        normalized_description=merchant,
        merchant=merchant,
        amount_minor=amount,
        flow_direction="debit",
        currency="GBP",
        kind="expense",
        category=category,
        classification_source="import",
        import_source="test",
        fingerprint=f"{merchant}-{day}",
    )


def test_monthly_subscription_exposes_evidence_and_uncertainty() -> None:
    result = detect_recurring(
        [
            row(date(2026, 6, 15), "NETFLIX", 1_099, "subscriptions"),
            row(date(2026, 7, 15), "NETFLIX", 1_099, "subscriptions"),
            row(date(2026, 8, 15), "NETFLIX", 1_099, "subscriptions"),
        ]
    )

    assert result == [
        {
            "merchant": "NETFLIX",
            "likely_recurring": True,
            "confidence": "moderate",
            "cadence": "monthly",
            "observations": 3,
            "median_gap_days": 30.5,
            "amount_similarity_ratio": 1.0,
            "average_amount_minor": 1_099,
            "last_seen": "2026-08-15",
        }
    ]


def test_weekly_grocery_pattern_is_not_reported_as_a_recurring_payment() -> None:
    transactions = [
        row(date(2026, 8, 1), "TESCO", 5_000, "groceries"),
        row(date(2026, 8, 8), "TESCO", 5_200, "groceries"),
        row(date(2026, 8, 15), "TESCO", 4_800, "groceries"),
        row(date(2026, 8, 22), "TESCO", 5_100, "groceries"),
    ]
    assert detect_recurring(transactions) == []


def test_variable_monthly_utility_is_detected_but_missing_and_annual_cadences_are_not() -> None:
    utility = [
        row(date(2026, 6, 1), "WATER", 10_000, "utilities"),
        row(date(2026, 7, 1), "WATER", 11_000, "utilities"),
        row(date(2026, 8, 1), "WATER", 9_500, "utilities"),
    ]
    missing_month = [
        row(date(2026, 1, 1), "GYM", 3_000, "subscriptions"),
        row(date(2026, 2, 1), "GYM", 3_000, "subscriptions"),
        row(date(2026, 4, 1), "GYM", 3_000, "subscriptions"),
    ]
    annual = [
        row(date(2024, 8, 1), "INSURER", 40_000, "insurance"),
        row(date(2025, 8, 1), "INSURER", 40_000, "insurance"),
        row(date(2026, 8, 1), "INSURER", 40_000, "insurance"),
    ]

    assert detect_recurring(utility)[0]["cadence"] == "monthly"
    assert detect_recurring(missing_month) == []
    assert detect_recurring(annual) == []
