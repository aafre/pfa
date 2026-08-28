from __future__ import annotations

import re
from datetime import date

from pfa.analytics.service import AnalyticsService
from pfa.domain.money import Money
from pfa.planning.service import PlanningService

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


def _amount(minor: int) -> str:
    return f"GBP {Money(minor).to_major():,.2f}"


def _period_for_name(analytics: AnalyticsService, name: str) -> date | None:
    rows = analytics.transactions.all()
    year = max((row.transaction_date.year for row in rows), default=date.today().year)
    return date(year, _MONTHS[name.lower()], 1)


def deterministic_answer(
    analytics: AnalyticsService, planning: PlanningService, question: str
) -> str | None:
    """Answer common factual intents without asking a model to choose numeric parameters."""
    lower = question.lower()
    if "more expensive" in lower or "compared with" in lower:
        names = list(dict.fromkeys(re.findall(r"\b(" + "|".join(_MONTHS) + r")\b", lower)))
        if len(names) >= 2:
            current, previous = (
                _period_for_name(analytics, names[0]),
                _period_for_name(analytics, names[1]),
            )
            if current and previous:
                comparison = analytics.compare_periods(current, previous)
                current_categories = {
                    item.category: item.total_minor for item in analytics.category_spending(current)
                }
                previous_categories = {
                    item.category: item.total_minor
                    for item in analytics.category_spending(previous)
                }
                increases = sorted(
                    (
                        (category, amount - previous_categories.get(category, 0))
                        for category, amount in current_categories.items()
                        if amount > previous_categories.get(category, 0)
                    ),
                    key=lambda item: -item[1],
                )[:3]
                reasons = (
                    "; ".join(f"{category} +{_amount(delta)}" for category, delta in increases)
                    or "no category increased"
                )
                delta = comparison.current.spending_minor - comparison.previous.spending_minor
                direction = "increased" if delta >= 0 else "decreased"
                return (
                    f"Spending {direction} from {_amount(comparison.previous.spending_minor)} "
                    f"in {comparison.previous.period} to "
                    f"{_amount(comparison.current.spending_minor)} "
                    f"in {comparison.current.period}. Main changes: {reasons}."
                )
    if "recurring" in lower or "subscriptions" in lower:
        recurring = analytics.recurring_payments()
        if not recurring:
            return "No likely recurring payments found in the available transaction history."
        return (
            "Likely recurring payments: "
            + "; ".join(
                f"{item['merchant']} ({item['cadence']}, "
                f"{_amount(int(str(item['average_amount_minor'])))})"
                for item in recurring
            )
            + "."
        )
    if "afford" in lower:
        match = re.search(r"(?:\u00a3|gbp)\s*([\d,]+(?:\.\d{1,2})?)", lower)
        if match:
            cost = Money.from_major(match.group(1).replace(",", "")).minor
            result = planning.simulate_purchase(cost)
            return (
                f"Scenario result: projected cash is "
                f"{_amount(result.projected_month_end_cash_minor)} versus "
                f"baseline {_amount(result.baseline_month_end_cash_minor)}. "
                f"Affordable under the stated model: {result.affordable}."
            )
    if "afford" in lower and (match := re.search(r"(?:£|gbp)\s*([\d,]+(?:\.\d{1,2})?)", lower)):
        cost = Money.from_major(match.group(1).replace(",", "")).minor
        result = planning.simulate_purchase(cost)
        return (
            f"Scenario result: projected cash is {_amount(result.projected_month_end_cash_minor)} "
            f"versus baseline {_amount(result.baseline_month_end_cash_minor)}. "
            f"Affordable under the stated model: {result.affordable}."
        )
    return None
