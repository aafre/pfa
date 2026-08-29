from pathlib import Path

from pfa.ingestion import candidates as codes
from pfa.ingestion.candidates import ExtractionResult, StatementSource
from pfa.ingestion.extractors.csv import CsvStatementExtractor


def extract(path: Path) -> ExtractionResult:
    source = StatementSource(path=path, original_filename=path.name, media_type="text/csv")
    return CsvStatementExtractor().extract(source)


def test_header_aliases_map_onto_the_canonical_candidate_fields(tmp_path) -> None:
    path = tmp_path / "aliases.csv"
    path.write_text(
        "Transaction Date,Narrative,Value,Account Name,Transaction ID,Posted Date\n"
        "2026-08-01,Tesco Metro,-12.50,Everyday,tx-1,2026-08-02\n",
        encoding="utf-8",
    )

    row = extract(path).candidates[0]

    assert row.transaction_date == "2026-08-01"
    assert row.raw_description == "Tesco Metro"
    assert row.raw_fields["amount"] == "-12.50"
    assert row.account_hint == "Everyday"
    assert row.external_id == "tx-1"
    assert row.posted_date == "2026-08-02"


def test_utf8_bom_is_stripped_from_the_first_header(tmp_path) -> None:
    path = tmp_path / "bom.csv"
    path.write_text("date,description,amount\n2026-08-01,Tesco,-5\n", encoding="utf-8-sig")

    result = extract(path)

    assert result.issues == []
    assert result.candidates[0].transaction_date == "2026-08-01"


def test_semicolon_and_tab_delimiters_are_detected(tmp_path) -> None:
    for name, delimiter in (("semi.csv", ";"), ("tab.csv", "\t")):
        path = tmp_path / name
        path.write_text(
            delimiter.join(("date", "description", "amount"))
            + "\n"
            + delimiter.join(("2026-08-01", "Tesco", "-5"))
            + "\n",
            encoding="utf-8",
        )

        row = extract(path).candidates[0]

        assert (row.transaction_date, row.raw_description) == ("2026-08-01", "Tesco")


def test_blank_lines_are_ignored_and_line_numbers_stay_physical(tmp_path) -> None:
    path = tmp_path / "blanks.csv"
    path.write_text(
        "date,description,amount\n2026-08-01,Tesco,-5\n\n2026-08-02,Uber,-7\n",
        encoding="utf-8",
    )

    result = extract(path)

    assert [row.source_line for row in result.candidates] == [2, 4]
    assert [row.candidate_id for row in result.candidates] == ["c1", "c2"]


def test_missing_header_row_is_a_batch_issue_not_a_crash(tmp_path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")

    result = extract(path)

    assert result.candidates == []
    assert [(issue.code, issue.message) for issue in result.issues] == [
        (codes.NO_HEADER_ROW, "CSV has no header row")
    ]


def test_debit_and_credit_columns_become_one_signed_amount(tmp_path) -> None:
    path = tmp_path / "two_column.csv"
    path.write_text(
        "date,description,paid out,paid in\n2026-08-01,Tesco,12.50,\n2026-08-02,Salary,,3000\n",
        encoding="utf-8",
    )

    rows = extract(path).candidates

    assert [row.raw_fields["amount"] for row in rows] == ["-12.50", "3000"]
    assert [row.state for row in rows] == [codes.VALID, codes.VALID]
    assert rows[0].raw_fields["debit"] == "12.50"


def test_both_debit_and_credit_populated_is_ambiguous_rather_than_guessed(tmp_path) -> None:
    path = tmp_path / "ambiguous.csv"
    path.write_text(
        "date,description,debit,credit\n2026-08-01,Tesco,12.50,3.00\n2026-08-02,Uber,,\n",
        encoding="utf-8",
    )

    rows = extract(path).candidates

    assert [issue.code for row in rows for issue in row.issues] == [codes.AMBIGUOUS_SIGN] * 2
    assert [row.state for row in rows] == [codes.ERROR, codes.ERROR]
    assert [row.amount_minor for row in rows] == [None, None]


def test_a_single_amount_column_wins_over_any_debit_credit_columns(tmp_path) -> None:
    path = tmp_path / "mixed.csv"
    path.write_text(
        "date,description,amount,debit,credit\n2026-08-01,Tesco,-12.50,99,99\n",
        encoding="utf-8",
    )

    row = extract(path).candidates[0]

    assert row.raw_fields["amount"] == "-12.50"
    assert row.issues == []


def test_detected_currency_and_account_are_reported_only_when_unambiguous(tmp_path) -> None:
    path = tmp_path / "accounts.csv"
    path.write_text(
        "date,description,amount,account\n2026-08-01,Tesco,-5,Everyday\n2026-08-02,Uber,-7,Savings\n",
        encoding="utf-8",
    )

    result = extract(path)

    assert result.detected_currency == "GBP"
    assert result.detected_account is None
    assert result.extractor == "csv/1"


def test_non_utf8_bytes_fail_with_an_actionable_batch_issue(tmp_path) -> None:
    path = tmp_path / "latin1.csv"
    path.write_bytes("date,description,amount\n2026-08-01,Caf\xe9,-5\n".encode("latin-1"))

    result = extract(path)

    assert result.candidates == []
    assert result.issues[0].code == codes.UNREADABLE_FILE
    assert "UTF-8" in result.issues[0].message
