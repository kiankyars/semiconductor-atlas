# Reviewed source-native project targets

This gate integrates explicitly reviewed source statements with the core claim store. It must not
turn a planned date into an attained milestone or assign a source-native project to a canonical
facility without identity evidence.

## Representation

Schema 5 preserves unknown claim-effective dates and uncalibrated confidence as null. Source-stated
calendar periods retain their literal wording and precision, with no invented midpoint. Existing
dated values and historical schema-4 release payloads retain their original representation.

Physical-world queries continue to exclude claims whose effective date is unknown. A separate
knowledge-time source-claim view exposes what retained document versions say, including source
lineage, actual database admission, and explicit unknown effective time. Schema-5 source knowledge,
history, entity and source-input cutoffs preserve microseconds; source export visibility uses the
explicit admission clock rather than the earlier validation-start clock. Historical source versions
remain auditable; this view is not a current-world facility census or a calibrated forecast.

The TSMC review selects an unassigned source-native Fab 2 project within the NIST Arizona page.
The old document says 2028; the later retained version says second half of 2027. Both are source
statements accepted at the actual new database-admission time, not backdated to retrieval. Separate
document-version assertions preserve both observations without inventing an intervening period
when the system accepted the old target as current. Their comparison is a source-target revision,
not an exact days-early measure, proof of production, or independently corroborated event.

The original seven-company manufacturing baseline and its first-fab TSMC scope remain unchanged.
Source-native project identity, canonical facility assignment, company ownership, and production-unit
containment are distinct decisions. No capacity, qualification, yield, utilization or geometry
claim follows from this target revision.

## Acceptance and replay gates

- Exact retained before/after bytes and evidence spans, separately reviewed claim scope, and current
  source-text handoff tokens must verify before a transaction can accept claims.
- Raw publisher bodies remain local. Public derivatives contain only reviewed values, compact
  evidence/locators, hashes, and required attribution.
- Unknown-effective statements must appear in knowledge-time exports and history, but not silently
  enter physical-world, legacy midpoint-alert or forecast calculations.
- A populated schema-4 database must migrate without losing rows, lineage, foreign keys, immutable
  content guards or old value hashes. Original databases and release archives are not rewritten.
- Exact replay must use the recorded admission clock and perform no writes; failed verification or
  conflicting input must roll back without partial acceptance. The immutable ingestion run retains
  the complete helper-code and migration fingerprint. Later code drift is not an exact replay.
- The source queue is reserved through core commit. The source handoff cannot change after its final
  check while the core database is being committed. Identical period values are reaffirmations, not
  target revisions; overlapping or changed periods do not imply an exact acceleration duration.

SQLite table reconstruction follows its [documented generalized ALTER TABLE procedure](https://www.sqlite.org/lang_altertable.html#making_other_kinds_of_table_schema_changes),
with foreign-key validation before commit. A new working copy of the existing schema-4 database
preserved all 104,286 claims, 100,758 evidence links, 13,424 dependencies, 27 data tables, 69 triggers
and 23 indexes. Every original-column row was hashed and compared, all old migration records were
preserved, and full database validation passed. The original 251,465,728-byte database retains SHA-256
`c6083299cae65a96890dff831ca201566de82dae7532ca555c65391f7ee21ca9`.

## Command and local evidence

The importer requires an existing schema-5 working database and a separate, explicitly reviewed
claim decision. It does not migrate a database implicitly, accept a discovery/access approval as a
claim review, or publish a release:

```sh
python3 -m semiconductor_atlas accept-project-targets \
  --database artifacts/2026-09-07-source-project-targets.sqlite \
  --review review_plans/2026-09-07-tsmc-fab2-project-target-claims.json \
  --reference-root . \
  --source-queue artifacts/2026-09-07-amkor-source-review.sqlite
```

The [source-text review](../review_plans/2026-09-07-tsmc-fab2-source-text-adjudication.json) corrects
the earlier lead's narrative locator and retrieval-clock interpretation without rewriting it.
Its source-queue handoff was admitted at `2026-09-07T09:07:41.724282Z`; the queue has 20 events,
12 verified packets, eight candidates, and zero pending/recheck items at that cutoff. This is not
complete coverage. The separate [claim review](../review_plans/2026-09-07-tsmc-fab2-project-target-claims.json)
binds exact target literals, calendar bounds, both narrative/timeline byte spans and post-handoff
queue state. It is not a source refresh or claim of early detection.

Local evidence is under ignored `artifacts/`; a Git clone alone does not contain the publisher
snapshots, source queue or working database. Historical replay requires retained input paths and
the pinned implementation. The migration summary and independent compatibility checks are retained
in `2026-09-07-project-target-migration-verification.json` and
`2026-09-07-project-target-compatibility-verification.json` there. No raw source redistribution,
new public data release, forecast or delivery eligibility follows from this gate.

## Verified September 7 pilot

The two claims were admitted at `2026-09-07T09:28:12.852741Z` under run
`574fa308-9352-5cf1-939e-9f7a48de055a`, on project `8f33e76f-ac2c-5962-a9a2-2cd1bcb55d86`.
The prior target claim is `b350581c-3257-5e6c-aabb-aab20d884e9c`; the later target claim is
`4870d11d-3555-52f9-bf9a-8d67e200ecff`. There are four evidence links and two source records, with
null effective dates, confidence and midpoint, and no canonical assignment. Their shared source
family denotes common provenance, not two independent confirmations.

Read-only replay passed with SQLite writes denied and the database hash unchanged. All original
rows in all 27 data tables survived the admission. Six historical projections include the prior
observations, source handoff, validation start, one microsecond before admission, and admission:
none exposes these statements before admission; both appear at admission; physical-world claims
remain empty for this project even at a 2030 world cutoff.

The local core export `artifacts/2026-09-07-project-target-core-export-v1` rebuilt byte-identically
across all 20 files including its manifest. Its 96,803 source statements include the inherited
registry; **only two** are newly reviewed Fab 2 statements. The whole working database is not a new
review of inherited evidence and must not be confused with the seven-company r3 baseline.

| Local artifact | SHA-256 |
| --- | --- |
| Working core database | `18c0d0ad1d6ddbbd9d97b376f2a98d35fea16676cb79df46db317df045e65b82` |
| Core export manifest | `0466588448beb040661ffaece6fa537cd3c4290f270a2c0a26d4b9c0e04d8b90` |
| Pilot verification JSON | `f0b27b19d5f2c2947c8253f67638b17fe1620291d64d4d802579fe737c62a1e9` |
| Migration verification JSON | `bf629e7a6d9f099afa201041b96f1e09771cea201224ed6a05d595f970b20e1e` |
| Compatibility verification JSON | `bd6d9dfd0f7cc9c874a1bd4d2f66f4cdafb65e32d352979a7d0e07527793cc55` |

The full local suites passed 856 core and 12 web tests. All 353 entries in the original
`LOCAL_DATA_SHA256SUMS` passed, with the ledger unchanged. r3 archive/producer, Samsung handoff,
Amkor frozen diagnostic, all four historical coverage versions and every source/discovery queue
event cutoff passed independent regression checks. A green build does not complete blind detection
evaluation, facility identity review, broader source coverage, calibrated forecasts or supply-demand
signal validation.
