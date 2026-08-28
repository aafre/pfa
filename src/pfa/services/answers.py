from __future__ import annotations

import re
from datetime import date, timedelta

from pfa.analytics.service import AnalyticsService
from pfa.domain.money import Money
from pfa.domain.transactions import SpendingCategory
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
_CATEGORY_ALIASES = {item.value.replace("_", " "): item.value for item in SpendingCategory}


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
    if re.search(r"\b(?:drop|delete|update|insert)\b.*\b(?:table|sql|transactions)\b", lower):
        return "PFA cannot execute SQL or mutate the financial database through advisor tools."
    if re.match(r"\s*(?:transfer|send|move|buy|purchase|trade)\b", lower):
        return "PFA is read-only and cannot move money, place trades, or make purchases."
    if "how much" in lower and ("spend" in lower or "spent" in lower):
        category = next(
            (value for alias, value in _CATEGORY_ALIASES.items() if alias in lower), None
        )
        month_name = next((name for name in _MONTHS if re.search(rf"\b{name}\b", lower)), None)
        if category and month_name:
            period = _period_for_name(analytics, month_name)
            if period:
                total = next(
                    (
                        item.total_minor
                        for item in analytics.category_spending(period)
                        if item.category == category
                    ),
                    0,
                )
                return f"{category} spending in {period.strftime('%Y-%m')} was {_amount(total)}."
    month_name = next((name for name in _MONTHS if re.search(rf"\b{name}\b", lower)), None)
    if month_name and any(word in lower for word in ("spending", "spent", "estimate")):
        period = _period_for_name(analytics, month_name)
        if period:
            summary = analytics.monthly_summary(period)
            return f"Total spending in {summary.period} was {_amount(summary.spending_minor)}."
    if "categories" in lower and "increased" in lower:
        rows = analytics.transactions.all()
        if rows:
            latest = max(row.transaction_date for row in rows).replace(day=1)
            categories = sorted({row.category for row in rows if row.category})
            increases = []
            for category in categories:
                points = analytics.category_trend(category, latest, 3)
                delta = int(points[-1]["total_minor"]) - int(points[0]["total_minor"])
                if delta > 0:
                    increases.append((category, delta))
            increases.sort(key=lambda item: -item[1])
            if not increases:
                return (
                    "No category increased from the first to the last of the latest three months."
                )
            return "Category increases over the latest three months: " + "; ".join(
                f"{category} +{_amount(delta)}" for category, delta in increases
            )
    if "savings rate" in lower:
        rows = analytics.transactions.all()
        if rows:
            year = max(row.transaction_date.year for row in rows)
            names = [name for name in _MONTHS if re.search(rf"\b{name}\b", lower)]
            if len(names) >= 2:
                cursor = date(year, _MONTHS[names[0]], 1)
                end = date(year, _MONTHS[names[-1]], 1)
            else:
                end = max(row.transaction_date for row in rows).replace(day=1)
                cursor = end
                for _ in range(2):
                    cursor = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
            rate_points: list[str] = []
            while cursor <= end and len(rate_points) < 24:
                summary = analytics.monthly_summary(cursor)
                rate_points.append(f"{summary.period}: {summary.savings_rate_percent:.2f}%")
                month = cursor.month % 12 + 1
                year_cursor = cursor.year + (1 if cursor.month == 12 else 0)
                cursor = date(year_cursor, month, 1)
            return "Savings rate by month: " + "; ".join(rate_points) + "."
    if "goal" in lower:
        goals = analytics.goal_progress()
        if not goals:
            return "No active financial goals are recorded."
        return "Active goals: " + "; ".join(
            f"{goal.name}: {_amount(goal.current_minor)} of {_amount(goal.target_minor)} "
            f"({goal.progress_percent:.2f}%)"
            for goal in goals
        )
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
    return None
