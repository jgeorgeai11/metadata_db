"""Validate the catalog corpus without a database connection.

The loader's first three steps — discovery, assembly, corpus validation —
are pure functions of the YAML on disk: they need no Postgres. Only the
diff-time checks (`update_reason` discipline and the mass-delete guard)
compare against live rows, because only they can.

This script runs exactly those first three steps, so a contributor with
no database access can still check every file-shape and corpus-validation
rule (CONTRIBUTING.md waves 1 and 2) before pushing. A clean run here means
the pre-merge job will only ever fail on the two diff-time rules.

Usage:
    uv run code/load_catalog_data/check_corpus.py
    uv run code/load_catalog_data/check_corpus.py --config code/load_catalog_data/config/load_catalog_data.toml

The corpus root comes from the loader's own TOML config (`data_root`),
so this checker always validates exactly what the loader would load;
`--config` defaults to the loader's config file, which keeps the
zero-argument run above working. Only that default path is
cwd-independent — the corpus root inside the config is relative
(`data_catalog`) and resolves against the working directory, so run the
commands above from the repo root, as the loader is run.

Exit status is 0 when the corpus is clean and 1 when it is not; the
issues are printed exactly as the loader reports them — to stderr for
the person running it, and to `logs/load_catalog_data/check_corpus.jsonl`
for anything reading runs after the fact.
"""

import argparse
import logging
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "code" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from logconfig import get_logger, setup_logging  # noqa: E402

from corpus_assembly import assemble_corpus  # noqa: E402
from corpus_validation import ValidationError, validate_corpus  # noqa: E402
from yaml_discovery import discover_yaml_files  # noqa: E402

logger = get_logger(__name__)


def _mirror_log_to_stderr() -> None:
    """Also emit this run's log lines to stderr as plain text.

    `setup_logging` installs a JSON file handler, which is the right
    artifact for a CI run and the wrong one for a contributor at a
    terminal: reporting problems to a person is this script's entire job,
    and a run that says nothing while leaving its findings in
    `logs/load_catalog_data/check_corpus.jsonl` reads as "nothing
    happened".

    Attached to the ROOT logger, so the loader stages' own INFO lines
    (discovery counts, the assembly summary, the validation result) come
    through as well — they are the progress report, and their absence is
    what made a clean run indistinguishable from a no-op.

    stderr rather than stdout: the exit status is this script's
    machine-readable output, so keeping the prose off stdout leaves it
    free for a caller to redirect or pipe.
    """
    # Loader messages carry non-ASCII punctuation (em dashes, the `→` in
    # the determinism and cardinality messages). An interactive Windows
    # console already takes UTF-8, but a REDIRECTED stderr falls back to
    # the locale encoding (cp1252 here), which cannot encode `→` and
    # degrades it to a `\u2192` escape mid-sentence. Ask for UTF-8 so a
    # piped or captured report reads the same as the terminal one; the
    # guard keeps this working if stderr is replaced by a plain object
    # (as pytest's capture does) rather than a text stream.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    # Plain message text — the JSON envelope (timestamps, logger names,
    # run id) serves the log file, not a person reading a terminal.
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)


def main() -> None:
    """Assemble and validate the corpus, reporting issues and exiting 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Validate the catalog corpus offline (no database needed)."
    )
    # Defaulted rather than required (the loader's is required): the
    # zero-argument run CONTRIBUTING.md advertises must keep working. The
    # default resolves from this file's own location so the config file is
    # found from any working directory (the corpus root inside it stays
    # cwd-relative, so the run itself is done from the repo root).
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config" / "load_catalog_data.toml",
        help=(
            "Path to the loader's TOML configuration file "
            "(default: config/load_catalog_data.toml next to this script)."
        ),
    )
    args = parser.parse_args()

    setup_logging(log_dir="logs/load_catalog_data")
    _mirror_log_to_stderr()
    logger.info("=" * 60)

    # The corpus root comes from the loader's config so the two scripts can
    # never disagree about what "the corpus" is; only `data_root` is read —
    # the connection fields are the loader's business.
    if not args.config.exists():
        logger.error(f"Config file not found: {args.config}")
        logger.info("=" * 60)
        sys.exit(1)
    try:
        with open(args.config, "rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Failed to read config file: {e}")
        logger.info("=" * 60)
        sys.exit(1)
    try:
        data_root = Path(config["data_root"])
    except KeyError as e:
        logger.error(f"Missing required config field: {e}")
        logger.info("=" * 60)
        sys.exit(1)

    logger.info(f"Validating corpus at {data_root} (no database connection)")

    try:
        files, discovery_issues = discover_yaml_files(data_root)
        corpus = assemble_corpus(files, discovery_issues)
        validate_corpus(corpus)
    except ValidationError as e:
        # AssemblyError is its assembly-stage subclass, so this one arm
        # covers both stages; e.summary names the failing stage. One
        # record per issue keeps the JSONL log machine-friendly (no
        # multi-line messages), mirroring the loader.
        logger.error(e.summary)
        for issue in e.issues:
            logger.error(f"  - {issue}")
        logger.info("=" * 60)
        sys.exit(1)
    except FileNotFoundError:
        logger.error(f"Corpus root not found: {data_root}")
        logger.info("=" * 60)
        sys.exit(1)

    logger.info(
        f"Corpus OK — {len(corpus.data_sources)} data sources, "
        f"{len(corpus.tables)} tables, {len(corpus.columns)} columns, "
        f"{len(corpus.table_relationships)} relationships, "
        f"{len(corpus.column_mappings)} mappings, {len(corpus.concepts)} concepts"
    )
    logger.info("Not checked here (needs the database): update_reason discipline, mass-delete guard")
    logger.info("=" * 60)


if __name__ == "__main__":  # pragma: no cover
    main()
