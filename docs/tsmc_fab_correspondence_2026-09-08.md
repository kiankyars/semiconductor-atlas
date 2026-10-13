# September 8 TSMC source-native correspondence review

This review enriches the sixteen already admitted operating-fab rows with source-reported
geographic labels and two more precise commercial-production assertions. It does not resolve
canonical project phases or create a forecast/outcome pair.

## Evidence gained

The exact retained 2025 Form 20-F body is 10,355,816 bytes, SHA-256
`c3ebd05cd8fb383f53fc21a0ac497ee12cf908b709c380f9bec4f39c4916647b`.
The operating-table `Fab(1)` header refers directly to footnote (1), at UTF-8 byte span
`[1163178,1165235)`, SHA-256
`85f88ad7d616352d0683bc79d9a5eabd4ccfe5c5ee0d320f11a52cd4759d1ba6`.

| Source-native fab labels | Source-reported location |
| --- | --- |
| 2, 3, 5, 8, 12, 20 | Hsinchu Science Park |
| 6, 14, 18, 22 | Southern Taiwan Science Park |
| 15 | Central Taiwan Science Park |
| 10 | Shanghai, China |
| 11 | Washington State, United States |
| 16 | Nanjing, China |
| 21 | Arizona, U.S. |
| 23 | Kumamoto, Japan |

These are text scalar source claims, not point geometry, normalized country codes, or locations
backdated to historical production commencement. Their effective dates and confidence remain
unknown. No new facility entities are created.

Two narrative passages explicitly identify the manufacturing fab and the same commercial
commencement event at month precision:

| Fab | Additional source assertion | Full calendar bounds | Preserved table assertion |
| --- | --- | --- | --- |
| 11 | October 1998 | 1998-10-01 through 1998-10-31 | 1998 |
| 16 | April 2018 | 2018-04-01 through 2018-04-30 | 2018 |

Both month assertions use `commercial_production_commencement`, `completed`, and null midpoint.
They occupy separate claim series with dependencies on the original source-native year claims.
The year assertions are neither overwritten nor superseded. This is **16 distinct retrospective
fab subjects**, not 18 independently observed production outcomes. All reports come from one
issuer filing and retain the operating-table's survivor selection.

The exact [review](../review_plans/2026-09-08-tsmc-operating-fab-correspondence.json), SHA-256
`3686fc58176cd9eec4acab39fa8a7d7c22022777b96e9dc26c14fe3e465321c9`, records the complete sixteen-row
denominator, review exposure, source spans, and seven rejected/deferred correspondences.
The source-specific validation rule verifies the geographic subject groups and the exact
fab/commercial-month wording; it is not a general-purpose or independently calibrated resolver.

## Matches not made

- Table Fab 2 is in Hsinchu, so it is not the reviewed NIST Arizona second-fab project.
- Fab 21's narrative covers the Arizona manufacturing entity and multiple Phoenix facilities.
  The table row does not uniquely identify phase 1. The existing baseline's first-fab scope
  requires its own complete cross-source adjudication; this review does not rewrite that release.
- Fab 23's JASM narrative also describes a second facility. A unique first-phase identity is
  unresolved.
- Fab 11's July 1998 trial production is not its October commercial commencement.
- Fab 10's vague late-2004 wording does not justify an invented month or quarter.
- Fab 21 first-facility HVM and Fab 23 volume production are not silently aliased to the table's
  commercial commencement event.

Corporate manufacturing-entity correspondence is not a facility/corporation identity merge.
Neither incorporation dates nor current technology labels become historical commissioning
attributes. All sixteen canonical project-phase correspondences remain unresolved.

## Immutable successor workflow

The [original schema-6 admission](completed_milestone_core_2026-09-08.md) is an immutable snapshot.
Its v1 verifier intentionally rejects extra attached lineage, including otherwise legitimate
new statements on its source-native entities. This workflow preserves that exact parent and
constructs a **separate successor**; it does not weaken or edit the original importer.

`scripts/enrich_tsmc_fab_correspondence.py` is source-checkout-only, not an installed CLI or a
schema migration. The trusted review binds the exact parent image, cohort and source bytes.
The tool reads that image into a private in-memory database, adds only the reviewed assertions,
checks database integrity and lineage, and installs a new output without replacement. It never
opens the parent database for writes. Existing scheduled execution remains in the unchanged
parent checkout.

Admission records a new local system-UTC knowledge clock after source-review validation. Original
publication, retrieval, cohort, parent-admission and review clocks stay separate. This is not
independent timestamp attestation or evidence that the system knew these assertions historically.

