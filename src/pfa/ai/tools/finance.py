from __future__ import annotations

from datetime import date

from pydantic_ai import RunContext

from pfa.ai.deps import FinanceDependencies


def parse_month(period: str) -> date:
    year, month = (int(part) for part in period.split("-"))
    return date(year, month, 1)


def get_monthly_summary(ctx: RunContext[FinanceDependencies], period: str) -> dict[str, object]:
    """Return deterministic income, spending, savings and cashflow for YYYY-MM."""
    return ctx.deps.analytics.monthly_summary(parse_month(period)).model_dump()


def get_category_spending(
    ctx: RunContext[FinanceDependencies], period: str
) -> list[dict[str, object]]:
    """Return deterministic spending totals grouped by category for YYYY-MM."""
    return [item.model_dump() for item in ctx.deps.analytics.category_spending(parse_month(period))]


def get_merchant_spending(
    ctx: RunContext[FinanceDependencies], period: str
) -> list[dict[str, object]]:
    """Return deterministic spending totals grouped by merchant for YYYY-MM."""
    return [item.model_dump() for item in ctx.deps.analytics.merchant_spending(parse_month(period))]


def compare_periods(
    ctx: RunContext[FinanceDependencies], current: str, previous: str
) -> dict[str, object]:
    """Compare deterministic monthly facts for two YYYY-MM periods."""
    return ctx.deps.analytics.compare_periods(
        parse_month(current), parse_month(previous)
    ).model_dump()


def get_recurring_payments(ctx: RunContext[FinanceDependencies]) -> list[dict[str, object]]:
    """Find likely recurring payments using merchant, cadence and amount evidence."""
    return ctx.deps.analytics.recurring_payments()


def get_spending_trend(
    ctx: RunContext[FinanceDependencies], category: str, period: str, months: int = 6
) -> list[dict[str, int | str]]:
    """Return deterministic monthly category totals for a requested baseline window."""
    return ctx.deps.analytics.category_trend(category, parse_month(period), months)


def get_budget_status(ctx: RunContext[FinanceDependencies], period: str) -> list[dict[str, object]]:
    """Return deterministic budget actuals and remaining amounts for YYYY-MM."""
    return [item.model_dump() for item in ctx.deps.analytics.budget_status(parse_month(period))]


def get_goal_progress(ctx: RunContext[FinanceDependencies]) -> list[dict[str, object]]:
    """Return deterministic progress for active financial goals."""
    return [item.model_dump() for item in ctx.deps.analytics.goal_progress()]


def simulate_purchase(
    ctx: RunContext[FinanceDependencies],
    cost_minor: int,
    horizon_months: int = 3,
    period: str | None = None,
) -> dict[str, object]:
    """Simulate a purchase in minor units over a stated horizon; assumptions are explicit."""
    return ctx.deps.planning.simulate_purchase(
        cost_minor, horizon_months, parse_month(period) if period else None
    ).model_dump()


def simulate_monthly_contribution(
    ctx: RunContext[FinanceDependencies],
    additional_minor: int,
    horizon_months: int = 6,
    period: str | None = None,
) -> dict[str, object]:
    """Simulate an additional monthly saving/investment contribution in minor units."""
    return ctx.deps.planning.simulate_monthly_contribution(
        additional_minor, horizon_months, parse_month(period) if period else None
    ).model_dump()
