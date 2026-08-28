from pfa.ai.deps import FinanceDependencies
from pfa.ai.tools.finance import get_monthly_summary, simulate_purchase
from pfa.analytics.service import AnalyticsService
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.unit_of_work import UnitOfWork
from pfa.planning.service import PlanningService


def test_tools_use_deterministic_services_without_model() -> None:
    from pfa.config import Settings

    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    analytics = AnalyticsService(uow.transactions, uow.budgets, uow.goals)
    deps = FinanceDependencies(analytics, PlanningService(analytics, [], []))
    context = type("Context", (), {"deps": deps})()
    assert get_monthly_summary(context, "2026-08")["period"] == "2026-08"
    assert simulate_purchase(context, 200_000, 3, "2026-08")["scenario_cost_minor"] == 200_000
    session.close()
