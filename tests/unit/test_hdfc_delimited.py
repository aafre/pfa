import io
from pathlib import Path

from pfa.ingestion import candidates as codes
from pfa.ingestion.candidates import StatementSource
from pfa.ingestion.dialects import HDFC_IN_DELIMITED, detect_adapter
from pfa.ingestion.extractors.hdfc import HdfcDelimitedExtractor
from pfa.ingestion.reconciliation import reconcile_candidates

HEADER = [
    "Date",
    "Narration",
    "Value Dat",
    "Debit Amount",
    "Credit Amount",
    "Chq/Ref Number",
    "Closing Balance",
]


def extract(path: Path):
    return HdfcDelimitedExtractor().extract(StatementSource(path, path.name, "text/plain"))


def csv_text(rows: list[list[str]], *, leading_blank: bool = False) -> str:
    output = io.StringIO()
    if leading_blank:
        output.write("\n\n")
    import csv

    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    return output.getvalue()


def test_hdfc_header_is_exact_content_detection_and_filename_independent(tmp_path) -> None:
    path = tmp_path / "renamed-anything.txt"
    path.write_text(
        csv_text(
            [["01/08/2025", "SHOP, ONLINE", "01/08/2025", "1,000.00", "0.00", "", "9,000.00"]],
            leading_blank=True,
        ),
        encoding="utf-8-sig",
    )

    detection = detect_adapter(path)
    result = extract(path)
    row = result.candidates[0]

    assert detection.dialect is HDFC_IN_DELIMITED
    assert detection.dialect.adapter_id == "hdfc_in_delimited_v1"
    assert row.signed_minor == -100_000
    assert row.posted_date == "01/08/2025"
    assert row.raw_fields["source_reference"] == ""
    assert row.external_id is None
    assert row.direction_explicit is True
    assert result.issues == []


def test_hdfc_rejects_reordered_and_fuzzy_headers(tmp_path) -> None:
    for name, header in (
        ("reordered.txt", HEADER[:1] + HEADER[2:] + HEADER[1:2]),
        ("fuzzy.txt", [*HEADER[:2], "Value Date", *HEADER[3:]]),
    ):
        path = tmp_path / name
        path.write_text(
            ",".join(header) + "\n01/08/2025,SHOP,01/08/2025,1,0,R,9\n",
            encoding="utf-8",
        )
        result = extract(path)
        assert result.candidates == []
        assert result.issues[0].code == "HDFC_HEADER_NOT_FOUND"


def test_hdfc_requires_exactly_one_positive_amount_side(tmp_path) -> None:
    path = tmp_path / "sides.txt"
    path.write_text(
        csv_text(
            [
                ["01/08/2025", "ZERO", "01/08/2025", "0.00", "0", "1", "9"],
                ["02/08/2025", "BOTH", "02/08/2025", "1", "2", "2", "10"],
                ["03/08/2025", "DEBIT", "03/08/2025", "1,234.56", "0.00", "3", "-1,225.56"],
                ["04/08/2025", "CREDIT", "04/08/2025", "", "2,000.00", "4", "774.44"],
            ]
        ),
        encoding="utf-8",
    )

    rows = extract(path).candidates

    assert [row.state for row in rows[:2]] == [codes.ERROR, codes.ERROR]
    assert [row.issues[0].code for row in rows[:2]] == [
        "HDFC_AMOUNT_SIDES_INVALID",
        "HDFC_AMOUNT_SIDES_INVALID",
    ]
    assert rows[2].signed_minor == -123_456
    assert rows[3].signed_minor == 200_000
    assert all(row.direction_explicit for row in rows[2:])


def test_hdfc_enforces_seven_columns_and_row_order(tmp_path) -> None:
    path = tmp_path / "width.txt"
    path.write_text(
        csv_text(
            [
                ["02/08/2025", "SECOND", "02/08/2025", "1", "0", "2", "8"],
                ["03/08/2025", "BROKEN", "03/08/2025", "1", "0"],
                ["01/08/2025", "FIRST", "01/08/2025", "0", "2", "1", "10"],
            ]
        ),
        encoding="utf-8",
    )

    result = extract(path)

    assert [row.raw_description for row in result.candidates] == ["SECOND", "", "FIRST"]
    assert result.candidates[1].issues[0].code == "HDFC_ROW_WIDTH_INVALID"
    assert [row.source_line for row in result.candidates] == [2, 3, 4]


def test_hdfc_balance_chain_reports_baseline_and_source_mismatch(tmp_path) -> None:
    path = tmp_path / "balances.txt"
    path.write_text(
        csv_text(
            [
                ["01/08/2025", "FIRST", "01/08/2025", "100", "0", "1", "900"],
                ["02/08/2025", "SECOND", "02/08/2025", "0", "50", "2", "950"],
                ["03/08/2025", "THIRD", "03/08/2025", "25", "0", "3", "900"],
            ]
        ),
        encoding="utf-8",
    )

    rows = extract(path).candidates
    reconciliation = reconcile_candidates(rows, "current")

    assert reconciliation["status"] == "mismatch"
    assert reconciliation["checked_transition_count"] == 2
    assert reconciliation["mismatch_count"] == 1
    assert reconciliation["mismatch_source_rows"] == [4]
    assert reconciliation["opening_balance_suggestion"] == {
        "balance_minor": 100_000,
        "as_of": "2025-07-31",
        "provenance": "derived_from_first_row",
    }
    assert "25" not in "statement balances do not reconcile"


def test_hdfc_coverage_cannot_be_bypassed_by_excluding_a_row(tmp_path) -> None:
    path = tmp_path / "coverage.txt"
    path.write_text(
        csv_text(
            [
                ["01/08/2025", "FIRST", "01/08/2025", "100", "0", "1", "900"],
                ["02/08/2025", "SECOND", "02/08/2025", "0", "50", "2", "950"],
            ]
        ),
        encoding="utf-8",
    )
    rows = extract(path).candidates
    rows[1].included = False

    reconciliation = reconcile_candidates(rows, "current")

    assert reconciliation["arithmetic_integrity"] == "pass"
    assert reconciliation["coverage_integrity"] == "incomplete"
    assert reconciliation["status"] == "incomplete"


def test_hdfc_row_limit_is_blocking(tmp_path) -> None:
    path = tmp_path / "large.txt"
    path.write_text(
        csv_text(
            [
                [f"{day:02d}/08/2025", "SHOP", f"{day:02d}/08/2025", "1", "0", str(day), "1"]
                for day in range(1, 4)
            ]
        ),
        encoding="utf-8",
    )

    result = HdfcDelimitedExtractor(max_candidate_rows=2).extract(
        StatementSource(path, path.name, "text/plain")
    )

    assert len(result.candidates) == 2
    assert result.issues[0].code == codes.TOO_MANY_ROWS
    assert result.issues[0].severity == codes.ERROR
