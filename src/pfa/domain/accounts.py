from enum import StrEnum


class AccountType(StrEnum):
    CURRENT = "current"
    SAVINGS = "savings"
    CREDIT_CARD = "credit_card"
    INVESTMENT = "investment"
    CASH = "cash"
    LOAN = "loan"


NON_CASH_ACCOUNT_TYPES = {AccountType.INVESTMENT, AccountType.LOAN}
