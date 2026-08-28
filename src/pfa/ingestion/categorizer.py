from dataclasses import dataclass

from pfa.domain.transactions import SpendingCategory, TransactionKind, TransferPurpose


@dataclass(frozen=True, slots=True)
class Classification:
    kind: TransactionKind
    category: SpendingCategory | None = None
    transfer_purpose: TransferPurpose | None = None
    source: str = "rule"
    confidence: float | None = 1.0
    reason: str = "deterministic merchant rule"


_RULES: tuple[tuple[str, Classification], ...] = (
    ("SALARY", Classification(TransactionKind.INCOME, reason="salary rule")),
    ("PAYROLL", Classification(TransactionKind.INCOME, reason="payroll rule")),
    ("RENT", Classification(TransactionKind.EXPENSE, SpendingCategory.HOUSING, reason="rent rule")),
    (
        "TESCO",
        Classification(TransactionKind.EXPENSE, SpendingCategory.GROCERIES, reason="grocer rule"),
    ),
    (
        "SAINSBURY",
        Classification(TransactionKind.EXPENSE, SpendingCategory.GROCERIES, reason="grocer rule"),
    ),
    (
        "RESTAURANT",
        Classification(
            TransactionKind.EXPENSE, SpendingCategory.EATING_OUT, reason="restaurant rule"
        ),
    ),
    (
        "DELIVEROO",
        Classification(
            TransactionKind.EXPENSE, SpendingCategory.EATING_OUT, reason="restaurant rule"
        ),
    ),
    (
        "UBER",
        Classification(
            TransactionKind.EXPENSE, SpendingCategory.TRANSPORT, reason="transport rule"
        ),
    ),
    (
        "TRAIN",
        Classification(
            TransactionKind.EXPENSE, SpendingCategory.TRANSPORT, reason="transport rule"
        ),
    ),
    (
        "ELECTRIC",
        Classification(TransactionKind.EXPENSE, SpendingCategory.UTILITIES, reason="utility rule"),
    ),
    (
        "NETFLIX",
        Classification(
            TransactionKind.EXPENSE, SpendingCategory.SUBSCRIPTIONS, reason="subscription rule"
        ),
    ),
    (
        "SPOTIFY",
        Classification(
            TransactionKind.EXPENSE, SpendingCategory.SUBSCRIPTIONS, reason="subscription rule"
        ),
    ),
    ("ATM", Classification(TransactionKind.CASH_WITHDRAWAL, reason="cash withdrawal rule")),
    ("REFUND", Classification(TransactionKind.REFUND, reason="refund rule")),
    (
        "SAVINGS",
        Classification(
            TransactionKind.TRANSFER,
            transfer_purpose=TransferPurpose.SAVING,
            reason="savings transfer rule",
        ),
    ),
    (
        "INVEST",
        Classification(
            TransactionKind.TRANSFER,
            transfer_purpose=TransferPurpose.INVESTMENT,
            reason="investment transfer rule",
        ),
    ),
    (
        "TRANSFER",
        Classification(
            TransactionKind.TRANSFER, transfer_purpose=TransferPurpose.OTHER, reason="transfer rule"
        ),
    ),
)


def classify_known(description: str) -> Classification | None:
    upper = description.upper()
    for pattern, classification in _RULES:
        if pattern in upper:
            return classification
    return None
