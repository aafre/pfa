from __future__ import annotations

from datetime import date, timedelta
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
        first_date = date.fromisoformat(first.transaction_date or "")
    except ValueError:
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
            f"{max(len(rows) - 1, 0)}/{max(len(rows) - 1, 0)} ordered balance transitions checked"
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
    rows: list[tuple[CandidateTransaction, int]] = []
    for candidate in candidates:
        if not candidate.included or candidate.duplicate_of is not None:
            continue
        balance = _balance_minor(candidate.raw_fields.get("balance", ""), candidate.currency)
        if balance is None:
            continue
        if candidate.signed_amount_minor is None:
            continue
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
