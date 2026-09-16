import re
from dataclasses import dataclass

from pfa.domain.accounts import AccountType
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


def classify_known(
    description: str,
    *,
    account_type: AccountType | str | None = None,
    canonical_sign: int | None = None,
    owned_card: bool = False,
) -> Classification | None:
    upper = description.upper()
    if account_type is not None and canonical_sign is not None:
        account = AccountType(account_type)
        if (
            account == AccountType.CREDIT_CARD
            and canonical_sign > 0
            and re.search(r"PAYMENT RECEIVED(?:\s|[-])", upper)
        ):
            return Classification(
                TransactionKind.TRANSFER,
                transfer_purpose=TransferPurpose.CREDIT_CARD_PAYMENT,
                reason="credit-card payment rule",
            )
        if account in {AccountType.CURRENT, AccountType.SAVINGS} and re.search(
            r"\bAMERICAN EXPRESS\s+DD\b", upper
        ):
            if owned_card:
                return Classification(
                    TransactionKind.TRANSFER,
                    transfer_purpose=TransferPurpose.CREDIT_CARD_PAYMENT,
                    reason="owned card repayment rule",
                )
            return Classification(TransactionKind.UNKNOWN, reason="possible card repayment")
    for pattern, classification in _RULES:
        if re.search(rf"(?<![A-Z0-9]){re.escape(pattern)}(?![A-Z0-9])", upper):
            return classification
    return None
