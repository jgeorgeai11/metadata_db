"""Shared Postgres connection-kwargs helper for the metadata-db tools.

One definition of how every tool in this repo connects to Postgres:
credentials come from the four `POSTGRES_*` environment variables —
exported in the environment (how CI supplies them) or, for local
development, loaded from `.env`; an already-exported variable wins,
since `load_dotenv` does not override. The target database and schema
come from each tool's TOML config, and the schema is applied as
`options=-c search_path=<schema>` so a tool's SQL stays
schema-unqualified and lands in the configured schema.

Lives in `code/lib` beside `logconfig` — the sanctioned shared layer —
so `apply_ddl`, the catalog loader (`db_io`), and `load_ref_data` all
import one copy through the `sys.path.insert(... / "code" / "lib")`
preamble they already carry, instead of maintaining three drifting
duplicates.
"""

import os
import re

from dotenv import load_dotenv

# Env var names expected in the environment or the `.env` file.
ENV_VARS = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)

# Allowed shape of the `schema` config knob: a plain lowercase Postgres
# identifier. The schema is interpolated raw into the libpq
# `options=-c search_path={schema}` string, where spaces separate `-c`
# options — so the restriction removes the option-injection surface
# (spaces/quotes could smuggle extra -c options) and the mixed-case
# failure mode: Postgres folds an unquoted search_path to lowercase, so
# `Prod` would never resolve (while a quoting writer like apply_ddl's
# `ensure_schema` would create a literal "Prod" schema — one schema
# created, a different, nonexistent one written to). A leading digit is
# excluded because it is not a valid unquoted identifier. Validating
# here makes a malformed schema fail loudly at config time. The pattern
# is anchored so the compiled object encodes that whole-string shape on
# its own: it is re-exported as a contract constant, and an unanchored
# pattern would let a consumer reaching for `.match()`/`.search()`
# accept the very values described above as excluded.
SCHEMA_NAME_RE = re.compile(r"\A[a-z_][a-z0-9_]*\Z")


def connection_kwargs(database: str, schema: str) -> dict[str, str]:
    """Build psycopg2 connection kwargs from the env plus a DB + schema.

    Reads the `POSTGRES_*` components from the process environment,
    which is authoritative: `.env` is loaded first (literal values — no
    shell expansion, so secrets containing `$` survive intact) but only
    fills in names that are not already exported, because `load_dotenv`
    does not override. So a variable exported in the environment (how
    CI supplies these) wins over an edited `.env` entry, and no `.env`
    file need exist at all. Sets `options=-c search_path=<schema>` so
    every statement on the connection resolves unqualified names to
    `schema`.
    Pointing search_path at a not-yet-existing schema at connect time is
    harmless: it is evaluated per statement, so a caller (like
    apply_ddl) may create the schema after connecting.

    Args:
        database: Target database name (not a secret; from the config).
        schema: Target Postgres schema all objects live in (from
            config). Must match `[a-z_][a-z0-9_]*` — it is interpolated
            into the libpq options string (see `SCHEMA_NAME_RE`).

    Returns:
        Mapping of psycopg2 connection keyword arguments.

    Raises:
        ValueError: If `schema` is not a plain lowercase identifier.
        RuntimeError: If any required `POSTGRES_*` env var is missing
            or empty.
    """
    if not SCHEMA_NAME_RE.fullmatch(schema):
        raise ValueError(
            f"Invalid `schema` config value {schema!r}: must match "
            f"[a-z_][a-z0-9_]* (a plain lowercase identifier) — it is "
            f"interpolated into the connection's search_path options "
            f"string, and Postgres folds an unquoted search_path to "
            f"lowercase"
        )
    # interpolate=False keeps `.env` values literal — python-dotenv's
    # default expands `${VAR}` references, which would silently rewrite
    # secrets containing `${`.
    load_dotenv(interpolate=False)
    missing = [name for name in ENV_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing or empty required env var(s) in environment/.env: "
            f"{missing}"
        )
    return {
        "host": os.environ["POSTGRES_HOST"],
        "port": os.environ["POSTGRES_PORT"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "dbname": database,
        "options": f"-c search_path={schema}",
    }
