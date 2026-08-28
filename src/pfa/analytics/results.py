from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CategoryTotal(BaseModel):
    category: str
    total_minor: int
    transaction_count: int


class MerchantTotal(BaseModel):
    merchant: str
    total_minor: int
    transaction_count: int


class MonthlySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str
    currency: str = "GBP"
    income_minor: int = 0
    spending_minor: int = 0
    essential_spending_minor: int = 0
    discretionary_spending_minor: int = 0
    savings_minor: int = 0
    investments_minor: int = 0
    debt_payments_minor: int = 0
    net_cashflow_minor: int = 0
    savings_rate_percent: float = 0.0
    transaction_count: int = 0


class PeriodComparison(BaseModel):
    current: MonthlySummary
    previous: MonthlySummary
    changes_minor: dict[str, int]


class BudgetStatus(BaseModel):
    category: str | None
    budget_minor: int
    actual_minor: int
    remaining_minor: int
    over_budget: bool


class GoalProgress(BaseModel):
    name: str
    goal_type: str
    current_minor: int
    target_minor: int
    progress_percent: float = Field(ge=0)
    target_date: str | None = None
