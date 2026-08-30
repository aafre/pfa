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
    assert august.savings_minor == 50_000
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
        "date,description,amount,kind,currency\n2026-08-01,Salary,1000,income,XYZ\n",
        encoding="utf-8",
    )
    engine, uow, _ = services()
    result = ImportService(uow).import_csv(path)

    assert result.imported == 0
    assert len(result.errors) == 1
    assert "unsupported currency 'XYZ'" in result.errors[0]
    assert uow.transactions.all() == []
    uow.session.close()
    engine.dispose()


def test_classifier_receives_signed_amounts_from_the_bank_format_adapter(tmp_path) -> None:
    class RecordingClassifier:
        def __init__(self) -> None:
            self.amounts: list[int] = []

        def classify(self, description: str, signed_amount_minor: int) -> None:
            self.amounts.append(signed_amount_minor)

    path = tmp_path / "signs.csv"
    path.write_text(
        "date,description,amount\n"
        "2026-08-01,Unknown debit,-12.50\n"
        "2026-08-02,Unknown credit,2.50\n",
        encoding="utf-8",
    )
    engine, uow, _ = services()
    classifier = RecordingClassifier()
    ImportService(uow, classifier).import_csv(path)

    assert classifier.amounts == [-1_250, 250]
    uow.session.close()
    engine.dispose()


def test_headerless_csv_returns_a_parser_error_without_mutation(tmp_path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    engine, uow, _ = services()

    result = ImportService(uow).import_csv(path)

    assert result.imported == 0
    assert result.errors == ["CSV has no header row"]
    assert uow.transactions.all() == []
    assert uow.accounts.all() == []
    uow.session.close()
    engine.dispose()


def test_headerless_export_imports_every_row_with_the_signs_it_was_written_with(tmp_path) -> None:
    path = tmp_path / "hsbc.csv"
    path.write_text(
        "14/08/2025,COFFEE HOUSE LONDON GB,-14.38\n"
        "13/08/2025,NEWSAGENT LEEDS GB,-1.95\n"
        "12/08/2025,SALARY PAYMENT,2500.00\n",
        encoding="utf-8",
    )
    engine, uow, _ = services()

    result = ImportService(uow).import_csv(path)

    assert (result.imported, result.errors) == (3, [])
    ledger = sorted(uow.transactions.all(), key=lambda row: row.transaction_date)
    assert [row.transaction_date for row in ledger] == [
        date(2025, 8, 12),
        date(2025, 8, 13),
        date(2025, 8, 14),
    ]
    assert [(row.amount_minor, row.flow_direction) for row in ledger] == [
        (250_000, "credit"),
        (195, "debit"),
        (1_438, "debit"),
    ]
    uow.session.close()
    engine.dispose()


def test_mixed_currency_analytics_strictly_partitions_currencies_without_sum_pollution(
    tmp_path,
) -> None:
    """Invariant: an INR account alongside GBP must NEVER sum into 405,000 of something."""
    path_gbp = tmp_path / "gbp.csv"
    path_gbp.write_text(
        "date,description,amount,kind,category,currency,account\n"
        "2026-08-01,Salary,5000,income,,GBP,UK Bank\n"
        "2026-08-05,Groceries,-200,expense,groceries,GBP,UK Bank\n",
        encoding="utf-8",
    )
    path_inr = tmp_path / "inr.csv"
    path_inr.write_text(
        "date,description,amount,kind,category,currency,account\n"
        "2026-08-01,Consulting,400000,income,,INR,India Bank\n"
        "2026-08-10,Rent,-50000,expense,housing,INR,India Bank\n",
        encoding="utf-8",
    )

    engine, uow, analytics = services()
    importer = ImportService(uow)
    importer.import_csv(path_gbp)
    importer.import_csv(path_inr)

    # Check GBP analytics
    gbp_summary = analytics.monthly_summary(date(2026, 8, 1), currency="GBP")
    assert gbp_summary.currency == "GBP"
    assert gbp_summary.income_minor == 500_000  # 5,000.00 GBP
    assert gbp_summary.spending_minor == 20_000  # 200.00 GBP
    assert gbp_summary.net_cashflow_minor == 480_000
    assert gbp_summary.transaction_count == 2

    # Check INR analytics
    inr_summary = analytics.monthly_summary(date(2026, 8, 1), currency="INR")
    assert inr_summary.currency == "INR"
    assert inr_summary.income_minor == 40_000_000  # 400,000.00 INR
    assert inr_summary.spending_minor == 5_000_000  # 50,000.00 INR
    assert inr_summary.net_cashflow_minor == 35_000_000
    assert inr_summary.transaction_count == 2

    # Verify category spending is partitioned
    gbp_cats = {
        item.category: item.total_minor
        for item in analytics.category_spending(date(2026, 8, 1), currency="GBP")
    }
    assert gbp_cats == {"groceries": 20_000}

    inr_cats = {
        item.category: item.total_minor
        for item in analytics.category_spending(date(2026, 8, 1), currency="INR")
    }
    assert inr_cats == {"housing": 5_000_000}

    uow.session.close()
    engine.dispose()
