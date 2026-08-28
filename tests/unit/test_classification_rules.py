from pfa.config import Settings
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.models import MerchantRuleModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.transactions import SpendingCategory, TransactionKind
from pfa.ingestion.categorizer import classify_known


def test_known_rules_match_tokens_without_substring_false_positives() -> None:
    transfer = classify_known("CURRENT ACCOUNT TRANSFER")
    rent = classify_known("MONTHLY RENT")
    unrelated = classify_known("CURRENT EVENTS")

    assert transfer is not None and transfer.kind == TransactionKind.TRANSFER
    assert rent is not None and rent.category == SpendingCategory.HOUSING
    assert unrelated is None


def test_user_correction_rules_are_normalized_exact_matches() -> None:
    engine = make_engine(Settings(database_url="sqlite:///:memory:"))
    init_db(engine)
    session = make_session_factory(engine)()
    uow = UnitOfWork(session)
    uow.rules.add(
        MerchantRuleModel(
            pattern="LOCAL CAFE",
            kind="expense",
            category="eating_out",
            created_from_user_correction=True,
        )
    )

    assert uow.rules.match("LOCAL CAFE") is not None
    assert uow.rules.match("local cafe") is not None
    assert uow.rules.match("LOCAL CAFE EXPRESS") is None
    assert uow.rules.match("A LOCAL CAFE") is None
    session.close()
    engine.dispose()
