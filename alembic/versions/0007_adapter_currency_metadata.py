"""persist generic statement-adapter currency metadata"""

import sqlalchemy as sa
from alembic import op

revision = "0007_adapter_currency_metadata"
down_revision = "0006_transfer_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batches", sa.Column("suggested_currency", sa.String(length=3)))
    op.add_column("import_batches", sa.Column("currency_evidence", sa.String(length=40)))
    op.add_column("import_batches", sa.Column("compatible_account_types_json", sa.Text()))


def downgrade() -> None:
    op.drop_column("import_batches", "compatible_account_types_json")
    op.drop_column("import_batches", "currency_evidence")
    op.drop_column("import_batches", "suggested_currency")
