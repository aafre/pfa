from sqlalchemy.orm import Session

from .repositories import (
    AccountRepository,
    BudgetRepository,
    GoalRepository,
    RuleRepository,
    TransactionRepository,
)


class UnitOfWork:
    """Small session boundary used by services and CLI/API composition."""

    def __init__(self, session: Session):
        self.session = session
        self.transactions = TransactionRepository(session)
        self.accounts = AccountRepository(session)
        self.rules = RuleRepository(session)
        self.budgets = BudgetRepository(session)
        self.goals = GoalRepository(session)
