from datetime import date

from pfa.analytics.service import AnalyticsService
from pfa.config import Settings
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.unit_of_work import UnitOfWork
from pfa.ingestion.service import ImportService


def services() -> tuple[object, UnitOfWork, AnalyticsService]:
    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    return engine, uow, AnalyticsService(uow.transactions, uow.budgets, uow.goals)


def test_refunds_net_against_spending_in_their_transaction_month(tmp_path) -> None:
    path = tmp_path / "refunds.csv"
    path.write_text(
        "date,description,amount,kind,category\n"
        "2026-07-31,Restaurant,-100,expense,eating_out\n"
        "2026-08-01,Restaurant refund,40,refund,eating_out\n"
        "2026-08-02,Full purchase,-100,expense,shopping\n"
        "2026-08-31,Full refund,100,refund,shopping\n",
        encoding="utf-8",
    )
    engine, uow, analytics = services()
    ImportService(uow).import_csv(path)

    july = analytics.monthly_summary(date(2026, 7, 1))
    august = analytics.monthly_summary(date(2026, 8, 1))
    august_categories = {
        item.category: item.total_minor for item in analytics.category_spending(date(2026, 8, 1))
    }

    assert july.spending_minor == 10_000
    assert august.spending_minor == -4_000
    assert august_categories == {"shopping": 0, "eating_out": -4_000}
    uow.session.close()
    engine.dispose()


def test_transfers_cash_withdrawals_income_and_month_boundaries(tmp_path) -> None:
    path = tmp_path / "boundaries.csv"
    path.write_text(
        "date,description,amount,kind,category,transfer_purpose,account\n"
        "2026-07-31,Salary,3000,income,,,Current\n"
        "2026-08-01,Transfer to savings,-500,transfer,,saving,Current\n"
        "2026-08-01,Transfer from current,500,transfer,,saving,Savings\n"
        "2026-08-31,ATM,-100,cash_withdrawal,,,Current\n"
        "2026-09-01,Restaurant,-25,expense,eating_out,,Current\n",
        encoding="utf-8",
    )
    engine, uow, analytics = services()
    ImportService(uow).import_csv(path)

    august = analytics.monthly_summary(date(2026, 8, 1))
    september = analytics.monthly_summary(date(2026, 9, 1))
    assert (august.income_minor, august.spending_minor, august.net_cashflow_minor) == (0, 0, 0)
    assert september.spending_minor == 2_500
    uow.session.close()
    engine.dispose()


def test_legitimate_identical_rows_are_preserved_and_reimport_is_idempotent(tmp_path) -> None:
    path = tmp_path / "duplicates.csv"
    path.write_text(
        "date,description,amount,kind,category,account\n"
        "2026-08-10,Tesco,-20,expense,groceries,Current\n"
        "2026-08-10,Tesco,-20,expense,groceries,Current\n",
        encoding="utf-8",
    )
    engine, uow, _ = services()
    importer = ImportService(uow)

    first = importer.import_csv(path)
    uow.session.commit()
    second = importer.import_csv(path)

    assert (first.imported, first.duplicates) == (2, 0)
    assert (second.imported, second.duplicates) == (0, 2)
    assert len(uow.transactions.all()) == 2
    uow.session.close()
    engine.dispose()


def test_same_day_purchase_and_refund_do_not_share_a_fingerprint(tmp_path) -> None:
    path = tmp_path / "collision.csv"
    path.write_text(
        "date,description,amount,kind,category,account\n"
        "2026-08-10,Tesco,-20,expense,groceries,Current\n"
        "2026-08-10,Tesco,20,refund,groceries,Current\n",
        encoding="utf-8",
    )
    engine, uow, analytics = services()
    result = ImportService(uow).import_csv(path)

    assert (result.imported, result.duplicates) == (2, 0)
    assert analytics.monthly_summary(date(2026, 8, 1)).spending_minor == 0
    uow.session.close()
    engine.dispose()


def test_dry_run_rolls_back_accounts_transactions_and_state(tmp_path) -> None:
    path = tmp_path / "dry.csv"
    path.write_text(
        "date,description,amount\n2026-08-01,Valid purchase,-12.50\nnot-a-date,Bad row,-5\n",
        encoding="utf-8",
    )
    engine, uow, _ = services()
    result = ImportService(uow).import_csv(path, dry_run=True)

    assert result.imported == 1
    assert len(result.errors) == 1
    assert uow.transactions.all() == []
    assert uow.accounts.all() == []
    uow.session.close()
    engine.dispose()


def test_unsupported_currency_fails_closed_instead_of_reporting_false_gbp(tmp_path) -> None:
    path = tmp_path / "currency.csv"
    path.write_text(
        "date,description,amount,kind,currency\n2026-08-01,Salary,1000,income,USD\n",
        encoding="utf-8",
    )
    engine, uow, _ = services()
    result = ImportService(uow).import_csv(path)

    assert result.imported == 0
    assert result.errors == ["row 2: unsupported currency 'USD'; PFA v0.1 supports GBP only"]
    assert uow.transactions.all() == []
    uow.session.close()
    engine.dispose()
