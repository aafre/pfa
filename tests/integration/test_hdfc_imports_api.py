import csv
import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.config import Settings

HEADER = [
    "Date",
    "Narration",
    "Value Dat",
    "Debit Amount",
    "Credit Amount",
    "Chq/Ref Number",
    "Closing Balance",
]


def settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return Settings(database_url=database_url, upload_dir=tmp_path / "uploads")


def statement(*, bad_closing: bool = False) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(
        [
            ["01/08/2025", "SHOP, ONLINE", "01/08/2025", "1,000.00", "0.00", "R1", "99,000.00"],
            [
                "02/08/2025",
                "SALARY",
                "02/08/2025",
                "0.00",
                "2,500.00",
                "R2",
                "102,000.00" if bad_closing else "101,500.00",
            ],
        ]
    )
    return output.getvalue().encode()


def upload(client: TestClient, content: bytes, filename: str = "download.txt"):
    return client.post(
        "/imports/preview",
        files={"file": (filename, content, "text/plain")},
    )


def new_account() -> dict[str, object]:
    return {
        "name": "HDFC Current",
        "account_type": "current",
        "currency": "INR",
        "currency_confirmed": True,
        "institution": "hdfc_bank",
        "opening_balance_minor": 10000000,
        "opening_balance_as_of": "2025-07-31",
        "opening_balance_confirmed": True,
    }


def test_hdfc_txt_preview_persists_metadata_and_commits_to_inr_account(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        preview = upload(client, statement())
        assert preview.status_code == 200
        body = preview.json()
        assert body["adapter_id"] == "hdfc_in_delimited_v1"
        assert body["extractor"] == "hdfc_in_delimited_v1"
        assert body["detection_confidence"] == 0.99
        assert body["detected_institution"] == "hdfc_bank"
        assert body["detected_currency"] is None
        assert body["suggested_currency"] == "INR"
        assert body["currency_evidence"] == "adapter_suggestion"
        assert body["compatible_account_types"] == ["current", "savings"]
        assert body["reconciliation"]["status"] == "reconciled"
        assert body["reconciliation"]["checked_transition_count"] == 1
        assert body["reconciliation"]["coverage_complete"] is True
        assert body["semantic_totals"]["money_in_count"] == 1
        assert body["semantic_totals"]["money_out_count"] == 1
        assert body["semantic_totals"]["money_out_minor"] == 100000
        assert body["semantic_totals"]["money_in_minor"] == 250000
        assert body["candidates"][0]["raw_description"] == "SHOP, ONLINE"
        assert body["candidates"][0]["raw_fields"]["source_reference"] == "R1"
        assert body["candidates"][0]["external_id"] is None
        assert body["candidates"][0]["signed_amount_minor"] == -100000
        assert body["candidates"][1]["signed_amount_minor"] == 250000
        assert any(issue["code"] == "ACCOUNT_REQUIRED" for issue in body["issues"])

        batch_id = body["id"]
        patched = client.patch(f"/imports/{batch_id}", json={"new_account": new_account()})
        assert patched.status_code == 200
        assert patched.json()["status"] == "preview_ready"
        assert patched.json()["issues"] == []

        committed = client.post(f"/imports/{batch_id}/commit")
        assert committed.status_code == 200
        assert committed.json()["status"] == "committed"
        assert committed.json()["counts"]["imported"] == 2
        assert committed.json()["semantic_totals"]["money_in_minor"] == 250000

        account = client.get("/accounts").json()[0]
        assert (account["institution"], account["currency"], account["account_type"]) == (
            "hdfc_bank",
            "INR",
            "current",
        )
        transactions = client.get("/transactions").json()
        assert {row["signed_amount_minor"] for row in transactions} == {-100000, 250000}

    with TestClient(
        create_app(Settings(database_url=config.database_url, upload_dir=config.upload_dir))
    ) as client:
        refreshed = client.get(f"/imports/{batch_id}").json()
        assert refreshed["suggested_currency"] == "INR"
        assert refreshed["detected_currency"] is None
        assert refreshed["candidates"] == []


def test_hdfc_csv_and_txt_routes_are_content_equivalent(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        txt = upload(client, statement(), "bank-export.txt").json()
        csv_body = client.post(
            "/imports/preview",
            files={"file": ("renamed.csv", statement(), "text/csv")},
        ).json()

    assert txt["adapter_id"] == csv_body["adapter_id"] == "hdfc_in_delimited_v1"
    assert txt["detected_currency"] is None
    assert txt["candidates"] == csv_body["candidates"]
    assert txt["reconciliation"] == csv_body["reconciliation"]


def test_hdfc_balance_mismatch_blocks_without_raw_values_in_issue(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        body = upload(client, statement(bad_closing=True)).json()

    assert body["status"] == "blocked"
    balance_issue = next(
        issue for issue in body["issues"] if issue["code"] == "BALANCE_RECONCILIATION_FAILED"
    )
    assert balance_issue["severity"] == "error"
    assert "102,000" not in balance_issue["message"]
    assert body["reconciliation"]["mismatch_source_rows"] == [3]


def test_unsupported_hdfc_formats_return_guidance_and_clean_staging(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        formatted = client.post(
            "/imports/preview",
            files={
                "file": (
                    "statement.txt",
                    b"HDFC Bank Statement of Account\n"
                    b"Date Narration Chq./Ref.No Value Dt Withdrawal Amt "
                    b"Deposit Amt Closing Balance\n",
                    "text/plain",
                )
            },
        )
        spreadsheet = client.post(
            "/imports/preview",
            files={"file": ("statement.xls", b"legacy workbook", "application/vnd.ms-excel")},
        )
        unknown = client.post(
            "/imports/preview",
            files={"file": ("notes.txt", b"not a supported statement", "text/plain")},
        )

    assert formatted.status_code == 422
    assert formatted.json()["detail"]["code"] == "UNSUPPORTED_TEXT_LAYOUT"
    assert "Delimited" in formatted.json()["detail"]["message"]
    assert spreadsheet.status_code == 422
    assert spreadsheet.json()["detail"]["code"] == "UNSUPPORTED_SPREADSHEET_FORMAT"
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "UNSUPPORTED_TEXT_FORMAT"
    assert not config.upload_dir.exists() or list(config.upload_dir.iterdir()) == []


def test_legacy_hdfc_account_can_be_marked_inline_atomically(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        account = client.post(
            "/accounts",
            json={"name": "Old HDFC", "account_type": "current", "currency": "INR"},
        ).json()
        body = upload(client, statement()).json()
        patch = client.patch(
            f"/imports/{body['id']}",
            json={
                "destination_account_id": account["id"],
                "account_metadata_update": {"institution": "hdfc_bank"},
            },
        )

        assert patch.status_code == 200
        assert patch.json()["status"] == "preview_ready"
        assert client.get("/accounts").json()[0]["institution"] == "hdfc_bank"
        assert client.post(f"/imports/{body['id']}/commit").status_code == 200


def test_excluding_hdfc_row_makes_coverage_incomplete(tmp_path) -> None:
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        body = upload(client, statement()).json()
        excluded = body["candidates"][1]["candidate_id"]
        patched = client.patch(
            f"/imports/{body['id']}",
            json={"new_account": new_account(), "excluded_candidate_ids": [excluded]},
        )

    assert patched.status_code == 200
    result = patched.json()
    assert result["status"] == "blocked"
    assert result["reconciliation"]["arithmetic_integrity"] == "pass"
    assert result["reconciliation"]["coverage_integrity"] == "incomplete"
    assert any(issue["code"] == "RECONCILIATION_INCOMPLETE" for issue in result["issues"])
