"""batch amount sign"""

import sqlalchemy as sa
from alembic import op

revision = "0003_batch_amount_sign"
down_revision = "0002_import_batches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("import_batches", sa.Column("amount_sign", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("import_batches", "amount_sign")
