from datetime import date

from pfa.analytics.service import cash_position
from pfa.db.models import AccountModel, TransactionModel
from pfa.domain.transactions import signed_minor


def transaction(
    transaction_id: int,
    account_id: int,
    at: date,
    amount_minor: int,
    direction: str,
    kind: str = "transfer",
) -> TransactionModel:
    return TransactionModel(
        id=transaction_id,
        account_id=account_id,
        transaction_date=at,
        raw_description="test",
        normalized_description="TEST",
        amount_minor=amount_minor,
        flow_direction=direction,
        currency="GBP",
        kind=kind,
        classification_source="test",
        import_source="test",
        fingerprint=f"test-{transaction_id}",
    )


def test_signed_minor_is_independent_of_account_nature() -> None:
    assert signed_minor(1_000, "credit") == 1_000
    assert signed_minor(1_000, "debit") == -1_000


def test_cash_uses_liquid_accounts_and_end_of_day_baselines() -> None:
    current = AccountModel(
        id=1,
        name="Current",
        account_type="current",
        currency="GBP",
        opening_balance_minor=100_000,
        opening_balance_as_of=date(2026, 8, 31),
    )
    card = AccountModel(
        id=2,
        name="Card",
        account_type="credit_card",
        currency="GBP",
        opening_balance_minor=50_000,
        opening_balance_as_of=date(2026, 8, 31),
    )
    rows = [
        transaction(1, 1, date(2026, 8, 31), 10_000, "debit"),
        transaction(2, 1, date(2026, 9, 1), 20_000, "debit"),
        transaction(3, 2, date(2026, 9, 1), 30_000, "debit", "expense"),
    ]

    position = cash_position([current, card], rows, as_of=date(2026, 9, 1))

    assert position.total_minor == 80_000
    assert position.coverage_status == "complete"
    assert position.missing_account_ids == ()


def test_missing_cash_baseline_is_explicitly_incomplete() -> None:
    account = AccountModel(
        id=7,
        name="Old account",
        account_type="current",
        currency="GBP",
        opening_balance_minor=10_000,
    )

    position = cash_position([account], [], as_of=date(2026, 9, 1))

    assert position.total_minor is None
    assert position.coverage_status == "incomplete"
    assert position.missing_account_ids == (7,)
