from datetime import date

from pfa.analytics.service import AnalyticsService


def monthly_review_evidence(
    analytics: AnalyticsService, period: date, currency: str = "GBP"
) -> dict[str, object]:
    """Build the authoritative evidence bundle used by the review narrator."""
    previous = analytics.compare_periods(period, currency=currency).previous
    return {
        "summary": analytics.monthly_summary(period, currency=currency).model_dump(),
        "categories": [
            item.model_dump() for item in analytics.category_spending(period, currency=currency)
        ],
        "comparison": analytics.compare_periods(period, currency=currency).model_dump(),
        "previous_summary": previous.model_dump(),
        "recurring_payments": analytics.recurring_payments(currency=currency),
        "budget_status": [
            item.model_dump() for item in analytics.budget_status(period, currency=currency)
        ],
        "goal_progress": [item.model_dump() for item in analytics.goal_progress()],
        "category_spikes": analytics.category_spikes(period, currency=currency),
        "unusual_transactions": analytics.unusual_transactions(period, currency=currency),
    }
