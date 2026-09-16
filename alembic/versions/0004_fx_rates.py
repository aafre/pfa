"""fx rates table"""

import sqlalchemy as sa
from alembic import op

revision = "0004_fx_rates"
down_revision = "0003_batch_amount_sign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("rate", sa.String(length=32), nullable=False),
        sa.Column("effective_at", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "base_currency", "quote_currency", "effective_at", name="uq_fx_rates_base_quote_date"
        ),
    )
    op.create_index("ix_fx_rates_effective_at", "fx_rates", ["effective_at"])


def downgrade() -> None:
    op.drop_index("ix_fx_rates_effective_at", table_name="fx_rates")
    op.drop_table("fx_rates")
