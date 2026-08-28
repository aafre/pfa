from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta

from pfa.db.models import AccountModel, TransactionModel
from pfa.db.repositories import BudgetRepository, GoalRepository, TransactionRepository
from pfa.domain.accounts import NON_CASH_ACCOUNT_TYPES
from pfa.domain.transactions import SpendingCategory, TransactionKind, TransferPurpose

from .anomalies import category_spikes, unusual_transactions
from .recurring import detect_recurring
from .results import (
    BudgetStatus,
    CategoryTotal,
    GoalProgress,
    MerchantTotal,
    MonthlySummary,
    PeriodComparison,
)
from .trends import category_trend

_ESSENTIAL = {
    SpendingCategory.HOUSING.value,
    SpendingCategory.GROCERIES.value,
    SpendingCategory.TRANSPORT.value,
    SpendingCategory.UTILITIES.value,
    SpendingCategory.HEALTH.value,
    SpendingCategory.PERSONAL_CARE.value,
    SpendingCategory.INSURANCE.value,
    SpendingCategory.DEBT_PAYMENT.value,
    SpendingCategory.FEES.value,
}
_SPENDING_KINDS = {
    TransactionKind.EXPENSE.value,
    TransactionKind.FEE.value,
    TransactionKind.CASH_WITHDRAWAL.value,
}


def month_bounds(period: date) -> tuple[date, date]:
    start = period.replace(day=1)
    return start, period.replace(day=calendar.monthrange(period.year, period.month)[1])


def _spending(transaction: TransactionModel) -> int:
    if transaction.kind in _SPENDING_KINDS:
        return transaction.amount_minor
    if transaction.kind == TransactionKind.REFUND.value:
        return -transaction.amount_minor
    return 0


def _cash_delta(transaction: TransactionModel) -> int:
    if transaction.kind == TransactionKind.INCOME.value:
        return transaction.amount_minor
    if transaction.kind in _SPENDING_KINDS:
        return -transaction.amount_minor
    if transaction.kind == TransactionKind.REFUND.value:
        return transaction.amount_minor
    return 0


