from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field

from pfa.analytics.service import AnalyticsService, current_cash
from pfa.db.models import AccountModel, TransactionModel


class ScenarioResult(BaseModel):
    starting_cash_minor: int
    scenario_cost_minor: int
    projected_month_end_cash_minor: int
    baseline_month_end_cash_minor: int
    months_of_expenses_after: float | None
    affordable: bool
    assumptions: list[str] = Field(default_factory=list)


class PlanningService:
    def __init__(
        self,
        analytics: AnalyticsService,
        accounts: list[AccountModel],
        transactions: list[TransactionModel],
    ):
        self.analytics = analytics
        self.accounts = accounts
        self.transactions = transactions

    def _average_monthly_net(self, as_of: date, months: int = 3, currency: str = "GBP") -> int:
        values = []
        cursor = as_of.replace(day=1)
        for _ in range(months):
            cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
            values.append(
                self.analytics.monthly_summary(cursor, currency=currency).net_cashflow_minor
            )
        return (
            int((Decimal(sum(values)) / len(values)).quantize(Decimal("1"), ROUND_HALF_UP))
            if values
            else 0
        )

    def _average_monthly_spending(self, as_of: date, months: int = 3, currency: str = "GBP") -> int:
        values = []
        cursor = as_of.replace(day=1)
        for _ in range(months):
            cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
            values.append(
                max(self.analytics.monthly_summary(cursor, currency=currency).spending_minor, 0)
            )
        return (
            int((Decimal(sum(values)) / len(values)).quantize(Decimal("1"), ROUND_HALF_UP))
            if values
            else 0
        )

    def simulate_purchase(
        self,
        cost_minor: int,
        horizon_months: int = 3,
        as_of: date | None = None,
        currency: str = "GBP",
    ) -> ScenarioResult:
        as_of = as_of or date.today()
        starting = current_cash(self.accounts, self.transactions, currency=currency, as_of=as_of)
        monthly_net = self._average_monthly_net(as_of, currency=currency)
        average_expenses = self._average_monthly_spending(as_of, currency=currency)
        baseline = starting + monthly_net * horizon_months
        scenario = baseline - cost_minor
        months = (
            float(
                (Decimal(max(scenario, 0)) / Decimal(average_expenses)).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
            )
            if average_expenses
            else None
        )
        return ScenarioResult(
            starting_cash_minor=starting,
            scenario_cost_minor=cost_minor,
            projected_month_end_cash_minor=scenario,
            baseline_month_end_cash_minor=baseline,
            months_of_expenses_after=months,
            affordable=scenario >= 0,
            assumptions=[
                (
                    "average net cash flow from prior three complete months: "
                    f"{monthly_net} minor units ({currency})"
                ),
                (
                    "average spending from prior three complete months: "
                    f"{average_expenses} minor units ({currency})"
                ),
                "purchase occurs immediately; no investment returns assumed",
                f"horizon: {horizon_months} months",
            ],
        )

    def simulate_monthly_contribution(
        self,
        additional_minor: int,
        horizon_months: int = 6,
        as_of: date | None = None,
        currency: str = "GBP",
    ) -> ScenarioResult:
        result = self.simulate_purchase(0, horizon_months, as_of, currency=currency)
        scenario = result.baseline_month_end_cash_minor - additional_minor * horizon_months
        return result.model_copy(
            update={
                "scenario_cost_minor": additional_minor * horizon_months,
                "projected_month_end_cash_minor": scenario,
                "affordable": scenario >= 0,
                "assumptions": [
                    *result.assumptions,
                    f"additional monthly contribution: {additional_minor} minor units ({currency})",
                ],
            }
        )
