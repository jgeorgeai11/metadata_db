# metadata_db — Maintenance

The metadata_db follows a **GitOps** pattern: YAML files in a Git repo are the authoritative source; Postgres is a derived artifact the loader keeps in sync with what's on the `main` branch. The loader is run by a maintainer today and by CI once a runner is registered — the invariant is the same in both modes; see [Manual workflow](#manual-workflow-when-no-ci-runner-is-available) for the current operating mode and [Pipeline](#pipeline-githubworkflows) for the committed automation and its activation checklist.

## Repo layout

```
metadata_db/
├── data_catalog/
│   ├── systems.yaml                           # the venue registry — one entry per queryable platform
│   └── sources/
│       └── {label}/                           # one folder per data source (the catalog label)
│           ├── data_source.yaml               # what it is (owner, description, notes)
│           ├── deployments.yaml               # where it lives (venues + physical names)
│           ├── concepts.yaml                  # optional; data-source-anchored glossary (name = concept.<leaf>; concept_id = {label}.concept.<leaf>)
│           └── {schema}/                      # one folder per schema that stores tables, columns, relationships, and mappings
│               ├── schema.yaml
│               ├── tables.yaml                # row-list files may instead be shard folders (tables/, columns/, ...) — CONTRIBUTING.md, *Shard folders*
│               ├── columns.yaml
│               ├── table_relationships.yaml
│               ├── concepts.yaml              # optional; schema-, table-, and column-anchored glossary (name = [{table}[.{column}].]concept.<leaf>; anchor segments deepen the anchor)
│               └── mappings/                  # one mappings folder per schema
│                   └── {name}.yaml            # filename is a grouping label (typically the target dataset)
├── data_ref/                                  # the ref values themselves — one CSV per ref table (see The ref reference schema)
│   └── codes/                                 # folder = the table's documented schema (loader-enforced)
│       └── entity_type_cd.csv                    # header = the table's columns; git is the row history
├── code/                                      # one folder per executable module (folder == script)
│   ├── apply_ddl/                             # owns ALL DDL: both migration streams + grant scripts
│   │   ├── apply_ddl.py                       # applies the .sql files in the configured ddl dir
│   │   ├── grants/                            # everything a rebuild must re-run: per-role privilege models + database-scoped extras
│   │   │   ├── public_hardening.sql           # revokes PUBLIC's default CONNECT — run first
│   │   │   ├── metadata_db_ci.sql             # the write CI account (post-merge loader)
│   │   │   ├── metadata_db_ci_ro.sql          # the read-only CI account (pre-merge PR jobs)
│   │   │   ├── mcp_ro_metadata.sql            # the read-only query/BI account behind the MCP server
│   │   │   ├── pgweb_ro.sql                   # the pgweb UI account (doc-only: all access via IN ROLE mcp_ro_metadata)
│   │   │   └── database_description.sql       # the COMMENT ON DATABASE (dropped by rebuilds, like grants)
│   │   ├── ddl_catalog/                       # the catalog schema's numbered migrations
│   │   │   ├── 0001_initial_schema.sql
│   │   │   └── 0002_<descriptive_name>.sql
│   │   ├── ddl_ref/                           # the reference schema's numbered migrations
│   │   │   └── 0001_ref_initial.sql
│   │   ├── config/
│   │   │   ├── apply_ddl_catalog.toml         # the catalog migration stream
│   │   │   └── apply_ddl_ref.toml             # the ref migration stream (same script, second config)
│   │   └── unit_tests/
│   ├── lib/                                   # vendored shared packages; every module resolves them from its own
│   │   ├── logconfig/                         #   location via sys.path (works from any cwd, no .claude/ dependency)
│   │   └── pgconn/                            # shared Postgres connection helper (connection_kwargs, env vars, schema-name guard)
│   ├── load_ref_data/
│   │   ├── load_ref_data.py                   # validate + truncate-and-reload data_ref/<schema>/*.csv (--dry-run / --check)
│   │   ├── config/
│   │   │   └── load_ref_data.toml
│   │   └── unit_tests/
│   ├── load_catalog_data/
│   │   ├── load_catalog_data.py               # the loader entry point (orchestration)
│   │   ├── check_corpus.py                    # offline corpus check — discovery/assembly/validation, no DB (the contributor pre-push gate)
│   │   ├── yaml_discovery.py                  # classify YAML files by location (path grammar)
│   │   ├── corpus_assembly.py                 # read YAML into a Corpus (wave-1 shape checks)
│   │   ├── corpus_validation.py               # wave-2 corpus rules + update_reason discipline
│   │   ├── corpus_diff.py                     # corpus-vs-DB diff + mass-delete guard
│   │   ├── sql_parsing.py                     # sqlglot parsing / SQL-shape checks
│   │   ├── db_io.py                           # Postgres read/write (single load transaction)
│   │   ├── data_model.py                      # row dataclasses, Corpus/DbState, PK helpers
│   │   ├── config/
│   │   │   └── load_catalog_data.toml
│   │   └── unit_tests/                        # incl. the env-gated integration suite
│   ├── pgweb/                                 # pgweb web UI over the DB (read-only, multi-session, bookmarks-only)
│   │   ├── start_pgweb.ps1                    # launch/restart script (binary itself is downloaded, not vendored)
│   │   └── bookmarks/
│   │       └── metadata_db.toml.example       # copy to metadata_db.toml (gitignored) with the real password
│   └── revert_merge/
│       ├── revert_merge.py                    # the revert script (cleanup-bot only)
│       ├── preconditions.py                   # refusal checks (HEAD match, merge commit)
│       ├── git_ops.py                         # git subprocess wrappers (token redaction)
│       ├── config/
│       │   └── revert_merge.toml
│       └── unit_tests/
├── docs/                                      # process artifacts: activity plans, code reviews
├── README.md                                  # repo front page: what the catalog is and how it works
├── CONTRIBUTING.md                            # authoring guide: workflow, file formats, validation rules
├── MAINTAINING.md                             # this doc
├── SCHEMA.md                                  # physical schema: design rationale, constraints, indexes
├── .github/
│   ├── CODEOWNERS                             # team ownership routing
│   └── workflows/
│       ├── pre_merge.yml                      # the PR checks (unit tests, dry-run, sync check, ref validation)
│       └── post_merge.yml                     # the main-branch jobs (tests on code changes, load, auto-revert)
├── pyproject.toml                             # project metadata + dependencies (uv-managed)
└── uv.lock                                    # locked dependency versions
```

Identity is **venue-free**: no id contains a system name. A data source's folder name is its globally unique catalog label; ids compose as `{database}.{schema}.{table}.{column}`. Which system(s) host a data source — and what it's physically called there — lives exclusively in its `deployments.yaml`. See `README.md` for the full model.

## YAML files

Each YAML file loads into one of the nine main tables. The corpus under `data_catalog/` is itself the working reference for every file type; [CONTRIBUTING.md](CONTRIBUTING.md) carries the field-by-field guide.

| File | Scope | Loads into |
|---|---|---|
| `systems.yaml` | one file at the corpus root; one entry per venue | `systems` |
| `data_source.yaml` | one per data source | `data_sources` |
| `deployments.yaml` | one per data source (required); sparse venue entries — residency facts only | `deployment_tables` |
| `schema.yaml` | one per schema | `schemas` |
| `tables.yaml` | one file per schema, or a `tables/` shard folder | `tables` |
| `columns.yaml` | one file per schema, or a `columns/` shard folder | `columns` |
| `table_relationships.yaml` | one file per schema, or a `table_relationships/` shard folder | `table_relationships` |
| `mappings/{label}.yaml` | one or more per schema; the filename is a grouping label (typically the target dataset), not decoded by the loader | `column_mappings` |
| `concepts.yaml` | optional; at the data-source and/or schema level; single file or `concepts/` shard folder | `concepts` |

For the four row-list types a `<type>/` folder is a split `<type>.yaml`: shards union at assembly, stems are grouping labels never decoded into identity, the two forms are mutually exclusive per (type, scope), and the four folder names are reserved as schema names (the folder grammar would make them ambiguous). Authoring guidance: CONTRIBUTING.md, *Shard folders*.

**Corpus files must carry the lowercase `.yaml` extension.** A recognized corpus file with a `.yml` or case-variant extension (`concepts.yml`, `tables.YAML`) **fails the wave-1 dry-run** naming the file and the required spelling — it is never silently skipped, because a skipped file would read as delete-by-absence and remove its previously loaded rows. Files that are not YAML at all (`.md`, `.txt`, `.gitkeep`) remain ignored.

Every file except `systems.yaml` lives under `data_catalog/sources/` and takes its identity from its folder path — including `concepts.yaml`, whose body `name` is the id relative to the file's location (the reserved `concept` segment second-to-last), composing `concept_id` as path prefix + `.` + `name` at any of four anchor depths. Deeper-than-source anchors are authored only in the schema's folder. Concepts carry no FK columns and no `validated` flag; their one referential check is that every `related_object_ids` entry resolves. Full authoring semantics: CONTRIBUTING.md, *Add a concept*.

`deployments.yaml` is authored **sparse** and stored **expanded**: a bare venue entry deploys every documented schema and table under original names; an exhaustive `schemas:`/`tables:` map subsets and renames, with explicit lowercase physical names. The loader expands entries into table-grain `deployment_tables` rows, so the DB holds facts rather than defaulting rules. Venue entries carry residency facts only — no `notes`/`update_reason` keys (see SCHEMA.md, `deployment_tables`, for the rationale and the escalation path if venue-copy caveats are ever needed). Authoring rules and defaulting semantics: CONTRIBUTING.md, *Add or edit deployments*.

