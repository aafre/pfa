"""Page-selective local OCR fallback for scanned PDF pages.

Native extraction (``pdf.py``) always runs first and is free. This module only kicks in
for the pages that native extraction cannot read - it does not touch pages that already
have usable text, and it invokes the Tesseract binary at most once per page per batch.

No pytesseract: Tesseract is invoked directly as a subprocess, piping page PNG bytes to
stdin and reading positional TSV (left/top/width/height/conf/text) from stdout. That gives
an explicit timeout bound and turns "Tesseract isn't installed" into a plain
``FileNotFoundError`` this module can catch cleanly - no extra dependency, no C bindings.

OCR output is never authoritative financial data: every OCR-derived candidate carries a
review warning, and any date/amount/debit/credit field built from a low-confidence word is
a blocking error until the user confirms or excludes the row.
"""

from __future__ import annotations

import csv
import io
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from pdfplumber.page import Page

from pfa.config import Settings, get_settings
from pfa.ingestion.candidates import (
    OCR_UNAVAILABLE,
    CandidateIssue,
    ExtractionResult,
    StatementSource,
)
from pfa.ingestion.extractors.pdf import PdfStatementExtractor, Word


def native_words(page: Page) -> list[Word]:
    """The seam's own default (see pdf.py) - used for non-eligible pages and when OCR is off."""
    return page.extract_words()


# A page qualifies for OCR only when native text is this short or shorter - comfortably
# below a real statement line ("Date Description Amount" alone is well over this), so a
# normal digital page never reaches the OCR path at all.
MIN_TEXT_CHARS = 40
_IMAGE_COVERAGE_THRESHOLD = 0.6  # image area / page area, for the no-native-words case


@dataclass(slots=True)
class OcrWord:
    """One Tesseract TSV word: position in points, its confidence, and its text."""

    left: float
    top: float
    width: float
    height: float
    conf: float
    text: str


TesseractRunner = Callable[[bytes], list[OcrWord]]


class OcrUnavailableError(Exception):
    """Raised when Tesseract cannot be run at all: missing binary, timeout, or nonzero exit."""


def _parse_tsv(tsv_text: str) -> list[OcrWord]:
    """Positional TSV, never plain text - row/column reconstruction needs the coordinates.

    Tesseract's TSV mixes word rows with block/paragraph/line summary rows sharing the same
    columns; summary rows carry conf == -1 and/or empty text, so both are filtered out here.
    """
    words: list[OcrWord] = []
    for row in csv.DictReader(tsv_text.splitlines(), delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row["conf"])
            left, top = float(row["left"]), float(row["top"])
            width, height = float(row["width"]), float(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if conf < 0:
            continue
        words.append(OcrWord(left=left, top=top, width=width, height=height, conf=conf, text=text))
    return words


def run_tesseract(png_bytes: bytes, *, language: str, timeout: float) -> list[OcrWord]:
    """Invokes the real Tesseract binary. Requires it to be installed and on PATH."""
    try:
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", language, "--psm", "6", "tsv"],
            input=png_bytes,
            timeout=timeout,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OcrUnavailableError(
            "Tesseract OCR is not installed or not on PATH. Install Tesseract to enable "
            "scanned-PDF support, or upload a CSV or a digital (text) PDF instead."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise OcrUnavailableError(
            f"Tesseract OCR did not finish within {timeout:.0f}s; try a smaller or "
            "lower-resolution scan."
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise OcrUnavailableError(f"Tesseract OCR failed (exit code {proc.returncode}){suffix}")
    return _parse_tsv(proc.stdout.decode("utf-8", errors="replace"))


def page_needs_ocr(page: Page) -> bool:
    """True only for pages native extraction cannot usefully read - never "every page".

    Two independent triggers: too little native text to trust (the common case - a scanned
    page has none), or a page with no extractable words at all that is mostly a single
    embedded image (a photographed/scanned page with an unusual text layer quirk).
    """
    text = page.extract_text() or ""
    if len(text) < MIN_TEXT_CHARS:
        return True
    if not page.extract_words():
        page_area = page.width * page.height
        if page_area > 0:
            image_area = sum(
                max(image["x1"] - image["x0"], 0) * max(image["bottom"] - image["top"], 0)
                for image in page.images
            )
            if image_area / page_area >= _IMAGE_COVERAGE_THRESHOLD:
                return True
    return False


def _to_word(word: OcrWord) -> Word:
    return {
        "text": word.text,
        "x0": word.left,
        "x1": word.left + word.width,
        "top": word.top,
        "conf": word.conf,
    }


class OcrWordProvider:
    """The ``word_provider`` seam ``PdfStatementExtractor`` left for OCR (see pdf.py).

    Native words for every page that already has usable text - the runner is never called
    for those. For an eligible page, the OCR result is cached by page number so the runner
    executes at most once per page for the whole batch, however many times a page is
    revisited. A Tesseract failure records one OCR_UNAVAILABLE issue and then stops
    attempting OCR for the rest of the document; pages with native text keep extracting
    normally regardless.
    """

    def __init__(self, *, runner: TesseractRunner, dpi: int) -> None:
        self._runner = runner
        self._dpi = dpi
        self._cache: dict[int, list[OcrWord]] = {}
        self._unavailable = False
        self.issues: list[CandidateIssue] = []

    def __call__(self, page: Page) -> list[Word]:
        if not page_needs_ocr(page):
            return native_words(page)
        if self._unavailable:
            return []
        if page.page_number not in self._cache:
            try:
                self._cache[page.page_number] = self._runner(self._render_png(page))
            except OcrUnavailableError as exc:
                self._unavailable = True
                self.issues.append(CandidateIssue(OCR_UNAVAILABLE, str(exc)))
                return []
        return [_to_word(word) for word in self._cache[page.page_number]]

    def _render_png(self, page: Page) -> bytes:
        image = page.to_image(resolution=self._dpi).original
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


class OcrFallbackPdfExtractor:
    """``PdfStatementExtractor`` plus page-selective OCR - the extractor callers should use.

    Wires an ``OcrWordProvider`` into T5's word-provider seam and folds any OCR_UNAVAILABLE
    issue it collected into the batch-level issues the native extractor already returns, so
    callers see one coherent ``ExtractionResult`` regardless of which pages needed OCR.
    """

    name = "pdf/1+ocr"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        runner: TesseractRunner | None = None,
        max_pdf_pages: int | None = None,
        max_candidate_rows: int | None = None,
    ) -> None:
        settings = settings or get_settings()
        word_provider: Callable[[Page], list[Word]]
        if settings.ocr_enabled:
            provider = OcrWordProvider(
                runner=runner
                or partial(
                    run_tesseract,
                    language=settings.ocr_language,
                    timeout=settings.ocr_timeout_seconds,
                ),
                dpi=settings.ocr_dpi,
            )
            self._provider: OcrWordProvider | None = provider
            word_provider = provider
        else:
            self._provider = None
            word_provider = native_words
        self._extractor = PdfStatementExtractor(
            max_pdf_pages=max_pdf_pages,
            max_candidate_rows=max_candidate_rows,
            word_provider=word_provider,
            ocr_min_confidence=settings.ocr_min_confidence,
        )

    def extract(self, source: StatementSource) -> ExtractionResult:
        result = self._extractor.extract(source)
        if self._provider is not None and self._provider.issues:
            result.issues = [*self._provider.issues, *result.issues]
        return result
