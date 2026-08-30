from decimal import Decimal

import pytest

from pfa.domain.errors import ValidationError
from pfa.domain.money import Money


def test_money_rounds_to_integer_minor_units() -> None:
    assert Money.from_major("12.345").minor == 1235
    assert Money(1234).to_major() == Decimal("12.34")


def test_money_supports_different_currency_minor_units() -> None:
    # JPY has exponent 0 (no decimal places)
    jpy = Money.from_major("1500", "JPY")
    assert jpy.minor == 1500
    assert jpy.to_major() == Decimal("1500")

    # INR has exponent 2
    inr = Money.from_major("450.50", "INR")
    assert inr.minor == 45050
    assert inr.to_major() == Decimal("450.50")


def test_money_rejects_unsupported_currency() -> None:
    with pytest.raises(ValidationError, match="Unsupported currency"):
        Money(100, "XYZ")


def test_money_rejects_mixed_currency_arithmetic() -> None:
    with pytest.raises(ValidationError):
        Money(100, "GBP") + Money(100, "USD")
