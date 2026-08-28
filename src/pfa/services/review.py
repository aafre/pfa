from datetime import date

from pfa.analytics.service import AnalyticsService


def monthly_review_evidence(analytics: AnalyticsService, period: date) -> dict[str, object]:
    """Build the authoritative evidence bundle used by the review narrator."""
    previous = analytics.compare_periods(period).previous
    return {
        "summary": analytics.monthly_summary(period).model_dump(),
        "categories": [item.model_dump() for item in analytics.category_spending(period)],
        "comparison": analytics.compare_periods(period).model_dump(),
        "previous_summary": previous.model_dump(),
        "recurring_payments": analytics.recurring_payments(),
        "budget_status": [item.model_dump() for item in analytics.budget_status(period)],
        "goal_progress": [item.model_dump() for item in analytics.goal_progress()],
    }
