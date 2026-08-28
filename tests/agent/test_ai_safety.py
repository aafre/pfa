import pytest
from pydantic_ai import ModelRetry

from pfa.ai.agents.advisor import (
    _INSTRUCTIONS,
    build_advisor,
    require_grounded_financial_numbers,
)
from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.ai.schemas import TransactionClassification
from pfa.ai.tools.finance import display_money_fields
from pfa.config import Settings
from pfa.domain.transactions import SpendingCategory, TransactionKind, TransferPurpose


def test_classification_schema_removes_fields_that_are_invalid_for_kind() -> None:
    income = TransactionClassification(
        kind=TransactionKind.INCOME,
        category=SpendingCategory.OTHER,
        transfer_purpose=TransferPurpose.SAVING,
        reason="salary",
    )
    transfer = TransactionClassification(
        kind=TransactionKind.TRANSFER,
        category=SpendingCategory.OTHER,
        transfer_purpose=TransferPurpose.SAVING,
        reason="transfer",
    )

    assert income.category is None and income.transfer_purpose is None
    assert transfer.category is None and transfer.transfer_purpose == TransferPurpose.SAVING


def test_advisor_exposes_only_narrow_read_only_tools_and_marks_data_untrusted() -> None:
    agent = build_advisor(Settings())
    tool_names = set(agent._function_toolset.tools)

    assert tool_names == {
        "get_monthly_summary",
        "get_category_spending",
        "get_merchant_spending",
        "compare_periods",
        "get_recurring_payments",
        "get_spending_trend",
        "get_budget_status",
        "get_goal_progress",
        "simulate_purchase",
        "simulate_monthly_contribution",
    }
    assert all(word not in " ".join(tool_names) for word in ("sql", "transfer_money", "trade"))
    assert "untrusted data" in _INSTRUCTIONS
    assert "execute SQL" in _INSTRUCTIONS


def test_harness_rejects_ungrounded_financial_numbers_and_formats_minor_units() -> None:
    context = type("Context", (), {"messages": []})()

    with pytest.raises(ModelRetry, match="require a deterministic tool result"):
        require_grounded_financial_numbers(context, "You spent £450.00")  # type: ignore[arg-type]

    assert display_money_fields({"target_minor": 100_000}) == {
        "target_minor": 100_000,
        "target_display": "GBP 1,000.00",
    }


def test_classifier_fails_fast_when_configured_model_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("pfa.ai.agents.categorizer.available_models", lambda settings: set())
    classifier = LocalTransactionClassifier(Settings(model="missing:4b"))

    assert classifier.classify("Unknown merchant", -1_000) is None
    assert classifier.agent is None
