import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.pdf_builder import build_pdf, statement_page, write_scanned_pdf  # noqa: E402

from pfa.config import Settings  # noqa: E402
from pfa.ingestion import candidates as codes  # noqa: E402
from pfa.ingestion.candidates import StatementSource  # noqa: E402
from pfa.ingestion.extractors.ocr import (  # noqa: E402
    OcrFallbackPdfExtractor,
    OcrWord,
    OcrWordProvider,
)
from pfa.ingestion.extractors.pdf import PdfStatementExtractor  # noqa: E402


class _CountingRunner:
    """A stubbed TesseractRunner: records call count instead of shelling out."""

    def __init__(self, words: list[OcrWord] | None = None) -> None:
        self.calls = 0
        self._words = words or []

    def __call__(self, png_bytes: bytes) -> list[OcrWord]:
        self.calls += 1
        return self._words


def _source(path: Path) -> StatementSource:
    return StatementSource(path=path, original_filename=path.name, media_type="application/pdf")


def test_native_text_page_never_invokes_the_ocr_runner(tmp_path: Path) -> None:
    columns = [72.0, 160.0, 400.0]
    rows = [
        ["Date", "Description", "Amount"],
        ["2026-08-01", "Tesco Metro", "-12.50"],
        ["2026-08-02", "Salary", "3000.00"],
    ]
    path = tmp_path / "digital.pdf"
    path.write_bytes(build_pdf([statement_page(rows, columns)]))

    runner = _CountingRunner()
    provider = OcrWordProvider(runner=runner, dpi=150)
    result = PdfStatementExtractor(word_provider=provider, ocr_min_confidence=80.0).extract(
        _source(path)
    )

    assert runner.calls == 0
    assert [c.extraction_method for c in result.candidates] == ["pdf", "pdf"]
    assert [c.amount_minor for c in result.candidates] == [1250, 300000]


def test_ocr_runs_at_most_once_per_eligible_page(tmp_path: Path) -> None:
    path = tmp_path / "scanned.pdf"
    write_scanned_pdf(path, ["Statement image, no text layer"])

    runner = _CountingRunner([OcrWord(left=72, top=50, width=30, height=10, conf=99, text="Date")])
    provider = OcrWordProvider(runner=runner, dpi=150)

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        first = provider(page)
        second = provider(page)

    assert runner.calls == 1
    assert first == second


def test_missing_tesseract_reports_ocr_unavailable_and_digital_pages_still_preview(
    tmp_path: Path,
) -> None:
    digital_columns = [72.0, 160.0, 400.0]
    digital_page = statement_page(
        [["Date", "Description", "Amount"], ["2026-08-01", "Tesco Metro", "-12.50"]],
        digital_columns,
    )
    # A near-blank page: under MIN_TEXT_CHARS, so it is OCR-eligible even without an image.
    sparse_page = [(72.0, 720.0, "N/A", 10.0)]
    path = tmp_path / "mixed.pdf"
    path.write_bytes(build_pdf([digital_page, sparse_page]))

    # No runner stub here - this is the real Tesseract subprocess path, exercised because
    # Tesseract genuinely is not installed on this machine.
    extractor = OcrFallbackPdfExtractor(settings=Settings())
    result = extractor.extract(_source(path))

    assert [issue.code for issue in result.issues] == [codes.OCR_UNAVAILABLE]
    message = result.issues[0].message.lower()
    assert "tesseract" in message
    assert "install" in message or "csv" in message

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.extraction_method == "pdf"
    assert candidate.transaction_date == "2026-08-01"
    assert candidate.amount_minor == 1250


