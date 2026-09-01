-- database_description.sql
--
-- The database-level COMMENT for metadata_db, exposed to consumers via
-- pg_catalog.shobj_description() (the MCP server's list_databases reads it).
--
-- Lives here because the object it targets does: this repo owns metadata_db's
-- DDL, so it also owns the database's description (moved from the MCP server
-- repo's code/pg_metadata/ on 2026-08-16). Not a grant, but it sits in
-- grants/ because it shares the folder's lifecycle: database-scoped,
-- destroyed by DROP DATABASE, re-applied in the same rebuild step.
--
-- A rebuild (DROP DATABASE) drops the description with the database, so this
-- runs in the rebuild sequence alongside the grant scripts (see
-- MAINTAINING.md, *Applying migrations*). Idempotent: COMMENT ON overwrites.
--
-- Invocation (database name is a psql variable, defaulting to metadata_db):
--   psql -v database=metadata_db -d metadata_db -U metadata_db_maintainer \
--        -f code/apply_ddl/grants/database_description.sql

\set ON_ERROR_STOP on

\if :{?database}
\else
    \set database metadata_db
\endif

comment on database :"database" is
    'Metadata catalog describing all analytic systems, data sources, schemas, tables, columns, table relationships, and column-level lineage mappings — with row-versioned history and a checksummed migration / load-audit lifecycle';
