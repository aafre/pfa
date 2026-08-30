import pytest
from typer.testing import CliRunner

from pfa.cli.app import app
from pfa.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """get_settings() is process-wide @lru_cache'd. A test that points PFA_DATABASE_URL
    at a tmp_path DB must not inherit a stale cached Settings from an earlier test in this
    file, nor leak its own tmp_path-scoped Settings into whatever runs after it.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cli_missing_import_file_is_a_clean_usage_error(tmp_path) -> None:
    result = CliRunner().invoke(app, ["import", str(tmp_path / "missing.csv")])

    assert result.exit_code == 2
    assert "path must identify a local CSV file" in result.output
    assert "Traceback" not in result.output


def test_cli_fx_commands(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'pfa.db'}"
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    runner = CliRunner(env={"PFA_DATABASE_URL": database_url})
    set_res = runner.invoke(app, ["fx", "set", "GBP", "USD", "1.30", "--date", "2026-08-01"])
    assert set_res.exit_code == 0
    assert "FX rate GBP/USD = 1.30 set" in set_res.output

    list_res = runner.invoke(app, ["fx", "list"])
    assert list_res.exit_code == 0
    assert "GBP" in list_res.output
    assert "USD" in list_res.output
