-- public_hardening.sql
--
-- Database-level hardening that belongs to no single role: revokes the
-- CONNECT privilege PUBLIC holds on the database by default.
--
-- Every Postgres role inherits from PUBLIC, and PUBLIC is granted CONNECT
-- on every new database automatically. Left in place, that means any role
-- in the cluster — including ones provisioned for unrelated work — can
-- open a connection to metadata_db and see whatever the schemas expose.
-- Revoking it makes CONNECT explicit: a role reaches this database only
-- because a per-role grant script named it.
--
-- RUN THIS FIRST, before the per-role scripts in this folder. They each
-- grant CONNECT explicitly and their comments state that PUBLIC's default
-- has been revoked; run them without this and the roles still work, but
-- the database is open to every role in the cluster and nothing says so.
--
-- Re-run after every database rebuild: DROP DATABASE takes the revoke with
-- it, and the fresh database is created with PUBLIC's default restored.
--
-- Invocation (database is a psql variable, defaulting to metadata_db):
--   psql -v database=metadata_db \
--        -d metadata_db -U metadata_db_maintainer \
--        -f code/apply_ddl/grants/public_hardening.sql
--
-- Preconditions:
--   * The database exists.
--   * Run as the database owner (metadata_db_maintainer) or a superuser —
--     revoking a database-level privilege requires one of the two.
--   * Idempotent: revoking an already-revoked privilege is a no-op.

-- Abort on the first failed statement: a partially-applied hardening step
-- must not sit behind a zero exit code.
\set ON_ERROR_STOP on

-- Default for the database variable, applied only when not supplied on the
-- command line via -v. Keeps the configured database name the single
-- source of truth while letting a bare invocation work against the
-- standard name.
\if :{?database}
\else
    \set database metadata_db
\endif

-- PUBLIC's default CONNECT. Revoked so that database access is granted
-- role by role, never inherited. Each per-role script re-grants CONNECT
-- for the one role it provisions.
revoke connect on database :"database" from public;
