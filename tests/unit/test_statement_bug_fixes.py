from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pfa.domain.money import Money
from pfa.ingestion.candidates import StatementSource
from pfa.ingestion.dialects import (
    AMEX_UK_CSV,
    AMEX_UK_PDF,
    HSBC_UK_CARD,
    HSBC_UK_CURRENT,
    detect_adapter,
)
from pfa.ingestion.extractors.hdfc import HdfcDelimitedExtractor
from pfa.ingestion.extractors.pdf import (
    _resolve_amount,
    clean_amount_text,
)
from pfa.ingestion.normalizer import merchant_from_description
from pfa.services.answers import _amount


def test_bug1_hsbc_card_default_sign_and_amount_resolution():
    """HSBC card purchases default to debit, payments (CR) to credit."""
    # Purchase row without explicit CR
    resolved_debit = _resolve_amount(
        {"amount": "92.35", "cr": ""}, HSBC_UK_CARD, "GBP"
    )
    assert resolved_debit.direction == "debit"
    assert resolved_debit.minor == 9235

    # Payment row with CR marker
    resolved_credit = _resolve_amount(
        {"amount": "408.94", "cr": "CR"}, HSBC_UK_CARD, "GBP"
    )
    assert resolved_credit.direction == "credit"
    assert resolved_credit.minor == 40894


def test_bug2_dialect_detection_against_real_statements():
    """Check dialect detection against real files: HSBC card, HSBC current, and AMEX card."""
    statements_dir = Path(r"C:\Users\Amit\Downloads\Statements")
    if not statements_dir.exists():
        pytest.skip("Statements directory not present")

    hsbc_card = statements_dir / "HSBC" / "2025-05-17_Statement.pdf"
    if hsbc_card.exists():
        det = detect_adapter(hsbc_card)
        assert det.dialect.adapter_id == HSBC_UK_CARD.adapter_id

    hsbc_bank = statements_dir / "HSBC" / "Bank" / "2025-05-16_Statement.pdf"
    if hsbc_bank.exists():
        det = detect_adapter(hsbc_bank)
        assert det.dialect.adapter_id == HSBC_UK_CURRENT.adapter_id
        assert det.dialect.adapter_id != AMEX_UK_PDF.adapter_id

    amex_pdf = statements_dir / "AMEX" / "2025-05-19.pdf"
    if amex_pdf.exists():
        det = detect_adapter(amex_pdf)
        assert det.dialect.adapter_id == AMEX_UK_PDF.adapter_id


def test_bug5_foreign_currency_amount_cleaning():
    """Foreign currency prefix like 'CA 15.11' or 'USD 42.00' is cleaned to decimal amount."""
    cleaned, is_neg = clean_amount_text("CA 15.11")
    assert cleaned == "15.11"
    assert not is_neg

    cleaned_usd, _ = clean_amount_text("USD 42.00")
    assert cleaned_usd == "42.00"

    cleaned_eur, _ = clean_amount_text("EUR 1,234.56")
    assert cleaned_eur == "1234.56"

    cleaned_gbp, _ = clean_amount_text("£92.35")
    assert cleaned_gbp == "92.35"


def test_bug7_hdfc_fixed_width_text_extraction(tmp_path: Path):
    """HDFC fixed-width formatted text statements parse transactions accurately."""
    sample_text = (
        "Date        Narration                             Chq/Ref Number   Value Dt   Withdrawal Amt.   Deposit Amt.    Closing Balance\n"
        "----------  ------------------------------------  ---------------  ---------  ----------------  --------------  ----------------\n"
        "28/08/25    UPI-APPLE SERVICES-APPLE@OKAXIS-1234  000012345678     28/08/25   199.00                            15,000.00\n"
        "29/08/25    NEFT CR-KOTAK-SALARY CORP-N123456     000087654321     29/08/25                     75,000.00       90,000.00\n"
    )
    test_file = tmp_path / "sample.txt"
    test_file.write_text(sample_text, encoding="utf-8")

    source = StatementSource(
        path=test_file,
        original_filename="sample.txt",
        media_type="text/plain",
        size_bytes=len(sample_text),
    )
    extractor = HdfcDelimitedExtractor()
    result = extractor.extract(source)

    assert len(result.candidates) == 2
    c1, c2 = result.candidates
    assert c1.transaction_date == "28/08/25"
    assert c1.amount_minor == 19900
    assert c1.direction == "debit"
    assert c1.normalized_description == "APPLE SERVICES"

    assert c2.transaction_date == "29/08/25"
    assert c2.amount_minor == 7500000
    assert c2.direction == "credit"
    assert c2.normalized_description == "SALARY CORP"


def test_bug13_deterministic_answer_currency():
    """_amount and deterministic_answer format with target currency, not hardcoded GBP."""
    assert _amount(10000, "INR") == "INR 100.00"
    assert _amount(25000, "USD") == "USD 250.00"
    assert _amount(5000, "GBP") == "GBP 50.00"


def test_bug16_upi_and_neft_merchant_normalization():
    """Extract clean merchant from Indian UPI, NEFT, and ACH narrations."""
    assert merchant_from_description("UPI-APPLE MEDIA-APPLE@OKAXIS-423523523") == "APPLE MEDIA"
    assert merchant_from_description("UPI-CRED CLUB-PAYTO@AXIS-987654") == "CRED CLUB"
    assert merchant_from_description("POS 41234567 RELIANCE RETAIL MUMBAI") == "RELIANCE RETAIL MUMBAI"
    assert merchant_from_description("NEFT CR-HDFC0000001-ACME CORP SALARY-N1234") == "ACME CORP SALARY"
