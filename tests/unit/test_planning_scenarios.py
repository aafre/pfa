from datetime import date

from pfa.analytics.results import MonthlySummary
from pfa.db.models import AccountModel, TransactionModel
from pfa.planning.service import PlanningService


class StableHistory:
    def monthly_summary(self, period: date, currency: str = "GBP") -> MonthlySummary:
        return MonthlySummary(
            period=period.strftime("%Y-%m"),
            currency=currency,
            income_minor=400_000,
            spending_minor=300_000,
            net_cashflow_minor=100_000,
        )


def planning() -> PlanningService:
    account = AccountModel(
        name="Current", account_type="current", currency="GBP", opening_balance_minor=500_000
    )
    return PlanningService(StableHistory(), [account], [])  # type: ignore[arg-type]


def test_purchase_projection_matches_manual_arithmetic_for_multiple_horizons() -> None:
    service = planning()

    one = service.simulate_purchase(200_000, 1, date(2026, 9, 1))
    three = service.simulate_purchase(200_000, 3, date(2026, 9, 1))
    six = service.simulate_purchase(200_000, 6, date(2026, 9, 1))

    assert (one.baseline_month_end_cash_minor, one.projected_month_end_cash_minor) == (
        600_000,
        400_000,
    )
    assert (three.baseline_month_end_cash_minor, three.projected_month_end_cash_minor) == (
        800_000,
        600_000,
    )
    assert (six.baseline_month_end_cash_minor, six.projected_month_end_cash_minor) == (
        1_100_000,
        900_000,
    )
    assert (one.months_of_expenses_after, three.months_of_expenses_after) == (1.33, 2.0)


def test_negative_projection_and_monthly_contribution_are_explicit() -> None:
    service = planning()
    purchase = service.simulate_purchase(1_000_000, 3, date(2026, 9, 1))
    contribution = service.simulate_monthly_contribution(50_000, 6, date(2026, 9, 1))

    assert purchase.projected_month_end_cash_minor == -200_000
    assert purchase.months_of_expenses_after == 0
    assert purchase.affordable is False
    assert contribution.projected_month_end_cash_minor == 800_000
    assert contribution.scenario_cost_minor == 300_000


def test_projection_starting_cash_excludes_future_dated_transactions() -> None:
    account = AccountModel(
        id=1,
        name="Current",
        account_type="current",
        currency="GBP",
        opening_balance_minor=500_000,
    )
    future = TransactionModel(
        account_id=1,
        transaction_date=date(2026, 10, 1),
        raw_description="Future bill",
        normalized_description="FUTURE BILL",
        merchant="FUTURE BILL",
        amount_minor=100_000,
        flow_direction="debit",
        currency="GBP",
        kind="expense",
        category="utilities",
        classification_source="import",
        import_source="test",
        fingerprint="future",
    )
    service = PlanningService(StableHistory(), [account], [future])  # type: ignore[arg-type]

    result = service.simulate_purchase(0, 1, date(2026, 9, 1))
    assert result.starting_cash_minor == 500_000
