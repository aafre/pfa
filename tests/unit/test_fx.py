from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pfa.db.models import Base
from pfa.db.repositories import FxRateRepository
from pfa.db.unit_of_work import UnitOfWork
from pfa.domain.errors import ValidationError
from pfa.domain.fx import to_base
from pfa.domain.money import Money
from pfa.services.fx import fetch_and_store_fx_rates


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
    engine.dispose()


@pytest.fixture
def fx_repo(session):
    return FxRateRepository(session)


def test_set_and_retrieve_fx_rate(fx_repo):
    model = fx_repo.set_rate("INR", "GBP", "0.0095", date(2026, 8, 1), source="manual")
    assert model.rate == "0.0095"
    assert model.base_currency == "INR"
    assert model.quote_currency == "GBP"

    # Upsert with new rate on same date
    updated = fx_repo.set_rate("INR", "GBP", "0.0096", date(2026, 8, 1), source="manual")
    assert updated.rate == "0.0096"
    assert len(fx_repo.all()) == 1


def test_rate_on_date_semantics(fx_repo):
    fx_repo.set_rate("INR", "GBP", "0.0090", date(2026, 8, 1))
    fx_repo.set_rate("INR", "GBP", "0.0095", date(2026, 8, 15))

    # Before earliest rate -> None
    assert fx_repo.rate_on(date(2026, 7, 31), "INR", "GBP") is None

    # On exact date
    rate_aug1, model1 = fx_repo.rate_on(date(2026, 8, 1), "INR", "GBP")
    assert rate_aug1 == Decimal("0.0090")
    assert model1.effective_at == date(2026, 8, 1)

    # Between dates -> nearest rate at or before
    rate_aug10, model10 = fx_repo.rate_on(date(2026, 8, 10), "INR", "GBP")
    assert rate_aug10 == Decimal("0.0090")

    # On later date
    rate_aug15, model15 = fx_repo.rate_on(date(2026, 8, 15), "INR", "GBP")
    assert rate_aug15 == Decimal("0.0095")

    # After latest date -> stays at latest rate at or before
    rate_aug20, model20 = fx_repo.rate_on(date(2026, 8, 20), "INR", "GBP")
    assert rate_aug20 == Decimal("0.0095")


def test_inverse_rate_resolution(fx_repo):
    # Store GBP to EUR rate: 1 GBP = 1.20 EUR
    fx_repo.set_rate("GBP", "EUR", "1.20", date(2026, 8, 1))

    # Rate from EUR to GBP should be 1 / 1.20 = 0.8333...
    rate_eur_gbp, model = fx_repo.rate_on(date(2026, 8, 10), "EUR", "GBP")
    assert rate_eur_gbp == Decimal(1) / Decimal("1.20")
    assert model.base_currency == "GBP"


def test_to_base_conversion(fx_repo):
    fx_repo.set_rate("INR", "GBP", "0.00863", date(2026, 8, 29))

    # Convert 100,000 INR (10,000,000 minor) to GBP
    inr_money = Money(10_000_000, "INR")  # 100,000.00 INR
    converted, rate_used = to_base(inr_money, date(2026, 8, 29), fx_repo, "GBP")

    # 100,000 * 0.00863 = 863.00 GBP -> 86300 minor
    assert converted.currency == "GBP"
    assert converted.minor == 86300
    assert rate_used.rate == Decimal("0.00863")
    assert rate_used.base_currency == "INR"
    assert rate_used.quote_currency == "GBP"


def test_to_base_identity_for_same_currency(fx_repo):
    gbp_money = Money(5000, "GBP")
    converted, rate_used = to_base(gbp_money, date(2026, 8, 29), fx_repo, "GBP")
    assert converted.currency == "GBP"
    assert converted.minor == 5000
    assert rate_used.rate == Decimal("1.0")


def test_to_base_missing_rate_raises(fx_repo):
    usd_money = Money(1000, "USD")
    with pytest.raises(ValidationError, match="No FX rate available"):
        to_base(usd_money, date(2026, 8, 29), fx_repo, "GBP")


def test_fetch_and_store_fx_rates_keeps_full_decimal_precision(session):
    """The response's JSON numbers must never round-trip through a binary float - a rate
    with more decimal digits than float can hold exactly must be stored byte-for-byte."""
    body = (
        b'{"amount":1.0,"base":"GBP","date":"2026-08-28",'
        b'"rates":{"INR":129.123456789012345,"USD":1.3583}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["base"] == "GBP"
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    uow = UnitOfWork(session)

    stored = fetch_and_store_fx_rates(
        uow, base_currency="GBP", on_date=date(2026, 8, 28), client=client
    )

    by_quote = {model.quote_currency: model for model in stored}
    assert by_quote["INR"].rate == "129.123456789012345"
    assert by_quote["INR"].source == "frankfurter"
    assert by_quote["USD"].rate == "1.3583"
    assert by_quote["INR"].effective_at == date(2026, 8, 28)

    rate_decimal, _ = uow.fx_rates.rate_on(date(2026, 8, 28), "GBP", "INR")
    assert rate_decimal == Decimal("129.123456789012345")


def test_fetch_and_store_fx_rates_excludes_base_from_symbols(session):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "GBP" not in request.url.params["symbols"].split(",")
        return httpx.Response(
            200,
            json={"amount": 1.0, "base": "GBP", "date": "2026-08-28", "rates": {"INR": 129.5}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    uow = UnitOfWork(session)

    stored = fetch_and_store_fx_rates(
        uow, base_currency="GBP", symbols=["GBP", "INR"], on_date=date(2026, 8, 28), client=client
    )
    assert [m.quote_currency for m in stored] == ["INR"]
