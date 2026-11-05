"""MySQL checks that need no server: engine construction and the DDL our schema compiles to.

These do NOT replace running the suite against a real MySQL (TEST_DATABASE_URL, see conftest.py);
they catch type and collation mistakes early, on any machine.
"""

from __future__ import annotations

import io

from alembic import command
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable

from app.database import migrations
from app.database.connection import create_db_engine
from app.database.models import Base

MYSQL_URL = "mysql+pymysql://aml:secret@db.example:3306/aml"


def ddl(table_name: str, dialect) -> str:
    return str(CreateTable(Base.metadata.tables[table_name]).compile(dialect=dialect)).replace(
        "\n", " "
    )


def test_mysql_engine_is_built_without_connecting():
    engine = create_db_engine(MYSQL_URL)
    try:
        assert engine.dialect.name == "mysql"
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


def test_mysql_integer_keys_autoincrement_as_bigint():
    sql = ddl("users", mysql.dialect())
    assert "id BIGINT NOT NULL AUTO_INCREMENT" in sql
    assert "organization_id BIGINT NOT NULL" in sql


def test_sqlite_integer_keys_are_rowid_aliases():
    # Only a column declared exactly INTEGER (not BIGINT) autoincrements on SQLite.
    sql = ddl("users", sqlite.dialect())
    assert "id INTEGER NOT NULL" in sql
    assert "BIGINT" not in sql


def test_mysql_timestamps_keep_microseconds():
    assert "created_at DATETIME(6) NOT NULL" in ddl("users", mysql.dialect())


def test_mysql_identifier_columns_are_case_sensitive():
    chains = ddl("chains", mysql.dialect())
    assets = ddl("chain_assets", mysql.dialect())
    assert "id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL" in chains
    assert "chain_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL" in assets
    assert "asset_key VARCHAR(128) COLLATE utf8mb4_bin NOT NULL" in assets
    # Ordinary text columns keep the server default (case-insensitive) collation.
    assert "slug VARCHAR(32) NOT NULL" in chains


def test_sqlite_identifier_columns_need_no_collation():
    # SQLite compares text with BINARY collation by default, which is already case-sensitive.
    assert "COLLATE" not in ddl("chains", sqlite.dialect())


def test_offline_mysql_migration_sql_matches_the_models():
    config = migrations.alembic_config(MYSQL_URL)
    config.output_buffer = io.StringIO()
    command.upgrade(config, "head", sql=True)
    script = config.output_buffer.getvalue().replace("\n", " ")

    assert "CREATE TABLE users" in script
    assert "id BIGINT NOT NULL AUTO_INCREMENT" in script
    assert "created_at DATETIME(6) NOT NULL" in script
    assert "CONSTRAINT fk_users_organization_id_organizations FOREIGN KEY" in script
    # Migration 0002: case-sensitive identifiers and the seed rows.
    assert "id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL" in script
    assert "chain_id VARCHAR(64) COLLATE utf8mb4_bin NOT NULL" in script
    assert "tron:728126428" in script
    assert script.count("INSERT INTO chains") == 5
