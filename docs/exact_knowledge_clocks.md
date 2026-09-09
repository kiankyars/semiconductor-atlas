# Exact knowledge clocks

Schema 10 compares knowledge timestamps as integer UTC microseconds. SQLite's `julianday`
rounds sub-millisecond differences: in schema 9, evidence retrieved one microsecond after
a claim could pass admission and validation, while a legitimate correction one microsecond
later could be rejected as out of order. This upgrade addresses that precision mismatch.

## Contract

The Atlas runtime registers the deterministic `atlas_timestamp_us` SQLite function before
migrations or normal connections. It uses timezone-aware timestamps, normalizes offsets to
UTC, and returns integer microseconds without a floating-point conversion. Invalid or naive
knowledge timestamps fail the affected guards. The supported precision is microseconds,
matching the existing Python models; this is not a nanosecond clock or proof that a supplied
timestamp reflects an independently verified real-world event.

Schema-10 admission, validation and database guards enforce the existing clock relationships:

- evidence retrieval, source-record observation, ingestion-run start, dependency admission,
  and subject/series/target creation cannot follow the claim's admission;
- corrections and supersessions must advance the knowledge clock, including by one microsecond;
- resolution inputs must have completed by resolution start; candidates, decisions and
  assignments must respect their evidence, run, entity and predecessor clocks; and
- assignment intervals cannot overlap in both valid time and knowledge time.

Exact assignment cutoff reads, release identity/review-run exports, coverage source-scope and
review-effect selectors, and GeoJSON source-term lineage now use the same clock. Claim cutoff
reads have used exact microseconds since schema 5.
Physical validity dates remain separate from knowledge timestamps.
An announcement still does not establish operation, qualification, utilization or output.

Direct SQL receives claim-lineage guards as well as the existing immutable-content and identity
guards. This is not a replacement for `validate_database`: callers must still use the complete
repository workflow and validator for evidence completeness, typed values and other semantic
invariants. Ingestion completion is not newly required to precede each claim's admission;
an importer may build claims while its run is in progress. A dossier's full completed-run
availability checks remain a separate, stricter read boundary.

## Migration and compatibility

Migration 10 rebuilds `claim_versions`, `ingestion_runs`, `entity_resolution_runs` and
`source_entity_assignments` to replace their rounded constraints. Original IDs, row values,
literal timestamp strings, indexes and immutable guards are retained. The first nine migration
files and ledger entries are not rewritten.

The migration preflights claim lineage, checks the rebuilt foreign-key graph, and runs complete
database validation under schema 10 before committing. That final check also covers retained
identity rows whose insert triggers are not replayed by a table rebuild. Any failure rolls back
migration 10, including its ledger entry, and restores the connection's foreign-key setting.
It does not delete, redate or otherwise repair inconsistent historical evidence.

Schemas 1–9 retain their original admission behavior when explicitly targeted. Historical
producers and snapshots remain frozen. Upgrading one database does not upgrade the scheduled
parent database or republish a release. A bare SQLite connection needs the Atlas function
registered before schema-10 writes or `PRAGMA integrity_check`; an absent function is an error,
not a fallback to rounded time. Use `database.connect`, `initialize` or `apply_migrations`, or
`register_clock_functions` on an existing connection. Registration is idempotent, including while
an outer validation query is still active. EEA portable-history restoration and the shared
read-only dossier/viewer connection register the function before their consumers run.

## Verification

`tests/test_exact_knowledge_clocks.py` covers one-microsecond boundaries, equivalent timezone
offsets, repository and raw-SQL rejection, independent corruption audits, corrections, identity
intervals, row-preserving migration and atomic rejection of naturally admitted schema-9 defects.
The retained-schema dossier tests deliberately continue using schema 9 to exercise read-time
rejection of legacy inconsistencies that new schema-10 writes now prevent.

This closes a temporal-integrity defect. It does not establish source completeness, measured
alert precision, independent outcomes, forecast calibration or a passed roadmap exit gate.

