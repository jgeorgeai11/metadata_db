"""Unit tests for the shared pgconn connection helper.

`load_dotenv` is stubbed in most tests so a developer's real `.env`
never leaks into the asserted environment; one test replaces it with a
fake that plants env vars, proving `.env` values are read through it.
"""

from unittest.mock import MagicMock

import pytest

import pgconn
from pgconn import pgconn as pgconn_module


@pytest.fixture
def stub_dotenv(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub load_dotenv (a MagicMock, so calls remain assertable).

    Patched on the defining module — `connection_kwargs` looks the name
    up in `pgconn.pgconn`'s globals, not on the re-exporting package.
    """
    mock = MagicMock(return_value=None)
    monkeypatch.setattr(pgconn_module, "load_dotenv", mock)
    return mock


@pytest.fixture
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide all four POSTGRES_* env vars."""
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")


def test_connection_kwargs_happy_path_builds_six_key_mapping(
    stub_dotenv: MagicMock, postgres_env: None
) -> None:
    result = pgconn.connection_kwargs("mydb", "catalog")

    assert result == {
        "host": "localhost",
        "port": "5432",
        "user": "u",
        "password": "p",
        "dbname": "mydb",
        # search_path rides on the connection options so the caller's
        # schema-unqualified SQL resolves to the configured schema.
        "options": "-c search_path=catalog",
    }
    # interpolate=False is the contract, not an incidental argument: it
    # keeps `.env` values literal so a secret containing `${` is not
    # silently rewritten by python-dotenv's default expansion.
    stub_dotenv.assert_called_once_with(interpolate=False)


def test_connection_kwargs_sets_search_path_option(
    stub_dotenv: MagicMock, postgres_env: None
) -> None:
    result = pgconn.connection_kwargs("mydb", "myschema")

    assert result["options"] == "-c search_path=myschema"


@pytest.mark.parametrize(
    "missing_var",
    ["POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER", "POSTGRES_PASSWORD"],
)
@pytest.mark.parametrize(
    "mode",
    [pytest.param("delete", id="deleted"), pytest.param("empty", id="empty")],
)
def test_connection_kwargs_missing_env_var_named_in_error(
    stub_dotenv: MagicMock,
    postgres_env: None,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    missing_var: str,
) -> None:
    # A set-but-empty variable is deliberately treated as missing — an
    # empty host or password cannot connect.
    if mode == "delete":
        monkeypatch.delenv(missing_var, raising=False)
    else:
        monkeypatch.setenv(missing_var, "")

    with pytest.raises(RuntimeError, match=missing_var):
        pgconn.connection_kwargs("mydb", "catalog")


def test_connection_kwargs_all_env_vars_missing_names_every_one(
    stub_dotenv: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in pgconn.ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        pgconn.connection_kwargs("mydb", "catalog")

    for name in pgconn.ENV_VARS:
        assert name in str(excinfo.value)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param("Prod", id="mixed_case"),
        pytest.param("PROD", id="uppercase"),
        pytest.param("1prod", id="leading_digit"),
        pytest.param("my schema", id="space"),
        pytest.param("catalog' -c foo='x", id="quote"),
        pytest.param("catalog -c search_path=public", id="option_injection"),
        pytest.param("bad-name", id="hyphen"),
        pytest.param("catalog;drop", id="semicolon"),
        pytest.param("", id="empty"),
    ],
)
def test_connection_kwargs_invalid_schema_raises(
    stub_dotenv: MagicMock, postgres_env: None, schema: str
) -> None:
    # The schema is interpolated into the libpq options string, so
    # anything outside [a-z_][a-z0-9_]* is a config error — closing the
    # option-injection surface, the mixed-case search_path folding
    # failure mode, and the invalid leading digit.
    with pytest.raises(ValueError, match="Invalid `schema` config value"):
        pgconn.connection_kwargs("mydb", schema)


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param("catalog", id="lowercase"),
        pytest.param("_private", id="leading_underscore"),
        # Pins the accepting side of the leading-digit boundary that
        # `1prod` pins above: digits are legal after the first
        # character, so a real schema like `catalog_v2` must pass.
        pytest.param("catalog_v2", id="trailing_digit"),
    ],
)
def test_connection_kwargs_valid_lowercase_schema_passes(
    stub_dotenv: MagicMock, postgres_env: None, schema: str
) -> None:
    result = pgconn.connection_kwargs("mydb", schema)

    assert result["options"] == f"-c search_path={schema}"


def test_connection_kwargs_reads_dotenv_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `.env` values arrive via load_dotenv planting them in the process
    # environment; a fake load_dotenv proves connection_kwargs calls it
    # before reading the POSTGRES_* names.
    for name in pgconn.ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    # Signature-agnostic so the fake survives call-site changes (the
    # real call passes interpolate=False).
    def fake_load_dotenv(*args: object, **kwargs: object) -> None:
        monkeypatch.setenv("POSTGRES_HOST", "dotenv-host")
        monkeypatch.setenv("POSTGRES_PORT", "6543")
        monkeypatch.setenv("POSTGRES_USER", "dotenv-user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "dotenv-pass")

    monkeypatch.setattr(pgconn_module, "load_dotenv", fake_load_dotenv)

    result = pgconn.connection_kwargs("mydb", "catalog")

    assert result["host"] == "dotenv-host"
    assert result["port"] == "6543"
    assert result["user"] == "dotenv-user"
    assert result["password"] == "dotenv-pass"
