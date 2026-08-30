from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pfa.domain.errors import ValidationError
from pfa.domain.money import Money

if TYPE_CHECKING:
    from pfa.db.repositories import FxRateRepository


@dataclass(frozen=True, slots=True)
class FxRate:
    base_currency: str
    quote_currency: str
    rate: Decimal
    effective_at: date
    source: str
    retrieved_at: datetime


def to_base(
    money: Money,
    on_date: date,
    fx_rates: FxRateRepository,
    base_currency: str = "GBP",
) -> tuple[Money, FxRate]:
    """Converts a Money instance to base currency as of a specific date.
    Returns (converted_money, applied_fx_rate).
    """
    target_curr = base_currency.upper()
    if money.currency == target_curr:
        identity_rate = FxRate(
            base_currency=target_curr,
            quote_currency=target_curr,
            rate=Decimal("1.0"),
            effective_at=on_date,
            source="identity",
            retrieved_at=datetime.now(UTC).replace(tzinfo=None),
        )
        return money, identity_rate

    rate_info = fx_rates.rate_on(on_date, base=money.currency, quote=target_curr)
    if rate_info is None:
        raise ValidationError(
            f"No FX rate available to convert {money.currency} to {target_curr} "
            f"on or before {on_date}"
        )

    rate_dec, model = rate_info
    target_major = money.to_major() * rate_dec
    converted_money = Money.from_major(target_major, target_curr)
    applied_rate = FxRate(
        base_currency=money.currency,
        quote_currency=target_curr,
        rate=rate_dec,
        effective_at=model.effective_at if model else on_date,
        source=model.source if model else "direct",
        retrieved_at=model.retrieved_at if model else datetime.now(UTC).replace(tzinfo=None),
    )
    return converted_money, applied_rate
