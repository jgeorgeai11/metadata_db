"""Shared fixtures for load_ref_data unit tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# load_ref_data modules live one directory up from unit_tests/. Put that
# directory on the path so `import load_ref_data` resolves regardless of
# pytest's rootdir handling (mirrors the load_catalog_data conftest).
MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


@pytest.fixture
def fake_cursor() -> MagicMock:
    """A mock psycopg2 cursor (the object yielded by `with conn.cursor()`)."""
    return MagicMock()


@pytest.fixture
def fake_conn(fake_cursor: MagicMock) -> MagicMock:
    """A mock psycopg2 connection whose cursor() context manager yields
    `fake_cursor`."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = fake_cursor
    conn.cursor.return_value.__exit__.return_value = False
    return conn
