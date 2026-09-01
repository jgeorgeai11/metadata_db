-- metadata_db_ci.sql
--
-- The complete privilege model for metadata_db_ci — the loader's WRITE CI
-- account, used only by the post-merge main-branch workflow
-- (.github/workflows/post_merge.yml). One file per role: everything this
-- identity can reach, in both schemas, is here.
--
-- Restores the model after a database rebuild (DROP DATABASE drops all
-- database-, schema-, and table-level grants; the LOGIN role itself is
-- cluster-level and survives).
--
-- Privilege model
--   database  CONNECT.
--   catalog   USAGE on the schema; DML on the 9 main tables
--             (deployment_tables is the venue-residency table); INSERT-only
--             on the 9 _hstry tables (no UPDATE/DELETE/TRUNCATE); SELECT on
--             ddl_versions; SELECT+INSERT on load_audit.
--   reference USAGE + SELECT only — ref loading is maintainer-manual, so
--             the loader role gets no DML here.
-- No DDL is ever granted. The INSERT-only history grants make _hstry
-- append-only for this role specifically; they do not prevent an
-- owner/superuser from mutating those tables.
--
-- The catalog table set is enumerated rather than schema-wide, and that is
-- deliberate: it is what keeps _hstry INSERT-only and excludes nothing by
-- accident. Canonical set defined in
-- code/apply_ddl/ddl_catalog/0001_initial_schema.sql — a migration that
-- adds a main table must add it to BOTH catalog grant lists below, and to
-- metadata_db_ci_ro.sql. The reference grants are schema-wide plus default
-- privileges, so a new ref code set needs no edit here.
--
-- Invocation (schema and database names are psql variables, defaulting to
-- catalog / reference / metadata_db; pass -v explicitly to match the
-- configured knobs in code/apply_ddl/config/):
--   psql -v catalog_schema=catalog -v ref_schema=reference \
--        -v database=metadata_db \
--        -d metadata_db -U metadata_db_maintainer \
--        -f code/apply_ddl/grants/metadata_db_ci.sql
--
-- Preconditions:
--   * The metadata_db_ci role already exists (a cluster-level LOGIN role;
--     it survives a database rebuild, but on first activation it must be
--     created first — see the maintenance doc's activation checklist).
--     Grants to a nonexistent role fail.
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
-- database is revoked (public_hardening.sql), so the loader role must be
-- granted CONNECT explicitly — without it the role cannot open a
-- connection at all, regardless of its table grants.
grant connect on database :"database" to metadata_db_ci;

-- --- schema: catalog --------------------------------------------------
-- Resolve unqualified table names deterministically to the configured
-- catalog schema, independent of the maintainer's ambient search_path.
set search_path = :"catalog_schema";

-- Schema-level USAGE. Required before any table grant in the schema
-- resolves — without USAGE on the schema the role cannot reference the
-- objects inside it even with table privileges.
grant usage on schema :"catalog_schema" to metadata_db_ci;

grant select, insert, update, delete on
    systems,
    data_sources,
    schemas,
    tables,
    columns,
    deployment_tables,
    table_relationships,
    column_mappings,
    concepts
to metadata_db_ci;

-- INSERT only: the history mirrors are append-only for this role.
grant insert on
    systems_hstry,
    data_sources_hstry,
    schemas_hstry,
    tables_hstry,
    columns_hstry,
    deployment_tables_hstry,
    table_relationships_hstry,
    column_mappings_hstry,
    concepts_hstry
to metadata_db_ci;

grant select on ddl_versions to metadata_db_ci;

grant select, insert on load_audit to metadata_db_ci;

-- --- schema: reference ------------------------------------------------
-- Read-only context. Schema-qualified rather than search_path-resolved,
-- since the catalog schema is what search_path points at above.
grant usage on schema :"ref_schema" to metadata_db_ci;

-- SELECT on every existing table: the code-set tables, the freshness
-- ledger (ref_load_audit), and the ref migration ledger (ddl_versions).
-- Schema-wide instead of a per-table list so this never needs editing when
-- a migration adds a code set.
grant select on all tables in schema :"ref_schema" to metadata_db_ci;

-- Future tables: any table a later ref migration creates is SELECT-able at
-- creation — no re-run of this script, no per-table grant step in the
-- new-code-set runbook. Scoped `for role` to the maintainer because
-- default privileges attach to the CREATING role, and all ref DDL is
-- applied by metadata_db_maintainer.
alter default privileges for role metadata_db_maintainer in schema :"ref_schema"
    grant select on tables to metadata_db_ci;
