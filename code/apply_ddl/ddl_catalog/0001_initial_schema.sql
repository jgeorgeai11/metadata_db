-- 0001_initial_schema.sql
--
-- Initial DDL for metadata_db: 9 main tables, 9 _hstry mirrors, the
-- load_audit table, foreign keys, CHECK constraints, and indexes. See
-- SCHEMA.md for column-level documentation.
--
-- The ddl_versions tracking table is managed by apply_ddl.py and is
-- NOT created here. apply_ddl.py wraps each migration in its own
-- transaction; this file contains DDL statements only.
--
-- Identity is venue-free: ids never contain a system name. ID columns
-- are typed `ltree` (the dotted `database.schema.table.column` paths
-- are hierarchical label paths). This enables GiST-indexed
-- subtree/ancestor queries (`<@`, `@>`, `lquery`) for end users. ltree
-- labels accept `[A-Za-z0-9_-]` (hyphens require PostgreSQL 16+); the
-- loader's validate_identifier_segment enforces the stricter lowercase
-- subset `[a-z0-9_-]`, so every assembled ID is a legal ltree value.
-- Venue-dependent facts (which system hosts a table, under what
-- physical names) live exclusively in `deployment_tables`.
--
-- Required documentation prose is NOT NULL (the DB backstop to the
-- loader's required-field checks — see the schema reference's DB-level backstops
-- inventory): all five `description`s (systems, data_sources, schemas,
-- tables, columns), concepts' `definition`, `data_sources.owner`, and
-- both ltree[] columns (`column_mappings.target_tables_referenced`,
-- `concepts.related_object_ids` — the loader always writes an array;
-- "none" is the empty array, never NULL). `deployment_tables` is a
-- pure-facts table: every column NOT NULL, no freeform columns.
--
-- Declarative DB-level backstops (single-table CHECKs / unique indexes —
-- the DDL says what it can say cheaply so a row assembled by any non-loader
-- route cannot disagree; existence rules that need cross-table or
-- variable-depth reasoning stay loader-owned). Enforced here, per table:
--   * Hierarchy consistency (schemas/tables/columns): the stored parent FK
--     equals the id's leading label(s) — the principle already on
--     deployment_tables, now extended to the three tables it depends on.
--   * Leaf-name redundancy (schemas/tables/columns): schema_name /
--     table_name / column_name equals the id's last label.
--   * Lowercase identity (see the schema reference): every ltree id (systems, data_sources,
--     schemas, tables, columns, concepts) and every deployment_tables
--     physical_*_name equals its own lower() — case-variant manual inserts
--     would otherwise break plain-equality joins and the physical-address
--     uniqueness key.
--   * update_reason pairing (every authored main table): update_reason is
--     non-null exactly on updates (insert_ts <> update_ts) — the pairing
--     already backstopped for validated / validated_ts.
--   * concept_id shape: nlevel between 3 and 6 (data-source, schema,
--     table, and column anchors) with the reserved `concept` segment
--     second-to-last (anchor *existence* is a wave-2 loader check).
--   * load_audit.loaded_ts UNIQUE: one audit row per timestamp, so the
--     lineage join and _hstry correlation cannot fan out.
--   * table_relationships unordered-pair uniqueness: both orientations of
--     one table pair + relationship_name cannot coexist (the invariant the
--     reverse-cardinality reading depends on).
-- The `*_hstry` mirrors deliberately gain none of these (see the in-place
-- comments on each mirror): history legitimately holds superseded values.


-- Platform requirement: PostgreSQL 16 or newer. The corpus identifier
-- charset permits hyphens in ltree labels, which ltree supports only from
-- PostgreSQL 16. Assert the server version up front so a too-old server
-- fails cleanly at apply time with a named requirement, instead of months
-- later mid-load with a raw ltree syntax error on the first hyphenated
-- label. server_version_num is the integer form (e.g. 160003 for 16.3).
do $$
begin
    if current_setting('server_version_num')::int < 160000 then
        raise exception
            'metadata_db requires PostgreSQL 16 or newer (hyphenated ltree labels); server_version_num = %',
            current_setting('server_version_num');
    end if;
end
$$;


create extension if not exists ltree;


-- Main tables

