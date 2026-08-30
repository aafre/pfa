"""Multipart upload staging: bounded, streamed, and signature-checked.

Accepts CSV and PDF. Never trust the client-supplied filename as a path component;
the staged name is always generated. The extension decides which signature check runs
and, downstream, which extractor the batch layer selects.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import UploadFile

from pfa.config import Settings
from pfa.domain.errors import UploadRejected

from .candidates import (
    FILE_TOO_LARGE,
    INVALID_SIGNATURE,
    UNSUPPORTED_FILE_TYPE,
    UNSUPPORTED_SPREADSHEET_FORMAT,
    UNSUPPORTED_TEXT_FORMAT,
    UNSUPPORTED_TEXT_LAYOUT,
    UPLOAD_FAILED,
    StatementSource,
)
from .dialects import HDFC_IN_DELIMITED, detect_adapter

CHUNK_SIZE = 64 * 1024
SUPPORTED_EXTENSIONS = {".csv", ".pdf", ".txt"}
DEFAULT_MEDIA_TYPES = {".csv": "text/csv", ".pdf": "application/pdf", ".txt": "text/plain"}
PDF_SIGNATURE = b"%PDF-"
REJECTED_MEDIA_PREFIXES = ("image/",)


def stage_upload(
    file: UploadFile, settings: Settings, content_length: int | None = None
) -> StatementSource:
    """Streams the upload to a generated path under settings.upload_dir, hashing as it goes.

    Rejects early when the declared Content-Length exceeds the cap, then aborts and
    unlinks the instant the running byte count exceeds it too - a lying or absent header
    doesn't buy the client an unbounded write. Extension, media type, and a content
    signature check are all required; none alone is sufficient.
    """
    if content_length is not None and content_length > settings.max_upload_bytes:
        raise UploadRejected(FILE_TOO_LARGE, "file exceeds the upload size limit")

    original_filename = Path(file.filename or "upload").name
    ext = Path(original_filename).suffix.lower()
    if ext in {".xls", ".xlsx"}:
        raise UploadRejected(
            UNSUPPORTED_SPREADSHEET_FORMAT,
            "Excel statements are not supported; for HDFC, download the Delimited format",
        )
    if ext not in SUPPORTED_EXTENSIONS:
        raise UploadRejected(
            UNSUPPORTED_FILE_TYPE,
            f"unsupported file type {ext or '(none)'!r}; only .csv, .txt, and .pdf are accepted",
        )
    media_type = file.content_type or ""
    # Lowercased: a blocklist that "Image/PNG" walks straight through is not a blocklist.
    if media_type.lower().startswith(REJECTED_MEDIA_PREFIXES):
        raise UploadRejected(UNSUPPORTED_FILE_TYPE, f"unsupported content type {media_type!r}")

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    staged_path = settings.upload_dir / f"{uuid.uuid4().hex}{ext}"
    digest = hashlib.sha256()
    total = 0
    try:
        with staged_path.open("wb") as out:
            while True:
                chunk = file.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise UploadRejected(FILE_TOO_LARGE, "file exceeds the upload size limit")
                digest.update(chunk)
                out.write(chunk)
    except UploadRejected:
        staged_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        staged_path.unlink(missing_ok=True)
        raise UploadRejected(UPLOAD_FAILED, "failed to read the uploaded file") from exc

    if total == 0:
        staged_path.unlink(missing_ok=True)
        raise UploadRejected(UPLOAD_FAILED, "uploaded file is empty")

    with staged_path.open("rb") as handle:
        head = handle.read(4096)
    if ext == ".pdf":
        if not head.startswith(PDF_SIGNATURE):
            staged_path.unlink(missing_ok=True)
            raise UploadRejected(INVALID_SIGNATURE, "file is not a valid PDF")
    else:
        try:
            head.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            staged_path.unlink(missing_ok=True)
            raise UploadRejected(
                INVALID_SIGNATURE,
                "file is not valid UTF-8 delimited text",
            ) from exc
        if ext == ".txt":
            detection = detect_adapter(staged_path, media_type)
            if detection.dialect is not HDFC_IN_DELIMITED:
                text = staged_path.read_text(encoding="utf-8-sig", errors="ignore")[:100_000]
                lower = " ".join(text.lower().split())
                formatted_markers = (
                    "statement of account" in lower
                    or "withdrawal amt" in lower
                    or "deposit amt" in lower
                    or "chq./ref.no" in lower
                    or "value dt" in lower and "closing balance" in lower
                )
                staged_path.unlink(missing_ok=True)
                if formatted_markers:
                    raise UploadRejected(
                        UNSUPPORTED_TEXT_LAYOUT,
                        "HDFC formatted text is not supported; download the Delimited format",
                    )
                raise UploadRejected(
                    UNSUPPORTED_TEXT_FORMAT,
                    "unrecognized text statement; for HDFC, download the Delimited format",
                )

    return StatementSource(
        path=staged_path,
        original_filename=original_filename,
        media_type=media_type or DEFAULT_MEDIA_TYPES[ext],
        size_bytes=total,
        sha256=digest.hexdigest(),
    )


def sweep_upload_dir(settings: Settings) -> None:
    """Startup cleanup for stragglers left behind by a crashed or killed process."""
    if not settings.upload_dir.exists():
        return
    for path in settings.upload_dir.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)
