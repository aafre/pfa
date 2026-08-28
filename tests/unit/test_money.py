from decimal import Decimal

import pytest

from pfa.domain.errors import ValidationError
from pfa.domain.money import Money


def test_money_rounds_to_integer_minor_units() -> None:
    assert Money.from_major("12.345").minor == 1235
    assert Money(1234).to_major() == Decimal("12.34")


def test_money_rejects_mixed_currency_arithmetic() -> None:
    with pytest.raises(ValidationError):
        Money(100, "GBP") + Money(100, "USD")
