from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import ValidationError

SUPPORTED_CURRENCIES: dict[str, int] = {
    "GBP": 2,
    "INR": 2,
    "USD": 2,
    "EUR": 2,
    "JPY": 0,
}


def minor_units(value: str | Decimal | int | float, currency: str = "GBP") -> int:
    """Converts a major-unit amount to an integer minor-unit count for `currency`.

    An unrecognised code falls back to a 2-place exponent rather than raising, so a row's
    amount can always be parsed before its currency is validated as supported - the two are
    separate checks and the caller decides which error the row surfaces.
    """
    exponent = SUPPORTED_CURRENCIES.get(currency.upper(), 2)
    quantize_unit = Decimal("1") if exponent == 0 else Decimal("0." + "0" * (exponent - 1) + "1")
    try:
        amount = Decimal(str(value)).quantize(quantize_unit, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"Invalid monetary value: {value!r}") from exc
    return int(amount * (10**exponent))


@dataclass(frozen=True, slots=True)
class Money:
    minor: int
    currency: str = "GBP"

    def __post_init__(self) -> None:
        if not isinstance(self.minor, int):
            raise ValidationError("Money must use integer minor units")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValidationError("Currency must be a three-letter code")
        curr = self.currency.upper()
        if curr not in SUPPORTED_CURRENCIES:
            supported = ", ".join(sorted(SUPPORTED_CURRENCIES))
            raise ValidationError(f"Unsupported currency {curr!r}; supported: {supported}")
        object.__setattr__(self, "currency", curr)

    @classmethod
    def from_major(cls, value: str | Decimal | int | float, currency: str = "GBP") -> Money:
        curr = currency.upper() if isinstance(currency, str) else "GBP"
        return cls(minor_units(value, curr), curr)

    def to_major(self) -> Decimal:
        exponent = SUPPORTED_CURRENCIES.get(self.currency, 2)
        return Decimal(self.minor) / Decimal(10**exponent)

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValidationError("Cannot combine different currencies")
