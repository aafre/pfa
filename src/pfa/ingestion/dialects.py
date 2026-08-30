from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(frozen=True, slots=True)
class Dialect:
    name: str = "generic"
    date_formats: tuple[str, ...] = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %b %y",
        "%b %d %Y",
        "%b %d, %Y",
        "%d/%m/%y",
    )
    date_order: str = "day_first"  # "day_first" | "month_first"
    credit_markers: tuple[str, ...] = ("CR", "CREDIT", "CR.")
    default_sign: str | None = None  # None = ask user, "debit_positive", "as_written"
    two_column: bool = False


GENERIC = Dialect()

HSBC = replace(
    GENERIC,
    name="hsbc",
    date_formats=GENERIC.date_formats + ("%d %b %y", "%d %b %Y"),
    credit_markers=("CR", "CREDIT"),
)

AMEX_CARD = replace(
    GENERIC,
    name="amex",
    date_formats=("%b%d", "%b %d", "%d %b %y", "%d %b %Y") + GENERIC.date_formats,
    default_sign="debit_positive",
)

BARCLAYCARD = replace(
    GENERIC,
    name="barclaycard",
    date_formats=GENERIC.date_formats + ("%d %b %y", "%d %b %Y"),
    two_column=True,
)

DIALECTS: dict[str, Dialect] = {
    "generic": GENERIC,
    "hsbc": HSBC,
    "amex": AMEX_CARD,
    "barclaycard": BARCLAYCARD,
}


def dialect_for_name(name: str | None) -> Dialect:
    if not name:
        return GENERIC
    clean = name.strip().lower()
    for key, dialect in DIALECTS.items():
        if key in clean:
            return dialect
    return GENERIC