### Retained-image acceptance, September 9

The isolated installed-wheel check migrated a private backup of
`artifacts/2026-09-09-new-albany-project-statements-schema9.sqlite`. It matched all **543,225
existing rows in 30 tables**, including the original nine migration-ledger entries. All 69
indexes and 75 existing triggers remain; five claim-knowledge guards are added. Integrity,
foreign-key and full semantic validation passed. A second migration call made no changes.

The 85 matching query payloads cover both sides of the City of New Albany admission boundary:
`2026-09-09T05:52:00.817387Z` and `2026-09-09T05:52:00.817388Z`. They include complete raw
current/source claim views, release history and identity/review views, coverage, GeoJSON,
six search probes, and hydrated records and dossiers for seven named subjects. Each full
physical-current view still contains 104,286 claims. Only top-level schema-version metadata
is removed from versioned JSON comparisons; no claim payload or timestamp is normalized away.
The seven-subject dossier check is not exhaustive dossier coverage.

The worker ran under Python `-I -S -B`, with all 24 Atlas imports coming from the unpacked
wheel. All 103 packaged files also matched the working source tree byte-for-byte. The checker
installs a new database only after all checks pass and refuses to replace an existing output.

| Retained local artifact | SHA-256 |
| --- | --- |
| Schema-9 source image | `0901ee33eeada18c70e949411be3c9582117262ec4e8cfd87638681f25787b5d` |
| `artifacts/2026-09-09-exact-clocks-schema10-v2.sqlite` (310,259,712 bytes) | `a1d59ad714c7df0280335faf9c4c7fd73a4a31d9f838185f60f5d1b5052306cd` |
| `artifacts/2026-09-09-check-exact-clocks-migration.py` | `0608304d21e16be3ac26ab9e9fcc1075846deb9e1fc4792fd86fe398c61182e2` |
| `artifacts/2026-09-09-exact-clocks-migration-check-v2.json` | `ff33013ba83b4619da81135d92e221e88b8cc4eb18addd4df1407824ecc43b72` |
| Tested wheel in `artifacts/2026-09-09-schema10-dist-v2/` | `c1f6f717cea58ef125833e753158b78b631cdaafde2b17633e6401f7a589b299` |
| Built sdist in the same directory | `52e351e2fb379c15a690fbe1b4645135137840758e592fb5266f21d6b12272b4` |

The migration's new `applied_at` is its actual execution time, `2026-09-09 07:51:49` UTC.
This acceptance proves row preservation and query equivalence, not byte-identical rebuilding
of the schema-10 image. The larger SQLite image retains free pages from the table rebuild;
no vacuum or source-image rewrite was performed.

The first installed experiment found the active-query function-registration defect. It failed
without installing a successor and proved complete rollback; its failure receipt remains at
`artifacts/2026-09-09-exact-clocks-migration-check-v1.json`, SHA-256
`28b279598a789e656d0590732a8facb7c79cbbd9c1bb3bee508d800d576029e2`.
That failed v1 experiment is not an accepted schema-10 artifact.

Final validation: **2,022 core tests executed, 2,021 passed and one expected optional-payload
skip** (283.659 seconds); 12 web tests and 13 viewer-state JavaScript tests passed. The 59 new
clock/coverage tests pass, and the larger 61-test focused run includes the existing coverage
tests. The earlier full run exposed registration/restore defects and three stale latest-schema
assertions; the successful full rerun followed their repair.

The old City source-admission artifact still rebuilds byte-for-byte under its frozen producer:
`artifacts/2026-09-09-post-schema10-city-frozen-replay-v2.json`, SHA-256
`4bb2c38d028c2c43a7c7b5aec72f6cdd5143d217f462294f433c3fe3953f6653`.
The scheduled parent checkout/database, baseline, checksum ledger, old migrations, forecasts and
release artifacts remain unchanged. Nothing was pushed, published or rescheduled.
