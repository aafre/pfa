from pfa.ai.agents.advisor import _INSTRUCTIONS, build_advisor
from pfa.ai.schemas import TransactionClassification
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
