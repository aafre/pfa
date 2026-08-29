from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pfa.domain.transactions import TransactionKind

from .models import (
    AccountModel,
    BudgetModel,
    GoalModel,
    ImportBatchModel,
    MerchantRuleModel,
    TransactionModel,
)


class TransactionRepository:
    def __init__(self, session: Session):
        self.session = session

    def all(self) -> list[TransactionModel]:
        return list(
            self.session.scalars(
                select(TransactionModel).order_by(TransactionModel.transaction_date)
            )
        )

    def between(self, start: date, end: date) -> list[TransactionModel]:
        statement = (
            select(TransactionModel)
            .where(
                TransactionModel.transaction_date >= start,
                TransactionModel.transaction_date <= end,
            )
            .order_by(TransactionModel.transaction_date)
        )
        return list(self.session.scalars(statement))

    def find_fingerprint(self, fingerprint: str) -> TransactionModel | None:
        return self.session.scalar(
            select(TransactionModel).where(TransactionModel.fingerprint == fingerprint)
        )

    def add(self, transaction: TransactionModel) -> TransactionModel:
        self.session.add(transaction)
        self.session.flush()
        return transaction

    def uncategorized(self) -> list[TransactionModel]:
        return list(
            self.session.scalars(
                select(TransactionModel).where(
                    TransactionModel.category.is_(None),
                    TransactionModel.kind.in_(
                        [TransactionKind.EXPENSE.value, TransactionKind.UNKNOWN.value]
                    ),
                )
            )
        )


class AccountRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(
        self, name: str, currency: str = "GBP", account_type: str = "current"
    ) -> AccountModel:
        account = self.session.scalar(select(AccountModel).where(AccountModel.name == name))
        if account is None:
            account = AccountModel(name=name, currency=currency, account_type=account_type)
            self.session.add(account)
            self.session.flush()
        return account

    def all(self) -> list[AccountModel]:
        return list(self.session.scalars(select(AccountModel).order_by(AccountModel.name)))


class RuleRepository:
    def __init__(self, session: Session):
        self.session = session

    def match(self, description: str) -> MerchantRuleModel | None:
        rules = self.session.scalars(select(MerchantRuleModel)).all()
        upper = description.upper()
        return next((rule for rule in rules if rule.pattern.upper() == upper), None)

    def find_pattern(self, pattern: str) -> MerchantRuleModel | None:
        return self.session.scalar(
            select(MerchantRuleModel).where(MerchantRuleModel.pattern == pattern)
        )

    def add(self, rule: MerchantRuleModel) -> MerchantRuleModel:
        self.session.add(rule)
        self.session.flush()
        return rule


class BudgetRepository:
    def __init__(self, session: Session):
        self.session = session

    def active_on(self, at: date) -> list[BudgetModel]:
        statement = select(BudgetModel).where(
            BudgetModel.effective_from <= at,
            (BudgetModel.effective_to.is_(None) | (BudgetModel.effective_to >= at)),
        )
        return list(self.session.scalars(statement))

    def add(self, budget: BudgetModel) -> BudgetModel:
        self.session.add(budget)
        self.session.flush()
        return budget


class ImportBatchRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, batch_id: str) -> ImportBatchModel | None:
        return self.session.get(ImportBatchModel, batch_id)

    def add(self, batch: ImportBatchModel) -> ImportBatchModel:
        self.session.add(batch)
        self.session.flush()
        return batch

    def list_expired(self, at: datetime) -> list[ImportBatchModel]:
        """Batches still holding staged candidate data whose TTL has passed."""
        statement = select(ImportBatchModel).where(
            ImportBatchModel.expires_at <= at,
            ImportBatchModel.status.in_(["preview_ready", "blocked"]),
        )
        return list(self.session.scalars(statement))


class GoalRepository:
    def __init__(self, session: Session):
        self.session = session

    def active(self) -> list[GoalModel]:
        return list(self.session.scalars(select(GoalModel).where(GoalModel.active.is_(True))))

    def add(self, goal: GoalModel) -> GoalModel:
        self.session.add(goal)
        self.session.flush()
        return goal
