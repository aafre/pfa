from datetime import date

from pfa.analytics.service import AnalyticsService
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.models import MerchantRuleModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.transactions import TransactionKind
from pfa.ingestion.service import ImportService


def test_import_is_idempotent_and_transfers_do_not_count_as_spending(tmp_path) -> None:
    path = tmp_path / "transactions.csv"
    path.write_text(
        "date,description,amount,kind,category,transfer_purpose,account\n"
        "2026-08-01,Salary,3000,income,,,Main\n"
        "2026-08-02,Rent,-1000,expense,housing,,Main\n"
        "2026-08-03,Savings transfer,-500,transfer,,saving,Main\n"
        "2026-08-04,Investment transfer,-300,transfer,,investment,Main\n"
        "2026-08-05,Refund,50,refund,,,Main\n"
        "2026-08-06,ATM cash withdrawal,-20,cash_withdrawal,,,Main\n"
    )
    engine = make_engine(
        __import__("pfa.config", fromlist=["Settings"]).Settings(database_url="sqlite:///:memory:")
    )
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    importer = ImportService(uow)
    first = importer.import_csv(path)
    session.commit()
    second = importer.import_csv(path)
    service = AnalyticsService(uow.transactions, uow.budgets, uow.goals)
    summary = service.monthly_summary(date(2026, 8, 1))
    assert (first.imported, first.duplicates) == (6, 0)
    assert (second.imported, second.duplicates) == (0, 6)
    assert summary.income_minor == 300000
    assert summary.spending_minor == 95000
    assert summary.savings_minor == 50000
    assert summary.investments_minor == 30000
    assert summary.savings_rate_percent == 26.67
    session.close()


def test_unknown_expense_is_reported_for_review(tmp_path) -> None:
    path = tmp_path / "transactions.csv"
    path.write_text("date,description,amount\n2026-08-01,Unknown shop,-12.50\n")
    from pfa.config import Settings

    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    result = ImportService(UnitOfWork(session)).import_csv(path)
    assert result.imported == 1
    assert result.requires_classification == 1
    assert len(UnitOfWork(session).transactions.uncategorized()) == 1
    assert (
        session.query(__import__("pfa.db.models", fromlist=["TransactionModel"]).TransactionModel)
        .one()
        .kind
        == TransactionKind.EXPENSE.value
    )
    session.close()


def test_persisted_correction_rule_is_used_on_later_import(tmp_path) -> None:
    path = tmp_path / "transactions.csv"
    path.write_text("date,description,amount\n2026-08-01,LOCAL CAFE,-12.50\n")
    from pfa.config import Settings

    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    uow.rules.add(MerchantRuleModel(pattern="LOCAL CAFE", kind="expense", category="eating_out"))
    result = ImportService(uow).import_csv(path)
    row = uow.transactions.all()[0]
    assert result.requires_classification == 0
    assert row.category == "eating_out"
    assert row.classification_source == "rule"
    session.close()