Verification requires both the trusted review hash and retained successor hash. It reconstructs
the entire successor from the original parent and recorded admission clocks, then compares exact
database bytes. Parent, source, review, successor and implementation are rechecked before success.
No write is made to either retained database during verification. Later enrichment requires
another explicit successor, not mutation of either verified snapshot.

Run from the isolated `artifacts/core-v6-evolution` checkout:

```sh
python3 scripts/enrich_tsmc_fab_correspondence.py admit \
  --parent artifacts/2026-09-08-completed-milestones-schema6.sqlite \
  --review review_plans/2026-09-08-tsmc-operating-fab-correspondence.json \
  --source-body /Users/kian/Developer/semiconductor_atlas/source_snapshots/2026-08-20-ai-critical-manufacturing-v1/tsmc-2025-form-20f.html \
  --expected-review-sha256 3686fc58176cd9eec4acab39fa8a7d7c22022777b96e9dc26c14fe3e465321c9 \
  --output artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite
```

Use `verify` instead of `admit` with the same paths and add `--expected-output-sha256` with the
retained successor hash. The admission command refuses an existing output; it is not a refresh
or overwrite operation. These local artifacts do not authorize publisher-body redistribution,
public release, production alerts, calibrated forecasts, or investment conclusions.

## Retained admission and verification

The actual successor is `artifacts/2026-09-08-tsmc-fab-correspondence-schema6.sqlite`,
241,635,328 bytes, SHA-256
`d31fd0597ddf188f76c44d2758f46f95d00f0a22169c1360b34b7c390aaeaeeb`.
The new core admission clock is `2026-09-08T18:57:31.175514Z`, run
`5066483e-9563-5b9c-93a6-7bb8780cd37d`.

The admission receipt is `artifacts/2026-09-08-tsmc-fab-correspondence-admission-v1.json`,
5,626 bytes, SHA-256
`f128e5cb7db9a5a337f829fbfdad16d1fa1b072bb20cd567e4dde6348f4faec5`.
The separately generated validation record is
`artifacts/2026-09-08-tsmc-fab-correspondence-validation-v1.json`, 16,083 bytes, SHA-256
`02a23e29675759740a5f8a8bf99f8e6cfda38f8473a513efdc273f5917398faa`.

Verified against the actual retained evidence and databases:

- The CLI reconstructed the full successor byte-for-byte using the trusted review and successor
  hashes. It made no retained-database writes.
- A separate read-only, INSERT/UPDATE/DELETE-denied SQL audit compared every original table row
  with its successor counterpart. All original rows, schemas, indexes and triggers survived.
- The 104,436 original claims are unchanged; there are now 104,454 claims. Additions are exactly
  18 claim series/versions/values, 16 source records, 54 evidence links, 18 dependencies,
  one ingestion run and two ingestion-run document roles. There are no new entities, source
  documents, geometry, capacity, relationships or canonical assignments.
- All eighteen new assertions are absent one microsecond before their new admission clock and
  present at admission. All sixteen original year reports remain visible. No new assertion is
  present in physical-current claims; all new effective dates and confidence values are null.
- Foreign-key checks and database validation returned no errors; SQLite integrity returned `ok`.
  Both retained database hashes remained unchanged through verification.
- The original core import separately passed its unchanged frozen-runtime replay before
  enrichment. The scheduled schema-5 database, baseline input and checksum ledger remain
  byte-identical, and the parent checkout remains clean.

Code at admission: `semiconductor_atlas/fab_correspondence.py` SHA-256
`03a72cf5d7a6283db2dc22ce578eefed4add43ad525176a0020572da6046dc36`;
`scripts/enrich_tsmc_fab_correspondence.py` SHA-256
`47793667c45b78fbbd641990e8d146e1cdae2c2e1326f27384e40174fe40a584`.
The run retains all used module/migration fingerprints and interpreter identity.

All 22 new synthetic tests passed, including exact reconstruction, original-row preservation,
knowledge cutoffs, wrong-fab/trial-date rejection, trusted-hash failures, input changes, target
mutation/removal during replay, and failure before publication. Only their small fixture's
publisher SHA is substituted; review parsing, core mapping and reconstruction run unmocked.
The existing suite ran 1,421 tests in 236.851 seconds with 1,420 passing and the expected single
optional source-payload skip. All 12 web tests, Python syntax checks and source/wheel builds
passed. These engineering checks do not certify independent outcome truth or forecast accuracy.
