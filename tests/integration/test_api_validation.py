from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from pfa.api.app import create_app
from pfa.config import Settings


def test_api_rejects_invalid_requests_with_stable_client_errors(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with TestClient(create_app(Settings(database_url=database_url))) as client:
        assert client.get("/analytics/monthly?month=2026-13").status_code == 422
        assert client.get("/transactions?limit=0").status_code == 422
        assert client.post("/scenarios/purchase", json={"cost_minor": -1}).status_code == 422
        assert client.post("/imports", json={"path": "missing.csv"}).status_code == 400
        assert client.post("/chat", json={"message": ""}).status_code == 422
