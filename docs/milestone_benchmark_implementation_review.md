# Milestone benchmark implementation review

Status: implementation and independent regressions complete; no real study registered.

The prospective sidecar implementation in `semiconductor_atlas/milestone_benchmark.py` is a
separate evaluation-foundation step beyond the verified EEA integrity changes. It must not be reported as
calibrated forecasting or as an accepted real-world prediction/outcome dataset.

## Reproduced acceptance/verification mismatch

A disposable schema-5 fixture contained one source-native project, one scalar scope claim and
two expected milestone claims: `production_start` and `high_volume_production`. A study case
declared the production-start event. Initial freezing used the production-start input and a
prospective date interval.

Replacing the frozen input reference with the HVM claim, rebuilding its exact database lineage
graph, and recomputing the outer artifact hash caused `verify_vintage` to succeed. The initial
`freeze_vintage` rejects that event substitution. No real database, study or prediction was used
or changed in this probe.

Corrected during draft review: expected-event/status validation is now shared between creation
and replay. The independent `test_milestone_benchmark_review.py` regression verifies that a
source-valid but event-incompatible input is rejected even when packet hashes and database
lineage are internally consistent. The combined draft benchmark and independent replay tests
passed 29 tests. Hash integrity does not substitute for the producer's semantic contract; this
is not an authenticity claim about self-signed local timestamps.

A final independent probe changed the roster's source-native stable key while retaining the
actual entity ID and graph, then resealed both study and vintage. Replay had omitted the
registration-time entity-kind/stable-key check. Registration and replay now share exact project
identity validation; the independent re-sealed-key regression rejects that mismatch before scoring.

## Publisher-content boundary corrected

The early draft embedded full outcome source bodies as base64. The accepted implementation
instead retains a sorted manifest of source hashes and byte sizes; review and scoring require
caller-supplied local bodies and reverify all referenced bytes and excerpts. A missing or altered
body fails scoring. Retained publisher bodies stay local; see the
[release rights boundary](ai_critical_baseline_v1_gap_matrix.md#public-release-rights-boundary).
Bounded excerpts remain explicitly local-review material, not cleared for redistribution. Source
hashes and exact spans do not grant permission to publish quoted content.

## Transitive lineage, held-outs and publication

The independent audit also reproduced a training input with a held-out project dependency and a
derived claim whose documentless ingestion source/family was omitted, allowing future provenance
to pass the cutoff. The verifier now traverses every producing run's source/family and checks its
creation clocks. Project-bearing transitive inputs must respect the registered project group,
geography and partition; unregistered or cross-group dependencies fail. These are checks on
structured inputs, not proof that an opaque model was never trained on held-out data.

The early writer could delete another writer's replacement on failure and report success under
a renamed output directory. Publication now uses a private, inode-checked staging file, exact-byte
verification, no-replace linking and parent-path identity checks. Cleanup touches only its staging
inode, never an output target. Failure after publication may leave a complete artifact requiring
verification. Independent before/after-link swap tests confirm failure without deleting another
writer's replacement.

The five-command CLI has a synthetic end-to-end test covering registration, freezing, verification,
reference-only outcome review and scoring. The source database stays byte-identical and existing
output bytes are preserved. Separate tests reject duplicate/incorrect local source references,
unbounded or duplicate-key inputs, and symlinked/missing databases.

## Final local validation

- 1,305 core tests passed, including 43 benchmark, CLI and independent regression tests.
- 12 web tests and Python compilation passed.
- Source and wheel distributions built successfully with isolated build tooling; project
  dependencies were not changed.
- Both registered detector code manifests (24 and 25 files) rehashed without drift.
- The final producer SHA-256 is
  `e24b164e099fa8134d38f2048f529413430137b181c1e4f770849e1aec72cc8e`.
- The real Fab 2 identity/lineage check below was repeated with this final producer and zero
  attempted database writes. The retained database and legacy checksum ledger stayed unchanged.

## Real evidence remains separate

The [forecast-readiness audit](forecast_readiness_2026-09-07.md) records a verified retrospective
commercial-production outcome lead for Fab 21. It is not yet admitted into this sidecar system,
not automatically a source-native project identity, and not an HVM event with exact calendar
bounds. It cannot create a pre-outcome system forecast vintage. No real prediction registration,
outcome admission, forecast score, or capacity-calibration result has been produced here.

The real working database's reviewed Fab 2 target also passed `_graph` and exact-event input
verification with SQLite `mode=ro`, `query_only`, and all DML denied. No write was attempted.
The source-native project is `8f33e76f-ac2c-5962-a9a2-2cd1bcb55d86`; its latest claim is
`4870d11d-3555-52f9-bf9a-8d67e200ecff`, value SHA-256
`10675b9521ed7a46f83abd0d743b59a82bfb91f0ed0e330c8b8e61d05a727c5b`.
It is `production_start` / `expected`, H2 2027, with null base date, effective time and confidence.
At cutoff `2026-09-07T19:00:00Z`, the graph contains one claim, two documents and two evidence links,
with SHA-256 `e3055ac4137a259e858bd8fbae64ca855c354be822be9c0677871e5ce0f95fee`.
The database stayed byte-identical at SHA-256
`7b60f67911a0cb73ce17e47451d534c6e5738e7cd804398734fdf652385db778`.

This is real-input feasibility, not a forecast: a justified prediction and reviewed multi-project
roster still need to be developed. Two Fab 2 document versions are one project, not independent
cases. Legacy past targets cannot become prospective vintages; Amkor/SK hynix `mass_production`
and OSM `construction_observed` claims are not aliases for this contract's supported events.

The next real evidence step is a separately reviewed Fab 21 commercial-production-commencement
observation with year-2024 bounds and no midpoint, not an all-abstention forecast demonstration.
The current core `MilestoneValue` only permits literal/precision-bearing, null-midpoint dates
for `expected` status. A direct `completed` / `commercial_production_commencement` / year-2024
constructor probe rejects with `source target periods require expected status and no midpoint`.
That frozen core contract must not be bypassed by raw SQL, an invented July 1 date or an
`expected` label. A deliberate realized-observation contract is needed before admission; this
prospective benchmark also cannot manufacture a vintage preceding the already-known outcome.
