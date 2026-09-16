import sys
from pathlib import Path

import pdfplumber
import pytest
from pdfminer.pdfdocument import PDFPasswordIncorrect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.pdf_builder import Placement, build_pdf, statement_page  # noqa: E402

from pfa.ingestion import candidates as codes  # noqa: E402
from pfa.ingestion.candidates import ExtractionResult, StatementSource  # noqa: E402
from pfa.ingestion.dialects import BARCLAYCARD  # noqa: E402
from pfa.ingestion.extractors.pdf import PdfStatementExtractor  # noqa: E402


def _extract(tmp_path: Path, pages: list[list[Placement]], **kwargs: object) -> ExtractionResult:
    path = tmp_path / "statement.pdf"
    path.write_bytes(build_pdf(pages))
    source = StatementSource(
        path=path, original_filename="statement.pdf", media_type="application/pdf"
    )
    return PdfStatementExtractor(**kwargs).extract(source)  # type: ignore[arg-type]


def test_digital_fixture_yields_expected_rows_and_no_balance_candidates(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 320.0, 400.0, 470.0]
    rows = [
        ["Date", "Description", "Debit", "Credit", "Balance"],
        ["2026-08-01", "Tesco Metro", "12.50", "", "987.50"],
        ["2026-08-02", "Salary", "", "3000.00", "3987.50"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    assert result.issues == []
    assert [c.transaction_date for c in result.candidates] == ["2026-08-01", "2026-08-02"]
    assert [c.raw_description for c in result.candidates] == ["Tesco Metro", "Salary"]
    assert [c.amount_minor for c in result.candidates] == [1250, 300000]
    assert [c.direction for c in result.candidates] == ["debit", "credit"]
    assert [c.source_page for c in result.candidates] == [1, 1]
    assert all(c.raw_fields.get("balance") for c in result.candidates)
    # Balance is provenance only - never its own transaction row, and never sets amount/direction.
    assert all(c.amount_minor not in (98750, 398750) for c in result.candidates)


def test_header_aliases_map_onto_the_canonical_fields(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 300.0, 400.0, 470.0]
    rows = [
        ["Date", "Narrative", "Withdrawn", "Received", "Transaction ID"],
        ["2026-08-05", "Alias Test", "9.99", "", "ref-123"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    assert result.issues == []
    candidate = result.candidates[0]
    assert candidate.transaction_date == "2026-08-05"
    assert candidate.raw_description == "Alias Test"
    assert (candidate.amount_minor, candidate.direction) == (999, "debit")
    assert candidate.external_id == "ref-123"


def test_wrapped_description_joins_into_the_row_above_within_line_height(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["2026-08-01", "Tesco Metro", "-12.50"],
        ["", "card ref 99213", ""],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns, line_height=14.0)])

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.raw_description == "Tesco Metro card ref 99213"
    assert candidate.issues == []


def test_broken_data_rows_do_not_join_into_previous_description(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["01/08/2026", "Tesco Metro", "-12.50"],
        ["21 Jul 25", "E.ON NEXT COVENTRY", "-295.79"],
        ["02/08/2026", "Salary", "2000.00"],
        ["03/08/2026", "Card payment", "450.62CR"],
    ]

    result = _extract(tmp_path, [statement_page(rows, columns)])

    assert len(result.candidates) == 4
    assert [c.raw_description for c in result.candidates] == [
        "Tesco Metro",
        "E.ON NEXT COVENTRY",
        "Salary",
        "Card payment",
    ]


def test_continuation_line_far_from_any_row_is_dropped_as_noise(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["2026-08-01", "Tesco Metro", "-12.50"],
    ]
    page = statement_page(rows, columns, line_height=14.0)
    orphan_y = page[-1][1] - 3 * 14.0  # far below the last real row - not a plausible wrap
    page.append((160.0, orphan_y, "Orphan note", 10.0))

    result = _extract(tmp_path, [page])

    assert len(result.candidates) == 1
    assert result.candidates[0].raw_description == "Tesco Metro"


def test_amex_banner_header_and_statement_chatter_never_become_candidates(
    tmp_path: Path,
) -> None:
    columns = [72.0, 160.0, 300.0, 420.0, 500.0]
    rows = [
        ["Prepared for", "Membership Number", "", "", "Date"],
        ["A CUSTOMER", "xxxx-xxxxxx-12345", "", "", "19/08/25"],
        ["", "", "", "", ""],
        ["Date", "Date", "Transaction Details", "Foreign Spend", "Amount"],
        ["Jul31", "Jul31", "PAYMENT RECEIVED - THANK YOU", "", "1,940.64"],
        ["", "", "", "", "CR"],
        ["Jul21", "Jul21", "ZETTLE *REDACTED", "", "6.30"],
        ["", "", "Important information about your account", "", ""],
        ["", "", "Visit americanexpress.com/offers", "", ""],
    ]

    result = _extract(tmp_path, [statement_page(rows, columns)])

    assert [(c.transaction_date, c.amount_minor) for c in result.candidates] == [
        ("Jul31", 194064),
        ("Jul21", 630),
    ]
    assert result.issues == []
    # The repeated "Date" column (AMEX prints it twice) never leaks into the description.
    assert [c.raw_description for c in result.candidates] == [
        "PAYMENT RECEIVED - THANK YOU",
        "ZETTLE *REDACTED",
    ]
    # The own-line "CR" marker attaches to the row above it, not a candidate of its own,
    # and marks that row's direction explicit so a later sign convention cannot flip it.
    payment, purchase = result.candidates
    assert payment.direction == "credit"
    assert payment.direction_explicit is True
    assert purchase.direction_explicit is False


def test_header_with_zero_plausible_data_rows_reports_pdf_not_extractable(
    tmp_path: Path,
) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["", "Important information", ""],
        ["", "No transaction activity this period", ""],
    ]

    result = _extract(tmp_path, [statement_page(rows, columns)])

    assert [
        (c.transaction_date, c.raw_description, c.amount_minor) for c in result.candidates
    ] == []
    assert [issue.code for issue in result.issues] == [codes.PDF_NOT_EXTRACTABLE]


def test_parenthesised_amount_is_negative(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["2026-08-01", "Refund reversal", "(12.50)"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    candidate = result.candidates[0]
    assert (candidate.amount_minor, candidate.direction) == (1250, "debit")


def test_trailing_minus_amount_is_negative(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["2026-08-01", "Refund", "5.00-"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    candidate = result.candidates[0]
    assert (candidate.amount_minor, candidate.direction) == (500, "debit")


def test_debit_and_credit_both_populated_is_ambiguous(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 320.0, 400.0]
    rows = [
        ["Date", "Description", "Debit", "Credit"],
        ["2026-08-01", "Both columns", "12.50", "3.00"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    candidate = result.candidates[0]
    assert [issue.code for issue in candidate.issues] == [codes.AMBIGUOUS_SIGN]
    assert candidate.state == codes.ERROR
    assert candidate.amount_minor is None


def test_parenthesised_value_in_credit_column_is_ambiguous(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 320.0, 400.0]
    rows = [
        ["Date", "Description", "Debit", "Credit"],
        ["2026-08-01", "Paren in credit", "", "(3.00)"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    candidate = result.candidates[0]
    assert [issue.code for issue in candidate.issues] == [codes.AMBIGUOUS_SIGN]
    assert candidate.amount_minor is None


def test_encrypted_pdf_reports_pdf_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(
        build_pdf([statement_page([["Date", "Description", "Amount"]], [72, 160, 400])])
    )

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise PDFPasswordIncorrect("password required")

    monkeypatch.setattr(pdfplumber, "open", _raise)
    source = StatementSource(
        path=path, original_filename="encrypted.pdf", media_type="application/pdf"
    )

    result = PdfStatementExtractor().extract(source)

    assert [issue.code for issue in result.issues] == [codes.PDF_ENCRYPTED]
    assert result.candidates == []


def test_over_page_limit_reports_pdf_too_many_pages_without_extracting(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    page_1 = statement_page(
        [["Date", "Description", "Amount"], ["2026-08-01", "A", "-1.00"]], columns
    )
    page_2 = statement_page(
        [["Date", "Description", "Amount"], ["2026-08-02", "B", "-2.00"]], columns
    )

    result = _extract(tmp_path, [page_1, page_2], max_pdf_pages=1)

    assert [issue.code for issue in result.issues] == [codes.PDF_TOO_MANY_PAGES]
    assert result.page_count == 2
    assert result.candidates == []


def test_too_many_candidate_rows_is_truncated_and_flagged(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [["Date", "Description", "Amount"]] + [
        [f"2026-08-{(i % 28) + 1:02d}", f"Row {i}", "-1.00"] for i in range(5)
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)], max_candidate_rows=3)

    assert [issue.code for issue in result.issues] == [codes.TOO_MANY_ROWS]
    assert len(result.candidates) == 3


def test_no_recognizable_rows_reports_pdf_not_extractable_with_actionable_copy(
    tmp_path: Path,
) -> None:
    page = [(72.0, 720.0, "This statement has no recognizable table structure", 10.0)]

    result = _extract(tmp_path, [page])

    assert [issue.code for issue in result.issues] == [codes.PDF_NOT_EXTRACTABLE]
    message = result.issues[0].message.lower()
    assert "csv" in message
    assert result.candidates == []


def test_money_out_and_money_in_headers_are_recognised(tmp_path: Path) -> None:
    # Monzo, Starling and Lloyds all label their columns this way. The CSV extractor has
    # always known the wording; this proves the PDF word-based header map reads it too.
    columns = [72.0, 160.0, 320.0, 420.0]
    rows = [
        ["Date", "Description", "Money Out", "Money In"],
        ["2026-08-01", "Tesco Metro", "12.50", ""],
        ["2026-08-02", "Salary", "", "3000.00"],
    ]
    result = _extract(tmp_path, [statement_page(rows, columns)])

    assert result.issues == []
    assert [c.amount_minor for c in result.candidates] == [1250, 300000]
    assert [c.direction for c in result.candidates] == ["debit", "credit"]


def test_barclaycard_two_column_layout_clustering(tmp_path: Path) -> None:
    # Left column: transactions at x ~ 50..280
    # Right column: marketing copy at x ~ 350..550
    left_columns = [50.0, 130.0, 260.0]
    left_rows = [
        ["Date", "Description", "Amount"],
        ["27 Jul 25", "COFFEE HOUSE LONDON", "-3.50"],
        ["28 Jul 25", "NEWSAGENT LEEDS", "-2.10"],
    ]

    page = statement_page(left_rows, left_columns)
    # Add right column marketing words at same y positions
    page.append((360.0, 720.0, "Understanding your interest", 10.0))
    page.append((360.0, 706.0, "Your interest rates this month", 10.0))
    page.append((360.0, 692.0, "Visit barclaycard.co.uk", 10.0))

    result = _extract(tmp_path, [page], dialect=BARCLAYCARD)

    assert result.issues == []
    assert len(result.candidates) == 2
    assert [c.transaction_date for c in result.candidates] == ["27 Jul 25", "28 Jul 25"]
    assert [c.raw_description for c in result.candidates] == [
        "COFFEE HOUSE LONDON",
        "NEWSAGENT LEEDS",
    ]
    assert [c.amount_minor for c in result.candidates] == [350, 210]
