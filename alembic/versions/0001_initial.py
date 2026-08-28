"""initial schema"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("account_type", sa.String(length=30), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("opening_balance_minor", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("discretionary", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("goal_type", sa.String(length=40), nullable=False),
        sa.Column("target_minor", sa.Integer(), nullable=False),
        sa.Column("current_minor", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "merchant_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pattern", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=True),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("transfer_purpose", sa.String(length=30), nullable=True),
        sa.Column("created_from_user_correction", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern"),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=True),
        sa.Column("raw_description", sa.String(length=500), nullable=False),
        sa.Column("normalized_description", sa.String(length=500), nullable=False),
        sa.Column("merchant", sa.String(length=240), nullable=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("flow_direction", sa.String(length=6), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("transfer_purpose", sa.String(length=30), nullable=True),
        sa.Column("classification_source", sa.String(length=20), nullable=False),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("classification_reason", sa.String(length=500), nullable=True),
        sa.Column("import_source", sa.String(length=500), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_transactions_fingerprint"),
    )
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_category", "transactions", ["category"])
    op.create_index("ix_transactions_kind", "transactions", ["kind"])
    op.create_index("ix_transactions_merchant", "transactions", ["merchant"])
    op.create_index(
        "ix_transactions_normalized_description", "transactions", ["normalized_description"]
    )
    op.create_index("ix_transactions_transaction_date", "transactions", ["transaction_date"])


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("merchant_rules")
    op.drop_table("goals")
    op.drop_table("budgets")
    op.drop_table("accounts")
