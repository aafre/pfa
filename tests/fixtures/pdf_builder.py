"""Hand-rolled raw-PDF writer for extractor tests.

No PDF library and no binary fixtures: a content stream of absolute-position ``Tj``
operators, wrapped in the minimal object/xref structure pdfplumber needs to open it.
A monospaced Courier font with an explicit /Widths array gives every glyph a real,
predictable advance, so word x-coordinates in the rendered PDF are exact -- unlike the
zero-width fallback pdfminer uses when no width table is present at all.
"""

from __future__ import annotations

from pathlib import Path

_COURIER_WIDTH = 600  # /1000 em, per character, for the whole printable ASCII range
Placement = tuple[float, float, str, float]  # x, y, text, font size


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(placements: list[Placement]) -> bytes:
    lines = [
        f"BT 1 0 0 1 {x} {y} Tm /F1 {size} Tf ({_escape(text)}) Tj ET".encode()
        for x, y, text, size in placements
    ]
    return b"\n".join(lines)


def build_pdf(
    pages: list[list[Placement]], *, page_size: tuple[float, float] = (612, 792)
) -> bytes:
    """Assemble a minimal single-font PDF from per-page absolute text placements."""
    width, height = page_size
    n = len(pages)
    font_obj = 3 + n * 2  # objects 1=catalog, 2=pages, 3..: (page, content) pairs
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n))
    widths = " ".join([str(_COURIER_WIDTH)] * 95)
    font_dict = (
        f"<< /Type /Font /Subtype /Type1 /BaseFont /Courier "
        f"/FirstChar 32 /LastChar 126 /Widths [{widths}] >>"
    ).encode()

    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    ]
    for i, placements in enumerate(pages):
        page_num = 3 + i * 2
        content_num = page_num + 1
        bodies.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
                f"/MediaBox [0 0 {width} {height}] /Contents {content_num} 0 R >>"
            ).encode()
        )
        stream = _content_stream(placements)
        bodies.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    bodies.append(font_dict)

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for num, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    total = len(bodies) + 1
    out += f"xref\n0 {total}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


def statement_page(
    rows: list[list[str]],
    columns: list[float],
    *,
    y_start: float = 720.0,
    line_height: float = 14.0,
    font_size: float = 10.0,
) -> list[Placement]:
    """Lay out a grid of row-cells at fixed column x-positions, one line per row."""
    placements: list[Placement] = []
    y = y_start
    for row in rows:
        for x, cell in zip(columns, row, strict=False):
            if cell:
                placements.append((x, y, cell, font_size))
        y -= line_height
    return placements


def write_pdf(path: Path, pages: list[list[Placement]]) -> Path:
    path.write_bytes(build_pdf(pages))
    return path


def write_scanned_pdf(path: Path, lines: list[str], *, size: tuple[int, int] = (850, 1100)) -> Path:
    """Writes a one-page, image-only PDF: a raster of `lines`, no PDF text layer at all.

    This is the "scanned statement" fixture - Pillow's PDF writer embeds the image directly
    with no vector text, so pdfplumber's native extract_text()/extract_words() come back
    empty and the page qualifies for OCR. The drawn text is never read by the real Tesseract
    in tests (the runner is stubbed) - it only needs to exist so the page has visible content.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    y = 20
    for line in lines:
        draw.text((20, y), line, fill="black")
        y += 20
    image.save(path, "PDF")
    return path
