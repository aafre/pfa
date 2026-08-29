import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from pfa.config import Settings
from pfa.db.engine import make_engine
from pfa.db.models import Base
from pfa.services.runtime import open_services


def migrate(database_url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    if revision == "base":
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


def test_runtime_requires_an_explicit_migration(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'unmigrated.db'}")

    with pytest.raises(OperationalError, match="no such table"):
        open_services(settings)

    engine = make_engine(settings)
    assert inspect(engine).get_table_names() == []
    engine.dispose()


def test_alembic_schema_matches_models_and_downgrades_cleanly(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    migrate(database_url, "head")
    engine = make_engine(Settings(database_url=database_url))
    inspector = inspect(engine)

    assert set(inspector.get_table_names()) == {*Base.metadata.tables, "alembic_version"}
    for table in Base.metadata.sorted_tables:
        actual = {column["name"]: column for column in inspector.get_columns(table.name)}
        assert set(actual) == {column.name for column in table.columns}
        for column in table.columns:
            assert actual[column.name]["nullable"] == column.nullable

    engine.dispose()
    migrate(database_url, "base")
    engine = make_engine(Settings(database_url=database_url))
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_import_batches_migration_adds_and_removes_only_its_own_table(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'batches.db'}"
    migrate(database_url, "0001_initial")
    engine = make_engine(Settings(database_url=database_url))
    tables_before = set(inspect(engine).get_table_names())
    assert "import_batches" not in tables_before
    engine.dispose()

    migrate(database_url, "0002_import_batches")
    engine = make_engine(Settings(database_url=database_url))
    inspector = inspect(engine)
    assert "import_batches" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("import_batches")}
    assert columns == {
        "id",
        "original_filename",
        "media_type",
        "size_bytes",
        "sha256",
        "extractor",
        "status",
        "destination_account",
        "detected_account",
        "detected_currency",
        "statement_start",
        "statement_end",
        "page_count",
        "candidates_json",
        "issues_json",
        "counts_json",
        "committed_transaction_ids_json",
        "created_at",
        "updated_at",
        "expires_at",
        "committed_at",
    }
    index_names = {index["name"] for index in inspector.get_indexes("import_batches")}
    assert "ix_import_batches_status" in index_names
    engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "0001_initial")
    engine = make_engine(Settings(database_url=database_url))
    tables_after_downgrade = set(inspect(engine).get_table_names())
    assert "import_batches" not in tables_after_downgrade
    assert tables_after_downgrade == tables_before
    engine.dispose()


def test_amount_sign_migration_adds_and_removes_only_its_own_column(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'amount_sign.db'}"
    migrate(database_url, "0002_import_batches")
    engine = make_engine(Settings(database_url=database_url))
    columns_before = {c["name"] for c in inspect(engine).get_columns("import_batches")}
    assert "amount_sign" not in columns_before
    engine.dispose()

    migrate(database_url, "0003_batch_amount_sign")
    engine = make_engine(Settings(database_url=database_url))
    columns = {c["name"]: c for c in inspect(engine).get_columns("import_batches")}
    assert set(columns) == columns_before | {"amount_sign"}
    assert columns["amount_sign"]["nullable"]
    engine.dispose()

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config, "0002_import_batches")
    engine = make_engine(Settings(database_url=database_url))
    assert {c["name"] for c in inspect(engine).get_columns("import_batches")} == columns_before
    engine.dispose()
