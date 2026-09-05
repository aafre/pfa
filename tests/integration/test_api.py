from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.config import Settings


def test_api_import_and_analytics_are_local_and_typed(tmp_path) -> None:
    csv_path = tmp_path / "one.csv"
    csv_path.write_text(
        "date,description,amount,kind,category\n2026-08-01,Salary,1000,income,\n2026-08-02,Rent,-400,expense,housing\n"
    )
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        response = client.post("/imports", json={"path": str(csv_path)})
        assert response.status_code == 200
        assert response.json()["imported"] == 2
        summary = client.get("/analytics/monthly?month=2026-08")
    assert summary.status_code == 200
    assert summary.json()["income_minor"] == 100000
    assert client.get("/transactions").json()[0]["amount_minor"] == 100000
    assert client.get("/transactions").json()[0]["flow_direction"] == "credit"


def test_patch_transaction_category_is_a_user_correction(tmp_path) -> None:
    csv_path = tmp_path / "one.csv"
    csv_path.write_text(
        "date,description,amount,kind,category\n2026-08-02,CORNER SHOP,-4.20,expense,\n"
    )
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        client.post("/imports", json={"path": str(csv_path)})
        tx_id = client.get("/transactions").json()[0]["id"]
        assert client.get("/transactions").json()[0]["category"] is None

        assert "groceries" in client.get("/categories").json()
        bad = client.patch(f"/transactions/{tx_id}", json={"category": "nonsense"})
        assert bad.status_code == 422
        missing = client.patch("/transactions/999999", json={"category": "groceries"})
        assert missing.status_code == 404

        patched = client.patch(f"/transactions/{tx_id}", json={"category": "groceries"})
        assert patched.status_code == 200
        assert patched.json()["category"] == "groceries"
        assert patched.json()["classification_source"] == "user"
        assert client.get("/transactions").json()[0]["category"] == "groceries"


def test_dashboard_and_static_assets_are_served(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        html_resp = client.get("/")
        assert html_resp.status_code == 200
        assert "text/html" in html_resp.headers.get("content-type", "")
        html = html_resp.text
        assert "view-overview" in html
        assert "view-import" in html
        assert "view-categories" in html
        assert "view-activity" in html
        assert "view-ask" in html

        css_resp = client.get("/static/styles.css")
        assert css_resp.status_code == 200
        assert "text/css" in css_resp.headers.get("content-type", "")

        js_resp = client.get("/static/app.js")
        assert js_resp.status_code == 200
        assert "javascript" in js_resp.headers.get("content-type", "")


def test_api_fx_rates_endpoints(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        # Set manual rate
        post_resp = client.post(
            "/fx/rates",
            json={
                "base_currency": "GBP",
                "quote_currency": "INR",
                "rate": "105.5",
                "effective_at": "2026-08-01",
            },
        )
        assert post_resp.status_code == 200
        data = post_resp.json()
        assert data["base_currency"] == "GBP"
        assert data["quote_currency"] == "INR"
        assert data["rate"] == "105.5"

        bad_resp = client.post(
            "/fx/rates",
            json={
                "base_currency": "GBP",
                "quote_currency": "USD",
                "rate": "not-a-number",
                "effective_at": "2026-08-01",
            },
        )
        assert bad_resp.status_code == 422

        # Get rates
        get_resp = client.get("/fx/rates?base=GBP")
        assert get_resp.status_code == 200
        rates = get_resp.json()
        assert len(rates) == 1
        assert rates[0]["quote_currency"] == "INR"


def test_transactions_month_filter_and_chat_currency(tmp_path) -> None:
    csv_aug = tmp_path / "aug.csv"
    csv_aug.write_text(
        "date,description,amount,kind,category\n2026-08-01,Salary,1000,income,\n2026-08-02,Rent,-400,expense,housing\n2026-07-15,Past,100,income,\n"
    )
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        import_resp = client.post("/imports", json={"path": str(csv_aug)})
        assert import_resp.status_code == 200

        # /transactions with month filter
        tx_aug = client.get("/transactions?month=2026-08")
        assert tx_aug.status_code == 200
        assert len(tx_aug.json()) == 2
        assert all(r["date"].startswith("2026-08") for r in tx_aug.json())

        tx_jul = client.get("/transactions?month=2026-07")
        assert tx_jul.status_code == 200
        assert len(tx_jul.json()) == 1

        tx_jun = client.get("/transactions?month=2026-06")
        assert tx_jun.status_code == 200
        assert len(tx_jun.json()) == 0

        # /chat respects currency
        chat_resp = client.post(
            "/chat",
            json={"message": "how much did I spend in August?", "currency": "INR"},
        )
        assert chat_resp.status_code == 200
        assert "INR" in chat_resp.json()["answer"]
