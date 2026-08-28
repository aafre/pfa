from typer.testing import CliRunner

from pfa.cli.app import app


def test_cli_missing_import_file_is_a_clean_usage_error(tmp_path) -> None:
    result = CliRunner().invoke(app, ["import", str(tmp_path / "missing.csv")])

    assert result.exit_code == 2
    assert "path must identify a local CSV file" in result.output
    assert "Traceback" not in result.output
