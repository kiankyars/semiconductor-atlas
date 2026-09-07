# EEA scope-revision contract

Status: implemented with a real evidence-only correction, exact replay and portable restoration.

Scope decisions and source statements have separate histories. Later scope withdrawal must not
delete, supersede, or date-close an exact EEA source statement, and must not assert facility closure.
The existing v1/v2 import is the historical genesis. Its original proven admission time remains
unchanged; a later v3 revision is recorded only at its actual admission clock.

## Required behavior

- Retain one complete, evidence-bound decision for every candidate in the unchanged snapshot and
  queue. A different source edition needs a separately versioned source-refresh contract.
- Record revisions as immutable core ingestion runs with the expected predecessor's ID and hash,
  the exact review bytes/hash, actual admission time, and explicit document lineage. No new tables
  or database migration are necessary.
- Reuse all original records and claims for previously admitted candidates. First acceptance adds
  only exact source scalars with unknown effective dates and confidence. Evidence-only corrections,
  withdrawal and reacceptance create no duplicate source statements.
- Compare actual UTC instants with microsecond precision. Cutoff queries select only revisions
  admitted by that cutoff. Historical replay verifies the original event without moving the head.
  Reacceptance requires a fresh explicit review, not resubmission of an old artifact.
- Validate the whole predecessor chain and exact materialization graph, rejecting stale heads,
  forks, missing or extra lineage, altered bindings, and nonmonotonic clocks. Reverify immutable
  inputs and roll back all changes on any failure.
- Provide an explicit scoped consumer and portable historical export. Unfiltered source-knowledge
  queries remain audit views and must not be represented as current in-scope membership.
- Restore only into a fresh derivative of a verified parent, retaining original clocks as historical
  restoration. A restore must not authorize arbitrary backdated admission into an existing database.

The frozen source-project database, registered prospective detector study and its pinned producer
code remain unchanged. Successful revision mechanics do not establish operating capacity, detection
performance, calibrated forecasts or the full north-star.

## Source history versus current scope

| Review transition | Retained source statements | Scoped consumer |
| --- | --- | --- |
| Accepted to accepted, including changed evidence | Original IDs and knowledge clocks reused | Included |
| Accepted to deferred or rejected | Preserved without supersession or valid-time closure | Excluded from the new admission time |
| Never admitted to accepted | Exact source scalars inserted once, effective time/confidence null | Included from actual admission |
| Previously admitted to accepted again | Original IDs and clocks reused | Included again |

`eea_scope_revisions.py` uses the original v1/v2 ingestion run as genesis, not a newly backdated
adoption event. Each v3 run binds the genesis and predecessor row hashes, complete review bytes,
unchanged snapshot/queue, cumulative original materialization links and newly admitted candidate IDs.
It retains explicit links to both source documents even when no candidate is newly materialized.
All changes occur inside one SQLite transaction; a caller-owned transaction remains caller-owned.

The original v1/v2 importer remains single-review and byte-unchanged. Its ceiling is not bypassed.
The separate revision entry point rejects stale predecessors, changed snapshot/queue bindings,
regressing clocks, reused review artifacts under another predecessor, and broken row/claim/document
lineage. Identical historical revision replay returns that revision without moving the current head.
The original v1 representation is preserved rather than silently correcting old effective dates or
confidence values. These legacy values are not newly calibrated probabilities.

The scoped report is a source-knowledge export, not a canonical-facility or physical-world view.
It contains the selected revision, cutoff-visible history, all 108 dispositions, original
materialization references and only the currently in-scope source statements. Generic unfiltered
source exports intentionally keep withdrawn records for audit; they must not be mistaken for this
scope-filtered view.

## Real correction and retained artifacts

The [first admission audit](eea_scope_admission_2026-09-07.md) retained an early rehearsal whose
Newport decision omitted the issuer process passage. A working derivative of that rehearsal now
appends the already reviewed correction through the new route. Neither the rehearsal nor the
separately validated v2 candidate was overwritten. The earlier missing-evidence decision is retained
as history, not presented as the current supported decision.

- Genesis run: `aa0b010a-9d9a-52c1-9799-014ba99bb519`, admitted at
  `2026-09-07T17:43:24.540118Z`.
- Revision run: `4f808891-967d-506d-98d7-d7b8c762d438`, admitted at
  `2026-09-07T18:37:18.522671Z`; processing began at `2026-09-07T18:36:56.357884Z`.
