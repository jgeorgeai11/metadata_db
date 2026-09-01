-- mcp_ro_metadata.sql
--
-- The complete privilege model for mcp_ro_metadata — the read-only account
-- behind the metadata_db MCP server instance (query / BI access; not used
-- by CI). One file per role: everything this identity can reach, in both
-- schemas, is here.
--
-- This is the DB-enforced backing for the server's read-only guarantee: the
-- role holds SELECT and nothing else, so run_sql cannot write no matter
-- what SQL a client sends. Keep this file in sync with the live grants.
--
-- Restores the model after a database rebuild (DROP DATABASE drops all
-- database-, schema-, and table-level grants; the LOGIN role itself is
-- cluster-level and survives). Note the MCP server is operated from a
-- separate repo; these grants live here because the objects they target do.
--
-- Privilege model
--   database  CONNECT.
--   catalog   USAGE on the schema; SELECT on ALL tables, plus default
--             privileges for future ones.
--   reference USAGE on the schema; SELECT on ALL tables, plus default
--             privileges for future ones.
--   session   search_path = catalog, reference (role-level, this database
--             only) — set out-of-band by a CREATEROLE role; see the
--             Preconditions below.
--
-- Deliberately broader than metadata_db_ci_ro, which is restricted to the
-- 9 main catalog tables: schema-wide SELECT here also exposes the 9 _hstry
-- mirrors and load_audit. That is intended for a query/BI identity — "when
-- did this column's description change" is a legitimate question for this
-- role and not for a PR validation job — and it is what lets a new catalog
-- table become visible with no edit to this file. Narrow it to an
-- enumerated list if the history mirrors should stay out of reach.
--
-- Invocation (schema and database names are psql variables, defaulting to
-- catalog / reference / metadata_db; pass -v explicitly to match the
-- configured knobs in code/apply_ddl/config/):
--   psql -v catalog_schema=catalog -v ref_schema=reference \
--        -v database=metadata_db \
--        -d metadata_db -U metadata_db_maintainer \
--        -f code/apply_ddl/grants/mcp_ro_metadata.sql
--
-- Preconditions:
--   * The mcp_ro_metadata role already exists. It is created out-of-band
--     because its password is a secret and is NOT stored here; for
--     reference, it is provisioned once as:
--       CREATE ROLE mcp_ro_metadata LOGIN PASSWORD '<secret>'
--         NOSUPERUSER NOCREATEDB NOCREATEROLE;
--   * The role's session search_path is set out-of-band at the same time
--     (ALTER ROLE ... SET requires CREATEROLE with ADMIN on the role, which
--     the database owner deliberately lacks — run it as the DBA-side role):
--       ALTER ROLE mcp_ro_metadata IN DATABASE metadata_db
--         SET search_path = catalog, reference;
--     Why it matters: the ltree extension is installed INTO the catalog
--     schema, so its type and operators resolve unqualified only with that
--     schema on the path — and the MCP server's run_sql is single-statement,
--     so a consumer cannot prepend `set search_path`. Without this setting,
--     every documented ltree query pattern (`table_id = 'a.b.c'`,
--     `concept_id <@ 'x'::ltree`, the concepts retrieval union in SCHEMA.md)
--     fails with "type ltree does not exist". Scoped IN DATABASE, so the
--     setting is dropped with the database: a full DB rebuild re-runs it in
--     the DBA step (see MAINTAINING.md, *Applying migrations*); a
--     schema-scoped rebuild leaves it intact. New sessions only — restart
--     the MCP server (or let its pool recycle) after setting it.
--   * Run AFTER apply_ddl.py has applied BOTH migration streams — every
--     grant target must already exist. This file spans both schemas, so
--     running it with only the catalog stream applied aborts at the
--     reference block (ON_ERROR_STOP) with the catalog grants already
--     made. That is safe: grants are additive and idempotent, so apply the
--     ref migrations and re-run the whole file.
--   * Run public_hardening.sql first — the CONNECT grant below assumes
--     PUBLIC's default CONNECT has been revoked.
--   * Run as the database owner (metadata_db_maintainer) or a superuser.
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
-- database is revoked (public_hardening.sql), so this role must be granted
-- CONNECT explicitly — without it the role cannot open a connection at all,
-- regardless of its table grants. Revoking PUBLIC's default is also what
-- makes role-driven list_databases show only the databases this instance is
-- actually granted.
grant connect on database :"database" to mcp_ro_metadata;

-- --- schema: catalog --------------------------------------------------
grant usage on schema :"catalog_schema" to mcp_ro_metadata;
grant select on all tables in schema :"catalog_schema" to mcp_ro_metadata;

-- --- schema: reference ------------------------------------------------
grant usage on schema :"ref_schema" to mcp_ro_metadata;
grant select on all tables in schema :"ref_schema" to mcp_ro_metadata;

-- Future tables created by the owner are covered automatically, in both
-- schemas. This MUST name the owning role (metadata_db_maintainer):
-- ALTER DEFAULT PRIVILEGES only applies to objects created by the named
-- role, and all DDL in both streams is applied by the maintainer. Repeated
-- per schema because default privileges are scoped to one schema each.
alter default privileges for role metadata_db_maintainer in schema :"catalog_schema"
    grant select on tables to mcp_ro_metadata;
alter default privileges for role metadata_db_maintainer in schema :"ref_schema"
    grant select on tables to mcp_ro_metadata;
