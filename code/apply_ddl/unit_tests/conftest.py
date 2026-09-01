"""Shared fixtures for apply_ddl unit tests."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import psycopg2
import pytest

# apply_ddl.py lives one directory up from unit_tests/. Put it on the path
# so `import apply_ddl` resolves regardless of pytest's rootdir handling.
APPLY_DDL_DIR = Path(__file__).resolve().parent.parent
if str(APPLY_DDL_DIR) not in sys.path:
    sys.path.insert(0, str(APPLY_DDL_DIR))


@pytest.fixture
def fake_cursor() -> MagicMock:
    """A mock psycopg2 cursor (the object yielded by `with conn.cursor()`)."""
    # spec= the real cursor class so typo'd/removed attribute access fails
    # loudly. The code under test only calls execute/fetchone/fetchall.
    return MagicMock(spec=psycopg2.extensions.cursor)


@pytest.fixture
def fake_conn(fake_cursor: MagicMock) -> MagicMock:
    """A mock psycopg2 connection whose cursor() context manager yields `fake_cursor`."""
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = fake_cursor
    # __exit__ returns False so exceptions raised inside `with conn.cursor()`
    # propagate rather than being swallowed — test_apply_one_rolls_back_on_error
    # depends on this.
    conn.cursor.return_value.__exit__.return_value = False
    return conn
