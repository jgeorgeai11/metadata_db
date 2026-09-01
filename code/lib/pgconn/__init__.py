"""Shared Postgres connection helper.

Provides `connection_kwargs()` for every tool that connects to
metadata_db, plus the `ENV_VARS` / `SCHEMA_NAME_RE` contract constants.
"""

from .pgconn import ENV_VARS, SCHEMA_NAME_RE, connection_kwargs

__all__ = ["ENV_VARS", "SCHEMA_NAME_RE", "connection_kwargs"]
