import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.config import Settings
from pfa.db.engine import make_engine, make_session_factory
from pfa.db.models import ImportBatchModel
from pfa.db.repositories import TransactionRepository

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.pdf_builder import build_pdf, statement_page  # noqa: E402

from pfa.ingestion.candidates import ExtractionResult  # noqa: E402
from pfa.ingestion.extractors.csv import CsvStatementExtractor  # noqa: E402


def _settings(tmp_path, **overrides) -> Settings:
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return Settings(database_url=database_url, upload_dir=tmp_path / "uploads", **overrides)


def _csv_bytes(rows: str = "") -> bytes:
    header = "date,description,amount,account\n"
    body = rows or (
        "2026-08-01,Salary,2000,Main account\n"
        "2026-08-02,Coffee Shop,-350,Main account\n"
        "2026-08-03,Rent,-900,Main account\n"
    )
    return (header + body).encode("utf-8")


def _upload(client: TestClient, content: bytes, filename: str = "statement.csv", **data):
    return client.post(
        "/imports/preview",
        files={"file": (filename, content, "text/csv")},
        data=data,
    )


def test_preview_patch_commit_flow_reports_correct_counts_at_each_step(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        preview = _upload(client, _csv_bytes())
        assert preview.status_code == 200
        body = preview.json()
        assert body["status"] == "preview_ready"
        assert body["counts"] == {
            "total": 3,
            "valid": 3,
            "warning": 0,
            "error": 0,
            "duplicate": 0,
            "excluded": 0,
            "imported": 0,
        }
        batch_id = body["id"]
        rent_id = next(
            c["candidate_id"] for c in body["candidates"] if "Rent" in c["raw_description"]
        )

        patched = client.patch(
            f"/imports/{batch_id}",
            json={"excluded_candidate_ids": [rent_id], "account": "Checking"},
        )
        assert patched.status_code == 200
        patched_body = patched.json()
        assert patched_body["counts"]["excluded"] == 1
        assert patched_body["destination_account"] == "Checking"

        committed = client.post(f"/imports/{batch_id}/commit")
        assert committed.status_code == 200
        committed_body = committed.json()
        assert committed_body["status"] == "committed"
        assert committed_body["counts"]["imported"] == 2
        assert len(committed_body["committed_transaction_ids"]) == 2

        transactions = client.get("/transactions").json()
        assert len(transactions) == 2
        accounts = client.get("/accounts").json()
        assert any(a["name"] == "Checking" for a in accounts)

        # upload_dir is swept clean of staged files after the request cycle.
        assert list(settings.upload_dir.iterdir()) == []


def test_reupload_of_same_csv_reports_duplicates_and_inserts_nothing_new(tmp_path) -> None:
    settings = _settings(tmp_path)
    csv_bytes = _csv_bytes()
    with TestClient(create_app(settings)) as client:
        first = _upload(client, csv_bytes, account="Main account")
        batch_id = first.json()["id"]
        first_commit = client.post(f"/imports/{batch_id}/commit")
        assert first_commit.json()["counts"]["imported"] == 3

        second = _upload(client, csv_bytes, account="Main account")
        second_body = second.json()
        assert second_body["counts"]["duplicate"] == 3

        second_commit = client.post(f"/imports/{second_body['id']}/commit")
        assert second_commit.status_code == 200
        assert second_commit.json()["counts"]["imported"] == 0

        transactions = client.get("/transactions").json()
        assert len(transactions) == 3


def test_fake_pdf_upload_is_rejected_and_leaves_upload_dir_empty(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/imports/preview",
            files={"file": ("statement.pdf", b"not really a pdf at all", "application/pdf")},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_SIGNATURE"
    assert not settings.upload_dir.exists() or list(settings.upload_dir.iterdir()) == []


def test_signed_but_unparseable_pdf_blocks_the_batch_and_leaves_upload_dir_empty(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/imports/preview",
            files={"file": ("statement.pdf", b"%PDF-1.4 truncated garbage", "application/pdf")},
        )
    # Signature passes, so this is an extraction problem, not an upload rejection - the
    # extractor names it, and it must stay sanitized: no staged path, no traceback.
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert [issue["code"] for issue in body["issues"]] == ["PDF_NOT_EXTRACTABLE"]
    assert str(settings.upload_dir) not in response.text
    assert "Traceback" not in response.text
    assert list(settings.upload_dir.iterdir()) == []


def test_oversize_upload_is_rejected_and_leaves_upload_dir_empty(tmp_path) -> None:
    settings = _settings(tmp_path, max_upload_bytes=64)
    with TestClient(create_app(settings)) as client:
        response = _upload(client, _csv_bytes())
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert not settings.upload_dir.exists() or list(settings.upload_dir.iterdir()) == []


def test_expired_batch_returns_410_and_discard_and_committed_delete_rules(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        preview = _upload(client, _csv_bytes())
        batch_id = preview.json()["id"]

        _force_expiry(settings, batch_id)

        expired_get = client.get(f"/imports/{batch_id}")
        assert expired_get.status_code == 410
        assert expired_get.json()["detail"]["code"] == "BATCH_EXPIRED"

        # A fresh, unexpired batch can be discarded.
        fresh = _upload(client, _csv_bytes())
        fresh_id = fresh.json()["id"]
        discard = client.delete(f"/imports/{fresh_id}")
        assert discard.status_code == 200
        assert discard.json()["status"] == "discarded"

        # A committed batch cannot be deleted.
        committed_source = _upload(client, _csv_bytes())
        committed_id = committed_source.json()["id"]
        client.post(f"/imports/{committed_id}/commit")
        delete_committed = client.delete(f"/imports/{committed_id}")
        assert delete_committed.status_code == 409
        assert delete_committed.json()["detail"]["code"] == "BATCH_ALREADY_COMMITTED"


def test_get_after_a_simulated_refresh_restores_the_preview(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        preview = _upload(client, _csv_bytes())
        batch_id = preview.json()["id"]

    # A brand-new app instance (and TestClient) over the same database stands in for a
    # browser refresh: nothing survives except what's persisted.
    with TestClient(create_app(_settings_reuse(settings))) as fresh_client:
        refreshed = fresh_client.get(f"/imports/{batch_id}")
        assert refreshed.status_code == 200
        refreshed_body = refreshed.json()
        assert refreshed_body["id"] == batch_id
        assert refreshed_body["status"] == "preview_ready"
        assert len(refreshed_body["candidates"]) == 3


def _settings_reuse(settings: Settings) -> Settings:
    """Same database and upload dir, standing in for a second app process/browser tab."""
    return Settings(database_url=settings.database_url, upload_dir=settings.upload_dir)


def test_commit_is_atomic_when_a_row_fails_partway(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    original_add = TransactionRepository.add
    calls = {"n": 0}

    def flaky_add(self, transaction):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated database failure")
        return original_add(self, transaction)

    monkeypatch.setattr(TransactionRepository, "add", flaky_add)

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        preview = _upload(client, _csv_bytes())
        batch_id = preview.json()["id"]
        commit = client.post(f"/imports/{batch_id}/commit")
        assert commit.status_code == 500

    monkeypatch.undo()
    with TestClient(create_app(_settings_reuse(settings))) as verify_client:
        assert verify_client.get("/transactions").json() == []


def test_no_raw_uploaded_bytes_anywhere_in_sqlite(tmp_path) -> None:
    settings = _settings(tmp_path)
    csv_bytes = (
        b"date,description,amount,account\n2026-08-01,VERY_UNIQUE_MARKER_XYZ987,-350,Main account\n"
    )
    with TestClient(create_app(settings)) as client:
        preview = _upload(client, csv_bytes)
        batch_id = preview.json()["id"]
        client.post(f"/imports/{batch_id}/commit")

    db_path = tmp_path / "pfa.db"
    db_bytes = db_path.read_bytes()
    assert csv_bytes not in db_bytes
    assert list(settings.upload_dir.iterdir()) == []


def _force_expiry(settings: Settings, batch_id: str) -> None:
    """Puts the TTL in the past directly, standing in for elapsed wall-clock time."""
    engine = make_engine(settings)
    session = make_session_factory(engine)()
    batch = session.get(ImportBatchModel, batch_id)
    assert batch is not None
    batch.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    session.commit()
    session.close()
    engine.dispose()


def _reload_batch(settings: Settings, batch_id: str) -> ImportBatchModel:
    """Reads the row through a fresh engine, so nothing in-session can mask a rollback."""
    engine = make_engine(settings)
    session = make_session_factory(engine)()
    batch = session.get(ImportBatchModel, batch_id)
    assert batch is not None
    session.expunge(batch)
    session.close()
    engine.dispose()
    return batch


def test_expiry_purge_survives_the_410_response(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        batch_id = _upload(client, _csv_bytes()).json()["id"]
        _force_expiry(settings, batch_id)

        expired = client.get(f"/imports/{batch_id}")
        assert expired.status_code == 410
        assert expired.json()["detail"]["code"] == "BATCH_EXPIRED"

    # The 410 is raised as an error, which rolls the request back - the purge must not
    # ride along, or candidate rows outlive their TTL.
    persisted = _reload_batch(settings, batch_id)
    assert persisted.status == "expired"
    assert persisted.candidates_json is None


def test_extraction_timeout_is_reported_and_leaves_no_staged_file(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, extraction_timeout_seconds=0.05)

    def slow_extract(self, source):  # type: ignore[no-untyped-def]
        # Holds the staged file open past the timeout, which is what makes the request's
        # own unlink fail on Windows.
        with source.path.open("rb"):
            time.sleep(0.6)
        return ExtractionResult(candidates=[])

    monkeypatch.setattr(CsvStatementExtractor, "extract", slow_extract)

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = _upload(client, _csv_bytes())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert [issue["code"] for issue in body["issues"]] == ["EXTRACTION_TIMEOUT"]
    # Sanitized: the batch metadata is by design, the staged path and traceback are not.
    assert str(settings.upload_dir) not in response.text
    assert "Traceback" not in response.text

    # Cleanup is deferred to the worker thread, so give it until it finishes.
    deadline = time.monotonic() + 10
    while list(settings.upload_dir.iterdir()) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert list(settings.upload_dir.iterdir()) == []


def test_service_open_failure_after_staging_leaves_no_staged_file(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)

    def unavailable(_settings):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated database unavailability")

    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        # Patched only after startup, so the app opens normally and fails on the request.
        monkeypatch.setattr("pfa.api.app.open_services", unavailable)
        response = _upload(client, _csv_bytes())
        assert response.status_code == 500
        assert list(settings.upload_dir.iterdir()) == []


def _pdf_bytes(rows: list[list[str]]) -> bytes:
    """A one-page digital statement: header row plus `rows`, at fixed column positions."""
    header = [["Date", "Description", "Amount"]]
    return build_pdf([statement_page(header + rows, [72, 160, 400])])


def _upload_pdf(client: TestClient, content: bytes, **data):
    return client.post(
        "/imports/preview",
        files={"file": ("statement.pdf", content, "application/pdf")},
        data=data,
    )


def test_digital_pdf_previews_and_commits_through_the_api(tmp_path) -> None:
    settings = _settings(tmp_path)
    pdf = _pdf_bytes(
        [
            ["2026-08-01", "Salary", "2000.00"],
            ["2026-08-02", "Coffee Shop", "-3.50"],
        ]
    )
    with TestClient(create_app(settings)) as client:
        preview = _upload_pdf(client, pdf, account="Main account")
        assert preview.status_code == 200
        body = preview.json()
        # The whole point of the wiring: a PDF must reach the PDF extractor, not the CSV one.
        assert body["extractor"] == "pdf/1+ocr"
        assert body["page_count"] == 1
        assert body["status"] == "preview_ready"
        assert body["counts"]["total"] == 2
        assert {c["raw_description"] for c in body["candidates"]} == {"Salary", "Coffee Shop"}
        assert all(c["source_page"] == 1 for c in body["candidates"])

        commit = client.post(f"/imports/{body['id']}/commit")
        assert commit.status_code == 200
        assert commit.json()["counts"]["imported"] == 2

        assert len(client.get("/transactions").json()) == 2
    assert list(settings.upload_dir.iterdir()) == []


def test_reuploading_the_same_pdf_reports_duplicates_and_imports_nothing(tmp_path) -> None:
    settings = _settings(tmp_path)
    pdf = _pdf_bytes([["2026-08-01", "Salary", "2000.00"]])
    with TestClient(create_app(settings)) as client:
        first = _upload_pdf(client, pdf, account="Main account")
        client.post(f"/imports/{first.json()['id']}/commit")

        second = _upload_pdf(client, pdf, account="Main account")
        body = second.json()
        assert body["counts"]["duplicate"] == 1
        commit = client.post(f"/imports/{body['id']}/commit")
        assert commit.json()["counts"]["imported"] == 0

        assert len(client.get("/transactions").json()) == 1


def test_pdf_over_the_page_limit_is_reported_and_leaves_upload_dir_empty(tmp_path) -> None:
    settings = _settings(tmp_path, max_pdf_pages=1)
    two_pages = build_pdf(
        [
            statement_page([["Date", "Description", "Amount"]], [72, 160, 400]),
            statement_page([["2026-08-01", "Salary", "2000.00"]], [72, 160, 400]),
        ]
    )
    with TestClient(create_app(settings)) as client:
        body = _upload_pdf(client, two_pages).json()
    assert body["status"] == "blocked"
    assert "PDF_TOO_MANY_PAGES" in [issue["code"] for issue in body["issues"]]
    assert list(settings.upload_dir.iterdir()) == []


def test_scanned_pdf_without_tesseract_reports_ocr_unavailable(tmp_path) -> None:
    """Tesseract is not installed here, so this is the real local experience for a scan."""
    from fixtures.pdf_builder import write_scanned_pdf

    scanned = write_scanned_pdf(tmp_path / "scan.pdf", ["2026-08-01  Salary  2000.00"])
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        body = _upload_pdf(client, scanned.read_bytes()).json()
    assert body["extractor"] == "pdf/1+ocr"
    codes = [issue["code"] for issue in body["issues"]]
    assert "OCR_UNAVAILABLE" in codes
    assert list(settings.upload_dir.iterdir()) == []


def test_malformed_content_length_does_not_500(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post(
            "/imports/preview",
            files={"file": ("statement.csv", _csv_bytes(), "text/csv")},
            headers={"content-length": "not-a-number"},
        )
    # Starlette may reject the framing itself; what must not happen is a 500 from int().
    assert response.status_code != 500
