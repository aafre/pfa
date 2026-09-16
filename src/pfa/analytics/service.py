from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from pfa.db.models import AccountModel, TransactionModel
from pfa.db.repositories import (
    AccountRepository,
    BudgetRepository,
    GoalRepository,
    TransactionRepository,
)
from pfa.domain.accounts import LIQUID_CASH_ACCOUNT_TYPES, AccountType
from pfa.domain.transactions import SpendingCategory, TransactionKind, TransferPurpose, signed_minor

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
}


@dataclass(frozen=True, slots=True)
class CashPosition:
    total_minor: int | None
    known_subtotal_minor: int
    coverage_status: str
    missing_account_ids: tuple[int, ...]
    currency: str


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
        self,
        transactions: TransactionRepository,
        budgets: BudgetRepository,
        goals: GoalRepository,
        accounts: AccountRepository | None = None,
    ):
        self.transactions = transactions
        self.budgets = budgets
        self.goals = goals
        self.accounts = accounts

    def _account_type(self, transaction: TransactionModel) -> AccountType | None:
        account = getattr(transaction, "account", None)
        if account is None and self.accounts is not None:
            account = self.accounts.get(transaction.account_id)
        if account is None:
            return None
        try:
            return AccountType(account.account_type)
        except ValueError:
            return None

    def _filter_currency(
        self, transactions: list[TransactionModel], currency: str
    ) -> list[TransactionModel]:
        curr = currency.upper()
        return [t for t in transactions if (getattr(t, "currency", None) or "GBP").upper() == curr]

    def monthly_summary(self, period: date, currency: str = "GBP") -> MonthlySummary:
        start, end = month_bounds(period)
        all_rows = self.transactions.between(start, end)
        curr = currency.upper()
        rows = self._filter_currency(all_rows, curr)
        income = sum(row.amount_minor for row in rows if row.kind == TransactionKind.INCOME.value)
        spending = sum(_spending(row) for row in rows)
        essential = sum(_spending(row) for row in rows if row.category in _ESSENTIAL)
        savings = sum(
            row.amount_minor
            for row in rows
            if row.transfer_purpose == TransferPurpose.SAVING.value
            and row.flow_direction == "debit"
        )
        investments = sum(
            row.amount_minor
            for row in rows
            if row.transfer_purpose == TransferPurpose.INVESTMENT.value
            and row.flow_direction == "debit"
        )
        debt_costs = sum(
            _spending(row) for row in rows if row.category == SpendingCategory.DEBT_PAYMENT.value
        )
        debt_repayments = sum(
            row.amount_minor
            for row in rows
            if row.kind == TransactionKind.TRANSFER.value
            and row.transfer_purpose == TransferPurpose.CREDIT_CARD_PAYMENT.value
            and signed_minor(row.amount_minor, row.flow_direction) > 0
            and self._account_type(row) == AccountType.CREDIT_CARD
        )
        rate = (
            float(
                (Decimal(savings + investments) / Decimal(income) * 100).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
            if income
            else 0.0
        )
        return MonthlySummary(
            period=start.strftime("%Y-%m"),
            currency=curr,
            income_minor=income,
            spending_minor=spending,
            essential_spending_minor=essential,
            discretionary_spending_minor=spending - essential,
            savings_minor=savings,
            investments_minor=investments,
            debt_payments_minor=debt_costs,
            debt_repayments_minor=debt_repayments,
            debt_costs_minor=debt_costs,
            net_cashflow_minor=income - spending,
            savings_rate_percent=rate,
            transaction_count=len(rows),
        )

    def category_spending(self, period: date, currency: str = "GBP") -> list[CategoryTotal]:
        all_rows = self.transactions.between(*month_bounds(period))
        rows = self._filter_currency(all_rows, currency)
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

    def merchant_spending(self, period: date, currency: str = "GBP") -> list[MerchantTotal]:
        all_rows = self.transactions.between(*month_bounds(period))
        rows = self._filter_currency(all_rows, currency)
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

    def compare_periods(
        self, current: date, previous: date | None = None, currency: str = "GBP"
    ) -> PeriodComparison:
        previous = previous or (current.replace(day=1) - timedelta(days=1))
        current_summary = self.monthly_summary(current, currency=currency)
        previous_summary = self.monthly_summary(previous, currency=currency)
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

    def largest_transactions(
        self, period: date, limit: int = 10, currency: str = "GBP"
    ) -> list[TransactionModel]:
        all_rows = self.transactions.between(*month_bounds(period))
        rows = self._filter_currency(all_rows, currency)
        return sorted(rows, key=lambda row: _spending(row), reverse=True)[:limit]

    def recurring_payments(self, currency: str = "GBP") -> list[dict[str, object]]:
        all_rows = self.transactions.all()
        rows = self._filter_currency(all_rows, currency)
        return detect_recurring(rows)

    def budget_status(self, period: date, currency: str = "GBP") -> list[BudgetStatus]:
        curr = currency.upper()
        actual_by_category = {
            item.category: item.total_minor
            for item in self.category_spending(period, currency=curr)
        }
        statuses = []
        active_budgets = [
            b
            for b in self.budgets.active_on(month_bounds(period)[0])
            if (getattr(b, "currency", None) or "GBP").upper() == curr
        ]
        for budget in active_budgets:
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
                progress_percent=float(
                    (Decimal(goal.current_minor) / Decimal(goal.target_minor) * 100).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                )
                if goal.target_minor
                else 0,
                target_date=goal.target_date.isoformat() if goal.target_date else None,
            )
            for goal in self.goals.active()
        ]

    def cashflow(self, period: date, currency: str = "GBP") -> dict[str, int | str]:
        summary = self.monthly_summary(period, currency=currency)
        return {
            "period": summary.period,
            "currency": summary.currency,
            "income_minor": summary.income_minor,
            "spending_minor": summary.spending_minor,
            "net_cashflow_minor": summary.net_cashflow_minor,
        }

    def unusual_transactions(self, period: date, currency: str = "GBP") -> list[dict[str, object]]:
        all_rows = self.transactions.all()
        rows = self._filter_currency(all_rows, currency)
        return unusual_transactions(rows, period)

    def category_spikes(
        self, current: date, previous: date | None = None, currency: str = "GBP"
    ) -> list[dict[str, object]]:
        previous = previous or (current.replace(day=1) - timedelta(days=1))
        all_rows = self.transactions.all()
        rows = self._filter_currency(all_rows, currency)
        return category_spikes(rows, current, previous)

    def category_trend(
        self, category: str, as_of: date, months: int = 6, currency: str = "GBP"
    ) -> list[dict[str, int | str]]:
        all_rows = self.transactions.all()
        rows = self._filter_currency(all_rows, currency)
        return category_trend(rows, category, as_of, months)