Fields in each file mirror the columns of the corresponding table, minus those the loader fills in automatically: identity columns it can derive from the file's path, timestamps (`insert_ts`, `update_ts`), and computed columns like `column_mappings.target_tables_referenced`. YAML files hold **current state only** — no history records, no `end_ts`. History is materialized into the `_hstry` tables by the loader script.

## How is the database maintained and used?

Three human roles share the work (distinct from the Postgres [Database roles](#database-roles) below):

- **Data experts** interact via git: edit YAML and open PRs. They never write to Postgres directly, and do not merge their own PRs — a repo maintainer reviews and merges. Stewardship per source is recorded in `data_source.yaml`'s `owner` field and routed via CODEOWNERS ([Ownership routing](#ownership-routing)).
- **Repo maintainers** own everything outside `data_catalog/sources/` — code, DDL, CI, the venue registry, CODEOWNERS, the docs ([Ownership routing](#ownership-routing)). The loader is the only thing that writes *data* (DML) to Postgres; DDL is applied manually by maintainers via `apply_ddl.py` (see [Database schema](#database-schema)).
- **End users** interact via Postgres — through the `metadata_db` MCP server (registered in the repo's `.mcp.json`, backed by the read-only `mcp_ro_metadata` role) or the pgweb browser UI (`code/pgweb/`, backed by the read-only `pgweb_ro` role).

## Ownership routing

[CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) (`.github/CODEOWNERS`) maps file paths to owning users or teams; GitHub auto-requests the owners as reviewers on any PR touching their paths, and the **last** matching rule wins.

**Committed today** — a single catch-all rule routing every path to one individual reviewer (see `.github/CODEOWNERS`). This is deliberate. A handle that is not a real user or a provisioned team with write access makes its rule **silently inert**, and an inert per-source rule does not fall back to the catch-all — it routes its paths to nobody. Until the `@Warehouse` team slugs exist and are staffed, one individual-owned rule is strictly safer than a partially-provisioned tree.

**The intended structure**, once the teams exist — two ownership tiers plus one pairing, written catch-all and maintainer rules first so last-match-wins hands stewards their folders:

- **Each data source's folder routes to its steward team** — the team its `owner` field names. Concepts, deployments, and mappings route with the folder; mappings rules also list the target-side team as a second owner (the source team knows the source columns, the target team knows what the expression should produce).
- **Everything else routes to maintainers** — `code/`, `docs/`, `.github/`, and, inside the corpus, `data_catalog/` itself and `data_catalog/systems.yaml` — so creating a new source folder is maintainer-reviewed, while content inside an existing folder belongs to its steward.
- **`data_ref/` routes to the ref steward.** The ref code sets are stewarded like any other data source; this rule just extends that per-source ownership to the one artifact living outside `data_catalog/` — the values — so the source's two halves cannot end up with different owners. Not a dual rule with maintainers: dual-owner semantics are either-satisfies (below), so adding maintainers would let values merge *without* steward review rather than requiring both. The maintainer gate arrives by other routes — a shape change carries a migration under `code/`, which needs maintainer approval on the same PR, and no values reach the database without the maintainer-manual load.

**Enforcement** is a branch-protection choice: with **"Require review from Code Owners"** on, a PR needs an approving owner of *every* matched rule — the merge gate that makes CODEOWNERS more than advisory. Keep it **off until every slug exists and is staffed**, or PRs stall on reviewers that cannot be requested. One limit: a dual-owner rule is satisfied by *either* team's approval (GitHub has no both-must-approve mode); if hard both-teams enforcement is ever required, the fallback is a custom required check that computes approvers from the PR's changed paths — at the cost of maintaining that script and its team-membership resolution.

## Change lifecycle

The contribution flow — branch, edit, self-check, PR, validation, review, merge, load-or-revert — is one flow for everyone, and its home is [CONTRIBUTING.md](CONTRIBUTING.md), *The workflow*. Maintainers follow the same loop; what differs is that they also operate the machinery around it, each piece documented in its own section:

- **Validation and loading** — [CI & loader](#ci--loader); today's manual execution is the [Manual workflow](#manual-workflow-when-no-ci-runner-is-available).
- **Review routing** — [Ownership routing](#ownership-routing).
- **Merge, revert, and platform settings** — [The revert script](#the-revert-script), [Pipeline](#pipeline-githubworkflows), and [GitHub platform settings](#github-platform-settings).

## Database schema

The structure of the metadata_db — the 9 main tables and their 9 `_hstry` mirrors, plus indexes and constraints — is defined as **plain SQL migration files** committed to the repo. The loader operates on existing tables; it never creates or alters schema. Schema management is a separate, maintainer-owned concern.

### The `catalog` Postgres schema

Every metadata_db object lives in a dedicated Postgres schema named **`catalog`** — the physical inventory and `search_path` querying model are [SCHEMA.md](SCHEMA.md), *Where the objects live*. The operational side: the name is the `schema` config knob (alongside `database` in both the apply_ddl and loader configs — the single source of truth); both scripts set `options=-c search_path=<schema>` and keep all SQL schema-unqualified; `apply_ddl.py` creates the schema first (`create schema if not exists`, skipped in `--check`, which must not write) and reapplies the optional `schema_comment` knob as `COMMENT ON SCHEMA` on every run (a schema-agnostic migration cannot carry one — it needs the literal name); and `0001_initial_schema.sql` carries no schema literal, building into whatever schema the config names.

### Layout

```
code/apply_ddl/
├── apply_ddl.py
├── grants/
│   ├── public_hardening.sql
│   ├── metadata_db_ci.sql
│   ├── metadata_db_ci_ro.sql
│   ├── mcp_ro_metadata.sql
│   ├── pgweb_ro.sql
│   └── database_description.sql
├── ddl_catalog/
│   ├── 0001_initial_schema.sql
│   └── 0002_<descriptive_name>.sql
├── ddl_ref/
│   └── 0001_ref_initial.sql
└── config/
    ├── apply_ddl_catalog.toml
    └── apply_ddl_ref.toml
```

The numbered `.sql` files live in the per-stream DDL folders (`ddl_catalog/` for the catalog schema, `ddl_ref/` for the reference schema) next to the `apply_ddl.py` script that consumes them. Each file is a self-contained set of DDL statements that moves the database from version N–1 to version N. The numeric prefix is the migration's identity — it determines apply order and serves as the key in the tracking table. `0001_initial_schema.sql` contains all initial DDL: the 19 tables (9 main + 9 `_hstry` + `load_audit`), every FK, every CHECK constraint, every index the design needs at launch — including the ltree ID typing, the `deployment_tables` table with its physical-address UNIQUE constraint, the `validated_ts` columns, and `load_audit`, all of which are part of the launch schema rather than later diffs. Subsequent migrations (`0002`, `0003`, …) are diffs — `ALTER TABLE`, `CREATE INDEX`, etc.

### `ddl_versions` tracking table

A small table inside Postgres records which migrations have been applied:

```sql
CREATE TABLE ddl_versions (
    version TEXT PRIMARY KEY,                   -- e.g., "0001"
    checksum TEXT NOT NULL,                     -- SHA-256 of the migration file
    applied_ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The apply script ensures this table exists via `CREATE TABLE IF NOT EXISTS` before doing anything else, so the bootstrap is idempotent. The `checksum` is a newline-normalized SHA-256 of the migration file recorded at apply time; on every run (apply and `--check`) the script re-hashes each already-applied migration still in the repo and **refuses to proceed if any differs** — enforcing the append-only rule (an applied migration is never edited; changes go in a new file).

### `code/apply_ddl/apply_ddl.py`

A small Python script that brings a target database up to the latest schema version:

1. Connect to the target DB.
2. Ensure the target schema exists (`create schema if not exists`, skipped in `--check`, which must not write) and create `ddl_versions` if absent.
3. List `.sql` files in the configured DDL directory in numeric order.
4. Query `ddl_versions` to find versions already applied.
5. For each new file (in order), run it inside a transaction; on success, insert the corresponding row into `ddl_versions`; on failure, roll back and exit non-zero.

Re-runs are no-ops once all known migrations have been applied. The same script bootstraps a fresh database (applies `0001` onward) and brings an existing one current (applies whatever is missing). Connection host/port/user/password are read from `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` (loaded from a `.env` file locally, or set as Actions secrets in CI); the target database name lives in `code/apply_ddl/config/apply_ddl_catalog.toml`. A `--create-db` flag creates the target database first if it doesn't exist (requires `CREATEDB` privilege) — a shortcut for a role with `CREATEDB` that also applies the migrations as that same role; the two-role rebuild flow under [Applying migrations](#applying-migrations) creates the empty database separately so the maintainer owns the schema it builds.

### Conventions

- **Migrations are append-only.** Once a migration is committed and applied to the live DB, it is never edited. Schema changes go into a new file with the next number.
- **One concern per migration.** Don't bundle "add a column" + "drop a column" into a single file. Makes review and selective rollback easier.
- **Migrations are reviewed.** Schema changes go through PR + CODEOWNERS approval just like loader-script changes; both route to maintainers. `code/` is listed in `.github/CODEOWNERS` under the repo-plumbing block.

### Database roles

The work is split across Postgres roles by least privilege. Two are maintainer-side:

| Role | Owns / can do | Cannot |
|---|---|---|
| *a role with `CREATEDB`* (a DBA / superuser), e.g. `claudedb_user` | `CREATE DATABASE`; and — as a **member of the owner role** `metadata_db_maintainer` — `DROP DATABASE` too. The role that stands up (and can tear down) the database. | — |
| `metadata_db_maintainer` | **Owns the database** and every table, sequence, and `ddl_versions` in it; applies migrations; runs the grant scripts; runs the loader. As the owner it **can** `DROP DATABASE`. | Create a *new* database — `rolcreatedb` is off, so it can drop the one it owns but cannot spin up others. |
| `metadata_db_ci` | The loader's **write** CI account (post-merge only) — **CONNECT on the database**, DML on main tables, INSERT-only on `_hstry`, SELECT on `ddl_versions`, SELECT+INSERT on `load_audit`; SELECT on `reference`. Provisioned by `grants/metadata_db_ci.sql`. | Any DDL. |
| `metadata_db_ci_ro` | The **read-only** CI account (pre-merge PR jobs) — CONNECT on the database, USAGE on the schema, SELECT on the 9 main tables and `ddl_versions`. No DML, no `_hstry`, no `load_audit`; SELECT on `reference`. Provisioned by `grants/metadata_db_ci_ro.sql`. | Any write; any DDL. |
| `mcp_ro_metadata` | Read-only SELECT (query / BI access) on **all** tables in both schemas — deliberately broader than `metadata_db_ci_ro`, so it also sees the `_hstry` mirrors and `load_audit`. Provisioned by `grants/mcp_ro_metadata.sql`; the MCP server itself is operated from a separate repo. | Any write. |
| `pgweb_ro` | The pgweb UI account (`code/pgweb/`) — no direct grants; a member of `mcp_ro_metadata` (with inheritance), so it holds exactly that role's read-only access and tracks its grant changes automatically. Separate role so its password rotates independently of the MCP server's credential. Documented by `grants/pgweb_ro.sql` (doc-only), which carries the out-of-band provisioning SQL like the other role files. | Any write. |

The key separation: **`metadata_db_maintainer` owns the database and everything inside it — it applies migrations and, as owner, can also drop the database. A separate role holding `CREATEDB` (e.g. `claudedb_user`) creates the database and, by being a member of the owner role, can drop it too.** What the maintainer deliberately lacks is `CREATEDB` — the right to spin up *new* databases — so database *creation* stays with the DBA-side role, while ownership and full management of this database rest with the maintainer. Because the maintainer owns every object, it applies both additive and altering migrations with no ownership hand-off. The loader role (`metadata_db_ci`) never holds any DDL.

**CI job accounts — split by trust boundary.** The credential model splits the DB role in two so unreviewed branch code never holds a write credential:

- **Post-merge load** (`load_catalog_data`) writes as **`metadata_db_ci`**: full DML. It runs only on `main`, *after* review, so its credentials are supplied via the **write** secret set `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD`, stored in the **`metadata-db-write` deployment environment restricted to the `main` branch**. The environment restriction is what confines the write credential to reviewed, on-main code — plain repository secrets would be readable by any same-repo PR that edits a workflow file (only *fork* PRs are denied secrets by default). This is the GitHub analogue of GitLab's protected variables.
- **Pre-merge dry-run** (`validate_catalog_data`) and **pre-merge schema-check** (`check_schema_in_sync`, `apply_ddl.py --check`) both read as **`metadata_db_ci_ro`**: SELECT on the main tables and `ddl_versions` only. These jobs run *unreviewed branch code* in PR workflows, so they must not hold write credentials. Their credentials come from a **read-only** repository-secret set `POSTGRES_RO_HOST` / `POSTGRES_RO_PORT` / `POSTGRES_RO_USER` / `POSTGRES_RO_PASSWORD`; each PR job maps `POSTGRES_* := secrets.POSTGRES_RO_*` at job level (see `.github/workflows/pre_merge.yml`). The accepted residual is that branch code runs holding *some* credential — reduced here to SELECT on catalog metadata (low sensitivity).

`mcp_ro_metadata` is a fourth read-only role for query/BI users, not used by CI. The MCP server that uses it is operated from a separate repo, but its grants are provisioned here (`grants/mcp_ro_metadata.sql`) because the objects they target are. `pgweb_ro` is a fifth, riding on `mcp_ro_metadata` via membership — it exists so the pgweb UI (`code/pgweb/`) has its own rotatable credential; see `grants/pgweb_ro.sql`.

**Migration-PR sync-check flow.** `check_schema_in_sync` normally fails if the repo holds a migration the DB has not applied — which would make the PR that *introduces* `0002` unable to pass its own checks (the migration is only applied post-merge, by a maintainer). The job resolves this by computing the migration files the PR *newly adds* (a `git diff --diff-filter=A` against the PR's base) and passing them to `apply_ddl.py --check` via the repeatable `--allow-pending <filename>` flag, which exempts exactly those files while staying strict about every other pending migration. So a migration PR passes its own checks; the deadlock is resolved in code, not by relaxing the check.

### Applying migrations

Migrations are applied **manually by `metadata_db_maintainer`**. CI never auto-applies — DDL (`CREATE` / `ALTER` / `DROP`) is sensitive enough that every application deserves human attention and chosen timing. The loader's CI account (`metadata_db_ci`) is DML-only; it never holds DDL privileges.

Connection credentials (`POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD`) are read from the environment — a `.env` file locally (the maintainer/owner credentials are kept commented and swapped in per operation), or a secrets store / CI-CD variables otherwise — and are never committed. CI does not have access to these credentials.

**Existing database (the common case, after a migration PR merges).** No database-lifecycle step, so it runs entirely as `metadata_db_maintainer`:

1. Pull latest `main`.
2. As `metadata_db_maintainer`, run (no `--create-db` — the database already exists), ideally during a low-traffic window:
   ```bash
   uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl_catalog.toml
   ```

**Creating or rebuilding the database** (fresh instance, or a pre-launch bootstrap rebuild). This is the only flow that touches the database lifecycle, so it spans both roles:

1. As **a role with `CREATEDB` privilege** (e.g. `claudedb_user` — the DBA-side role that creates/drops databases):
   ```sql
   -- rebuild only: drop the existing DB first. WITH (FORCE) terminates
   -- lingering sessions, but a non-superuser can only terminate its OWN
   -- role's backends — close any other-role connections first (e.g. an
   -- mcp_ro_metadata read-only session) or the drop is refused.
   DROP DATABASE IF EXISTS metadata_db WITH (FORCE);
   CREATE DATABASE metadata_db;
   -- Hand the whole database to the maintainer: it then owns the database
   -- and everything it creates — so it can build the schema and, as owner,
   -- drop the database. CREATEDB stays OFF the maintainer, so it still
   -- cannot create NEW databases.
   ALTER DATABASE metadata_db OWNER TO metadata_db_maintainer;
   -- The MCP and pgweb roles' session search_path is database-scoped, so
   -- the drop took it with the database. Re-set both here — ALTER ROLE ...
   -- SET needs CREATEROLE with ADMIN on the role, which the owner
   -- deliberately lacks. Rationale: grants/mcp_ro_metadata.sql,
   -- Preconditions (pgweb_ro rides on the same ltree requirement:
   -- grants/pgweb_ro.sql).
   ALTER ROLE mcp_ro_metadata IN DATABASE metadata_db
     SET search_path = catalog, reference;
   ALTER ROLE pgweb_ro IN DATABASE metadata_db
     SET search_path = catalog, reference;
   ```
   > The `CREATEDB` role keeps the ability to drop `metadata_db` by being a **member of** the owner role (`GRANT metadata_db_maintainer TO <createdb_role>` — a one-time, cluster-level setup). Do **not** use `apply_ddl.py --create-db` in this model: that shortcut creates the DB *and* applies the migrations as the one `CREATEDB` role, leaving every object owned by it and forcing a follow-up `REASSIGN OWNED BY <createdb_role> TO metadata_db_maintainer`. Creating the empty DB and handing it to the maintainer avoids that.
   >
   > A **schema-scoped rebuild** is an equivalent lighter path when the database itself can stay: as `metadata_db_maintainer`, `DROP SCHEMA catalog CASCADE` (which takes `ddl_versions` and the `ltree` extension with it), then continue from step 2 — `apply_ddl.py` recreates the schema, and the per-role grant scripts re-establish each role's schema/table privileges.
2. As **`metadata_db_maintainer`**, apply the migrations. `apply_ddl.py` reads the `schema` config knob (currently `catalog`), issues `create schema if not exists catalog`, sets `search_path=catalog` on its connection, then applies the schema-agnostic `0001` — so `ltree`, all 18 tables, `load_audit`, and `ddl_versions` land in **`catalog`** (the maintainer **owns** every object it creates; `create extension if not exists ltree` installs into `catalog` because that is the current search_path):
   ```bash
   uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl_catalog.toml
   ```
   Also drop the default `public` schema `CREATE DATABASE` ships — nothing in this database uses it (`ltree` lives in `catalog`), the integration fixture drops it for the same reason, and leaving it around makes schema browsers (pgweb) open an empty schema by default:
   ```bash
   psql -d metadata_db -U metadata_db_maintainer -c "drop schema public"
   ```
3. Apply the **ref** migration stream too (see [The ref reference schema](#the-ref-reference-schema)) before granting. Each role's grant script covers both schemas in one file, so both streams must exist first.
4. As **`metadata_db_maintainer`** (the owner, so it can `GRANT` on the schemas, their tables, and the database), restore every role's privileges — a rebuild drops all per-object grants (the LOGIN roles are cluster-level and survive; their privileges on the recreated objects do not). Run the hardening script first, then one script per role, then the database description (dropped with the database like the grants), in this order:
   ```bash
   psql -v database=metadata_db -d metadata_db -U metadata_db_maintainer \
        -f code/apply_ddl/grants/public_hardening.sql

   for role in metadata_db_ci metadata_db_ci_ro mcp_ro_metadata; do
       psql -v catalog_schema=catalog -v ref_schema=reference -v database=metadata_db \
            -d metadata_db -U metadata_db_maintainer \
            -f "code/apply_ddl/grants/${role}.sql"
   done

   psql -v database=metadata_db -d metadata_db -U metadata_db_maintainer \
        -f code/apply_ddl/grants/database_description.sql
   ```
   `public_hardening.sql` goes first because it revokes PUBLIC's default database `CONNECT`; each role script then re-grants `CONNECT` for its one role, along with schema `USAGE` (without which no table grant resolves) and that role's table privileges. Every script carries `\if`-guarded defaults — `catalog_schema=catalog`, `ref_schema=reference`, `database=metadata_db` — so a bare invocation works against the standard names, but always pass the `-v` values explicitly so they stay tied to the config knobs rather than silently falling back. See [Database roles](#database-roles) for what each role ends up holding. (`pgweb_ro` needs no line here: its access is all inherited through `mcp_ro_metadata`'s membership — `grants/pgweb_ro.sql` is doc-only. And the throwaway `metadata_db_integration` gets the same PUBLIC-`CONNECT` revoke applied by the integration suite's fixture at the moment it recreates that database, so cluster roles cannot connect there either.)
5. Reload the corpus by running the loader (see the runbook below). The loader reads the same `schema` knob and sets `search_path=catalog`, so its rows land in `catalog`.

> **Bootstrap note (pre-launch schema edits).** Some pre-production schema changes were adopted by editing `0001_initial_schema.sql` and rebuilding, rather than as separate `ALTER` migrations: the ltree typing of the ID columns, making `0001` schema-agnostic (no schema literal — the target schema comes only from the `schema` config knob, applied via `search_path`), and the venue-free identity + deployments restructure (which reshaped every table), and widening the `concepts.concept_id` shape CHECK from `nlevel in (3, 4)` to `nlevel between 3 and 6` when table- and column-level concept anchors were introduced. All are one-time, pre-launch exceptions justified because the only data (the sandbox corpus and the PUFs) is reproducible from YAML. Once a real production consumer depends on the DB, `0001` is immutable like any applied migration and all schema changes go into new numbered files.

**Rehearsing a risky migration.** No persistent staging environment is maintained. If a migration is non-trivial — anything beyond a simple `CREATE INDEX` or adding a nullable column — the maintainer can spin up an ephemeral Postgres (local Docker container, or a one-off CI job), restore a recent production `pg_dump` into it, apply the migration there, and verify before applying to production. The ephemeral DB exists only for the duration of the rehearsal.

**YAML coordination.** A migration that changes the *shape* of the main tables — adding, dropping, or renaming a column, or introducing a new table type — usually requires coordinated changes to YAML format and loader code. Bundle these in the same PR, or land them in a sequence that never leaves the loader unable to read existing YAML. Migrations that only affect indexes, constraints already satisfied by existing data, or anything below the YAML's level of detail leave the YAML untouched.

### Drift safeguard

Because application is manual, there's a window between a migration PR merging and the maintainer running the apply step where the **loader code on `main`** expects a newer DB schema than the **live DB** actually has. The next post-merge load — typically triggered by an unrelated YAML PR — will fail with "column doesn't exist" or similar, because the loader's SQL assumes columns or tables that haven't been added yet. The author of that PR didn't do anything wrong; they're just the unlucky one whose load triggered the drift.

A pre-merge job (`check_schema_in_sync`) closes that window. It runs `apply_ddl.py --check`: the script connects (read-only), queries `ddl_versions`, compares to the file list in `code/apply_ddl/ddl_catalog/`, and **exits non-zero if the DB is missing any migration that already exists in the repo**. The required check fails, the PR can't be merged, the drift is surfaced.

Forces the ordering: migration PR merges -> maintainer applies it -> subsequent PRs become mergeable again. The CI account for this job needs `CONNECT` on the database, `USAGE` on the schema, and `SELECT` on `ddl_versions` — no DDL privileges, no read access to data.

## The ref reference schema

Curated code sets (claim type codes, and future sets such as the Type of Bill components or status codes) live as **real typed tables in the metadata_db instance's `reference` Postgres schema** — one table per set, so heterogeneous attributes stay typed columns instead of flattening into a generic code/value model. They exist for **context retrieval** (a human or LLM resolving what a code value means), not for runtime joins: no relationships, mappings, or venue materialization are documented against them, and the `columns.ref_table_id` pointer that links a consuming column to its code set implies no join path and no co-deployment (see `SCHEMA.md`, `columns`).

**Architecture — two homes, one truth:**

- **`data_ref/` is the data.** One CSV per ref table at exactly `data_ref/<documented_schema>/<table>.csv` (header = the table's columns; the folder must name a documented schema of the ref source — loader-enforced, see the tree conventions below), git-versioned and hand-maintained — reviewable in PR diffs and editable by non-engineers. `code/load_ref_data/load_ref_data.py` validates and **truncate-and-reloads** every table in one transaction; the loader is deliberately dumb (no diffing, no row history) because **PR review replaces `update_reason` and git history replaces `_hstry`**. It lives *outside* `data_catalog/` on purpose: `data_catalog/` is the corpus (metadata about the world), `data_ref/` is the one piece of the world the repo itself hosts — so the corpus YAML discovery walk never touches it.
- **`data_catalog/sources/ref/` is the metadata.** The ref tables are documented as an ordinary data source (`ref`, schema `codes`), deployed to exactly one venue — the `metadata_db` system (the catalog's own Postgres) — with the documented labels (`ref.codes.*`) mapping to the physical address (`metadata_db.reference.*`) via the standard `database_name`/`schemas` rename knobs in `deployments.yaml`. The documented schema (`codes`) is a content label; the physical name (`reference`) lives solely in the `deployments.yaml` schemas map, and the folder/loader family (`data_ref/`, `load_ref_data`, `ddl_ref/`) keeps `ref` as its shorthand.
- **Values ownership.** Unlike every other source, the ref values are *owned here*: this repo is their source of truth, not a mirror of an external system. That is what licenses the loader's **consistency gate** — the documented corpus columns must equal the CSV header must equal the live DDL columns — making the catalog **guaranteed accurate** for ref (the one source where docs-vs-reality drift is mechanically preventable). Keep all three in sync in one PR.

**Two-config apply_ddl.** The reference schema gets numbered migrations, checksums, and the sync check with zero new DDL tooling: `apply_ddl.py` is schema-agnostic, so a second config (`code/apply_ddl/config/apply_ddl_ref.toml`, pointing at `code/apply_ddl/ddl_ref/` and `schema = "reference"`) drives the ref stream. Each schema keeps an **independent `ddl_versions` ledger** (apply_ddl auto-creates one per schema), so the sync check must run `--check` once per config.

**Guardrail.** The loader refuses a CSV with more rows than the `max_rows_per_table` config knob (default 1,000, `code/load_ref_data/config/load_ref_data.toml`) — ref is for *curated* sets; open-ended domains (NDC, ICD) are data, documented the ordinary way. Raise the knob deliberately, in a reviewed PR, if a genuinely curated set outgrows it.

**Adding a new ref table (runbook):**

1. **Check the ref table list** (the `data_catalog/sources/ref/codes/tables/` shards — the one schema's table list is the registry) to confirm the set doesn't already exist under another name.
2. **Author the CSV** at `data_ref/<documented_schema>/<table>.csv` — `data_ref/codes/<table>.csv` today (header = the intended columns; values curated by the steward). The folder must name a documented schema of the ref source and the stem must be globally unique across folders; the loader errors on any other placement.
3. **Add a ref migration** under `code/apply_ddl/ddl_ref/` (`000N_add_<table>.sql`: `create table` + `comment on` statements) — the ref stream is append-only like the catalog stream.
4. **Document the table in the corpus** — a `tables/<table>.yaml` and a `columns/<table>.yaml` shard under `data_catalog/sources/ref/codes/` (the consistency gate fails the load if these disagree with the CSV/DDL). Per-table shards are the convention (shard stems stay freeform for the corpus loader; the shard folders and the single-file forms are mutually exclusive). Ref `data_type` values are a machine contract — see the authoring convention below.
5. **Link the consuming columns** — set `ref_table: ref.codes.<table>` on every documented column that carries the set's values (every carrier, not just the "main" one — context lookup should work from whichever column a consumer lands on). Linked rows that already exist in the DB are updates and need `update_reason`. **Do not self-link the ref table's own key column** (e.g. `ref.codes.entity_type_cd.code` gets no `ref_table: ref.codes.entity_type_cd`): the column *is* the enumeration — a consumer landing on it is already at the code set. The convention is stated, not loader-enforced.
6. **Open the PR** (values + migration + docs + links together); `load_ref_data.py --dry-run` validates the CSV pre-merge. The new table does not exist in the DB yet (its migration applies post-merge), so its missing-from-DB error is downgraded via the repeatable `--dry-run --allow-missing-table <table>` flag, and the CSV validates **against the documented shape** instead of the live columns — full strength (docs gate, guardrail, header == documented columns, values parse per documented `data_type`, empty cells only on nullable columns, PK uniqueness over the documented key columns), not a shallow fallback. The CI `validate_ref_data` job computes the flag automatically from the PR's own added CSVs (see the escape-hatch family below), so authors cannot exempt arbitrary tables.
7. **Maintainer applies and loads** (post-merge, out-of-band): apply the ref migration, then run the ref loader:

   ```bash
   uv run code/apply_ddl/apply_ddl.py --config code/apply_ddl/config/apply_ddl_ref.toml
   uv run code/load_ref_data/load_ref_data.py --config code/load_ref_data/config/load_ref_data.toml
   ```

   **No grant step for a new table**: each role's grant script sets `ALTER DEFAULT PRIVILEGES` for the maintainer in the `reference` schema, so every table a later ref migration creates is SELECT-able by the three read roles at creation — the scripts are only run once per rebuild, never edited per table:

   ```bash
   for role in metadata_db_ci metadata_db_ci_ro mcp_ro_metadata; do
       psql -v catalog_schema=catalog -v ref_schema=reference -v database=metadata_db \
            -d metadata_db -U metadata_db_maintainer \
            -f "code/apply_ddl/grants/${role}.sql"
   done
   ```

   Each grants USAGE on `reference` plus SELECT on **all its tables, current and future** (schema-wide grant + default privileges; that covers `ref_load_audit` and the ref `ddl_versions` too) to its one role — `metadata_db_ci`, `metadata_db_ci_ro`, and `mcp_ro_metadata` respectively; **writes stay with the maintainer role** — ref loading is maintainer-manual, mirroring the manual-DDL stance (values change rarely and deserve chosen timing). The corpus loader run for the `data_catalog/sources/ref/` docs is the ordinary post-merge load.

**The escape-hatch family (pre-merge only).** Three repeatable `--dry-run`-only flags cover the window where the DB legitimately lags a PR whose migration applies post-merge; the loader rejects every one of them on a real load or `--check` (a real run must never skip a drift or missing-table error). The CI `validate_ref_data` job computes each from the PR's **own diff** (authors cannot exempt arbitrary tables), always with `--no-renames` so a renamed CSV appears as an add plus a delete instead of an `R` entry that `--diff-filter=A`/`D` would silently drop. An exemption naming a table that nothing matches downgrades nothing and fails nothing on its own, so the loader logs a WARNING naming it — a broken or stale diff computation is visible in the dry-run log rather than passing silently (the same guardrail `apply_ddl` gives an unused `--allow-pending`):

- `--allow-missing-table <table>` — computed from the PR's **added** CSVs (`--diff-filter=A` over `data_ref/`): the new table's missing-from-DB error downgrades to a warning; its CSV validates against the documented shape.
- `--allow-dropped-table <table>` — computed from the PR's **deleted** CSVs (`--diff-filter=D`): the retired table's DB-table-without-CSV drift error downgrades to a warning.
- `--allow-reshaped-table <table>` — computed from the PR's **modified** CSVs (`--diff-filter=M`), but **only when the PR also touches `code/apply_ddl/ddl_ref/`** (a value-only PR gets no shape exemptions and stays fully strict against the live columns): the named existing table validates against the documented shape instead of the live columns. Known over-breadth, accepted: when `ddl_ref/` is touched, *all* modified CSVs in the PR get shape exemptions, including value-only edits to unrelated tables, which then validate at docs-shape strictness instead of live.

**Reshaping a ref table (runbook).** One PR carrying all three legs together: the `ALTER TABLE` migration under `ddl_ref/`, the reshaped CSV, and the updated `columns/<table>.yaml` shard (plus `update_reason` on every changed column row that is already loaded, per rule 20, *update_reason discipline*). Pre-merge, the reshaped table validates against the docs shape (`--allow-reshaped-table`, CI-computed as above); post-merge the maintainer applies the migration and runs the loader, which re-validates everything against the now-reshaped live columns — docs and migration *can* disagree pre-merge (docs-shape validation narrows but cannot eliminate the gap), and that disagreement fails loudly at the post-merge load.

**Retiring a ref table (runbook).** One PR: the `drop table` migration under `ddl_ref/`, the CSV deletion, the removal of the table's `tables/`/`columns/` doc shards, and the `ref_table` unlink on every consuming column. `validate_ref_data` exempts the dropped table automatically from the PR's own deleted CSVs (`--allow-dropped-table`, computed as above). The catalog-side ceremony is explicit: post-first-load, every `ref_table` unlink is an **UPDATE to a loaded column row and needs `update_reason`** (rule 20, *update_reason discipline*), and removing the table's doc shards delete-by-absences its catalog rows (the ordinary corpus delete path — mind the mass-delete guard on large retirements). A **rename is retire-plus-create** — the CI diff correctly emits both `--allow-dropped-table <old>` and `--allow-missing-table <new>` — with the full ceremony on both sides (drop + create migrations, both doc shards, every consuming `ref_table` repointed with `update_reason`); a rename is never cheap.

**Ref docs authoring convention (`data_type`).** Unlike the freeform `data_type` prose acceptable elsewhere in the corpus, ref column docs are a machine contract: docs-shape validation parses CSV values per the documented type, so every ref `data_type` must come from the loader's parser vocabulary — `bigint`, `boolean`, `date`, `double precision`, `integer`, `numeric`, `real`, `smallint`, `text`, `timestamp with time zone`, `timestamp without time zone`. A documented type outside the vocabulary is an explicit validation issue naming the column and the allowed set.

**Tree conventions.** `data_ref/<documented_schema>/<table>.csv` is loader-enforced: the folder must name a documented schema (a schema folder under `data_catalog/sources/ref/`), no CSV may sit at the top level or deeper than one folder, and stems are globally unique across folders (one physical namespace underneath). Docs shard files under `tables/`/`columns/` are per-table **by convention only** — shard stems stay freeform for the corpus loader.

**Freshness.** Each ref load appends one `ref_load_audit` row per table (CSV content hash — the SHA-256 of the exact bytes loaded — row count, `loaded_ts`, and an `audit_id` identity PK that tiebreaks the latest-row query) inside the load transaction. `load_ref_data.py --check` compares each CSV's hash against the latest audit row and exits non-zero naming any table that is stale, never loaded, or missing from the DB — plus **bidirectional drift**: a ref-schema table or a documented ref table with no `data_ref/<schema>/<table>.csv` fails the check too, so docs == CSV == DDL holds in both directions. The ref analogue of the catalog's drift check, and what surfaces a forgotten manual load.

**Outside the auto-revert perimeter — by construction.** `data_ref/` is outside `data_catalog/`, so a CSV PR never matches the post-merge load job's `data_catalog/**/*.yaml` rule: it creates no load job and never engages `revert_failed_load`. A failed manual ref load rolls back its own transaction (the DB stays at its prior state) and is recovered via the manual runbook — fix forward or hand-revert the merge — mirroring the manual corpus-load stance. There is deliberately no concurrency lock in the ref loader: concurrent truncate-and-reload runs self-serialize through `TRUNCATE`'s ACCESS EXCLUSIVE locks and are idempotent for identical inputs.

## CI & loader

### The loader script

A single Python script (`code/load_catalog_data/load_catalog_data.py`) handles all 9 main tables on every run:

1. Read all YAML files for each main table. Discovery and assembly errors — misplaced files, unparsable YAML, bad rows, duplicate PKs, deployment defects — are **aggregated across the whole corpus**, so authors see every problem per run instead of one CI round-trip per mistake. Sparse `deployments.yaml` entries are expanded into explicit table-grain rows during assembly. Errors surface in at most three waves: all shape issues first (discovery + assembly, one report), then — only once the corpus assembles cleanly — all step-2 validation issues, then `update_reason` discipline (which needs the diff).
2. Validate the assembled corpus. This runs before any DB contact — which is what lets `check_corpus.py` run the same checks offline. The rules are the 21 numbered, named rules in CONTRIBUTING.md (*What gets validated*): uniqueness and label/system disjointness, reference existence (with case-mismatch hints), identifier syntax, recognized keys, required prose, the deployments rules, folder anchoring, co-deployment, pair/mapping disambiguation, and SQL parsability and shape. Issues are aggregated across all rules and reported together, and validation runs only on a corpus whose step-1 shape checks all passed — so a skipped bad row never cascades into phantom cross-reference errors. Two maintainer-side stances worth restating here:
   - **Postgres is the canonical dialect.** Expressions must parse as Postgres; runnability in a (possibly non-Postgres) hosting system is *not* checked. The parse is a syntax gate — correctness against real target data is a human judgment recorded via `validated` / `validated_ts`, and a researcher translates the expression to the target's own dialect when running it.
   - **Quoting.** An identifier that is a SQL reserved word or otherwise needs quoting must be quoted in expressions (e.g. `…bene."order"`).
3. Derive computed fields — notably `column_mappings.target_tables_referenced`, from the expression parse trees already built during validation.
4. Connect, take the single-writer advisory lock (fail-fast — see the dry-run note below), resolve the commit SHA for `load_audit`, and read current DB state.
5. Compute diff (insert / update / delete).
6. Enforce `update_reason` discipline against the diff — non-null on every changed row, null on inserts, deployments exempt. The loader checks only that a reason is *present* on a change, not that it *changed* or is accurate: the **authoritative** rationale is the git commit, linkable from any row via `load_audit` (join `load_audit.loaded_ts = <row>.update_ts`, then `git show <commit_sha>`); reason quality is a CODEOWNERS review concern. Then run the mass-delete guard — in dry-run and real runs alike — see [Mass-delete guard](#mass-delete-guard) below.
7. Inside one transaction per run:
   - Write old values to `_hstry` for every to-be-updated and to-be-deleted row, tagged with `end_ts = now()`. The history row carries the *old* `update_reason` (i.e., why the now-superseded version had existed); the *new* `update_reason` lands on the new main row.
   - Apply inserts, updates, and deletes to the main tables.
8. Exit on success; rollback on any failure.

The loader is **idempotent**: running it twice in a row with no YAML changes is a no-op. Idempotency requires that the diff in step 5 compares row *content* (excluding `insert_ts`/`update_ts`) — a row is only treated as an "update" if at least one content field changed, and `update_ts` is only touched then. Otherwise re-runs would churn `update_ts` and emit spurious `_hstry` rows. The whole-run transaction means partial failures leave the DB unchanged.

#### Dry-run mode

Invoking the loader with `--dry-run` runs the read/validate/diff/guard steps and stops before the writes: no rows are inserted, updated, or deleted, and no `load_audit` heartbeat is written. It does, however, open a connection it never writes on and — like every run — takes the single-writer advisory lock as its first statement, holding it for the connection's duration. So a long dry-run fail-fasts a concurrent real load with "another metadata_db load is already in progress", and vice versa (the reader excludes the writer by design — better a clear refusal than a read-then-write against state that moved). Dry-run still requires **read access** to the DB — the loader needs current state to determine which rows are inserts vs. updates vs. deletes, which in turn drives several validations (including the `update_reason` check). The CI account used for the pre-merge dry-run holds `SELECT` on main tables and nothing more; it has no `INSERT`/`UPDATE`/`DELETE` and no DDL.

#### Mass-delete guard

Deletes are computed by absence — any DB row whose PK is missing from the assembled corpus is scheduled for deletion — so a wrong `data_root`, a half-finished folder rename, or a botched merge would silently convert into mass deletion. The loader therefore refuses to proceed, in **dry-run and real runs alike** (so pre-merge CI blocks the mistake before merge), when a diff would delete more than `mass_delete_fraction` (default 0.25) of the DB's current rows, once at least `mass_delete_min_count` (default 20) rows are being deleted — the absolute floor keeps small/bootstrap corpora from tripping on routine cleanups. Both knobs live in `code/load_catalog_data/config/load_catalog_data.toml`, so an **intended** mass removal (e.g. decommissioning a data source) can raise them in the same PR — the config is under `code/`, so that change routes to maintainers via CODEOWNERS. Alternatively, a maintainer running the loader manually can pass `--allow-mass-delete`, which is gated by `METADATA_DB_ALLOW_MASS_DELETE=1` (the same flag + env-var double gate as `--reset-hstry`). Rows deleted past the guard still land in `_hstry` as usual.

#### Bootstrap-phase history reset

During initial setup — before the repo has "live" consumers — `_hstry` accumulates a lot of throwaway versions as authors iterate on YAML to get rows right. The loader accepts a maintainer-only `--reset-hstry` flag that truncates every `_hstry` table inside the load transaction, before applying the diff. It is already gated behind an explicit environment guard: the loader refuses `--reset-hstry` unless `METADATA_DB_ALLOW_RESET_HSTRY=1` is set in the environment — **in dry-run mode too**, so a pre-merge dry-run fails exactly as the real run would (mirroring `--allow-mass-delete`). The flag also requires **maintainer credentials**: `metadata_db_ci`'s INSERT-only `_hstry` grant deliberately excludes `TRUNCATE`, so the reset cannot run as the CI role. It is intended for the bootstrap phase only and should be removed once the metadata_db is in use by downstream consumers. After that point, any `_hstry` cleanup needs a more deliberate mechanism — that is intentionally left as future work rather than baked in now.

### The revert script

A small Python script (`code/revert_merge/revert_merge.py`) is invoked by the `revert_failed_load` job (see [Pipeline](#pipeline-githubworkflows) below) when a post-merge loader run fails. Its sole job is to push a clean revert of the failed merge commit directly to `main`, restoring the invariant that `main` matches the (unchanged) DB.

**Inputs:**
- `--commit-sha` — the SHA of the failed merge commit. Passed by the CI job as `$GITHUB_SHA` (on the post-merge `push` event, the merge commit itself).
- `CLEANUP_BOT_TOKEN` env var — the cleanup bot's token (a fine-grained PAT or GitHub App installation token). See [Cleanup bot account](#cleanup-bot-account).
- `--config` TOML — `remote_url_template` (with a `{token}` placeholder), `main_branch`, and the cleanup bot's git identity `bot_name` / `bot_email` (the identity the revert commit is authored under; shipped as `<...>` placeholders that the config sanity block refuses until filled).

**Algorithm:**

1. Refuse (before any git operation) if the configured remote URL template still carries an unfilled placeholder such as `<group>`, lacks the `{token}` placeholder, or if the cleanup bot's git identity (`bot_name` / `bot_email`) is blank or still an unfilled `<...>` placeholder — a never-customized config must fail loudly, not push somewhere odd or fail mid-incident at the commit step.
2. Compose the authenticated URL from the template and the token. It is injected **per command** (passed on the `git fetch`/`git push` command lines) and never written to `.git/config`, so on a reused runner workspace the credential cannot outlive the job.
3. Fetch `main` from that URL and force the local branch to its tip (`git checkout -B main origin/main` — robust against a stale local `main` left by a previous job on a reused checkout).
4. Verify preconditions:
   - `HEAD` of `main` equals `--commit-sha` (no other commit has landed since the failed merge).
   - The commit at `--commit-sha` is a merge commit (has two parents).
5. Run `git revert --no-edit -m 1 <commit-sha>` to produce the revert commit, supplying the cleanup bot's identity as per-invocation `-c user.name=… -c user.email=…` options (from `bot_name` / `bot_email` in the config). A stock CI container configures no git identity, so **without this the revert would fail at the commit step** with `main` left ahead of the DB — the entire auto-revert mechanism would be inoperative. Per-command `-c` (rather than `git config` writes) keeps nothing identity-related in `.git/config` on a reused workspace, mirroring the token hygiene.
6. Push to `main` at the authenticated URL.
7. Exit 0.

The token-bearing URL is redacted from every log line the script emits (git itself anonymizes URL credentials in its own error output).

**Refusal.** If any precondition in step 4 fails, the script exits non-zero without pushing anything. Possible causes: another commit somehow landed despite the [merge serialization](#merge-serialization) gate, the `--commit-sha` argument doesn't point at a merge commit, or git operations fail. In all such cases the script leaves `main` untouched and the failed run surfaces the inconsistency for human review — papering over the situation would silently widen the `main`/DB drift the design exists to prevent.

**Bounded authority.** This script is the only thing the cleanup bot's identity is ever wired to. Together with branch protection (only the bot can push to `main`) and the merge-serialization gate (no other merge can land while a load is in flight), this guarantees that any commit the bot pushes is a clean revert of the immediately preceding merge commit — the blast radius of a compromised token stays small.

### Pipeline (`.github/workflows/`)

Seven jobs across two GitHub Actions workflows: `pre_merge.yml` (`on: pull_request`) carries the four PR jobs — `unit_tests`, `validate_catalog_data`, `check_schema_in_sync`, `validate_ref_data` — and `post_merge.yml` (`on: push` to `main`) carries the three main-branch jobs — `unit_tests_main`, `load_catalog_data`, and the on-failure `revert_failed_load`.

**Two workflows, per-job path gating.** GitHub evaluates `paths:` filters at the workflow *trigger*, not per job, and the jobs within each workflow gate on different paths — so each gated job carries its own **change-detection step** (a `git diff` against the PR base, or against `github.event.before` — the push's previous tip — on `main`) instead. The hard constraint the GitLab config preserved verbatim carries over: `revert_failed_load` `needs:` the load job and must exist in exactly the same runs — both jobs exist in every push run, the revert fires only on `needs.load_catalog_data.result == 'failure'` (scoped to the load job's outcome, never an unrelated failure), and it re-checks the load's own path gate before pushing anything.

**Dormancy gate.** The committed workflows are **dormant by design** until setup is complete: every job is gated on the repository Actions variable `METADATA_DB_CI_ENABLED` equaling `"true"` (Settings -> Secrets and variables -> Actions -> Variables), and skips otherwise. Until then maintainers run the scripts manually (see the manual workflow below). Note a skipped job *satisfies* a required status check, so branch protection's required checks only bite once the variable is set.

**Activation checklist** (do all before setting `METADATA_DB_CI_ENABLED=true`):

1. **A self-hosted runner registered and connected, carrying the labels the jobs select on:** github.example.com is GitHub Enterprise Server, which provides no GitHub-hosted runners — Actions must be enabled on the instance and a registered self-hosted runner must have Docker (the jobs run in a `python:3.14-slim` container), a network path to Postgres, and access to the Debian/PyPI mirrors the setup steps install from. Every job selects on `runs-on: [self-hosted, linux, docker]` rather than bare `self-hosted` (which matches *any* registered runner, including a future one without Docker), so **register the runner with the `linux` and `docker` labels** — without them no job is ever assigned and every run queues indefinitely.
2. **Postgres reachable from that runner, both CI roles created and granted.** Create each LOGIN role first — the grant scripts never create roles, and a grant to a nonexistent role fails:
   - Create `metadata_db_ci` (write) and `metadata_db_ci_ro` (read-only) as cluster-level LOGIN roles.
   - Apply **both** migration streams (catalog and ref) before granting: each role's script covers both schemas in one file, so a run with only the catalog stream applied aborts at the reference block. The PR jobs need the `reference` grants either way (`validate_ref_data` reads the live ref columns; `check_schema_in_sync` reads `reference.ddl_versions`).
   - Run `grants/public_hardening.sql`, then `grants/metadata_db_ci.sql`, `grants/metadata_db_ci_ro.sql`, and `grants/mcp_ro_metadata.sql` — hardening first, since it revokes the PUBLIC default `CONNECT` that each role script then re-grants for its own role.
3. **Set the two secret sets on their trust boundaries:**
   - Read-only: `POSTGRES_RO_HOST` / `POSTGRES_RO_PORT` / `POSTGRES_RO_USER` / `POSTGRES_RO_PASSWORD` — **repository secrets** (the PR jobs map `POSTGRES_* := secrets.POSTGRES_RO_*`).
   - Write: `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD`, plus `CLEANUP_BOT_TOKEN` — secrets in the **`metadata-db-write` deployment environment, restricted to the `main` branch** (confines the write credentials to reviewed, on-main code; see [Cleanup bot account](#cleanup-bot-account)).
4. **Fill `code/revert_merge/config/revert_merge.toml`:** the cleanup bot's git identity (`bot_name` / `bot_email`) — the remote URL template is committed already; verify it matches the repo's final address.
5. **Restrict the PR merge method to "merge commit" only** (see [Merge method and fork PRs](#merge-method-and-fork-prs)) — squash/rebase merging silently disables the revert's 2-parent precondition.
6. **Protect `main`** — require PRs, the pre-merge jobs as required status checks, and the cleanup bot's bypass allowance (see [Cleanup bot account](#cleanup-bot-account)).
7. **Set the `METADATA_DB_CI_ENABLED` repository variable to `true`.**

Standing facts to record at activation (the full statements live in the workflow headers): ref loading stays **maintainer-manual** — `data_ref/**` changes never trigger an automatic load job (`load_ref_data.py --check` surfaces a forgotten load); from activation onward `0001_initial_schema.sql` **and** `0001_ref_initial.sql` are immutable (the pre-launch edit-and-rebuild exception cannot coexist with an active checksum gate — all subsequent schema changes go in new numbered migrations); activation ends the "held load" pattern (every `data_catalog/**` merge loads or auto-reverts immediately, so migration+rebuild sequences must complete before dependent data PRs merge — `check_schema_in_sync` enforces the ordering); and the env-gated integration suite could run in CI via a Postgres service container — parked until runner capacity is understood.

The per-job shape, summarized — the committed files carry the full rationale comments and are the reference; read them before editing:

| Workflow | Job | Runs | Credentials | Command |
|---|---|---|---|---|
| `pre_merge` (`on: pull_request`) | `unit_tests` | every PR | none | `uv run --locked pytest code/` |
| | `validate_catalog_data` | every PR | read-only set | the loader with `--dry-run` |
| | `check_schema_in_sync` | every PR | read-only set | `apply_ddl.py --check`, once per migration stream, with stream-aware `--allow-pending` computed from the PR's own added migration files |
| | `validate_ref_data` | PRs touching a ref-gate leg (`data_ref/`, `data_catalog/sources/ref/`, `code/apply_ddl/ddl_ref/`) | read-only set | `load_ref_data.py --dry-run`, with the escape-hatch flags computed from the PR's own diff |
| `post_merge` (`on: push` to `main`) | `unit_tests_main` | pushes touching `code/` | none | `uv run --locked pytest code/` |
| | `load_catalog_data` | pushes touching corpus YAML | write set (`metadata-db-write` environment) | the real load |
| | `revert_failed_load` | only when `load_catalog_data` failed (`needs:` plus a result check, re-running the same path gate before pushing) | write environment plus `CLEANUP_BOT_TOKEN` | `revert_merge.py --commit-sha "$GITHUB_SHA"` |

Conventions shared by every job: the dormancy gate (`if: vars.METADATA_DB_CI_ENABLED == 'true'`); the runner label set (`runs-on: [self-hosted, linux, docker]`); `timeout-minutes: 30`, so a hung job never holds the shared runner for the 360-minute default; a `python:3.14-slim` container matching `requires-python`, with git installed before checkout (the slim image lacks it, and a git-less checkout silently falls back to an API tarball with no `.git` directory); `persist-credentials: false` on every checkout; uv installed by pinned version (`uv==0.11.1` — the one dependency `uv.lock` cannot cover); and `--locked` on every `uv run`, failing on a stale lockfile instead of silently rewriting it. Both workflows declare `permissions: contents: read`. `pre_merge` cancels a superseded run per ref; `post_merge` serializes with `cancel-in-progress: false`, because a post-merge run is not disposable.

**No-op runs.** Commits to `main` that don't touch any YAML under `data_catalog/` (docs, CI changes, loader-script edits) skip the load step — and, by the mirrored change-detection gate, the revert — entirely, so they have no DB-side effect. The pre-merge jobs still run on every PR regardless of what changed.

**Diff previews** can be added as an extra pre-merge job that runs the loader against an ephemeral copy of the production DB and posts a comment on the PR showing what rows would be added / updated / deleted. Useful for high-impact changes; deliberately not built yet.

### GitHub platform settings

Repository-level configurations that make the post-merge load + revert flow safe. All live on `https://github.example.com/Warehouse/metadata_db` (Settings), none in repo files.

#### Branch protection on `main`

The foundation everything else builds on: a branch protection rule (or ruleset) on `main` that **requires a pull request before merging** with at least one approving review, requires the pre-merge jobs (`unit_tests`, `validate_catalog_data`, `check_schema_in_sync`, `validate_ref_data`) as **required status checks**, and forbids force pushes. Disable **"Automatically delete head branches"** alongside it: a failed load's auto-revert leaves `main` reverted while the work still needs to land, and the surviving branch is what the author pushes fixes onto. Optionally add **"Require review from Code Owners"** — but only after the CODEOWNERS team slugs exist and are staffed (see [Ownership routing](#ownership-routing)). The only bypass is the cleanup bot's (see [Cleanup bot account](#cleanup-bot-account)); no human pushes to `main` directly.

#### Merge serialization

The post-merge load + potential revert must complete before any subsequent PR is allowed to merge. Otherwise a PR2 can land on top of PR1 while PR1's load is still running, and if PR1's load then fails, the revert can no longer cleanly undo just PR1 — `main` and the DB drift out of sync in a way the design has no automatic recovery for.

What GitHub offers, layered:

- **Merge queue** (availability depends on the GHES version — confirm with the instance administrators). The queue serializes the *merges themselves* and re-validates each queued PR against the merges ahead of it. Its gate is the PR's required checks, though — it does **not** hold the next merge for the previous merge's *post-merge* workflow, so even with the queue on, a second merge can land while the first merge's load is in flight. Enable it if available; it removes the racing-merges failure mode without fully serializing merge -> load -> next merge.
- **Actions `concurrency` group** on `post_merge.yml` (committed, no setting needed) — serializes the post-merge *runs*: a second merge's run queues behind an in-flight load instead of racing it at the DB. This replaces the semaphore/resource-group workaround the GitLab-era design documented. GitHub keeps only the newest pending run per group (an intermediate queued run is superseded), which is safe for the *load* because the loader is full-state — the newest run loads the complete corpus at its own commit, covering any superseded merge's changes. It is not a blanket guarantee for the run: a superseded run also drops `unit_tests_main` for its commit, so a `code/` change merged between two rapid merges goes untested on `main` until the next `code/` merge.
- **The residual overlap is safe-but-manual.** If PR2 merges while PR1's load runs and PR1's load then fails, the auto-revert cannot cleanly undo just PR1 — but it does not make things worse: `revert_merge.py` verifies HEAD *is* the failed merge commit before doing anything, and since PR2 has landed on top, **HEAD no longer matches and the revert refuses** (exits non-zero, pushes nothing — see [The revert script](#the-revert-script) "Refusal"). The result is `main` ahead of the DB with no bad push, flagged by the failed run. **Recovery runbook:** a maintainer (a) confirms which merge failed to load, (b) decides whether to hand-revert the offending merge or fix-forward with a corrective PR, then (c) runs the loader manually against `main` until `--dry-run` and a real run are clean and `main` and the DB agree again.

**DB-level backstop.** Merges are not database sessions — a manual loader run can still overlap an in-flight CI load regardless of the platform settings. The loader therefore takes a fail-fast transaction-scoped advisory lock (`pg_try_advisory_xact_lock`, scoped to the `(database, schema)` target) as the first statement of every run: a second concurrent loader exits immediately with "another metadata_db load is already in progress" rather than reading-then-writing against stale state. DB correctness never depends on the platform serialization holding — the lock enforces single-writer regardless of how the loader was invoked.

#### Merge method and fork PRs

Two more repository settings the revert flow depends on:

- **The merge method must be "merge commit" only.** Settings -> General -> Pull Requests -> enable **"Allow merge commits"** and **disable "Allow squash merging" and "Allow rebase merging"**. `revert_merge.py`'s precondition is that the failed commit is a **2-parent merge commit** (`git revert -m 1`); squash and rebase merging both produce single-parent commits, which would silently make the auto-revert refuse *every* incident (HEAD-is-a-merge check fails) and leave the GitOps invariant unenforceable. This is easy to miss because nothing else in the workflow cares about the merge method — unlike GitLab, GitHub enables squash/rebase alongside merge commits by default, so this is an explicit cutover step, not a default. If squash merges are ever wanted, the revert script needs a redesign (revert the squashed commit itself) first.
- **Fork PRs receive no secrets.** Secrets are withheld from fork-originated `pull_request` runs by default, so the RO-credentialed PR jobs cannot pass from a fork, and the write set is doubly unreachable (it lives in the `main`-restricted environment). That is the intended posture for this internal repo — contributions come from project members on branches, not forks. If external contribution via forks is ever needed, it requires a deliberate redesign of the credential plumbing.

#### Load-failure operational hazards

Two failure modes the auto-revert cannot cover; watch for them by hand:

- **A *cancelled* load is not a *failed* load.** The `revert_failed_load` job fires only on `needs.load_catalog_data.result == 'failure'`. A job a maintainer manually **cancels** (or one killed by a runner timeout) ends in the `cancelled` state, which is neither success nor failure — so **no revert runs**, yet the merge is on `main` while the DB never moved. After any manual cancellation of a `load_catalog_data` job, check `main` against the DB (a `--dry-run`, or `apply_ddl.py --check`) and run the loader manually to catch the DB up.
- **Never re-run a failed load job once its revert has landed.** A failed load triggers the revert, which removes the offending merge's content from `main`. Clicking **"Re-run failed jobs"** (or "Re-run all jobs") on that original run afterward re-runs at the run's original commit — loading a corpus that `main` has since reverted, re-introducing exactly the change the revert removed. If a load failed for a transient reason (e.g. a DB blip) and you want to try again, run the loader manually against the current `main` (or land a trivial corpus PR) instead of re-running the reverted job.

#### Cleanup bot account

The dedicated identity that [`code/revert_merge/revert_merge.py`](#the-revert-script) authenticates as in order to push directly to `main`. GitHub's built-in `GITHUB_TOKEN` **cannot push to a protected branch**, so the bot must be its own identity with an explicit branch-protection bypass — mirroring the GitLab-era Maintainer-role project access token. Two forms work:

- **A machine-user account with a fine-grained PAT** (simplest): a dedicated user (e.g. `metadata-db-cleanup-bot`) with write access to this repo only, holding a fine-grained personal access token scoped to this repository with **Contents: read and write** permission. The committed `remote_url_template` (`https://x-access-token:{token}@…`) accepts a PAT in the token position as-is.
- **A GitHub App** installed on the repo with **Contents: read and write**: tighter-scoped and org-owned, but the workflow must mint a short-lived installation token per run before invoking the revert script — an extra moving part; adopt it if org policy prefers Apps over machine users.

**Setup (one-time):**

1. **Create the bot identity** (machine user + fine-grained PAT, or GitHub App) as above.
2. **Store the token as `CLEANUP_BOT_TOKEN`** in the **`metadata-db-write` deployment environment** (Settings -> Environments), with the environment restricted to the `main` branch — only post-merge runs on `main` can read it.
3. **Grant the bot its bypass on `main`.** In the `main` branch protection rule, add the bot (or App) to the **bypass allowance** ("Allow specified actors to bypass required pull requests" and, on rulesets, the bypass list) so it — and only it — can push directly to `main`. Everything else, humans included, goes through PRs with required reviews; that required review is the human review gate. Keep force pushes disabled for everyone.
4. **Fill the bot's git identity** (`bot_name` / `bot_email`) in `code/revert_merge/config/revert_merge.toml` — `git revert` commits under it.
5. **Rotate the token on its expiry.** Fine-grained PATs carry an expiration date (1 year maximum). Create a calendar reminder; rotation is "create new token, swap the environment secret, delete old token" — minutes of work. (A GitHub App sidesteps rotation but adds the per-run token-minting step above.)

## Manual workflow (when no CI runner is available)

**This is the current operating mode.** The CI design above assumes a self-hosted Actions runner is registered and reachable — github.example.com is GitHub Enterprise Server, which provides no GitHub-hosted runners, so runner provisioning belongs to whoever administers the instance. Until a runner exists (and whenever it is down), the workflow files are captured but dormant (`METADATA_DB_CI_ENABLED` unset). Maintainers run the same scripts by hand. The GitOps invariant ("main is the source of truth, Postgres is derived") still holds; it just isn't enforced by automation.

### What still works without a runner

| Capability | With CI | Without CI |
|---|---|---|
| YAML is the source of truth | yes | yes |
| Loader applies YAML -> Postgres | automatic post-merge | maintainer runs manually |
| Pre-merge validation | automatic | author runs `check_corpus.py` (no DB needed); a maintainer's `--dry-run` covers the two diff-time rules |
| `_hstry` writes on every change | yes | yes |
| Schema-drift safeguard | automatic | maintainer runs `--check` manually |
| Loader-failure recovery | automatic `revert_merge.py` | maintainer runs `git revert` by hand |
| Audit trail (git log + `_hstry`) | yes | yes |

The cost of operating manually is *discipline-dependent*: nothing forces a maintainer to run the loader after merging. A skipped run leaves `main` ahead of the DB until the next run catches up.

### Runbook — what a maintainer does

**Before opening a PR that touches `data_catalog/`.** Authors run `check_corpus.py` — no database access needed (see CONTRIBUTING.md, *Before you start*). A maintainer with credentials can additionally run the loader in dry-run mode against the live `metadata_db`, which also exercises the two diff-time rules (`update_reason` discipline and the mass-delete guard) that the offline check cannot:

```bash
uv run code/load_catalog_data/load_catalog_data.py \
    --config code/load_catalog_data/config/load_catalog_data.toml \
    --dry-run
```

Fix any errors in the YAML and re-run until the dry-run validates cleanly (the reported diff is your change preview), then open the PR.

**Before merging any PR that adds a new migration under `code/apply_ddl/ddl_catalog/`.** Check that the live DB has every prior migration applied. Refuses to proceed if the DB is missing a migration that's already in the repo.

```bash
uv run code/apply_ddl/apply_ddl.py \
    --config code/apply_ddl/config/apply_ddl_catalog.toml \
    --check
```

If `--check` reports drift, the missing migration needs to be applied first (see [Applying migrations](#applying-migrations) above) before any further loader runs make sense.

**After merging a PR that touches `data_catalog/`.** Pull latest `main` and run the loader for real. The loader applies the diff inside one transaction; on failure, it rolls back and exits non-zero.

```bash
git checkout main && git pull
uv run code/load_catalog_data/load_catalog_data.py \
    --config code/load_catalog_data/config/load_catalog_data.toml
```

If this succeeds: `main` and the DB are back in sync. Done.

If this **fails**: `main` is ahead of the DB, mirroring the post-merge failure case the CI design handles automatically. Recovery:

```bash
git revert --no-edit -m 1 <merge-sha-of-the-failed-PR>
git push origin main
```

The merge commit is now reverted; `main` matches the (unchanged) DB. The PR author keeps working on the original branch (the repository setting "Automatically delete head branches" is disabled precisely for this), pushes fix commits, and opens a new PR against the now-reverted `main`.

**Re-landing after an auto-revert.** The revert undid the author's merge, so the original commits are no longer on `main`. The author does **not** need to recreate the branch — its commits still exist. From the (undeleted) source branch:

```bash
git checkout <source-branch>
git fetch origin && git rebase origin/main   # rebase onto the reverted main
# fix the YAML that made the load fail; then:
git commit -am "Fix <whatever the loader rejected>"
git push --force-with-lease                  # branch was already pushed; update it
# open a new PR against main
```

The new PR re-applies the original change (now rebased) plus the fix. Because `main` was reverted, there is no revert-of-a-revert to reason about — the branch simply lands cleanly on top of the reverted `main`.

**Periodically (or before any batch of merges).** A `--dry-run` against `main` confirms the loader still validates cleanly. Useful as a sanity check after multiple unrelated merges.

### Discipline gaps to be aware of

The manual workflow leaves three places where a careless maintainer can break the GitOps invariant. None are catastrophic — git history and `_hstry` retain the audit trail — but they're worth knowing:

1. **A PR can be merged with no pre-merge validation at all** — no `check_corpus.py` run by the author, no dry-run by a maintainer. Bad YAML lands on `main`; the maintainer discovers it when the post-merge loader run fails.
2. **A maintainer can forget to run the loader after a merge.** `main` drifts ahead of the DB silently.
3. **A migration PR can land without applying the migration.** Subsequent loader runs fail with "column doesn't exist" or similar; the maintainer discovers the gap and applies the missing migration.

When a runner becomes available, automating the corresponding CI jobs closes all three gaps.

## Backups & disaster recovery

The GitOps invariant covers **current state only**. `main` is the source of truth for what's in the main tables, and a fresh database can be rebuilt by running migrations followed by the loader. `_hstry` is *not* in YAML by design (the files explicitly hold current state only), so superseded versions live exclusively in Postgres.

This makes `_hstry` durability dependent on standard Postgres backups, not on the GitOps flow. Any deployment of metadata_db needs:

- **Routine `pg_dump` (or equivalent platform-specific snapshot) of the whole database.** Cadence and retention follow the org's existing Postgres operations; daily snapshots with multi-week retention is a reasonable default.
- **Periodic restoration tests.** Backups that have never been restored often aren't backups. Verify the restore path against an ephemeral test database at least once after initial setup.
- **Off-site storage.** Snapshots co-located with the DB don't survive a regional outage.

**Recovery scenarios:**

- *DB lost, recent backup available* — restore from backup, then run the loader to bring main tables up to current YAML. `_hstry` carries whatever was in the backup; anything between backup point and DB loss is gone.
- *DB lost, no recoverable backup* — run migrations against a fresh database, then the loader. Main tables come back; `_hstry` starts empty; all prior history is permanently lost.

The asymmetry is intentional: the GitOps invariant exists to make current state reproducible from the repo, not to act as a backup of historical state. Historical durability is a database-operations responsibility.

## Other things to consider
- **Consider multi-level `table_relationships.yaml` placement** — a relationship may span schemas or data sources (its endpoints just have to be co-deployed somewhere), but today it is always authored in one schema's folder, anchored to `table_a_id`'s source schema (see CONTRIBUTING.md, *Add a join relationship*). One future option is to place each relationship at the narrowest level containing both tables (schema / data source). **This is not currently supported by the loader:** `yaml_discovery.decode_path` only classifies `table_relationships.yaml` at the schema level — a file at the data-source level raises "Unrecognized YAML location". Adopting it would require a loader change in addition to an authoring convention and CODEOWNERS routing.
- **Full-reload scaling ceiling.** Each loader run reads the *entire* corpus (all YAML) and the *entire* DB state into memory and diffs in Python — it is O(corpus), not incremental. This is a non-issue for a human-curated catalog (thousands of rows), and correctness depends on it (delete-by-absence and global FK/expression-ref validation both need the complete picture). If the corpus ever grows to millions of rows — e.g. via automated inventory scraping — the fix is to push the diff into SQL (COPY the corpus into staging tables, diff via SQL joins), which preserves whole-corpus semantics while moving the set work to Postgres. Do **not** switch to path-scoped partial reads: that breaks delete-by-absence and cross-subtree reference validation.
