"""stable account binding and adapter metadata

Revision ID: 0005_account_import_binding
Revises: 0004_fx_rates
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_account_import_binding"
down_revision = "0004_fx_rates"
branch_labels = None
depends_on = None


def _rebuild_accounts(unique: bool) -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    op.create_table(
        "_accounts_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("account_type", sa.String(length=30), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("institution", sa.String(length=120), nullable=True),
        sa.Column("last4", sa.String(length=4), nullable=True),
        sa.Column("opening_balance_minor", sa.Integer(), nullable=False),
        sa.Column("opening_balance_as_of", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        *([sa.UniqueConstraint("name")] if unique else []),
    )
    op.execute(
        """INSERT INTO _accounts_new
        (id, name, account_type, currency, institution, last4,
         opening_balance_minor, opening_balance_as_of, active)
        SELECT id, name, account_type, currency, NULL, NULL,
               opening_balance_minor, NULL, active
        FROM accounts"""
    )
    op.drop_table("accounts")
    op.rename_table("_accounts_new", "accounts")
    bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    _rebuild_accounts(unique=False)
    op.add_column(
        "import_batches", sa.Column("destination_account_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_import_batches_destination_account_id",
        "import_batches",
        ["destination_account_id"],
    )
    with op.batch_alter_table("import_batches", recreate="always") as batch_op:
        batch_op.create_foreign_key(
            "fk_import_batches_destination_account_id",
            "accounts",
            ["destination_account_id"],
            ["id"],
        )
    for column in (
        sa.Column("new_account_json", sa.Text(), nullable=True),
        sa.Column("adapter_id", sa.String(length=80), nullable=True),
        sa.Column("detection_confidence", sa.Float(), nullable=True),
        sa.Column("detection_reason_codes_json", sa.Text(), nullable=True),
        sa.Column("detected_institution", sa.String(length=120), nullable=True),
        sa.Column("detected_account_hint", sa.String(length=40), nullable=True),
        sa.Column("reconciliation_json", sa.Text(), nullable=True),
    ):
        op.add_column("import_batches", column)


def downgrade() -> None:
    for name in (
        "reconciliation_json",
        "detected_account_hint",
        "detected_institution",
        "detection_reason_codes_json",
        "detection_confidence",
        "adapter_id",
        "new_account_json",
    ):
        op.drop_column("import_batches", name)
    with op.batch_alter_table("import_batches", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_import_batches_destination_account_id", type_="foreignkey")
    op.drop_index("ix_import_batches_destination_account_id", table_name="import_batches")
    op.drop_column("import_batches", "destination_account_id")
    _rebuild_accounts(unique=True)