- Review: `review_plans/2026-09-07-eea-industrial-v16-scope-admission-v2.json`, SHA-256
  `c0f724bac16d64f9dfb30a04a6469e5263874b155bd6dc89146bad3daa5ae34b`.
- Working database: `artifacts/2026-09-07-eea-scope-revisions-v3.sqlite`, 238,895,104 bytes,
  SHA-256 `7b60f67911a0cb73ce17e47451d534c6e5738e7cd804398734fdf652385db778`.
- Portable history: `artifacts/2026-09-07-eea-scope-history-v3.json`, 1,595,997 bytes,
  SHA-256 `574a4cc57407d3b41a651c213893019ed4bc17b2c498dbfc782015a901b29aa5`.
- Verified pre-EEA parent: `artifacts/2026-09-07-source-project-targets.sqlite`, SHA-256
  `18c0d0ad1d6ddbbd9d97b376f2a98d35fea16676cb79df46db317df045e65b82`.
- Restored working database: `artifacts/2026-09-07-eea-scope-restored-v3.sqlite`.

Only Newport's evidence/reason changed. The 15 accepted and 93 deferred outcomes, all 132 source
statements, and their original IDs and knowledge clocks remain identical. This revision added no
entity, source record or claim. Table-by-table comparison preserved every genesis row; the only
additions were one ingestion run and three document-role links. Real exact replay attempted zero
INSERT/UPDATE/DELETE operations with those operations denied, and left the working database hash
unchanged. `completed_at` retains the explicit prewrite validation/admission meaning used by the v2
importer; it is not a post-commit wall-clock completion timestamp.

The final suites passed 1,249 core and 12 web tests. New tests cover all four transition types,
all-deferred and legacy genesis, microsecond cutoffs, old-event replay under SQLite write denial,
stale heads, clock failures, rollback after writes, caller-owned transactions, extra lineage,
portable reconstruction, continuation after restoration, tampered predecessor bindings and CLI
no-overwrite behavior. The real restored history reproduced an identical portable export and
identical scoped views just before/at both the genesis and revision admission clocks.

## CLI

Append a genuinely new complete review to an existing EEA working database:

```sh
semiconductor-atlas accept-eea-scope-revision \
  --database path/to/working.sqlite \
  --snapshot source_snapshots/2026-07-20-eea-industrial-v16-semiconductor-candidates \
  --candidate-queue review_plans/2026-07-20-eea-industrial-v16-candidates.json \
  --review path/to/new-complete-review.json \
  --expected-predecessor-run-id CURRENT_HEAD_RUN_ID \
  --accept-now
```

For exact historical replay, use the original review, its original predecessor, and
`--accepted-at ORIGINAL_ADMISSION_CLOCK` instead of `--accept-now`. Never supply a past clock for a
new review. A reacceptance needs a newly authored review with advancing review/admission clocks.

Inspect the retained real correction:

```sh
semiconductor-atlas eea-scope-report \
  --database artifacts/2026-09-07-eea-scope-revisions-v3.sqlite \
  --candidate-queue review_plans/2026-07-20-eea-industrial-v16-candidates.json \
  --recorded-at 2026-09-07T18:37:18.522671Z
```

`export-eea-scope-history` takes `--database`, `--parent-database`, `--candidate-queue`,
`--recorded-at` and a new `--output` JSON path. `restore-eea-scope-history` takes the exact
`--parent-database`, `--history` JSON and a new `--output-database`. Both refuse existing output
targets; restore validates in a private staging database before no-replace publication. It does not
edit the parent or admit fresh claims with invented historical clocks.

The portable packet contains the exact queue, cutoff-selected core row graph, complete stored
reviews and scoped report. It is parent-bound EEA history, not a replacement for unrelated later
core work, the original 2.03 GB source snapshot, or exact-byte external review-source archives.
Continuing admission still requires the verified source snapshot. SQLite file bytes need not match
after reconstruction; logical rows, IDs, clocks and canonical exports must match.

## Still open

Different EEA source editions require explicit source-refresh semantics. The 93 deferred records
still require their documented evidence or scope decisions. Canonical identity, tabular-spatial
correspondence, operating/qualified capacity, blind detection evaluation, forecast calibration and
the downstream Supply Intelligence adapter are separate unfinished gates. This change does not
modify or restart the frozen prospective study.
