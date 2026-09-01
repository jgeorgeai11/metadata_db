-- 0001_ref_initial.sql
--
-- Initial DDL for the physical `reference` schema (the ref stream's
-- curated code sets): the first curated code-set table (entity_type_cd)
-- and the ref_load_audit freshness ledger. Applied by apply_ddl.py under
-- its own config (code/apply_ddl/config/apply_ddl_ref.toml) into the
-- metadata_db Postgres — the same instance that hosts the catalog — so
-- the ref migration stream keeps an independent ddl_versions ledger
-- (apply_ddl auto-creates one per schema).
--
-- Design (activity 20260729v01, Decisions #1/#2/#4):
--   * One real typed table per curated code set — heterogeneous attributes
--     stay typed columns instead of flattening into a generic code_values
--     model. The tables exist for CONTEXT RETRIEVAL (a human or LLM resolving
--     what a code means), not for runtime joins: no relationships, mappings,
--     or venue materialization are documented against them.
--   * Each table is seeded from a git-versioned CSV
--     (data_ref/<schema>/<table>.csv, the folder naming the table's
--     documented schema, header = the table's columns) by
--     code/load_ref_data/load_ref_data.py via truncate-and-reload. Git/PR review is the
--     change discipline; there is no per-row history — git history replaces _hstry.
--   * The catalog documents these tables as an ordinary data source
--     (data_catalog/sources/ref/), and the loader's consistency gate keeps the docs
--     mechanically equal to the DDL/CSV columns.
--
-- Like the catalog's 0001, this file is schema-agnostic: every statement is
-- unqualified and lands in the schema named by the config's search_path.


-- The first curated code set: NPPES entity type codes from the public
-- NPPES Data Dissemination file layout. Codes are stored as text (they are
-- codes, not quantities — preserving any future non-numeric values and
-- matching how consumers filter). The source layout carries no effective /
-- obsolete dating, so no date columns exist — add them in a later migration
-- if a dated source ever appears (never backfill guesses).
create table entity_type_cd (
    -- The entity type code value itself: '1' or '2'.
    code text primary key,
    -- The layout's description for the code.
    description text not null,
    -- Freeform caveats about this code beyond the source description.
    notes text
);

comment on table entity_type_cd is
    'Curated code set: NPPES entity type codes — the two kinds of enumerated provider (1 individual, 2 organization), from the public NPPES Data Dissemination file layout. Context retrieval only; seeded from data_ref/codes/entity_type_cd.csv (git is the history)';

comment on column entity_type_cd.code is
    'The entity type code value (stored as text — codes, not quantities)';
comment on column entity_type_cd.description is
    'The NPPES layout''s description for the code';
comment on column entity_type_cd.notes is
    'Freeform caveats about this code beyond the source description';

-- Freshness ledger — the ref analogue of the catalog's load_audit,
-- deliberately thinner: one append-only row per table per loader run,
-- carrying the loaded CSV's content hash. `load_ref_data.py --check`
-- compares each data_ref/<schema>/*.csv hash against the latest row here
-- to detect a stale or never-loaded table (main ahead of the DB). No
-- per-row lineage: git is the row history.
create table ref_load_audit (
    -- Identity PK: makes audit rows individually addressable and gives
    -- the latest-row freshness query a deterministic tiebreaker
    -- (`order by loaded_ts desc, audit_id desc`) — two loads of one
    -- table inside a single transaction share a now() timestamp, so
    -- loaded_ts alone cannot order them. GENERATED ALWAYS AS IDENTITY
    -- like the catalog's load_audit.load_id (no sequence grant needed).
    -- Not a (table_name, loaded_ts) composite: those same-transaction
    -- runs would collide; the identity column is immune.
    audit_id bigint generated always as identity primary key,
    table_name text not null,
    csv_sha256 text not null,
    row_count integer not null,
    loaded_ts timestamptz not null default now()
);

comment on table ref_load_audit is
    'Freshness ledger for the ref loader — one append-only row per table per load_ref_data.py run (CSV content hash, row count, loaded_ts). Exists for freshness detection (--check); no per-row lineage — git is the row history';
