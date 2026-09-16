"""auditable transfer events and persisted match decisions"""

import sqlalchemy as sa
from alembic import op

revision = "0006_transfer_events"
down_revision = "0005_account_import_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("match_method", sa.String(length=30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "transfer_legs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["transfer_events.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_id", name="uq_transfer_legs_transaction"),
    )
    op.create_table(
        "transfer_match_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stable_match_key", sa.String(length=64), nullable=False),
        sa.Column("left_transaction_id", sa.Integer(), nullable=False),
        sa.Column("right_transaction_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason_codes_json", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["transfer_events.id"]),
        sa.ForeignKeyConstraint(["left_transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["right_transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stable_match_key", name="uq_transfer_match_key"),
    )
    op.create_index(
        "ix_transfer_match_decisions_left_transaction_id",
        "transfer_match_decisions",
        ["left_transaction_id"],
    )
    op.create_index(
        "ix_transfer_match_decisions_right_transaction_id",
        "transfer_match_decisions",
        ["right_transaction_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transfer_match_decisions_right_transaction_id",
        table_name="transfer_match_decisions",
    )
    op.drop_index(
        "ix_transfer_match_decisions_left_transaction_id",
        table_name="transfer_match_decisions",
    )
    op.drop_table("transfer_match_decisions")
    op.drop_table("transfer_legs")
    op.drop_table("transfer_events")
