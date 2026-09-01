-- metadata_db_ci_ro.sql
--
-- The complete privilege model for metadata_db_ci_ro — the READ-ONLY CI
-- account, used by the pre-merge PR jobs (.github/workflows/pre_merge.yml:
-- validate_catalog_data, check_schema_in_sync, validate_ref_data). One file
-- per role: everything this identity can reach, in both schemas, is here.
--
-- Restores the model after a database rebuild (DROP DATABASE drops all
-- database-, schema-, and table-level grants; the LOGIN role itself is
-- cluster-level and survives).
--
-- Why this role exists: the PR jobs execute unreviewed branch code, so they
-- must never hold write credentials. Splitting the read-only role out lets
-- the write role (metadata_db_ci) be confined to the post-merge
-- main-branch workflow, while the PR jobs get only what a dry-run
-- validation and a schema-sync check need. This implements the credential
-- split the maintenance doc describes.
--
-- Privilege model
--   database  CONNECT.
--   catalog   USAGE on the schema; SELECT on the 9 main tables and
--             ddl_versions. No DML (the dry-run never writes), no DDL, and
--             no access to the 9 _hstry tables or load_audit — a read-only
--             validation has no reason to read the history mirrors, so
--             they stay outside this role's reach.
--   reference USAGE + SELECT (validate_ref_data reads the live ref
--             columns; check_schema_in_sync reads reference.ddl_versions).
--
-- The catalog table set is enumerated rather than schema-wide, and that is
-- deliberate: a schema-wide grant would sweep in _hstry and load_audit.
-- Canonical set defined in
-- code/apply_ddl/ddl_catalog/0001_initial_schema.sql — a migration that
-- adds a main table must add it to the catalog list below, and to
-- metadata_db_ci.sql. The reference grants are schema-wide plus default
-- privileges, so a new ref code set needs no edit here.
--
-- Invocation (schema and database names are psql variables, defaulting to
-- catalog / reference / metadata_db; pass -v explicitly to match the
-- configured knobs in code/apply_ddl/config/):
--   psql -v catalog_schema=catalog -v ref_schema=reference \
--        -v database=metadata_db \
--        -d metadata_db -U metadata_db_maintainer \
--        -f code/apply_ddl/grants/metadata_db_ci_ro.sql
--
-- Preconditions:
--   * Create the metadata_db_ci_ro role first (a cluster-level LOGIN role;
--     see the maintenance doc's activation checklist). Table grants to a
--     nonexistent role fail.
--   * Run AFTER apply_ddl.py has applied BOTH migration streams — every
--     grant target must already exist. This file spans both schemas, so
--     running it with only the catalog stream applied aborts at the
--     reference block (ON_ERROR_STOP) with the catalog grants already
--     made. That is safe: grants are additive and idempotent, so apply the
--     ref migrations and re-run the whole file.
--   * Run public_hardening.sql first — the CONNECT grant below assumes
--     PUBLIC's default CONNECT has been revoked.
--   * Grants are additive and idempotent: there is no REVOKE-before-GRANT,
--     so this assumes a freshly-rebuilt DB. Run against a non-rebuilt DB
--     and any stale grants on these tables would persist.

-- Abort on the first failed statement: a missing role or a not-yet-created
-- target must not leave a half-applied privilege model behind a zero exit.
\set ON_ERROR_STOP on

-- Defaults for the schema / database variables, applied only when not
-- supplied on the command line via -v. Keeps the config knobs the single
-- source of truth while letting a bare invocation still work against the
-- standard names.
\if :{?catalog_schema}
\else
    \set catalog_schema catalog
\endif
\if :{?ref_schema}
\else
    \set ref_schema reference
\endif
\if :{?database}
\else
    \set database metadata_db
\endif

-- Database-level CONNECT. Required because PUBLIC's default CONNECT on the
-- database is revoked (public_hardening.sql), so the read-only role must be
-- granted CONNECT explicitly — without it the role cannot open a
-- connection at all, regardless of its table grants.
grant connect on database :"database" to metadata_db_ci_ro;

-- --- schema: catalog --------------------------------------------------
-- Resolve unqualified table names deterministically to the configured
-- catalog schema, independent of the maintainer's ambient search_path.
set search_path = :"catalog_schema";

-- Schema-level USAGE. Required before any table grant in the schema
-- resolves — without USAGE on the schema the role cannot reference the
-- objects inside it even with table privileges.
grant usage on schema :"catalog_schema" to metadata_db_ci_ro;

-- SELECT-only on the 9 main tables: the dry-run validation reads current
-- rows (e.g. the update_reason change-lifecycle discipline).
grant select on
    systems,
    data_sources,
    schemas,
    tables,
    columns,
    deployment_tables,
    table_relationships,
    column_mappings,
    concepts
to metadata_db_ci_ro;

-- Separate from the main-table grant: ddl_versions is created by
-- apply_ddl.py, not by the DDL migrations, so it is not part of the
-- canonical migration table set. Read by the sync check (apply_ddl.py
-- --check), which skips the schema/ledger bootstrap and so needs no CREATE.
grant select on ddl_versions to metadata_db_ci_ro;

-- --- schema: reference ------------------------------------------------
-- Read-only context. Schema-qualified rather than search_path-resolved,
-- since the catalog schema is what search_path points at above.
grant usage on schema :"ref_schema" to metadata_db_ci_ro;

-- SELECT on every existing table: the code-set tables, the freshness
-- ledger (load_ref_data.py --check), and the ref migration ledger
-- (apply_ddl.py --check under the ref config). Schema-wide instead of a
-- per-table list so this never needs editing when a migration adds a code
-- set.
grant select on all tables in schema :"ref_schema" to metadata_db_ci_ro;

-- Future tables: any table a later ref migration creates is SELECT-able at
-- creation — no re-run of this script, no per-table grant step in the
-- new-code-set runbook. Scoped `for role` to the maintainer because
-- default privileges attach to the CREATING role, and all ref DDL is
-- applied by metadata_db_maintainer.
alter default privileges for role metadata_db_maintainer in schema :"ref_schema"
    grant select on tables to metadata_db_ci_ro;
