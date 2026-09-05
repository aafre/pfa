"""Manual transaction re-categorisation, shared by the CLI and the API.

Setting a category by hand is a user classification (confidence 1.0) and also teaches
a narrow exact-description merchant rule so future imports of the same line match
without asking again.
"""

from __future__ import annotations

from pfa.db.models import MerchantRuleModel, TransactionModel
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.errors import ValidationError
from pfa.domain.transactions import ClassificationSource, SpendingCategory


def correct_transaction(
    uow: UnitOfWork, transaction_id: int, category: SpendingCategory
) -> TransactionModel:
    row = uow.session.get(TransactionModel, transaction_id)
    if row is None:
        raise ValidationError(f"transaction {transaction_id} not found")

    row.category = category.value
    row.classification_source = ClassificationSource.USER.value
    row.classification_confidence = 1.0
    row.classification_reason = "explicit user correction"

    pattern = row.normalized_description
    if pattern and uow.rules.find_pattern(pattern) is None:
        uow.rules.add(
            MerchantRuleModel(
                pattern=pattern,
                kind=row.kind,
                category=category.value,
                transfer_purpose=row.transfer_purpose,
                created_from_user_correction=True,
            )
        )
    return row
