from dataclasses import dataclass
from datetime import date

from .transactions import SpendingCategory


@dataclass(frozen=True, slots=True)
class Budget:
    amount_minor: int
    currency: str
    effective_from: date
    effective_to: date | None = None
    category: SpendingCategory | None = None
    discretionary: bool = False
