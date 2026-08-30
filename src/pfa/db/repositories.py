from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pfa.domain.transactions import TransactionKind

from .models import (
    AccountModel,
    BudgetModel,
    FxRateModel,
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

    def get_by_name(self, name: str) -> AccountModel | None:
        """Read-only lookup - never creates a row, so a preview never has the side effect
        of persisting an account for a batch that might still be discarded."""
        return self.session.scalar(select(AccountModel).where(AccountModel.name == name))

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


class FxRateRepository:
    def __init__(self, session: Session):
        self.session = session

    def all(self) -> list[FxRateModel]:
        return list(
            self.session.scalars(select(FxRateModel).order_by(FxRateModel.effective_at.desc()))
        )

    def add(self, fx_rate: FxRateModel) -> FxRateModel:
        self.session.add(fx_rate)
        self.session.flush()
        return fx_rate

    def set_rate(
        self,
        base_currency: str,
        quote_currency: str,
        rate: Decimal | str | float,
        effective_at: date,
        source: str = "manual",
    ) -> FxRateModel:
        base = base_currency.upper()
        quote = quote_currency.upper()
        rate_str = str(rate)
        now = datetime.now(UTC).replace(tzinfo=None)
        statement = select(FxRateModel).where(
            FxRateModel.base_currency == base,
            FxRateModel.quote_currency == quote,
            FxRateModel.effective_at == effective_at,
        )
        existing = self.session.scalar(statement)
        if existing is not None:
            existing.rate = rate_str
            existing.source = source
            existing.retrieved_at = now
            self.session.flush()
            return existing
        model = FxRateModel(
            base_currency=base,
            quote_currency=quote,
            rate=rate_str,
            effective_at=effective_at,
            source=source,
            retrieved_at=now,
        )
        self.session.add(model)
        self.session.flush()
        return model

    def rate_on(
        self, effective_date: date, base: str, quote: str
    ) -> tuple[Decimal, FxRateModel | None] | None:
        """Finds nearest rate at or before effective_date (never after).
        Returns (rate_decimal, matched_model_or_none).
        """
        base_upper = base.upper()
        quote_upper = quote.upper()
        if base_upper == quote_upper:
            return Decimal("1.0"), None

        # Direct rate lookup: 1 base = rate quote
        direct_stmt = (
            select(FxRateModel)
            .where(
                FxRateModel.base_currency == base_upper,
                FxRateModel.quote_currency == quote_upper,
                FxRateModel.effective_at <= effective_date,
            )
            .order_by(FxRateModel.effective_at.desc())
            .limit(1)
        )
        direct = self.session.scalar(direct_stmt)
        if direct is not None:
            return Decimal(direct.rate), direct

        # Inverse rate lookup: 1 quote = rate base => 1 base = 1 / rate quote
        inverse_stmt = (
            select(FxRateModel)
            .where(
                FxRateModel.base_currency == quote_upper,
                FxRateModel.quote_currency == base_upper,
                FxRateModel.effective_at <= effective_date,
            )
            .order_by(FxRateModel.effective_at.desc())
            .limit(1)
        )
        inverse = self.session.scalar(inverse_stmt)
        if inverse is not None:
            inv_rate = Decimal(inverse.rate)
            if inv_rate != Decimal(0):
                return Decimal(1) / inv_rate, inverse

        return None

    def latest(self, base: str, quote: str) -> tuple[Decimal, FxRateModel | None] | None:
        base_upper = base.upper()
        quote_upper = quote.upper()
        if base_upper == quote_upper:
            return Decimal("1.0"), None

        direct_stmt = (
            select(FxRateModel)
            .where(
                FxRateModel.base_currency == base_upper,
                FxRateModel.quote_currency == quote_upper,
            )
            .order_by(FxRateModel.effective_at.desc())
            .limit(1)
        )
        direct = self.session.scalar(direct_stmt)
        if direct is not None:
            return Decimal(direct.rate), direct

        inverse_stmt = (
            select(FxRateModel)
            .where(
                FxRateModel.base_currency == quote_upper,
                FxRateModel.quote_currency == base_upper,
            )
            .order_by(FxRateModel.effective_at.desc())
            .limit(1)
        )
        inverse = self.session.scalar(inverse_stmt)
        if inverse is not None:
            inv_rate = Decimal(inverse.rate)
            if inv_rate != Decimal(0):
                return Decimal(1) / inv_rate, inverse

        return None
