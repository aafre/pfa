from dataclasses import dataclass

from pfa.analytics.service import AnalyticsService
from pfa.planning.service import PlanningService


@dataclass(slots=True)
class FinanceDependencies:
    analytics: AnalyticsService
    planning: PlanningService
