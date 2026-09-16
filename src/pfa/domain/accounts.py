from enum import StrEnum


class AccountType(StrEnum):
    CURRENT = "current"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    INVESTMENT = "investment"
    CASH = "cash"
    LOAN = "loan"


LIQUID_CASH_ACCOUNT_TYPES = frozenset({AccountType.CURRENT, AccountType.SAVINGS, AccountType.CASH})

_ACCOUNT_NATURE: dict[AccountType, str] = {
    AccountType.CURRENT: "asset",
    AccountType.SAVINGS: "asset",
    AccountType.CASH: "asset",
    AccountType.INVESTMENT: "asset",
    AccountType.CREDIT_CARD: "liability",
    AccountType.LOAN: "liability",
}

# Kept for callers that used the old constant; liquid-cash membership is the safer API.
NON_CASH_ACCOUNT_TYPES = set(AccountType) - LIQUID_CASH_ACCOUNT_TYPES


def account_nature(account_type: AccountType | str) -> str:
    return _ACCOUNT_NATURE[AccountType(account_type)]


def is_liquid_cash(account_type: AccountType | str) -> bool:
    return AccountType(account_type) in LIQUID_CASH_ACCOUNT_TYPES
