---
name: cr_20260812v01_CODEOWNERS
goal: Address code quality issues identified in .github/CODEOWNERS to align with the project's ownership-routing conventions, reviewed together with .github/workflows/pre_merge.yml and .github/workflows/post_merge.yml.
created: 2026-08-12 13:56:26
updated: 2026-08-12 14:31:05
---

## Implementation Plan

1. [completed] Route the tracked data directory the rules do not name - `.github/CODEOWNERS`
   - 1.1. [minor] Lines 26-29: `data_samples/` is tracked (54 files, currently the vendor-supplied OCS layout references under `data_samples/ocs_ref/`) and is source material for the `ocs` catalog, but no rule names it, so it falls to the `*` catch-all and a change to it never reaches the `ocs` steward that owns the catalog it feeds. Every other data-bearing path in the repo (`data_catalog/sources/*`, `data_ref/`) is routed explicitly, and this file's own convention is to state routing even when it is redundant ("Technically redundant with the catch-all above, listed for documentation", lines 32-33), so the silence here reads as an omission rather than a decision. It is also absent from the repo-layout tree in `readme/metadata-db-maintenance.md`, so no other document settles the question.
        - Current: `data_ref/                                           @Warehouse/data-ops`
        - Expected: `data_ref/                                           @Warehouse/data-ops\n\n# ----------------------------------------------------------------------\n# Sample / reference source material. Vendor-supplied layout files that\n# the catalog is derived from, so they route to the steward of the\n# source they document.\n# ----------------------------------------------------------------------\ndata_samples/ocs_ref/                               @Warehouse/data-ops`
        - Resolution: Implemented with a broader route than specified. The 54 tracked files are not all OCS: `data_samples/` holds `ocs_ref/` (40 files, OCS layout references), `edw_ref/` (13 files, EDW reference material), and `opi_ref/` (1 file). Routing only `ocs_ref/` would have left `edw_ref/` — source material for the `edwc_prd` catalog — on the same catch-all the finding objects to, so the new block mirrors the corpus-root pattern instead: `data_samples/` to `@Warehouse/metadata-db-maintainers` (so a sample folder added for a source with no rule yet routes to maintainers, as at the `data_catalog/` root), then `data_samples/ocs_ref/` and `data_samples/edw_ref/` to `@Warehouse/data-ops`, the steward named by both sources' `data_source.yaml` `owner`. `opi_ref/` documents no catalogued source, so it stays on the maintainer route and the comment says so and says to add a rule when a source exists. Two documentation edits accompany it, since the finding cites the docs' silence as part of the gap: the CODEOWNERS excerpt in `readme/metadata-db-maintenance.md` (Ownership routing) gained the same block, and the repo-layout tree gained a `data_samples/` entry with one line per sample folder.

2. [completed] Optional refinements - `.github/CODEOWNERS`
   - 2.1. [suggestion] Line 52: The `data_catalog/systems.yaml` rule is inert. The preceding `data_catalog/` rule (line 51) already routes it to the same team and no later rule matches it, so the line changes nothing — yet, unlike the plumbing block, its comment (lines 41-44) presents it as load-bearing ("the venue registry is infrastructure, so it is maintainer-owned too") rather than as documentation of an already-correct route.
        - Resolution: Deferred — optional; the rule is harmless and it survives a future reordering that the parent rule would not, which is a reasonable thing to want from the registry's route. If it is kept, the useful edit is one clause in the comment saying it is redundant with `data_catalog/` today, matching how the plumbing block labels its own redundancy.
   - 2.2. [suggestion] Lines 54-61: Nothing keeps these per-source rules in agreement with each source's `data_source.yaml` `owner`, which `readme/metadata-db-maintenance.md` (line 148) names as the source of truth they are derived from. Both directions drift silently: a new source folder merged without a rule here routes to maintainers, and an `owner` edit routes nowhere at all. A pre-merge check comparing the two is the natural enforcement point — see the matching finding in `cr_20260812v01_pre_merge.md` (5.4).
        - Resolution: Deferred — enforcing the agreement needs a new script and its own tests rather than an edit to this file; today the pairing is checked by the maintainer review that any `data_source.yaml` or CODEOWNERS change already requires, and both drift directions degrade safely to maintainer routing rather than to no review.
   - 2.3. [suggestion] Lines 54-61: A source's own steward team owns its `data_source.yaml`, including the `owner` field that names the accountable team — so a steward can rewrite the repo's record of who is accountable for its data without a maintainer in the loop. Adding a rule after the per-source block (`data_catalog/sources/*/data_source.yaml @Warehouse/metadata-db-maintainers`, last match winning) would route ownership changes to maintainers.
        - Resolution: Deferred — the `owner` field is documentation, not permission: routing is decided by this file, which is maintainer-owned via the `.github/` rule, so a self-serving `owner` edit grants nothing and only creates a disagreement that review catches. Against that, the rule would put a maintainer in the loop for every routine `description` edit in the same file, which is real friction for a governance gap that is already bounded.
   - 2.4. [suggestion] Lines 71-83: The dual-owner mappings rule lists a single handle today because both sides resolve to `@Warehouse/data-ops`, which makes it identical in effect to the `data_catalog/sources/ocs/` rule above it, so it is currently unexercised — the intended two-team behavior will only be observed the first time a source changes hands.
        - Resolution: Deferred — the rule is a deliberate placeholder and the comment already says so, spelling out how to extend it when stewards diverge; removing it would lose the documented intent and the header (lines 8-13) already records the caveat that any ONE owner satisfies a multi-owner rule.

## Skills with No Issues

1. Comments skill: No issues found — the header states the parsing rule, the enforcement precondition, and the provenance of each handle, and every block comment matches the rules beneath it
2. Docstrings skill: N/A - not Python
3. Type Hints skill: N/A - not Python
4. Logging skill: N/A - not Python
5. Exception Handling skill: N/A - not Python
6. Executable Scripts skill: N/A - a declarative routing table, not an executable script
7. Unit Tests skill: N/A - not testable in this repo; GitHub parses the file server-side
8. Data Validation skill: N/A - contains no data processing
9. SQL Development skill: N/A - contains no SQL