-- Registry of queryable platforms (venues). Referenced only by
-- deployment_tables — no catalog id contains a system name.
create table systems (
    system ltree primary key,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- Lowercase-identity backstop (identity invariant; see the schema reference): the loader
    -- lowercases every id, but a manual case-variant insert would break the
    -- plain-equality joins that assume canonical casing.
    check (system::text = lower(system::text)),
    -- update_reason pairing: present exactly on updates (insert_ts <>
    -- update_ts), NULL on a fresh insert — the same loader-managed pairing
    -- already backstopped for validated / validated_ts.
    check ((update_reason is null) = (insert_ts = update_ts))
);

-- data_source_id is the catalog label: a single ltree segment, globally
-- unique, chosen at authoring time. It defaults to the physical database
-- name but need not equal it — per-venue physical names live in
-- deployment_tables. The loader additionally enforces that no label
-- equals a systems.system value, keeping single-segment ids unambiguous.
create table data_sources (
    data_source_id ltree primary key,
    owner text not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- Lowercase-identity backstop; see systems.
    check (data_source_id::text = lower(data_source_id::text)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);

create table schemas (
    schema_id ltree primary key,
    data_source_id ltree not null references data_sources(data_source_id),
    schema_name text not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- Hierarchy-consistency backstop: the stored FK must equal the leading
    -- label of the id (the same principle already enforced on
    -- deployment_tables) so a non-loader route cannot disagree.
    check (data_source_id = subltree(schema_id, 0, 1)),
    -- Leaf-name redundancy: the name column must equal the id's last label.
    check (schema_name = subpath(schema_id, -1, 1)::text),
    -- Lowercase-identity backstop; see systems.
    check (schema_id::text = lower(schema_id::text)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);

create table tables (
    table_id ltree primary key,
    schema_id ltree not null references schemas(schema_id),
    table_name text not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- Hierarchy-consistency backstop; see schemas.
    check (schema_id = subltree(table_id, 0, 2)),
    -- Leaf-name redundancy; see schemas.
    check (table_name = subpath(table_id, -1, 1)::text),
    -- Lowercase-identity backstop; see systems.
    check (table_id::text = lower(table_id::text)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);

create table columns (
    column_id ltree primary key,
    table_id ltree not null references tables(table_id),
    column_name text not null,
    data_type text not null,
    is_nullable boolean not null,
    -- Records grain, not a loader-enforced constraint: a table's primary
    -- key is the set of its columns flagged is_primary_key (a composite
    -- key is simply multiple flags). Consumer knowledge — a researcher or
    -- tool derives the correct GROUP BY for an aggregate/multi-table
    -- mapping from these flags; the loader does not verify grain.
    is_primary_key boolean not null default false,
    -- Context-retrieval pointer: names the documented table that
    -- enumerates this column's value domain (e.g. a curated code set in
    -- the `ref` source). It implies no join path and carries no
    -- co-deployment semantics — a consumer resolving the column reads
    -- the referenced table's rows for what each value means, nothing
    -- more. Nullable (most columns enumerate nothing).
    -- NAMED and `deferrable initially immediate` (the deployment
    -- physical-address precedent): this is the catalog's one mutable
    -- non-PK FK, so the loader's deletes->updates->inserts phase order
    -- can violate it mid-transaction on a legal end state — an in-place
    -- columns UPDATE may point at a `tables` row INSERTed later in the
    -- same load transaction (linking a column to a same-MR ref table).
    -- The loader defers it (SET CONSTRAINTS ... DEFERRED) so it is
    -- checked once at COMMIT, when the rows have settled; every other
    -- writer still sees it enforced at statement time. The constraint
    -- name is a shared literal with the loader
    -- (db_io.COLUMNS_REF_TABLE_ID_FK_CONSTRAINT) — keep them in sync.
    ref_table_id ltree
        constraint columns_ref_table_id_fkey
        references tables(table_id)
        deferrable initially immediate,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- Hierarchy-consistency backstop; see schemas.
    check (table_id = subltree(column_id, 0, 3)),
    -- Leaf-name redundancy; see schemas.
    check (column_name = subpath(column_id, -1, 1)::text),
    -- Lowercase-identity backstop; see systems.
    check (column_id::text = lower(column_id::text)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);

-- One row per (documented table, venue): the table is materialized in
-- `system` under these physical names. Absence of a row IS the "not
-- deployed" fact — there are no tombstones. Named for its grain (one
-- row per table x venue), reserving deployment_dbs/deployment_schemas
-- for coarser-grain siblings. Authored sparse (a bare venue entry in
-- deployments.yaml means all schemas/tables, original names) and
-- expanded to explicit table-grain rows by the loader, so consumers
-- query facts and never re-implement the defaulting rules.
-- A deployment asserts table-level fidelity: every documented column
-- exists in the venue's copy under the documented column name.
-- Pure-facts table: every column NOT NULL, no freeform columns (no
-- notes/update_reason — rows are derived, so rationale lives in the git
-- commit joinable via load_audit, and caveats go through concepts; see
-- the overview's deployment_tables section).
-- data_source_id is redundant with table_id's leading segment but
-- stored for query convenience (venue-inventory queries read it
-- directly instead of parsing the prefix out of table_id).
-- Physical names are lowercase like every name in the catalog; venues
-- resolve unquoted identifiers case-insensitively, so the lowercase
-- spelling addresses the object regardless of the venue's rendering.
create table deployment_tables (
    table_id ltree not null references tables(table_id),
    system ltree not null references systems(system),
    data_source_id ltree not null references data_sources(data_source_id),
    physical_database_name text not null,
    physical_schema_name text not null,
    physical_table_name text not null,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- data_source_id is redundant with table_id's leading segment (the
    -- {database} label) — enforce the documented redundancy so a row
    -- assembled by any non-loader route cannot disagree.
    check (data_source_id = subltree(table_id, 0, 1)),
    -- Physical names are lowercase like every name in the catalog (see the
    -- table comment). The plain-equality physical-address uniqueness key
    -- below assumes canonical casing; a case-variant manual insert would
    -- silently defeat it, so enforce lowercase on each physical_*_name.
    check (physical_database_name = lower(physical_database_name)),
    check (physical_schema_name = lower(physical_schema_name)),
    check (physical_table_name = lower(physical_table_name)),
    primary key (table_id, system),
    -- One physical object, one documented identity: no two catalog
    -- tables may claim the same physical address in a venue. Named and
    -- `deferrable initially immediate` so it is checked at statement time
    -- for every writer, except that the loader's single load transaction
    -- explicitly defers it (SET CONSTRAINTS ... DEFERRED) to let a
    -- validated address swap/chain between updated rows settle at commit.
    -- The constraint name is a shared literal with the loader
    -- (db_io.DEPLOYMENT_TABLES_PHYSICAL_ADDRESS_CONSTRAINT) — keep them
    -- in sync.
    constraint deployment_tables_physical_address_key
        unique (system, physical_database_name, physical_schema_name,
                physical_table_name)
        deferrable initially immediate
);

-- A relationship records join LOGIC only — key equality between two
-- documented tables, a property of the data rather than of where it
-- sits. There is no system column: the venues where a join can run are
-- derived as the intersection of the endpoint tables' deployment sets
-- (loader-enforced non-empty, so every documented join is runnable in
-- at least one venue; consumers filter to the target venue at
-- code-generation time).
create table table_relationships (
    table_a_id ltree not null references tables(table_id),
    table_b_id ltree not null references tables(table_id),
    relationship_name text not null,
    join_condition text not null,
    -- Row correspondence read a->b (e.g. many_to_one = many table_a rows
    -- match one table_b row). NULL passes the CHECK — unknown is allowed,
    -- never guessed via a default. The reverse orientation is derivable
    -- (the unordered pair is unique per relationship_name, so each pair
    -- has one stored orientation — swap the enum's sides to read b->a).
    -- Join-type selection (inner vs. outer) is the consumer's per-query
    -- choice, informed by cardinality and notes.
    cardinality text
        check (cardinality in
            ('one_to_one', 'one_to_many', 'many_to_one', 'many_to_many')),
    use_when text,
    notes text,
    validated boolean not null default false,
    -- Loader-managed: set to now() when validated flips false->true,
    -- NULL when it flips back. Non-null exactly when validated is true.
    validated_ts timestamptz,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    primary key (table_a_id, table_b_id, relationship_name),
    check (validated = (validated_ts is not null)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);

-- A null target_expression marks a column intentionally dropped in the
-- target dataset; the rationale must be recorded in notes (enforced below).
-- There is no target_system column: the expression's own references
-- identify the target dataset, and where the equivalent is computable is
-- derived from those tables' deployments. mapping_name distinguishes
-- multiple mappings from the same source column and should say what the
-- mapping is toward (it is the only discriminator). There is no
-- source_system or source_data_source column: the source dataset is the
-- leading label of source_column_id. Mappings assert equivalence between
-- different data — "the same column at another physical address" is
-- deployment resolution, never a mapping.
create table column_mappings (
    source_column_id ltree not null references columns(column_id),
    mapping_name text not null,
    -- Loader-derived from target_expression; never NULL — an
    -- intentional-drop mapping stores the empty array (the loader always
    -- writes an array; the constraint codifies that contract).
    target_tables_referenced ltree[] not null,
    target_expression text,
    -- use_when: condition under which to prefer this mapping over the
    -- others for the same source_column_id. NULL when there is only one
    -- mapping.
    use_when text,
    notes text,
    validated boolean not null default false,
    -- Loader-managed: set to now() when validated flips false->true,
    -- NULL when it flips back. Non-null exactly when validated is true.
    validated_ts timestamptz,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    primary key (source_column_id, mapping_name),
    check (target_expression is not null or notes is not null),
    check (validated = (validated_ts is not null)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);

-- Business glossary: one row per business concept per data source,
-- schema, table, or column, captured as a freeform, never-parsed
-- definition for a researcher or tool to look up / RAG. Concepts are
-- anchored under a data source's folder — at the data-source, schema,
-- table, or column level — so concept_id is a path-derived ltree id
-- with a reserved `concept` segment. There are no FK columns and no
-- validated flag (a definition is not a verified equivalence).
create table concepts (
    concept_id ltree primary key,
    label text,
    -- Required: a definition-less concept is a glossary entry with
    -- nothing to look up.
    definition text not null,
    notes text,
    -- Authored links to the catalog objects the concept is about; each
    -- entry must resolve to an existing data_sources / schemas / tables /
    -- columns / concepts PK (loader-validated, so a stale link fails the
    -- pre-merge dry-run like any FK). systems rows are not linkable —
    -- concepts are about data, and the venue registry is infrastructure.
    -- Retrieval anchor for RAG: "which concepts reference object X?" is
    -- an array-containment lookup (see idx_concepts_related_objects_gist).
    -- Optional to author, but never NULL — a concept with no links
    -- stores the empty array (the loader always writes an array).
    related_object_ids ltree[] not null,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    -- concept_id shape: the anchor is 1 to 4 labels — a data source
    -- (<database>.concept.<name>, 3 labels), a schema
    -- (<database>.<schema>.concept.<name>, 4 labels), a table
    -- (<database>.<schema>.<table>.concept.<name>, 5 labels), or a column
    -- (<database>.<schema>.<table>.<column>.concept.<name>, 6 labels).
    -- Either way the second-to-last label is the reserved `concept`
    -- segment. The variable-depth anchor's *existence* is a wave-2 loader
    -- check (a plain FK cannot express a variable-depth prefix); this
    -- backstops the shape.
    check (
        nlevel(concept_id) between 3 and 6
        and subpath(concept_id, -2, 1) = 'concept'::ltree
    ),
    -- Lowercase-identity backstop; see systems.
    check (concept_id::text = lower(concept_id::text)),
    -- update_reason pairing; see systems.
    check ((update_reason is null) = (insert_ts = update_ts))
);


-- History tables — mirror main + end_ts. PK = main PK + end_ts.

-- NOT NULL data-shape constraints carry to the mirrors (the `owner`
-- precedent): every main row satisfied them while current, so its
-- history copies do too.
create table systems_hstry (
    system ltree not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (system, end_ts)
);

create table data_sources_hstry (
    data_source_id ltree not null,
    owner text not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (data_source_id, end_ts)
);

create table schemas_hstry (
    schema_id ltree not null,
    data_source_id ltree not null,
    schema_name text not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (schema_id, end_ts)
);

create table tables_hstry (
    table_id ltree not null,
    schema_id ltree not null,
    table_name text not null,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (table_id, end_ts)
);

create table columns_hstry (
    column_id ltree not null,
    table_id ltree not null,
    column_name text not null,
    data_type text not null,
    is_nullable boolean not null,
    -- Mirror of columns.is_primary_key; no default — a history row always
    -- carries the value copied from the superseded main-table row.
    is_primary_key boolean not null,
    -- Mirror of columns.ref_table_id; no FK per the hstry convention
    -- (history legitimately holds links to since-deleted tables).
    ref_table_id ltree,
    description text not null,
    notes text,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (column_id, end_ts)
);

create table deployment_tables_hstry (
    table_id ltree not null,
    system ltree not null,
    data_source_id ltree not null,
    physical_database_name text not null,
    physical_schema_name text not null,
    physical_table_name text not null,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    -- No physical-address uniqueness on the mirror: history legitimately
    -- holds superseded addresses that later rows reuse.
    primary key (table_id, system, end_ts)
);

create table table_relationships_hstry (
    table_a_id ltree not null,
    table_b_id ltree not null,
    relationship_name text not null,
    join_condition text not null,
    -- No CHECK on the mirror (matching the validated_ts precedent below):
    -- history holds whatever value the superseded main-table row carried.
    cardinality text,
    use_when text,
    notes text,
    validated boolean not null,
    -- No CHECK on the mirror: history legitimately holds both validated
    -- and unvalidated prior versions.
    validated_ts timestamptz,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (table_a_id, table_b_id, relationship_name, end_ts)
);

create table column_mappings_hstry (
    source_column_id ltree not null,
    mapping_name text not null,
    target_tables_referenced ltree[] not null,
    target_expression text,
    use_when text,
    notes text,
    validated boolean not null,
    -- No CHECK on the mirror: history legitimately holds both validated
    -- and unvalidated prior versions.
    validated_ts timestamptz,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (source_column_id, mapping_name, end_ts)
);

create table concepts_hstry (
    concept_id ltree not null,
    label text,
    definition text not null,
    notes text,
    related_object_ids ltree[] not null,
    update_reason text,
    insert_ts timestamptz not null,
    update_ts timestamptz not null,
    end_ts timestamptz not null,
    primary key (concept_id, end_ts)
);


-- Audit / lineage: one row per successful real loader run (written
-- inside the load transaction). loaded_ts == the run's now() == the
-- insert_ts/update_ts of every row it wrote, so a catalog row joins to
-- the commit that produced it via load_audit.loaded_ts = <row>.update_ts.
-- load_id is GENERATED ALWAYS AS IDENTITY so the loader role needs only
-- table-level INSERT (no sequence grant).
create table load_audit (
    load_id bigint generated always as identity primary key,
    commit_sha text not null,
    inserts integer not null,
    updates integer not null,
    deletes integer not null,
    reset_hstry boolean not null default false,
    loaded_ts timestamptz not null default now()
);

-- Backs both documented load_audit query patterns: the lineage join
-- (load_audit.loaded_ts = <row>.update_ts / insert_ts / end_ts) and the
-- drift check's ORDER BY loaded_ts DESC LIMIT 1. UNIQUE because both the
-- lineage join and the _hstry correlation assume exactly one audit row per
-- timestamp — every row a run writes carries that run's single now() as its
-- insert_ts/update_ts/end_ts. Making it UNIQUE turns the assumption into an
-- enforced error instead of a silent join fan-out (a real loader run writes
-- at most one load_audit row, so this never fires on the write path).
create unique index idx_load_audit_loaded_ts on load_audit(loaded_ts);


-- Indexes for common query paths.
-- FK columns get btree indexes so equality joins back to parents stay
-- fast (ltree supports the btree operator class for `=`/ordering).

create index idx_schemas_data_source_id on schemas(data_source_id);
create index idx_tables_schema_id on tables(schema_id);
create index idx_columns_table_id on columns(table_id);

-- FK column ref_table_id gets its own btree index (per the "FK columns
-- get btree indexes" rule) so "which columns point at this ref table?"
-- and the FK's delete-time referential checks stay index-served.
create index idx_columns_ref_table_id on columns(ref_table_id);

-- Venue-inventory queries: "what data sources are deployed in system X?"
-- and per-source deployment listings.
create index idx_deployment_tables_system_data_source
    on deployment_tables(system, data_source_id);

-- FK column data_source_id gets its own btree index: the composite index
-- above leads with `system`, so it does not serve equality joins that key
-- on data_source_id alone (per the "FK columns get btree indexes" rule).
create index idx_deployment_tables_data_source_id
    on deployment_tables(data_source_id);

-- Composite index supporting "find relationships involving table B".
create index idx_table_relationships_b
    on table_relationships(table_b_id, table_a_id);

-- Unordered-pair uniqueness: both orientations of the same table pair under
-- one relationship_name cannot coexist. The reverse-cardinality reading
-- (swap the enum's sides to read b->a) depends on each unordered pair having
-- exactly one stored orientation per relationship_name; this makes that a
-- DB-enforced invariant instead of a loader-only one. LEAST/GREATEST over
-- the ltree endpoints canonicalize the pair regardless of authored order.
create unique index idx_table_relationships_unordered_pair
    on table_relationships (
        least(table_a_id, table_b_id),
        greatest(table_a_id, table_b_id),
        relationship_name
    );

-- No standalone btree index on column_mappings.source_column_id: it is
-- the leading prefix of the (source_column_id, mapping_name) primary
-- key, so the PK's own index already serves equality lookups on it
-- (same reasoning as the no-separate-history-indexes note below).

-- GiST indexes on the primary ID columns support ltree hierarchical
-- operators (`<@` descendant, `@>` ancestor, `~` lquery) — e.g. "every
-- column under one schema" via `column_id <@ '<database>.<schema>'`.
-- btree cannot serve these operators, so a separate GiST index is needed.
-- (systems gets no GiST index: its ids are single labels with no
-- hierarchy beneath them.)
create index idx_data_sources_id_gist on data_sources using gist (data_source_id);
create index idx_schemas_id_gist on schemas using gist (schema_id);
create index idx_tables_id_gist on tables using gist (table_id);
create index idx_columns_id_gist on columns using gist (column_id);
-- Schema-level availability: "which venues carry anything under
-- <database>.<schema>?" via `table_id <@ '<database>.<schema>'`.
create index idx_deployment_tables_table_id_gist
    on deployment_tables using gist (table_id);
create index idx_table_relationships_a_gist
    on table_relationships using gist (table_a_id);
create index idx_column_mappings_source_col_gist
    on column_mappings using gist (source_column_id);
-- Supports subtree/lquery lookups over the concept hierarchy, e.g.
-- "every concept under a data source" via `concept_id <@ '<database>'`.
create index idx_concepts_id_gist on concepts using gist (concept_id);

-- Lineage / impact-analysis over target_tables_referenced (an ltree[]):
-- the gist__ltree_ops operator class indexes the ltree[]-vs-ltree/lquery
-- operators (`<@`, `@>`, `~`, `?`) — NOT the generic anyarray
-- containment forms (`@> '{...}'::ltree[]`, `<@ ...::ltree[]`, `&&`),
-- which no GiST opclass serves and which therefore scan at catalog
-- scale. Example: "which mappings reference table X (or anything under
-- it)?" runs index-served via
-- `target_tables_referenced <@ '<database>.<schema>.<table>'::ltree`.
create index idx_column_mappings_target_tables_gist
    on column_mappings using gist (target_tables_referenced gist__ltree_ops);

-- Concept-to-object retrieval over related_object_ids (an ltree[]): the
-- gist__ltree_ops operator class indexes the ltree[]-vs-ltree/lquery
-- operators (`<@`, `@>`, `~`, `?`) — a plain `using gist (...)` will NOT
-- serve them. Example: "which concepts reference object X (or anything
-- under it)?" runs index-served via
-- `related_object_ids <@ '<database>.<schema>.<table>'::ltree`.
-- Exact-membership via the anyarray form
-- (`related_object_ids @> '{...}'::ltree[]`) is also correct but is a
-- generic array operator no GiST opclass serves.
create index idx_concepts_related_objects_gist
    on concepts using gist (related_object_ids gist__ltree_ops);

-- No separate history indexes. The typical query "show me the history of
-- this row" filters on each _hstry table's main PK column(s) — which are
-- the leading prefix of the composite primary key (main PK + end_ts).
-- Postgres already serves leading-prefix lookups from the PK's own index,
-- so a standalone index on those columns would be redundant.


-- Object descriptions (surfaced via pg_catalog.obj_description()). Kept in
-- the DDL so a from-scratch rebuild recreates them automatically. Every
-- comment target here is created above by this migration — ddl_versions is
-- apply_ddl.py's own table, so its comment lives in apply_ddl.py's
-- bootstrap (commenting it here would make 0001 unapplyable through any
-- other path).
--
-- Table names are unqualified so they resolve via the connection's
-- search_path to whatever schema this migration is applied into (see
-- apply_ddl.py). There is no `comment on schema` here: a schema-level
-- comment needs the literal schema name, which would defeat the goal of a
-- schema-agnostic 0001 — that description lives in
-- MAINTAINING.md instead.

-- Venue registry and catalog hierarchy (ltree ids encode the parent path)
comment on table systems is
    'Registry of queryable platforms (venues) data can be hosted in — one row per system, keyed by an ltree label. Referenced only by deployment_tables';

comment on table data_sources is
    'One row per documented dataset, keyed by a globally unique catalog label, with the owning steward team. Parent of schemas';

comment on table schemas is
    'One row per schema within a data source (data_source_id -> schema_id). Parent of tables';

comment on table tables is
    'One row per table within a schema (schema_id -> table_id). Parent of columns and the endpoints of table_relationships';

comment on table columns is
    'One row per column within a table (table_id -> column_id) with column_name, data_type, is_nullable, is_primary_key (the table''s PK is the set of flagged columns), an optional ref_table_id domain pointer, and description';

comment on column columns.ref_table_id is
    'Optional pointer to the documented table that enumerates this column''s value domain (e.g. a curated code set in the ref source) — for context retrieval only: it implies no join path and carries no co-deployment semantics';

-- Venue residency
comment on table deployment_tables is
    'One row per (documented table, system): the venue the table is materialized in and its physical database/schema/table names there. The single home of venue-dependent truth — absence of a row means not deployed. Pure facts: every column NOT NULL, no freeform columns; named for its table x venue grain';

-- Relationships and lineage
comment on table table_relationships is
    'Join definitions between two documented tables (table_a_id, table_b_id, relationship_name) — join_condition, cardinality (a->b row correspondence; NULL = not yet recorded), use_when, and a validated flag (validated_ts). The venues where a join runs are derived from the endpoints'' deployments';

comment on table column_mappings is
    'Column-level equivalence mappings — for a source_column_id, a named (mapping_name) target_expression over another dataset''s columns that produces its equivalent, with use_when selection guidance and a validated flag (validated_ts)';

-- Business glossary (anchored under a data source at the data-source,
-- schema, table, or column level)
comment on table concepts is
    'Business glossary — one row per business concept per data source, schema, table, or column, with a freeform definition for lookup / RAG and related_object_ids (ltree[] links to the catalog objects the concept is about)';

-- Row-versioned history (one *_hstry per current-state table): mirrors the
-- base table's columns plus end_ts, the moment that version stopped being
-- current. The live version stays in the base table; superseded versions
-- accumulate here.
comment on table systems_hstry is
    'Row-versioned history of systems — one row per superseded version, mirroring systems plus end_ts (when that version stopped being current)';

comment on table data_sources_hstry is
    'Row-versioned history of data_sources — one row per superseded version, mirroring data_sources plus end_ts (when that version stopped being current)';

comment on table schemas_hstry is
    'Row-versioned history of schemas — one row per superseded version, mirroring schemas plus end_ts (when that version stopped being current)';

comment on table tables_hstry is
    'Row-versioned history of tables — one row per superseded version, mirroring tables plus end_ts (when that version stopped being current)';

comment on table columns_hstry is
    'Row-versioned history of columns — one row per superseded version, mirroring columns plus end_ts (when that version stopped being current)';

comment on table deployment_tables_hstry is
    'Row-versioned history of deployment_tables — one row per superseded version, mirroring deployment_tables plus end_ts (when that version stopped being current)';

comment on table table_relationships_hstry is
    'Row-versioned history of table_relationships — one row per superseded version, mirroring table_relationships plus end_ts (when that version stopped being current)';

comment on table column_mappings_hstry is
    'Row-versioned history of column_mappings — one row per superseded version, mirroring column_mappings plus end_ts (when that version stopped being current)';

comment on table concepts_hstry is
    'Row-versioned history of concepts — one row per superseded version, mirroring concepts plus end_ts (when that version stopped being current)';

-- Operational / load lifecycle (ddl_versions is commented in
-- apply_ddl.py's bootstrap, alongside its CREATE)
comment on table load_audit is
    'Loader-run audit — one row per load with commit_sha, insert/update/delete counts, a reset_hstry flag, and loaded_ts';
