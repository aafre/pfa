from datetime import date

from pfa.analytics.service import AnalyticsService
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.models import TransactionModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.planning.service import PlanningService
from pfa.services.answers import deterministic_answer


def test_common_comparison_is_answered_from_deterministic_facts() -> None:
    from pfa.config import Settings

    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    account = uow.accounts.get_or_create("Main")
    for day, amount, category in (
        (date(2026, 7, 1), 10000, "groceries"),
        (date(2026, 8, 1), 20000, "eating_out"),
    ):
        uow.transactions.add(
            TransactionModel(
                account_id=account.id,
                transaction_date=day,
                raw_description=category,
                normalized_description=category.upper(),
                merchant=category.upper(),
                amount_minor=amount,
                currency="GBP",
                kind="expense",
                category=category,
                import_source="test",
                fingerprint=f"{day}-{category}",
            )
        )
    analytics = AnalyticsService(uow.transactions, uow.budgets, uow.goals)
    answer = deterministic_answer(
        analytics,
        PlanningService(analytics, [account], uow.transactions.all()),
        "Why was August more expensive than July?",
    )
    assert answer is not None
    assert "GBP 200.00" in answer and "eating_out" in answer
    session.close()


def test_affordability_question_accepts_pound_sign() -> None:
    from pfa.config import Settings

    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    analytics = AnalyticsService(uow.transactions, uow.budgets, uow.goals)
    answer = deterministic_answer(
        analytics,
        PlanningService(analytics, [], []),
        "Can I afford a \N{POUND SIGN}2,000 purchase?",
    )
    assert answer is not None and "GBP 2,000.00" in answer
    session.close()
