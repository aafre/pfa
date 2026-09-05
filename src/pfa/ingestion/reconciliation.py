from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pfa.domain.accounts import AccountType, account_nature
from pfa.domain.errors import ImportRowError
from pfa.domain.money import minor_units

from .candidates import CandidateTransaction, parse_date
from .extractors.pdf import clean_amount_text


def _balance_minor(value: str, currency: str) -> int | None:
    cleaned, negative = clean_amount_text(value)
    try:
        amount = minor_units(Decimal(cleaned), currency)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def _coverage_pass(candidates: list[CandidateTransaction]) -> bool:
    return all(
        candidate.duplicate_of is not None or (candidate.included and candidate.state != "error")
        for candidate in candidates
    )


def _hdfc_reconciliation(candidates: list[CandidateTransaction]) -> dict[str, Any]:
    """Check HDFC's ordered asset-account closing-balance chain.

    The first row deterministically supplies a *suggested* end-of-day baseline. Every
    later row is checked against the previous source closing balance; excluded rows are
    still part of the arithmetic check, because removing one cannot repair bad evidence.
    """
    coverage_pass = _coverage_pass(candidates)
    rows: list[tuple[CandidateTransaction, int]] = []
    for candidate in candidates:
        closing = _balance_minor(candidate.raw_fields.get("closing_balance", ""), "INR")
        if closing is None or candidate.signed_amount_minor is None:
            continue
        rows.append((candidate, closing))

    if len(rows) != len(candidates) or not rows:
        return {
            "arithmetic_integrity": "not_available",
            "coverage_integrity": "pass" if coverage_pass else "incomplete",
            "status": "not available" if coverage_pass else "incomplete",
            "reconciled": False,
            "checked_transition_count": 0,
            "mismatch_count": 0,
            "source_ordering": "preserved",
            "coverage_complete": coverage_pass,
            "evidence": "HDFC closing-balance evidence is incomplete",
        }

    first, first_closing = rows[0]
    try:
        first_date = parse_date(first.transaction_date or "", "day_first")
    except (ImportRowError, ValueError):
        first_date = None
    first_signed = first.signed_amount_minor
    assert first_signed is not None
    opening = first_closing - first_signed
    suggestion = {
        "balance_minor": opening,
        "as_of": (first_date - timedelta(days=1)).isoformat() if first_date else None,
        "provenance": "derived_from_first_row",
    }

    mismatch_source_rows: list[int] = []
    for (_previous, previous_closing), (current, current_closing) in zip(
        rows, rows[1:], strict=False
    ):
        signed = current.signed_amount_minor
        assert signed is not None
        if previous_closing + signed != current_closing:
            if current.source_line is not None:
                mismatch_source_rows.append(current.source_line)

    mismatch_count = len(mismatch_source_rows)
    arithmetic_pass = mismatch_count == 0
    coverage = "pass" if coverage_pass else "incomplete"
    status = "reconciled" if arithmetic_pass and coverage_pass else "mismatch"
    if not coverage_pass:
        status = "incomplete"
    return {
        "arithmetic_integrity": "pass" if arithmetic_pass else "mismatch",
        "coverage_integrity": coverage,
        "status": status,
        "reconciled": arithmetic_pass and coverage_pass,
        "checked_transition_count": max(len(rows) - 1, 0),
        "mismatch_count": mismatch_count,
        "mismatch_source_rows": mismatch_source_rows,
        "source_ordering": "preserved",
        "coverage_complete": coverage_pass,
        "opening_balance_suggestion": suggestion,
        "closing_balance_minor": rows[-1][1],
        "currency": "INR",
        "evidence": (
            f"{max(len(rows) - 1, 0) - mismatch_count}/{max(len(rows) - 1, 0)} "
            "ordered balance transitions reconciled"
        ),
    }


def reconcile_candidates(
    candidates: list[CandidateTransaction],
    account_type: AccountType | str,
) -> dict[str, Any]:
    """Reconcile balance-chain evidence without changing the ledger."""
    if any("closing_balance" in candidate.raw_fields for candidate in candidates):
        return _hdfc_reconciliation(candidates)

    coverage_pass = _coverage_pass(candidates)
    has_any_balance = any(
        _balance_minor(candidate.raw_fields.get("balance", ""), candidate.currency) is not None
        for candidate in candidates
        if candidate.included and candidate.duplicate_of is None
    )

    if not has_any_balance:
        return {
            "arithmetic_integrity": "not_available",
            "coverage_integrity": "pass" if coverage_pass else "incomplete",
            "status": "not available" if coverage_pass else "incomplete",
            "reconciled": False,
            "evidence": "no opening/closing balance column was detected",
        }

    current_movement = 0
    previous: int | None = None
    expected: int = 0
    last_balance: int = 0
    arithmetic_pass = True
    balance_linked_count = 0
    first_currency = "GBP"

    for candidate in candidates:
        if not candidate.included or candidate.duplicate_of is not None:
            continue
        if candidate.signed_amount_minor is None:
            continue
        first_currency = candidate.currency
        movement = candidate.signed_amount_minor
        if account_nature(account_type) == "liability":
            movement = -movement
        current_movement += movement
        balance = _balance_minor(candidate.raw_fields.get("balance", ""), candidate.currency)
        if balance is not None:
            balance_linked_count += 1
            last_balance = balance
            if previous is None:
                expected = balance - current_movement
                previous = balance
                current_movement = 0
            else:
                if previous + current_movement != balance:
                    arithmetic_pass = False
                previous = balance
                current_movement = 0

    if balance_linked_count == 0:
        return {
            "arithmetic_integrity": "not_available",
            "coverage_integrity": "pass" if coverage_pass else "incomplete",
            "status": "not available" if coverage_pass else "incomplete",
            "reconciled": False,
            "evidence": "no opening/closing balance column was detected",
        }

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
        "closing_balance_minor": last_balance,
        "currency": first_currency,
        "evidence": f"{balance_linked_count} balance-linked transaction rows",
    }