def test_low_confidence_amount_blocks_commit(tmp_path: Path) -> None:
    path = tmp_path / "scanned.pdf"
    write_scanned_pdf(path, ["Statement image, no text layer"])

    header = [
        OcrWord(left=72, top=50, width=30, height=10, conf=99, text="Date"),
        OcrWord(left=160, top=50, width=70, height=10, conf=99, text="Description"),
        OcrWord(left=400, top=50, width=60, height=10, conf=99, text="Amount"),
    ]
    data_row = [
        OcrWord(left=72, top=70, width=70, height=10, conf=95, text="2026-08-01"),
        OcrWord(left=160, top=70, width=50, height=10, conf=95, text="Tesco"),
        OcrWord(left=400, top=70, width=50, height=10, conf=45, text="12.50"),
    ]
    runner = _CountingRunner(header + data_row)
    provider = OcrWordProvider(runner=runner, dpi=150)

    result = PdfStatementExtractor(word_provider=provider, ocr_min_confidence=80.0).extract(
        _source(path)
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.extraction_method == "ocr"
    assert candidate.amount_minor == 1250  # the value is still parsed - just not trusted
    assert candidate.state == codes.ERROR
    issue_codes = [issue.code for issue in candidate.issues]
    assert codes.OCR_LOW_CONFIDENCE in issue_codes
    low_conf_issue = next(i for i in candidate.issues if i.code == codes.OCR_LOW_CONFIDENCE)
    assert low_conf_issue.severity == codes.ERROR
    assert "amount" in low_conf_issue.message.lower()


def test_high_confidence_ocr_row_only_carries_the_review_warning(tmp_path: Path) -> None:
    path = tmp_path / "scanned.pdf"
    write_scanned_pdf(path, ["Statement image, no text layer"])

    header = [
        OcrWord(left=72, top=50, width=30, height=10, conf=99, text="Date"),
        OcrWord(left=160, top=50, width=70, height=10, conf=99, text="Description"),
        OcrWord(left=400, top=50, width=60, height=10, conf=99, text="Amount"),
    ]
    data_row = [
        OcrWord(left=72, top=70, width=70, height=10, conf=95, text="2026-08-01"),
        OcrWord(left=160, top=70, width=50, height=10, conf=95, text="Tesco"),
        OcrWord(left=400, top=70, width=50, height=10, conf=97, text="12.50"),
    ]
    runner = _CountingRunner(header + data_row)
    provider = OcrWordProvider(runner=runner, dpi=150)

    result = PdfStatementExtractor(word_provider=provider, ocr_min_confidence=80.0).extract(
        _source(path)
    )

    candidate = result.candidates[0]
    assert candidate.extraction_method == "ocr"
    assert candidate.state == codes.WARNING
    assert [issue.code for issue in candidate.issues] == [codes.OCR_EXTRACTED]


def test_page_needs_ocr_is_not_every_page(tmp_path: Path) -> None:
    from pfa.ingestion.extractors.ocr import page_needs_ocr

    digital_path = tmp_path / "digital.pdf"
    digital_path.write_bytes(
        build_pdf(
            [
                statement_page(
                    [
                        ["Date", "Description", "Amount"],
                        ["2026-08-01", "Tesco Metro", "-12.50"],
                    ],
                    [72.0, 160.0, 400.0],
                )
            ]
        )
    )
    scanned_path = tmp_path / "scanned.pdf"
    write_scanned_pdf(scanned_path, ["Some scanned content that pdfplumber cannot read as text"])

    with pdfplumber.open(digital_path) as pdf:
        assert page_needs_ocr(pdf.pages[0]) is False
    with pdfplumber.open(scanned_path) as pdf:
        assert page_needs_ocr(pdf.pages[0]) is True


def test_parse_tsv_recovers_positions_and_drops_non_word_rows() -> None:
    from pfa.ingestion.extractors.ocr import _parse_tsv

    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\t"
        "conf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t850\t1100\t-1\t\n"
        "5\t1\t1\t1\t1\t1\t72\t50\t70\t10\t95.5\t2026-08-01\n"
        "5\t1\t1\t1\t1\t2\t160\t50\t0\t0\t0\t\n"
    )

    words = _parse_tsv(tsv)

    assert len(words) == 1
    word = words[0]
    assert (word.left, word.top, word.width, word.height) == (72.0, 50.0, 70.0, 10.0)
    assert word.conf == 95.5
    assert word.text == "2026-08-01"
