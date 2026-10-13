# Completed source-reported milestones in the core

This repository-checkout workflow admits an already reviewed operating-fab cohort into an
isolated schema-6 database. It records retrospective publisher statements, not independently
verified physical outcomes, canonical identity, HVM, capacity, or evaluated forecasts.
The [September 8 local audit](completed_milestone_core_2026-09-08.md) records the actual 16-report
admission, preserved parent rows, exact hashes and write-denied replay.

## Precision and clocks

Migration 6 rebuilds only the leaf `milestone_values` table. Expected **and completed** source
milestones can retain literal calendar precision and a null midpoint. Legacy midpoint values,
hashes, parent rows, evidence links and immutable triggers remain unchanged. Calendar validation
continues in the model and database validator; SQL enforces the period/status shape.

The imported event is exactly `commercial_production_commencement`. A statement such as `2024`
becomes January 1 through December 31 of that year, not a July midpoint, probability interval,
HVM date or facility lifecycle summary. Confidence and effective dates remain null.

Each new core claim uses the actual current core-admission clock. The earlier sidecar admission
is preserved separately in its source record, including Fab 21's carried-forward observation.
These records appear in the knowledge-time source-claim view only after core admission, and
remain excluded from physical-world current claims. Neither historical event years nor source
publication/retrieval dates create earlier core knowledge or pre-outcome predictions.

## Verification and admission

The command requires an existing database with exactly schema 6 and matching migration hashes;
it never creates or migrates one. Retain schema-5 parents unchanged and prepare a separate copy.
Both exact cohort-file and frozen-runtime hashes, plus the runtime Git commit, are explicit trust
inputs. The existing [frozen verifier](frozen_runtime.md) checks the complete source table,
provenance, prior observation, review and cohort in an isolated historical process. The importer
binds its returned byte counts and hashes to the same local bytes, then rechecks inputs and its
own code at the transaction boundary. No acquisition is performed.

One atomic admission stores the full accepted/deferred inventory and verification metadata.
Each accepted case creates one explicitly source-native facility and one completed milestone;
deferred cases create source records but no facility or claim. Row support plus shared header
and introduction context use exact UTF-8 byte spans and hashes. Publisher bodies and excerpts
are not embedded. The complete operating-only selection and survivorship-bias disclosures stay
with the admission; this is not a historical project census or a training dataset.

Run from this isolated source checkout, with all evidence paths pointing to the original retained
files. This CLI depends on the repository's `scripts` runtime controllers and is not an installed
wheel entry point:

```sh
python3 scripts/import_realized_fab_cohort.py admit \
  --database artifacts/2026-09-08-completed-milestones-schema6.sqlite \
  --cohort /Users/kian/Developer/semiconductor_atlas/artifacts/2026-09-07-tsmc-operating-fab-realizations-v1.json \
  --source-body /Users/kian/Developer/semiconductor_atlas/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --provenance /Users/kian/Developer/semiconductor_atlas/baselines/ai_critical_manufacturing_v1.json \
  --frozen-runtime-bundle /Users/kian/Developer/semiconductor_atlas/artifacts/2026-09-07-f901691-frozen-runtime-v1.json \
  --expected-runtime-sha256 4e1c106943fd38c0a531e428325b9fb06585d3f6a25c26c92a21a92e5965ed38 \
  --expected-runtime-commit f901691f9fba998d1e59b450e1f24aaba265f875 \
  --expected-cohort-sha256 74c6b9aea5e789c2bb0e802090f97ba2eb83e8b637c583ececc9b861b01aa776
```

For subsequent verification, replace `admit` with `verify` and keep all inputs identical. Verify
opens the database read-only and reconstructs the complete owned rows in memory. Replays retain
the original admission time and perform zero target writes. Extra or changed attached lineage
fails verification. A different cohort overlapping any existing source-report key is rejected:
this v1 route does not implement corrections, merge cohorts or assign canonical identities.

## Remaining north-star work

The October 13 [repository consolidation](repository_consolidation.md) integrated this code into
`main` after sealing the registered study with its original producer. Historical verification
and the paused source-check task use the retained original runtime; baseline release and evidence
bytes stay unchanged. The new core records still need
reviewed project/event identity, independent realization evidence and broader coverage including
unbuilt, delayed, cancelled and closed projects. They create no forecast/outcome pairs and provide
no evidence of forecast calibration or manufacturing-change detector performance.
