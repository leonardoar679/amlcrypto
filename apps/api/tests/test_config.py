from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import API_ROOT, MIN_SECRET_LENGTH, Settings


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def database_of(url: str) -> str | None:
    # Compare parsed URLs: SQLAlchemy percent-encodes the rendered form (e.g. "C%3A/...") and
    # decodes it again on parse, so the string itself is not a stable thing to assert on.
    return make_url(url).database


def test_defaults_are_sqlite_in_dev():
    settings = make()
    assert settings.environment == "dev"
    assert settings.database_backend == "sqlite"


def test_relative_sqlite_path_is_anchored_at_api_root_not_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    resolved = make(database_url="sqlite:///./data/aml.db").resolved_database_url
    assert database_of(resolved) == (API_ROOT / "data" / "aml.db").as_posix()


def test_absolute_sqlite_path_is_kept(tmp_path):
    target = (tmp_path / "x.db").as_posix()
    assert database_of(make(database_url=f"sqlite:///{target}").resolved_database_url) == target


def test_sqlite_path_with_awkward_characters_survives_resolution(tmp_path):
    target = (tmp_path / "100% sure #1.db").as_posix()
    url = make_url("sqlite://").set(database=target).render_as_string(hide_password=False)
    assert database_of(make(database_url=url).resolved_database_url) == target


def test_mysql_url_is_passed_through_with_its_password():
    url = "mysql+pymysql://aml:p%40ss@db.internal:3306/aml"
    settings = make(database_url=url)
    assert settings.database_backend == "mysql"
    assert make_url(settings.resolved_database_url) == make_url(url)
    assert make_url(settings.resolved_database_url).password == "p@ss"


def test_database_type_must_match_the_url():
    assert make(database_url="sqlite:///x.db", database_type="sqlite").database_backend == "sqlite"
    with pytest.raises(ValidationError, match="does not match"):
        make(database_url="sqlite:///x.db", database_type="mysql")


def test_unsupported_backend_is_rejected():
    with pytest.raises(ValidationError, match="unsupported database backend"):
        make(database_url="postgresql://u:p@localhost/db")


def test_malformed_url_is_rejected():
    with pytest.raises(ValidationError, match="not a valid database URL"):
        make(database_url="this is not a url")


@pytest.mark.parametrize("environment", ["test", "prod"])
def test_secret_key_is_required_outside_dev(environment):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make(environment=environment)
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make(environment=environment, secret_key="short")
    ok = make(environment=environment, secret_key="x" * MIN_SECRET_LENGTH)
    assert ok.signing_key == "x" * MIN_SECRET_LENGTH


def test_dev_without_a_secret_gets_one_stable_ephemeral_key():
    first, second = make(), make()
    assert len(first.signing_key) >= MIN_SECRET_LENGTH
    assert first.signing_key == second.signing_key


def test_blank_values_from_a_copied_env_example_count_as_unset():
    settings = make(secret_key="", database_type=" ")
    assert settings.secret_key is None
    assert settings.database_type is None
    assert len(settings.signing_key) >= MIN_SECRET_LENGTH
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make(environment="prod", secret_key="")


def test_data_dir_resolves_against_api_root():
    assert make(data_dir=Path("./data")).resolved_data_dir == (API_ROOT / "data").resolve()


def test_secret_is_not_exposed_by_repr():
    assert "x" * MIN_SECRET_LENGTH not in repr(make(secret_key="x" * MIN_SECRET_LENGTH))
