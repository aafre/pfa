from enum import StrEnum


class TransactionKind(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    REFUND = "refund"
    CASH_WITHDRAWAL = "cash_withdrawal"
    FEE = "fee"
    UNKNOWN = "unknown"


class SpendingCategory(StrEnum):
    HOUSING = "housing"
    GROCERIES = "groceries"
    EATING_OUT = "eating_out"
    TRANSPORT = "transport"
    UTILITIES = "utilities"
    SUBSCRIPTIONS = "subscriptions"
    SHOPPING = "shopping"
    ENTERTAINMENT = "entertainment"
    HEALTH = "health"
    PERSONAL_CARE = "personal_care"
    TRAVEL = "travel"
    EDUCATION = "education"
    GIFTS_CHARITY = "gifts_charity"
    INSURANCE = "insurance"
    DEBT_PAYMENT = "debt_payment"
    FEES = "fees"
    OTHER = "other"


class ClassificationSource(StrEnum):
    IMPORT = "import"
    RULE = "rule"
    AI = "ai"
    USER = "user"
    UNKNOWN = "unknown"


class TransferPurpose(StrEnum):
    SAVING = "saving"
    INVESTMENT = "investment"
    OTHER = "other"
