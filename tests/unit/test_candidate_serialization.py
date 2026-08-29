from dataclasses import asdict

from pfa.ingestion.candidates import (
    CandidateIssue,
    CandidateTransaction,
    candidates_from_json,
    candidates_to_json,
)


def test_candidate_json_round_trip_is_lossless_including_issues_and_raw_fields() -> None:
    candidates = [
        CandidateTransaction(
            candidate_id="c1",
            transaction_date="2026-08-01",
            posted_date="2026-08-02",
            raw_description="TESCO STORES 1234",
            normalized_description="tesco stores",
            amount_minor=1250,
            direction="debit",
            currency="GBP",
            account_hint="Main account",
            external_id="ext-1",
            kind="expense",
            category="groceries",
            transfer_purpose=None,
            source_format="csv",
            source_line=4,
            source_page=None,
            extraction_method="csv",
            raw_fields={"amount": "-12.50", "description": "TESCO STORES 1234"},
            issues=[
                CandidateIssue(code="AMBIGUOUS_SIGN", message="both columns populated"),
                CandidateIssue(
                    code="DUPLICATE_ROW", message="looks like a repeat", severity="warning"
                ),
            ],
            duplicate_of=7,
            fingerprint="abc123",
            included=False,
        ),
        CandidateTransaction(candidate_id="c2"),
    ]

    payload = candidates_to_json(candidates)
    restored = candidates_from_json(payload)

    assert restored == candidates
    assert [asdict(c) for c in restored] == [asdict(c) for c in candidates]


def test_candidate_json_round_trip_handles_empty_list() -> None:
    assert candidates_from_json(candidates_to_json([])) == []
