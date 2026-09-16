import sys
from datetime import date
from pathlib import Path

from pfa.config import Settings
from pfa.services.runtime import close_services, open_services

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.grounded_answers import _seed_database


def test_grounded_answer_eval_seed_matches_its_reference_facts(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'grounded-eval.db'}"
    _seed_database(database_url)
    engine, services = open_services(Settings(database_url=database_url))
    try:
        summary = services.analytics.monthly_summary(date(2026, 8, 1))
        categories = services.analytics.category_spending(date(2026, 8, 1))
        merchants = services.analytics.merchant_spending(date(2026, 8, 1))

        assert summary.spending_minor == 19_345
        assert summary.income_minor == 500_000
        assert any(
            item.category == "groceries" and item.total_minor == 12_345 for item in categories
        )
        assert any(
            item.merchant == "Test Market" and item.total_minor == 12_345 for item in merchants
        )
        assert services.analytics.monthly_summary(date(2026, 7, 1)).spending_minor == 0
    finally:
        close_services(engine, services)
