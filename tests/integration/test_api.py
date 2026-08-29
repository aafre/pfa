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
