from pfa.config import Settings
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.unit_of_work import UnitOfWork
from pfa.ingestion import candidates as codes
from pfa.ingestion.candidates import CandidateTransaction
from pfa.ingestion.service import ImportService


def candidate(candidate_id="c1", amount="-12.50", **overrides) -> CandidateTransaction:
    fields = {
        "transaction_date": "2026-08-01",
        "raw_description": "Tesco groceries",
        "account_hint": "Main account",
        "raw_fields": {"amount": amount},
    }
    fields.update(overrides)
    return CandidateTransaction(candidate_id=candidate_id, **fields)


def importer() -> tuple[object, ImportService]:
    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    return engine, ImportService(UnitOfWork(session))


def test_validation_reports_one_issue_code_per_blocking_problem() -> None:
    rows = [
        candidate("c1", transaction_date="not-a-date"),
        candidate("c2", raw_description=""),
        candidate("c3", amount="not-a-number"),
        candidate("c4", currency="EUR"),
        candidate("c5", kind="teleportation"),
        candidate("c6", kind="expense", category="submarines"),
        candidate("c7", kind="transfer", transfer_purpose="hoarding"),
        candidate("c8", posted_date="32/13/2026"),
    ]
    engine, service = importer()

    service.validate(rows)

    assert [row.state for row in rows] == [codes.ERROR] * 8
    assert [row.issues[0].code for row in rows] == [
        codes.INVALID_DATE,
        codes.MISSING_DESCRIPTION,
        codes.INVALID_AMOUNT,
        codes.UNSUPPORTED_CURRENCY,
        codes.UNKNOWN_KIND,
        codes.UNKNOWN_CATEGORY,
        codes.UNKNOWN_TRANSFER_PURPOSE,
        codes.INVALID_DATE,
    ]
    assert rows[0].issues[0].message == "invalid date 'not-a-date'"
    service.uow.session.close()
    engine.dispose()


def test_validation_normalizes_amount_magnitude_direction_and_description() -> None:
    debit = candidate("c1", amount="-1,234.56", raw_description="  tesco   Metro ")
    credit = candidate("c2", amount="£20")
    engine, service = importer()

    service.validate([debit, credit])

    assert (debit.amount_minor, debit.direction, debit.signed_amount_minor) == (
        123_456,
        "debit",
        -123_456,
    )
    assert (credit.amount_minor, credit.direction, credit.signed_amount_minor) == (
        2_000,
        "credit",
        2_000,
    )
    assert debit.normalized_description == "TESCO METRO"
    assert debit.state == codes.VALID
    service.uow.session.close()
    engine.dispose()


def test_extractor_supplied_amount_is_trusted_and_not_reparsed() -> None:
    row = candidate("c1", amount="ignored", amount_minor=500, direction="credit")
    engine, service = importer()

    service.validate([row])

    assert (row.state, row.amount_minor, row.direction) == (codes.VALID, 500, "credit")
    service.uow.session.close()
    engine.dispose()


def test_repeated_identical_rows_in_one_statement_get_distinct_fingerprints() -> None:
    rows = [candidate("c1"), candidate("c2")]
    engine, service = importer()

    service.validate(rows)
    service.resolve_duplicates(rows)

    assert rows[0].fingerprint != rows[1].fingerprint
    assert [row.duplicate_of for row in rows] == [None, None]
    service.uow.session.close()
    engine.dispose()


def test_rows_already_in_the_ledger_are_flagged_duplicate_and_never_recommitted() -> None:
    engine, service = importer()
    first = [candidate("c1"), candidate("c2")]
    service.validate(first)
    service.resolve_duplicates(first)
    service.commit(first, source_label="statement")
    service.uow.session.commit()

    second = [candidate("c1"), candidate("c2")]
    service.validate(second)
    service.resolve_duplicates(second)
    committed = service.commit(second, source_label="statement")

    assert [row.duplicate_of for row in second] == [1, 2]
    assert [row.issues[0].code for row in second] == [codes.DUPLICATE_ROW] * 2
    assert [row.state for row in second] == [codes.WARNING] * 2
    assert committed == []
    assert len(service.uow.transactions.all()) == 2
    service.uow.session.close()
    engine.dispose()


def test_excluded_and_error_rows_are_never_committed() -> None:
    rows = [
        candidate("c1", included=False),
        candidate("c2", transaction_date="nope"),
        candidate("c3"),
    ]
    engine, service = importer()

    service.validate(rows)
    service.resolve_duplicates(rows)
    committed = service.commit(rows, source_label="statement")

    assert [transaction.raw_description for transaction in committed] == ["Tesco groceries"]
    assert len(service.uow.transactions.all()) == 1
    service.uow.session.close()
    engine.dispose()
