# September 8 completed-milestone core admission

This is a local source-native integration in the isolated `codex/completed-milestones-v6`
checkout, not a new public baseline or a forecasting result. Existing scheduled execution
continues from the unchanged parent checkout.

## Actual retained result

The new schema-6 database is
`artifacts/2026-09-08-completed-milestones-schema6.sqlite`, relative to the isolated checkout
`/Users/kian/Developer/semiconductor_atlas/artifacts/core-v6-evolution`.
It is 240,381,952 bytes, SHA-256
`945518cd5b2de08719a18b648ddae06a01ee79fbaf34ac8dcd353375667253a9`.

The full 16-row [TSMC operating-fab cohort](tsmc_operating_fab_realizations_2026-09-07.md)
was admitted at `2026-09-08T18:23:24.533255Z`, run
`2e817f05-e468-5a96-a0f1-343ab4412432`. It added:

| Core data | Added |
| --- | ---: |
| Source-native facility records | 16 |
| Source-reported completed commencement milestones | 16 |
| Source records retaining exact case and original sidecar time | 16 |
| Evidence links: row support, header context, introduction context | 48 |
| Source documents / ingestion runs | 1 / 1 |
| Numeric capacity, geometry, canonical assignments, physical-current claims | 0 |

The cohort's original 15 new / 1 carried / 0 deferred decisions are preserved in metadata.
All 16 are new **core** admissions at the same current clock; Fab 21's original sidecar
admission is not rewritten or substituted for this core clock. The event remains exactly
`commercial_production_commencement`, with year bounds, null midpoint, null effective dates
and null confidence. The source-native Fab 2 is not joined to Arizona Fab 2 by its label.

The local admission receipt is
`artifacts/2026-09-08-realized-cohort-core-admission-v1.json`, 6,956 bytes, SHA-256
`964b290de613f98005c93a772b6866f2feb1b9d772fccf185d9b32785bc226a1`.

## Verification evidence

The separate local validation record is
`artifacts/2026-09-08-realized-cohort-core-validation-v1.json`, SHA-256
`06e94b0856121bd99a8941e7591e13ee5d47b808a54b68d4a1863266292f2f11`.
It records the exact per-table parent counts and additions, and verifies:

- Every original row survived, including all 104,420 original claims and their lineage.
- The original schema-5 database remains byte-identical, SHA-256
  `7b60f67911a0cb73ce17e47451d534c6e5738e7cd804398734fdf652385db778`.
- Before admission, the populated migration preserved every value, hash and trigger; its
  schema-only derivative hash was `b8b89be97083fbf45908bce6f07e357ca3fd3622eb2bafc497ee6fe367cf86fc`.
  That working copy subsequently received the recorded admission; it is not a separately retained
  schema-only artifact.
- The core now has 104,436 claims. The 16 new claims are absent one microsecond before admission,
  present in knowledge-time source claims at admission, and absent from physical-current claims.
- Exact historical source verification ran using the retained `f901691` runtime and original
  10,355,816-byte source body, not the evolving package's historical verifier.
- Immediate exact core replay succeeded on a read-only connection with INSERT, UPDATE and DELETE
  explicitly denied. There were zero target changes, and the entire database hash stayed unchanged.
- Database validation and foreign-key checks returned no errors; SQLite integrity returned `ok`.

Importer code hashes at admission:

| File | SHA-256 |
| --- | --- |
| `semiconductor_atlas/realized_cohort_import.py` | `67821140c6f27a97aeef7f76fc5edbe0508ed4fc9639967b131df7d0a1e221ed` |
| `scripts/import_realized_fab_cohort.py` | `7fdd3c50f41f6d916376adf78a3dbdcae11a92dceb641f9b25214fd34aad350c` |
| `semiconductor_atlas/models.py` | `6796610aa17c1df4908758661301313769a841470dc212d0100c2d8c14490717` |
| `semiconductor_atlas/migrations/0006_completed_milestone_precision.sql` | `74ad92a7f695fe81349b317ccb5042749ef21a579c3fc98532b7d400648d63db` |

The ingestion run also retains the migration/controller fingerprints, trusted cohort/runtime
identities and full frozen verification receipt. See the [workflow](completed_milestone_core.md)
for the replay command and exact original input paths. No source bodies or excerpts are embedded;
the new core and receipts remain local-review-only and are not publication candidates.

## Local export verification

Two independently generated local exports, `artifacts/2026-09-08-completed-core-local-export-a`
and `artifacts/2026-09-08-completed-core-local-export-b`, match across all 20 files and
733,021,363 bytes per export. Their manifest SHA-256 is
`41b2485a4b1384c28d3933ee5671bd865043d4a8ed825b8e2a723c448ee9378c`.
All 16 new reports retain their exact event type, year precision, null midpoint, unknown
effective date and confidence in `source_claims.jsonl`; none appears in physical `claims.jsonl`.
The database hash remained unchanged. These are local validation exports, not public releases.

The file-by-file export check is
`artifacts/2026-09-08-completed-core-local-export-check-v1.json`, SHA-256
`02ae261bb3d059d3732d84098a196caf50bff194ee3ffdfc0319bf1346cd4473`.
The exports contain 96,951 source claims and 104,436 history rows; these totals include inherited
registry data and must not be read as counts of verified operating facilities.

## Regression and build checks

The final core suite ran 1,421 tests in 243.666 seconds: 1,420 passed and one optional test was
skipped because this isolated checkout has no ignored baseline source payloads. The original
payloads were independently exercised by the unmocked frozen cohort verification above. All
12 web tests passed, as did Python syntax checks and source/wheel builds. Build tooling ran in
a temporary virtual environment; the parent interpreter and scheduled environment were not changed.

The 29 focused precision/import tests include complete eight-value-kind migration preservation,
foreign-key ON/OFF restoration, raw-SQL calendar validation, actual admission cutoff export,
deferred inventory, exact replay with writes denied, source/runtime damage, commit-boundary
changes, duplicates, extra lineage, rollback, and clock regression. Synthetic mapping tests
mock the subprocess boundary; they are not substitutes for the recorded real-source replay.
Independent review found and verified the fix for initial-admission replay-clock regression.

## What this does not establish

This closes the completed/year/null-midpoint storage and bounded source-native core integration
gap in the isolated branch. It does not close canonical project/event identity, independent
physical corroboration, non-survivor coverage, non-announced capacity, forecast/outcome pairing,
calibration or production-alert readiness. The table still selects operating fabs, not all
historical projects. No scheduled workflow was rerouted and no active study was repinned.
