"""Multipart upload staging: bounded, streamed, and signature-checked.

CSV only in this slice - a PDF extractor arrives on a parallel branch and must plug in
without any change here. Never trust the client-supplied filename as a path component;
the staged name is always generated.
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
    UPLOAD_FAILED,
    StatementSource,
)

CHUNK_SIZE = 64 * 1024
# PDF support lands on a parallel branch; this build only ever accepts CSV.
SUPPORTED_EXTENSIONS = {".csv"}
REJECTED_MEDIA_PREFIXES = ("application/pdf", "image/")


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
    if ext not in SUPPORTED_EXTENSIONS:
        raise UploadRejected(
            UNSUPPORTED_FILE_TYPE,
            f"unsupported file type {ext or '(none)'!r}; only .csv is accepted in this build",
        )
    media_type = file.content_type or ""
    if media_type.startswith(REJECTED_MEDIA_PREFIXES):
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

    try:
        with staged_path.open("rb") as handle:
            handle.read(4096).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        staged_path.unlink(missing_ok=True)
        raise UploadRejected(INVALID_SIGNATURE, "file is not valid UTF-8 CSV text") from exc

    return StatementSource(
        path=staged_path,
        original_filename=original_filename,
        media_type=media_type or "text/csv",
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
