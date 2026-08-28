from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from pfa.analytics.service import AnalyticsService
from pfa.config import Settings
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.unit_of_work import UnitOfWork
from pfa.planning.service import PlanningService


@dataclass(slots=True)
class FinanceServices:
    uow: UnitOfWork
    analytics: AnalyticsService
    planning: PlanningService


def open_services(settings: Settings) -> tuple[Engine, FinanceServices]:
    engine = make_engine(settings)
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    analytics = AnalyticsService(uow.transactions, uow.budgets, uow.goals)
    planning = PlanningService(analytics, uow.accounts.all(), uow.transactions.all())
    return engine, FinanceServices(uow, analytics, planning)


def close_services(engine: Engine, services: FinanceServices, commit: bool = True) -> None:
    if commit:
        services.uow.session.commit()
    else:
        services.uow.session.rollback()
    services.uow.session.close()
    engine.dispose()
