from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountModel(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    account_type: Mapped[str] = mapped_column(String(30), default="current")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    opening_balance_minor: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    transactions: Mapped[list[TransactionModel]] = relationship(back_populates="account")


class TransactionModel(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_transactions_fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    posted_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    raw_description: Mapped[str] = mapped_column(String(500))
    normalized_description: Mapped[str] = mapped_column(String(500), index=True)
    merchant: Mapped[str | None] = mapped_column(String(240), nullable=True, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    flow_direction: Mapped[str] = mapped_column(String(6), default="debit")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    kind: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    transfer_purpose: Mapped[str | None] = mapped_column(String(30), nullable=True)
    classification_source: Mapped[str] = mapped_column(String(20), default="unknown")
    classification_confidence: Mapped[float | None] = mapped_column(nullable=True)
    classification_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    import_source: Mapped[str] = mapped_column(String(500))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    account: Mapped[AccountModel] = relationship(back_populates="transactions")


class BudgetModel(Base):
    __tablename__ = "budgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    discretionary: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class GoalModel(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    goal_type: Mapped[str] = mapped_column(String(40))
    target_minor: Mapped[int] = mapped_column(Integer)
    current_minor: Mapped[int] = mapped_column(Integer, default=0)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ImportBatchModel(Base):
    """A staged statement upload awaiting review. Candidate rows are a JSON blob, not a
    second table: they live 24 hours and are only ever read whole (see A3 in the
    implementation plan for the reasoning).
    """

    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4().hex
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    extractor: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), index=True)
    destination_account: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detected_account: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detected_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    statement_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    statement_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    committed_transaction_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MerchantRuleModel(Base):
    __tablename__ = "merchant_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(240), unique=True)
    kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transfer_purpose: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_from_user_correction: Mapped[bool] = mapped_column(Boolean, default=False)
