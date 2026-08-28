from datetime import date

from pfa.analytics.anomalies import category_spikes
from pfa.analytics.trends import category_trend
from pfa.db.models import TransactionModel


def _row(day: date, amount: int, category: str = "eating_out") -> TransactionModel:
    return TransactionModel(
        transaction_date=day,
        amount_minor=amount,
        kind="expense",
        category=category,
        normalized_description="TEST",
        raw_description="Test",
        import_source="test",
        fingerprint=f"{day}-{amount}",
        currency="GBP",
        account_id=1,
    )


def test_trend_and_category_spike_are_explainable() -> None:
    rows = [_row(date(2026, 7, 2), 10000), _row(date(2026, 8, 2), 20000)]
    assert category_trend(rows, "eating_out", date(2026, 8, 15), 2) == [
        {"period": "2026-07", "total_minor": 10000},
        {"period": "2026-08", "total_minor": 20000},
    ]
    assert category_spikes(rows, date(2026, 8, 1), date(2026, 7, 1))[0]["change_minor"] == 10000
