from pydantic import BaseModel, Field

from pfa.domain.transactions import SpendingCategory, TransactionKind, TransferPurpose


class TransactionClassification(BaseModel):
    kind: TransactionKind
    category: SpendingCategory | None = None
    transfer_purpose: TransferPurpose | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(max_length=300)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class ImportRequest(BaseModel):
    path: str
    dry_run: bool = False
