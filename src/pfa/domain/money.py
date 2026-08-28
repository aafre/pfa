from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import ValidationError


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: str = "GBP"

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int):
            raise ValidationError("Money must use integer minor units")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValidationError("Currency must be a three-letter code")
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def from_major(cls, value: str | Decimal | int | float, currency: str = "GBP") -> Money:
        try:
            amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError(f"Invalid monetary value: {value!r}") from exc
        return cls(int(amount * 100), currency)

    def to_major(self) -> Decimal:
        return Decimal(self.minor) / 100

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValidationError("Cannot combine different currencies")