class AnalyticsService:
    def __init__(
        self, transactions: TransactionRepository, budgets: BudgetRepository, goals: GoalRepository
    ):
        self.transactions = transactions
        self.budgets = budgets
        self.goals = goals

    def monthly_summary(self, period: date) -> MonthlySummary:
        start, end = month_bounds(period)
        rows = self.transactions.between(start, end)
        income = sum(row.amount_minor for row in rows if row.kind == TransactionKind.INCOME.value)
        spending = sum(_spending(row) for row in rows)
        essential = sum(_spending(row) for row in rows if row.category in _ESSENTIAL)
        savings = sum(
            row.amount_minor for row in rows if row.transfer_purpose == TransferPurpose.SAVING.value
        )
        investments = sum(
            row.amount_minor
            for row in rows
            if row.transfer_purpose == TransferPurpose.INVESTMENT.value
        )
        debt = sum(
            row.amount_minor for row in rows if row.category == SpendingCategory.DEBT_PAYMENT.value
        )
        rate = round((savings + investments) / income * 100, 2) if income else 0.0
        return MonthlySummary(
            period=start.strftime("%Y-%m"),
            income_minor=income,
            spending_minor=spending,
            essential_spending_minor=essential,
            discretionary_spending_minor=spending - essential,
            savings_minor=savings,
            investments_minor=investments,
            debt_payments_minor=debt,
            net_cashflow_minor=income - spending,
            savings_rate_percent=rate,
            transaction_count=len(rows),
        )

    def category_spending(self, period: date) -> list[CategoryTotal]:
        rows = self.transactions.between(*month_bounds(period))
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in rows:
            value = _spending(row)
            if value and row.category:
                totals[row.category][0] += value
                totals[row.category][1] += 1
        return [
            CategoryTotal(category=key, total_minor=value[0], transaction_count=value[1])
            for key, value in sorted(totals.items(), key=lambda item: -item[1][0])
        ]

    def merchant_spending(self, period: date) -> list[MerchantTotal]:
        rows = self.transactions.between(*month_bounds(period))
        totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in rows:
            value = _spending(row)
            if value:
                totals[row.merchant or row.normalized_description][0] += value
                totals[row.merchant or row.normalized_description][1] += 1
        return [
            MerchantTotal(merchant=key, total_minor=value[0], transaction_count=value[1])
            for key, value in sorted(totals.items(), key=lambda item: -item[1][0])
        ]

    def compare_periods(self, current: date, previous: date | None = None) -> PeriodComparison:
        previous = previous or (current.replace(day=1) - timedelta(days=1))
        current_summary = self.monthly_summary(current)
        previous_summary = self.monthly_summary(previous)
        fields = (
            "income_minor",
            "spending_minor",
            "savings_minor",
            "investments_minor",
            "net_cashflow_minor",
        )
        changes = {
            field: getattr(current_summary, field) - getattr(previous_summary, field)
            for field in fields
        }
        return PeriodComparison(
            current=current_summary, previous=previous_summary, changes_minor=changes
        )

    def largest_transactions(self, period: date, limit: int = 10) -> list[TransactionModel]:
        rows = self.transactions.between(*month_bounds(period))
        return sorted(rows, key=lambda row: _spending(row), reverse=True)[:limit]

    def recurring_payments(self) -> list[dict[str, object]]:
        return detect_recurring(self.transactions.all())

    def budget_status(self, period: date) -> list[BudgetStatus]:
        actual_by_category = {
            item.category: item.total_minor for item in self.category_spending(period)
        }
        statuses = []
        for budget in self.budgets.active_on(month_bounds(period)[0]):
            actual = (
                sum(actual_by_category.values())
                if budget.category is None
                else actual_by_category.get(budget.category, 0)
            )
            statuses.append(
                BudgetStatus(
                    category=budget.category,
                    budget_minor=budget.amount_minor,
                    actual_minor=actual,
                    remaining_minor=budget.amount_minor - actual,
                    over_budget=actual > budget.amount_minor,
                )
            )
        return statuses

    def goal_progress(self) -> list[GoalProgress]:
        return [
            GoalProgress(
                name=goal.name,
                goal_type=goal.goal_type,
                current_minor=goal.current_minor,
                target_minor=goal.target_minor,
                progress_percent=round(goal.current_minor / goal.target_minor * 100, 2)
                if goal.target_minor
                else 0,
                target_date=goal.target_date.isoformat() if goal.target_date else None,
            )
            for goal in self.goals.active()
        ]

    def cashflow(self, period: date) -> dict[str, int | str]:
        summary = self.monthly_summary(period)
        return {
            "period": summary.period,
            "income_minor": summary.income_minor,
            "spending_minor": summary.spending_minor,
            "net_cashflow_minor": summary.net_cashflow_minor,
        }

    def unusual_transactions(self, period: date) -> list[dict[str, object]]:
        return unusual_transactions(self.transactions.all(), period)

    def category_spikes(
        self, current: date, previous: date | None = None
    ) -> list[dict[str, object]]:
        previous = previous or (current.replace(day=1) - timedelta(days=1))
        return category_spikes(self.transactions.all(), current, previous)

    def category_trend(
        self, category: str, as_of: date, months: int = 6
    ) -> list[dict[str, int | str]]:
        return category_trend(self.transactions.all(), category, as_of, months)


def current_cash(accounts: list[AccountModel], transactions: list[TransactionModel]) -> int:
    opening = sum(
        account.opening_balance_minor
        for account in accounts
        if account.account_type not in {item.value for item in NON_CASH_ACCOUNT_TYPES}
    )
    return opening + sum(_cash_delta(row) for row in transactions)