def cash_position(
    accounts: list[AccountModel],
    transactions: list[TransactionModel],
    currency: str = "GBP",
    as_of: date | None = None,
) -> CashPosition:
    curr = currency.upper()
    liquid = [
        account
        for account in accounts
        if (getattr(account, "currency", None) or "GBP").upper() == curr
        and AccountType(account.account_type) in LIQUID_CASH_ACCOUNT_TYPES
    ]
    missing: list[int] = []
    subtotal = 0
    cutoff = as_of or date.max
    for account in liquid:
        baseline = account.opening_balance_as_of
        subtotal += account.opening_balance_minor
        if baseline is None or cutoff < baseline:
            missing.append(account.id)
            # Legacy accounts had no baseline contract. Keep their numeric behavior for
            # callers such as planning while exposing incomplete coverage to new callers.
            subtotal += sum(
                signed_minor(row.amount_minor, row.flow_direction)
                for row in transactions
                if row.account_id == account.id and row.transaction_date <= cutoff
            )
            continue
        subtotal += sum(
            signed_minor(row.amount_minor, row.flow_direction)
            for row in transactions
            if row.account_id == account.id
            and baseline < row.transaction_date <= cutoff
            and (getattr(row, "currency", None) or "GBP").upper() == curr
        )
    return CashPosition(
        total_minor=None if missing else subtotal,
        known_subtotal_minor=subtotal,
        coverage_status="incomplete" if missing else "complete",
        missing_account_ids=tuple(missing),
        currency=curr,
    )


def current_cash(
    accounts: list[AccountModel],
    transactions: list[TransactionModel],
    currency: str = "GBP",
    as_of: date | None = None,
) -> int:
    """Compatibility scalar; use ``cash_position`` when coverage matters."""
    position = cash_position(accounts, transactions, currency=currency, as_of=as_of)
    return (
        position.total_minor if position.total_minor is not None else position.known_subtotal_minor
    )
