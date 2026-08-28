from __future__ import annotations

from pydantic_ai import Agent

from pfa.ai.deps import FinanceDependencies
from pfa.ai.models import local_model
from pfa.ai.tools.finance import (
    compare_periods,
    get_budget_status,
    get_category_spending,
    get_goal_progress,
    get_merchant_spending,
    get_monthly_summary,
    get_recurring_payments,
    simulate_monthly_contribution,
    simulate_purchase,
)
from pfa.config import Settings

_INSTRUCTIONS = """
You are PFA, a private local personal finance advisor.
Use tools before making factual financial claims. Never invent balances, totals, rates,
transactions or projections. Never do arithmetic that a tool can do. Distinguish facts,
projections and assumptions. Never imply investment returns are guaranteed. The user remains
the decision maker. Tools are read-only; do not suggest that you moved money or changed an account.
Be concise, evidence-backed, and state what information is missing when a tool cannot answer.
"""


def build_advisor(settings: Settings) -> Agent[FinanceDependencies, str]:
    return Agent(
        local_model(settings),
        deps_type=FinanceDependencies,
        output_type=str,
        system_prompt=_INSTRUCTIONS,
        tools=[
            get_monthly_summary,
            get_category_spending,
            get_merchant_spending,
            compare_periods,
            get_recurring_payments,
            get_budget_status,
            get_goal_progress,
            simulate_purchase,
            simulate_monthly_contribution,
        ],
        retries=settings.agent_retries,
        tool_timeout=settings.agent_tool_timeout_seconds,
    )
