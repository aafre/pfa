from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from pfa.db.models import FxRateModel
    from pfa.db.unit_of_work import UnitOfWork

logger = logging.getLogger("pfa")

FRANKFURTER_API_BASE = "https://api.frankfurter.dev/v1"


def fetch_and_store_fx_rates(
    uow: UnitOfWork,
    base_currency: str = "GBP",
    symbols: list[str] | None = None,
    on_date: date | str | None = None,
    client: httpx.Client | None = None,
) -> list[FxRateModel]:
    base = base_currency.upper()
    symbols_list = symbols or ["EUR", "INR", "USD", "JPY"]
    filtered_symbols = [s.upper() for s in symbols_list if s.upper() != base]
    if not filtered_symbols:
        return []

    symbols_str = ",".join(filtered_symbols)
    date_segment = on_date.isoformat() if isinstance(on_date, date) else (on_date or "latest")
    url = f"{FRANKFURTER_API_BASE}/{date_segment}?base={base}&symbols={symbols_str}"

    close_client = False
    if client is None:
        client = httpx.Client(timeout=15.0)
        close_client = True

    try:
        response = client.get(url)
        response.raise_for_status()
        # Parse the response's own JSON numbers straight to Decimal - going through
        # response.json() would round-trip every rate through a binary float first.
        payload = json.loads(response.text, parse_float=Decimal)
    finally:
        if close_client:
            client.close()

    effective_date = date.fromisoformat(payload["date"])
    rates_data: dict[str, Decimal] = payload.get("rates", {})
    stored: list[FxRateModel] = []
    for quote, rate_value in rates_data.items():
        rate_model = uow.fx_rates.set_rate(
            base_currency=base,
            quote_currency=quote,
            rate=str(rate_value),
            effective_at=effective_date,
            source="frankfurter",
        )
        stored.append(rate_model)

    return stored
