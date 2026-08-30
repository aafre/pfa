from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pfa.domain.accounts import AccountType, account_nature
from pfa.domain.money import minor_units

from .candidates import CandidateTransaction
from .extractors.pdf import clean_amount_text


def _balance_minor(value: str, currency: str) -> int | None:
    cleaned, negative = clean_amount_text(value)
    try:
        amount = minor_units(Decimal(cleaned), currency)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def reconcile_candidates(
    candidates: list[CandidateTransaction],
    account_type: AccountType | str,
) -> dict[str, Any]:
    """Reconcile balance-chain evidence without changing the ledger.

    A statement balance is a closing balance for its row. The first row therefore gives a
    deterministic opening balance by subtracting that row's natural movement.
    """
    coverage_pass = all(
        candidate.included and (candidate.state != "error" or candidate.duplicate_of is not None)
        for candidate in candidates
    )
    rows: list[tuple[CandidateTransaction, int]] = []
    for candidate in candidates:
        if not candidate.included or candidate.duplicate_of is not None:
            continue
        balance = _balance_minor(candidate.raw_fields.get("balance", ""), candidate.currency)
        if balance is None:
            continue
        if candidate.signed_amount_minor is None:
            continue
        movement = candidate.signed_amount_minor
        if account_nature(account_type) == "liability":
            movement = -movement
        rows.append((candidate, balance))

    if not rows:
        return {
            "arithmetic_integrity": "not_available",
            "coverage_integrity": "pass" if coverage_pass else "incomplete",
            "status": "not available" if coverage_pass else "incomplete",
            "reconciled": False,
            "evidence": "no opening/closing balance column was detected",
        }

    arithmetic_pass = True
    first_signed = rows[0][0].signed_amount_minor
    assert first_signed is not None
    expected = rows[0][1] - (
        first_signed if account_nature(account_type) == "asset" else -first_signed
    )
    previous = expected
    for candidate, balance in rows:
        movement = candidate.signed_amount_minor or 0
        if account_nature(account_type) == "liability":
            movement = -movement
        if previous + movement != balance:
            arithmetic_pass = False
        previous = balance

    arithmetic = "pass" if arithmetic_pass else "mismatch"
    coverage = "pass" if coverage_pass else "incomplete"
    return {
        "arithmetic_integrity": arithmetic,
        "coverage_integrity": coverage,
        "status": "reconciled"
        if arithmetic_pass and coverage_pass
        else coverage
        if not coverage_pass
        else "mismatch",
        "reconciled": arithmetic_pass and coverage_pass,
        "opening_balance_minor": expected,
        "closing_balance_minor": rows[-1][1],
        "currency": rows[0][0].currency,
        "evidence": f"{len(rows)} balance-linked transaction rows",
    }
